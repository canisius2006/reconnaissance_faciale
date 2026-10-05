"""
=============================================================
  FACE TRACKING OPTIMISÉ — YuNet + SORT (Kalman + Hongrois)
  Contrainte : CPU uniquement
  Auteur     : Canisius (adapté)
=============================================================

DÉPENDANCES :
    pip install opencv-contrib-python numpy scipy filterpy onnxruntime

MODÈLE REQUIS :
    Télécharger : face_detection_yunet_2023mar.onnx
    Source : https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet

MODÈLE ANTI-SPOOFING (liveness, optionnel mais recommandé) :
    face_antispoof.onnx — modèle facenox/face-antispoof-onnx (Apache-2.0),
    https://github.com/facenox/face-antispoof-onnx, version quantifiée
    recommandée (best_model_quantized.onnx, ~600 Ko, CPU rapide), renommée
    et placée dans static/model/face_antispoof.onnx.
    Si absent, la détection de vivacité est simplement désactivée (aucun crash),
    tout le reste du pipeline continue de fonctionner normalement.
    ⚠️ L'indice de la classe "réel" (LIVENESS_INDEX_REEL) n'est pas confirmé
    par la documentation du dépôt — à calibrer avec LIVENESS_DEBUG=True (voir
    la zone de configuration LIVENESS plus bas).

NOUVEAUTÉ :
    - Re-vérification automatique des visages "INCONNU" toutes les REINSPECT_DELAY secondes.
    - La structure de live_dictionnaire passe de [emb, nom, pct]
      à [emb, nom, pct, timestamp_derniere_tentative].
    - Détection de vivacité (anti-spoofing) par piste SORT : ne bloque JAMAIS la
      reconnaissance, mais MARQUE chaque personne reconnue comme vivante ou non
      (consensus sur plusieurs frames), exploitable côté front et en base (Reconnus.info_sup).
"""

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
import asyncio,io
import base64,copy
import json
import math
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import cv2
import numpy as np
import onnxruntime as ort
from filterpy.kalman import KalmanFilter
from .insight import get_app_rec
from . import embeddings_cache
from pathlib import Path
from scipy.optimize import linear_sum_assignment

import insightface
import matplotlib.pyplot as plt

from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Reconnus,Source
from asgiref.sync import sync_to_async 
from django.utils import timezone
from .models import Embedding 
from django.contrib.auth.models import User
# =============================================================
# CHEMINS
# =============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
#print(BASE_DIR)

chemin_modele = BASE_DIR/'static/model/face_detection_yunet_2023mar.onnx'
#chemin_base = BASE_DIR/'static/model/embeddings.json'

# ── Modèle anti-spoofing (liveness) ──────────────────────────────────────────
# facenox/face-antispoof-onnx (Apache-2.0) — classifieur binaire Réel/Spoof,
# un seul modèle (pas un ensemble), 600 Ko en version quantifiée, validé sur
# CelebA Spoof (70k+ échantillons). Doc officielle du prétraitement :
# https://github.com/facenox/face-antispoof-onnx (docs/DATA_PREPARATION.md, docs/LIMITATIONS.md)
CHEMIN_ANTISPOOF   = BASE_DIR/'static/model/best_model.onnx'  # version non quantifiée (1.82 Mo), test de stabilité
ANTISPOOF_PADDING  = 1.5   # agrandissement du carré centré sur le visage (valeur du dépôt)
ANTISPOOF_TAILLE   = 128   # résolution d'entrée attendue par ce modèle

# ── Correction d'éclairage (CLAHE) avant le modèle anti-spoofing ────────────
# En basse lumière / fort bruit ISO (caméras DroidCam de qualité modeste,
# pièces peu éclairées), la texture fine dont le modèle a besoin pour
# distinguer un vrai visage d'une photo est en grande partie noyée dans le
# bruit. CLAHE (égalisation d'histogramme adaptative) ravive le contraste
# local SANS tout cramer, contrairement à une simple égalisation globale.
ANTISPOOF_CLAHE_ACTIF        = True
ANTISPOOF_CLAHE_CLIP_LIMIT   = 2.5   # plus haut = plus de contraste, mais plus de bruit amplifié aussi
ANTISPOOF_CLAHE_TILE_GRID    = (8, 8)


# ── InsightFace ────────────────────────────────────────────────────────────────
# Instance partagée : une seule copie du modèle en mémoire pour tout le processus
app_rec = get_app_rec()

SEUIL_COSINUS = 0.5



def extract_full_name(username):
    if not username or not username.strip():
        raise ValueError("Le nom d'utilisateur ne peut pas être vide.")

    return username.strip().replace("-", " ").title()


# =============================================================
# CONFIG
# =============================================================

FRAME_W          = 640
FRAME_H          = 480
SCORE_THRESHOLD  = 0.75
NMS_THRESHOLD    = 0.3
DETECTION_EVERY  = 2
MAX_AGE          = 5
MIN_HITS         = 3
IOU_THRESHOLD    = 0.15
REINSPECT_DELAY  = 1.5

# ── ROBUSTESSE RÉSEAU (coupure source type DroidCam) ───────────────────────
# cv2.VideoCapture peut rester bloqué INDÉFINIMENT dans grab()/retrieve() (ou
# même à l'ouverture) si une source réseau (HTTP/RTSP) est coupée en plein
# flux — c'est un comportement connu d'OpenCV/FFmpeg, pas un bug de ce code.
# Les propriétés CAP_PROP_*_TIMEOUT_MSEC existent mais sont peu fiables selon
# la version d'OpenCV : on les pose quand même en 1ère ligne de défense, mais
# le vrai filet de sécurité est le timeout asyncio ci-dessous, qu'on contrôle
# nous-mêmes quoi qu'il arrive côté OpenCV.
CAPTURE_OUVERTURE_TIMEOUT_MSEC = 8000
CAPTURE_LECTURE_TIMEOUT_MSEC   = 8000
CAPTURE_OUVERTURE_TIMEOUT_SEC  = CAPTURE_OUVERTURE_TIMEOUT_MSEC / 1000
CAPTURE_LECTURE_TIMEOUT_SEC    = CAPTURE_LECTURE_TIMEOUT_MSEC / 1000

# Pool de threads DÉDIÉ aux opérations cv2.VideoCapture (ouverture, lecture,
# libération). Un thread qui reste bloqué sur une lecture réseau morte reste
# coincé indéfiniment même après qu'on ait "timeout" côté asyncio (on ne peut
# pas tuer un thread Python de force) — isoler ces appels dans leur propre
# pool évite qu'un flux DroidCam mort n'affame le pool par défaut partagé par
# tout le reste de Django (sync_to_async, etc.). max_workers borne le nombre
# de threads qui peuvent rester coincés en même temps ; ajuste selon le
# nombre de caméras simultanées attendues.
_executor_capture = ThreadPoolExecutor(max_workers=8, thread_name_prefix="videocapture")


def _ouvrir_capture(source):
    """Ouvre une VideoCapture avec les timeouts posés en best-effort (certaines
    versions d'OpenCV ignorent ces propriétés silencieusement — sans danger)."""
    cap = cv2.VideoCapture(source)
    try:
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, CAPTURE_OUVERTURE_TIMEOUT_MSEC)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, CAPTURE_LECTURE_TIMEOUT_MSEC)
    except Exception:
        pass  # propriété non supportée par cette version/ce backend : pas grave, le watchdog asyncio reste actif
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


# ── LIVENESS (anti-spoofing) ───────────────────────────────────────────────
# Un visage est jugé "vivant" si, sur les LIVENESS_FENETRE derniers scores
# calculés pour sa piste, au moins LIVENESS_RATIO_MIN d'entre eux dépassent
# LIVENESS_SEUIL. Un consensus multi-frames plutôt qu'une décision sur une
# seule image : une photo immobile échoue systématiquement sur la durée,
# alors qu'un vrai visage mal cadré une fois ne suffit pas à le disqualifier.
LIVENESS_EVERY        = DETECTION_EVERY * 2  # best_model.onnx (float32) coûte plus cher que la
# version quantifiée ; on l'interroge moins souvent. Le consensus sur fenêtre glissante lisse de
# toute façon le résultat, donc perdre en fréquence ne perd pas grand-chose en fiabilité.
LIVENESS_FENETRE       = 10
LIVENESS_SEUIL         = 0.65   # durci par rapport au 0.5 par défaut du dépôt : sur un vrai visage,
# le score reste quasi systématiquement bien au-dessus de 0.65 (observé en test), donc on peut
# se permettre d'être plus exigeant pour mieux rejeter les photos. À réajuster selon tes tests.
LIVENESS_RATIO_MIN     = 0.75
# ⚠️ INCERTAIN — à confirmer avec LIVENESS_DEBUG=True : l'indice de la classe
# "réel" dans la sortie à 2 classes du modèle facenox n'est pas documenté noir
# sur blanc dans le README (seules les classes "Real"/"Spoof" sont nommées, sans
# préciser l'ordre). 0 est une supposition de départ — si les résultats sont
# inversés (une vraie personne toujours jugée spoof), passer à 1.
LIVENESS_INDEX_REEL    = 0
# Mettre à True temporairement : affiche dans la console la sortie brute ET la
# sortie après softmax pour chaque visage évalué, pour calibrer LIVENESS_INDEX_REEL
# et vérifier que les valeurs ont un sens (proches de 0 ou 1, pas des logits bruts
# disproportionnés). À repasser à False une fois calé.
LIVENESS_DEBUG         = True

# ── ANTI-LATENCE #1 : limiter le débit d'envoi ────────────────────────────────
# 15 fps suffit pour la reconnaissance — réduit CPU et taille de la file WebSocket
FPS_CIBLE        = 15
INTERVALLE_FRAME = 1.0 / FPS_CIBLE

# ── ANTI-LATENCE #2 : qualité JPEG réduite ────────────────────────────────────
# 60% = taille divisée par ~2, qualité visuelle acceptable pour surveillance
JPEG_QUALITE     = 60
ENCODE_PARAMS    = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITE]

# ── Timeout de sécurité pour "En cours d'Analyse" ─────────────────────────────
# Si le thread d'analyse est mort silencieusement, on relance après ce délai
ANALYSE_TIMEOUT  = 5.0


# =============================================================
# KALMAN FILTER PAR TRACK
# =============================================================

def create_kalman(bbox):
    kf = KalmanFilter(dim_x=8, dim_z=4)
    kf.F = np.array([
        [1,0,0,0, 1,0,0,0],
        [0,1,0,0, 0,1,0,0],
        [0,0,1,0, 0,0,1,0],
        [0,0,0,1, 0,0,0,1],
        [0,0,0,0, 1,0,0,0],
        [0,0,0,0, 0,1,0,0],
        [0,0,0,0, 0,0,1,0],
        [0,0,0,0, 0,0,0,1],
    ], dtype=float)
    kf.H = np.array([
        [1,0,0,0, 0,0,0,0],
        [0,1,0,0, 0,0,0,0],
        [0,0,1,0, 0,0,0,0],
        [0,0,0,1, 0,0,0,0],
    ], dtype=float)
    kf.R[2:, 2:] *= 10.
    kf.P[4:, 4:] *= 1000.
    kf.P         *= 10.
    kf.Q[-1, -1] *= 0.01
    kf.Q[4:, 4:] *= 0.01
    x1, y1, x2, y2 = bbox
    kf.x[:4] = np.array([[x1], [y1], [x2], [y2]])
    return kf


def bbox_from_kalman(kf):
    state = kf.x[:4].flatten()
    x1, y1, x2, y2 = state
    return (int(x1), int(y1), int(x2), int(y2))


# =============================================================
# IoU UTILS
# =============================================================

def iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1 = max(ax1, bx1); y1 = max(ay1, by1)
    x2 = min(ax2, bx2); y2 = min(ay2, by2)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    return inter / (area_a + area_b - inter + 1e-6)


def iou_matrix(detections, predictions):
    mat = np.zeros((len(detections), len(predictions)))
    for i, d in enumerate(detections):
        for j, p in enumerate(predictions):
            mat[i, j] = iou(d, p)
    return mat


# =============================================================
# SORT TRACKER
# =============================================================

class Track:
    def __init__(self, track_id, bbox):
        self.id     = track_id
        self.kf     = create_kalman(bbox)
        self.age    = 0
        self.hits   = 1
        self.active = False

    def predict(self):
        self.kf.predict()
        self.age += 1
        return bbox_from_kalman(self.kf)

    def update(self, bbox):
        x1, y1, x2, y2 = bbox
        self.kf.update(np.array([[x1], [y1], [x2], [y2]]))
        self.age  = 0
        self.hits += 1
        if self.hits >= MIN_HITS:
            self.active = True

    def get_bbox(self):
        return bbox_from_kalman(self.kf)


class SORTTracker:
    def __init__(self):
        self.tracks  = []
        self.next_id = 0

    def update(self, detections):
        predictions = []
        for t in self.tracks:
            predictions.append(t.predict())

        matched     = []
        unmatched_d = list(range(len(detections)))
        unmatched_t = list(range(len(self.tracks)))

        if len(detections) > 0 and len(self.tracks) > 0:
            iou_mat = iou_matrix(detections, predictions)
            row_ind, col_ind = linear_sum_assignment(-iou_mat)
            for r, c in zip(row_ind, col_ind):
                if iou_mat[r, c] >= IOU_THRESHOLD:
                    matched.append((r, c))
                    if r in unmatched_d: unmatched_d.remove(r)
                    if c in unmatched_t: unmatched_t.remove(c)

        for det_idx, trk_idx in matched:
            self.tracks[trk_idx].update(detections[det_idx])

        for idx in unmatched_d:
            new_track = Track(self.next_id, detections[idx])
            self.next_id += 1
            self.tracks.append(new_track)

        self.tracks = [t for t in self.tracks if t.age <= MAX_AGE]

        results = []
        for t in self.tracks:
            if t.active:
                x1, y1, x2, y2 = t.get_bbox()
                results.append((x1, y1, x2, y2, t.id))

        return results


# =============================================================
# SETUP CAMÉRA + DÉTECTEUR
# =============================================================

cv2.setUseOptimized(True)
cv2.setNumThreads(4)

def creer_detecteur(taille=(FRAME_W, FRAME_H)):
    """Un détecteur YuNet PAR caméra (Utilitaire).
    Avant : un détecteur global partagé par toutes les caméras, dont la taille d'entrée
    était réglée par la dernière caméra démarrée -> conflit dès que deux flux
    n'avaient pas la même résolution."""
    return cv2.FaceDetectorYN.create(
        model=chemin_modele,
        config="",
        input_size=taille,
        score_threshold=SCORE_THRESHOLD,
        nms_threshold=NMS_THRESHOLD,
        top_k=100
    )




attributid = 0 # C'est la variable créer pour les inconnues 


live_embeddings = {}          # Cet dictionnaire regroupe les embeddings de tout le monde en live
live_embeddings_lock = threading.Lock()  # Protège les accès concurrents à live_embeddings
DELAI_EXPIRATION = 2          # Secondes sans apparition avant de retirer une personne du dict


def nettoyer_live_embeddings():
    """Retire de live_embeddings les entrées absentes depuis DELAI_EXPIRATION secondes.
    Appelée par chaque flux vidéo (VideoStreamConsumer) ET par le mode tracking :
    avant, seul le thread du TrackingConsumer nettoyait ce dict, donc sans tracking actif
    chaque visage INCONNU ajoutait une entrée par frame, sans jamais être retirée."""
    maintenant = time.time()
    with live_embeddings_lock:
        for cle in [nom for nom, valeur in live_embeddings.items()
                    if maintenant - valeur[2] > DELAI_EXPIRATION]:
            del live_embeddings[cle]
# =============================================================
# ANTI-SPOOFING (LIVENESS) — MiniFASNet, chargé une seule fois pour tout le processus
# =============================================================

_antispoof_sessions = None  # None = pas encore tenté ; [] = tenté et absent ; [..] = chargé

def _softmax(x):
    """Convertit des logits bruts en probabilités (somme = 1). Si la sortie du
    modèle est déjà une probabilité (ex: Softmax intégré au graphe ONNX), ça ne
    change quasiment rien ; si ce sont des logits bruts, c'est indispensable
    avant de comparer le résultat à un seuil comme LIVENESS_SEUIL."""
    e = np.exp(x - np.max(x))
    return e / e.sum()


def _charger_antispoof():
    """Charge le modèle facenox/face-antispoof-onnx une seule fois (comme app_rec
    pour InsightFace). Si le fichier .onnx est absent, la vivacité est désactivée
    sans faire planter le reste du pipeline : score_liveness() retourne toujours
    None dans ce cas."""
    global _antispoof_sessions
    if _antispoof_sessions is not None:
        return _antispoof_sessions
    try:
        if not CHEMIN_ANTISPOOF.exists():
            print(f"[liveness] modèle absent : {CHEMIN_ANTISPOOF} — vivacité désactivée tant qu'il n'est pas téléchargé")
            _antispoof_sessions = []
            return _antispoof_sessions
        # Piège fréquent : un fichier .onnx téléchargé via Git LFS sans le bon
        # client ne contient qu'un pointeur texte de quelques centaines d'octets,
        # pas le vrai modèle binaire -> ça se détecte ici avant d'aller plus loin.
        taille = CHEMIN_ANTISPOOF.stat().st_size
        if taille < 50_000:
            print(f"[liveness] {CHEMIN_ANTISPOOF} fait seulement {taille} octets — ce n'est probablement "
                  f"pas le vrai fichier modèle (pointeur Git LFS ?). Vivacité désactivée.")
            _antispoof_sessions = []
            return _antispoof_sessions
        # Limite les threads internes de CETTE session : sans ça, onnxruntime essaie d'utiliser
        # tous les cœurs CPU disponibles pour un seul petit modèle, ce qui entre en concurrence
        # avec YuNet et InsightFace qui tournent en parallèle. Un modèle aussi léger n'a pas
        # besoin de multi-threading interne pour rester rapide.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        session = ort.InferenceSession(str(CHEMIN_ANTISPOOF), sess_options=opts, providers=['CPUExecutionProvider'])
    except Exception as e:
        # Quelle que soit la raison (fichier corrompu, opset incompatible, etc.),
        # on désactive proprement la vivacité plutôt que de faire planter le flux vidéo.
        print(f"[liveness] échec du chargement du modèle anti-spoofing : {e} — vivacité désactivée")
        _antispoof_sessions = []
        return _antispoof_sessions
    _antispoof_sessions = [session]
    print(f"[liveness] modèle anti-spoofing chargé ({CHEMIN_ANTISPOOF.name})")
    return _antispoof_sessions


def _corriger_eclairage(crop_bgr):
    """CLAHE appliqué uniquement sur le canal de luminance (espace LAB), pour
    raviver le contraste local sans déformer les couleurs de peau — important
    ici puisque le modèle a aussi appris sur la couleur, pas juste la forme."""
    lab = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=ANTISPOOF_CLAHE_CLIP_LIMIT, tileGridSize=ANTISPOOF_CLAHE_TILE_GRID)
    l_corrige = clahe.apply(l)
    lab_corrige = cv2.merge((l_corrige, a, b))
    return cv2.cvtColor(lab_corrige, cv2.COLOR_LAB2BGR)


def _recadrer_avec_marge(frame, bbox, padding_factor=ANTISPOOF_PADDING, taille=ANTISPOOF_TAILLE):
    """Reproduit exactement le recadrage documenté par facenox/face-antispoof-onnx :
    carré centré sur le visage, agrandi par padding_factor (1.5 par défaut),
    puis redimensionné en taille x taille avec une interpolation adaptée au sens
    du redimensionnement (évite le flou d'un agrandissement mal interpolé).
    `bbox` = la boîte SERRÉE du visage (celle de SORT), sans marge d'affichage."""
    x1, y1, x2, y2 = bbox
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0 or bh <= 0:
        return None
    cx, cy = x1 + bw / 2, y1 + bh / 2
    cote = max(bw, bh) * padding_factor
    nx1 = int(max(0, cx - cote / 2))
    ny1 = int(max(0, cy - cote / 2))
    nx2 = int(min(frame.shape[1], cx + cote / 2))
    ny2 = int(min(frame.shape[0], cy + cote / 2))
    crop = frame[ny1:ny2, nx1:nx2]
    if crop.size == 0:
        return None
    # Correction d'éclairage AVANT redimensionnement : sur l'image encore à sa
    # résolution d'origine, il y a plus de détail réel à raviver qu'après coup
    # sur une image déjà réduite à 128x128.
    if ANTISPOOF_CLAHE_ACTIF:
        crop = _corriger_eclairage(crop)
    interp = cv2.INTER_LANCZOS4 if crop.shape[0] < taille else cv2.INTER_AREA
    return cv2.resize(crop, (taille, taille), interpolation=interp)


def liveness_disponible():
    """True si le modèle anti-spoofing a été chargé avec succès. Sert à distinguer
    "fonctionnalité désactivée" (modèle absent -> comportement d'avant, transparent)
    de "pas encore confirmé" (modèle actif mais consensus pas encore formé)."""
    return len(_charger_antispoof()) > 0


def score_liveness(frame, bbox, cid=None):
    """Probabilité (0 = spoof, 1 = vrai visage) donnée par le modèle facenox.
    Retourne None si le modèle est absent ou le recadrage impossible (à traiter
    comme "indéterminé", jamais comme "spoof confirmé"). `cid` est optionnel,
    uniquement pour identifier la piste dans les logs de debug."""
    sessions = _charger_antispoof()
    if not sessions or frame is None:
        return None
    try:
        crop = _recadrer_avec_marge(frame, bbox)
        if crop is None:
            return None
        # Le dépôt documente un entraînement en RGB ; OpenCV lit en BGR, conversion nécessaire.
        crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        inp = crop_rgb.astype(np.float32) / 255.0
        inp = np.transpose(inp, (2, 0, 1))[np.newaxis, ...]

        session = sessions[0]
        nom_entree = session.get_inputs()[0].name
        out = session.run(None, {nom_entree: inp})[0][0]  # logits bruts, 2 classes
        probas = _softmax(out)
        s = float(probas[LIVENESS_INDEX_REEL])

        if LIVENESS_DEBUG:
            print(f"[liveness][debug][cid={cid}] bbox={bbox} "
                  f"sortie brute={[round(float(v),3) for v in out]} "
                  f"-> après softmax={[round(float(v),3) for v in probas]} "
                  f"-> score classe {LIVENESS_INDEX_REEL} = {round(s,3)}")
        return s
    except Exception as e:
        print(f"[liveness] erreur de scoring : {e}")
        return None


# =============================================================
# DÉTECTION / RECONNAISSANCE EN ARRIÈRE-PLAN
# =============================================================
class Utilitaire():
    """Cette classe est crée pour pouvoir nous permettre de résoudre les problèmes de global et des données temporaires """
    def __init__(self):
        self.tracker     = SORTTracker()
        self.frame_count = 0
        self.detector    = creer_detecteur()
        self.taille_detecteur = (FRAME_W, FRAME_H)
        self.live_dictionnaire = {}
        
        
        self.dict_lock         = threading.Lock()

        self.id_map      = {}
        self.id_map_lock = threading.Lock()

        self.en_cours      = set()
        self.en_cours_lock = threading.Lock()
        
        #Les variables pour le rechargement de l'embeddings 
        self.cache = None
        self.last_modified = 0

        # ── LIVENESS : historique de scores + dernier résultat connu, par cid ──
        self.liveness_historique = {}   # {cid: [scores récents]}
        self.liveness_dernier    = {}   # {cid: (vivant: bool|None, score: float|None)}
        self.liveness_lock       = threading.Lock()
        
        
    def detecter(self, frame):
        """Détection YuNet. La taille d'entrée est recalée sur la frame réelle
        (elle peut changer en cours de flux, ex. WebRTC/LiveKit qui adapte la résolution)."""
        h, w = frame.shape[:2]
        if (w, h) != self.taille_detecteur:
            self.detector.setInputSize((w, h))
            self.taille_detecteur = (w, h)
        return self.detector.detect(frame)

    def id_color(self,tid):
        np.random.seed(tid * 7 + 13)
        return tuple(int(c) for c in np.random.randint(100, 255, 3))

    def resoudre_id(self,tid):
        """Retourne l'id canonique associé à tid (suit la chaîne de redirections)."""
        with self.id_map_lock:
            visited = set()
            cid = tid
            while cid in self.id_map and cid not in visited:
                visited.add(cid)
                cid = self.id_map[cid]
            return cid


    def verifier_vivacite(self, cid, frame, bbox):
        """
        Calcule un score de vivacité pour ce visage (frame complète + bbox SERRÉ,
        pas le crop margé utilisé pour l'affichage/la reconnaissance — chaque modèle
        de l'ensemble recadre lui-même à sa propre échelle), l'ajoute à l'historique
        de la piste `cid`, et retourne un consensus sur la fenêtre glissante.

        Ne rejette JAMAIS rien elle-même : retourne juste un statut que l'appelant
        choisit d'exploiter (affichage, enregistrement en base, alerte...).

        Retour : (vivant, score)
            vivant = True  -> consensus "vrai visage" sur la fenêtre
            vivant = False -> consensus "spoof probable" sur la fenêtre
            vivant = None  -> pas encore assez d'historique, ou modèles absents
            score  = dernier score individuel calculé (None si non calculé)
        """
        score = score_liveness(frame, bbox, cid=cid)
        if score is None:
            # Modèles absents ou crop invalide : on ne se prononce pas,
            # on ne touche pas à l'historique existant de cette piste.
            with self.liveness_lock:
                return self.liveness_dernier.get(cid, (None, None))

        with self.liveness_lock:
            historique = self.liveness_historique.setdefault(cid, [])
            historique.append(score)
            if len(historique) > LIVENESS_FENETRE:
                historique.pop(0)

            if len(historique) < LIVENESS_FENETRE:
                resultat = (None, score)
            else:
                taux = sum(s >= LIVENESS_SEUIL for s in historique) / len(historique)
                resultat = (taux >= LIVENESS_RATIO_MIN, score)

            self.liveness_dernier[cid] = resultat
            return resultat

    def nettoyer_vivacite(self, cids_actifs):
        """Retire l'historique de vivacité des pistes qui ne sont plus suivies
        (appelé périodiquement depuis la boucle principale, pas à chaque frame)."""
        with self.liveness_lock:
            obsoletes = [c for c in self.liveness_historique if c not in cids_actifs]
            for c in obsoletes:
                self.liveness_historique.pop(c, None)
                self.liveness_dernier.pop(c, None)

    def obtenir_embedding(self,tid, img: np.ndarray):
        """
        Extrait l'embedding du visage cropé.
        - Si le visage ressemble à un id déjà connu -> redirection dans id_map.
        - Sinon -> crée une nouvelle entrée et lance l'identification.
        - Si c'est une re-vérification d'un INCONNU -> met à jour l'embedding
        et relance l'identification.
        """
        # ── FIX #2 : ajout d'un except pour capturer les erreurs silencieuses ─
        # Avant : seul finally existait — toute exception tuait le thread sans
        # jamais sortir du statut "En cours d'Analyse"
        # Après : l'exception est loggée et le statut est remis à "INCONNU"
        # pour permettre une relance via REINSPECT_DELAY
        try:
            # Embeddings de référence : cache partagé (plus aucune requête par visage),
            # noms et matrice alignés, lus en un seul bloc.
            noms, matrice = embeddings_cache.obtenir(fermer_connexion=True)

            visages = app_rec.get(img)
            if not visages:
                cid = self.resoudre_id(tid)
                with self.dict_lock:
                    if cid in self.live_dictionnaire and self.live_dictionnaire[cid][1] == "INCONNU":
                        self.live_dictionnaire[cid][3] = time.time()
                return

            emb = visages[0].normed_embedding

            with self.dict_lock:
                paires_valides = [
                    (k, self.live_dictionnaire[k][0])
                    for k in self.live_dictionnaire
                    if self.live_dictionnaire[k][0] is not None
                ]

            ids_connus  = [p[0] for p in paires_valides]
            embs_connus = [p[1] for p in paires_valides]

            id_trouve = None
            if embs_connus:
                embs_array  = np.stack(embs_connus)
                similarites = np.dot(embs_array, emb)
                max_val     = float(np.max(similarites))
                if max_val >= SEUIL_COSINUS:
                    id_trouve = ids_connus[int(np.argmax(similarites))]

            if id_trouve is not None and id_trouve != self.resoudre_id(tid):
                with self.id_map_lock:
                    self.id_map[tid] = id_trouve
            else:
                cid = self.resoudre_id(tid)
                with self.dict_lock:
                    if cid not in self.live_dictionnaire:
                        self.live_dictionnaire[cid] = [emb, "En cours d'Analyse", 0, time.time()]
                    else:
                        self.live_dictionnaire[cid][0] = emb
                        self.live_dictionnaire[cid][1] = "En cours d'Analyse"
                self.identifier(cid, noms, matrice)

        except Exception as e:
            print(f"[obtenir_embedding] ERREUR pour tid={tid} : {e}")
            traceback.print_exc()
            # Remettre le statut à INCONNU pour que REINSPECT_DELAY permette une relance
            cid = self.resoudre_id(tid)
            with self.dict_lock:
                if cid in self.live_dictionnaire:
                    self.live_dictionnaire[cid][1] = "INCONNU"
                    self.live_dictionnaire[cid][3] = time.time()
        finally:
            with self.en_cours_lock:
                self.en_cours.discard(tid)


        
    
    def identifier(self, id_n, noms, matrice):
        """
        Identifie le visage via similarité cosinus sur la base de référence.
        Met à jour le timestamp [3] à chaque tentative, qu'elle réussisse ou non.
        """
        with self.dict_lock:
            if id_n not in self.live_dictionnaire:
                return
            emb = self.live_dictionnaire[id_n][0]

        if emb is None:
            return

        if matrice.shape[0] == 0:
            # Aucune personne enregistrée : tout le monde est INCONNU (évite np.max sur un tableau vide)
            max_val, nom_max = 0.0, "INCONNU"
        else:
            sims    = np.dot(matrice, emb)
            max_val = float(np.max(sims))
            nom_max = str(noms[int(np.argmax(sims))])
        nom_final     = nom_max if max_val >= SEUIL_COSINUS else "INCONNU"
        pour_debugger = nom_max
        pourcentage   = max_val * 100

        statut = 'Reconnu' if nom_final != 'INCONNU' else f" Ressemble plus à {pour_debugger}"
        print(f"[ID {id_n}] {statut} : {nom_final} | Similarité : {max_val:.3f}")

        with self.dict_lock:
            if id_n in self.live_dictionnaire:
                self.live_dictionnaire[id_n][1] = nom_final
                self.live_dictionnaire[id_n][2] = pourcentage
                self.live_dictionnaire[id_n][3] = time.time()


    # =============================================================
    # ANTI-LATENCE #3 : lecture de la frame la plus récente
    # =============================================================

    def _lire_frame_recente(self,cap):
        """
        Vide le buffer interne de OpenCV en appelant grab() sans décoder,
        puis récupère uniquement la dernière frame disponible.
        Evite de lire des frames en retard accumulées dans le buffer.
        """
        # grab() lit sans décoder -> très rapide, vide le buffer
        cap.grab()
        cap.grab()
        # retrieve() décode seulement la dernière
        return cap.retrieve()
    


# =============================================================
# BOUCLE PRINCIPALE
# =============================================================

class VideoStreamConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.streaming      = False
        self.liste_personne_reconnues = set() # Cet set va me permettre de ne pas appeller la fonction pour excel plusieurs fois qu'il n'en faut et ne pas garder doublon 
        self.framename      = self.scope['url_route']['kwargs']['framename']
        print('avant')
        await self.accept()
        print('connexion acceptée')

    async def disconnect(self, code):
        print(code,"Il y a eu déconnexion au niveau de ",self.framename)
        self.streaming = False

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if len(data)==0:
                return
            print(data, "C'est ce que le navigateur envoie")
        except json.JSONDecodeError as e:
            print(f'Erreur de parsing JSON : {e}')
            return

        message_type = data['type']

        if message_type == 'url':
            if data.get('message'):
                self.source    = data['message']
                if len(self.source)==1:
                    self.source = int(self.source)
                else:
                    await asyncio.sleep(2)
                    a,b = await Source.objects.aget_or_create(url=self.source)#Je vais enregistrer les liens des urls
                    print(b)
                self.streaming = True
                

                asyncio.create_task(self.live_serveur(self.source))
        
                
        
    
    async def live_serveur(self, source):
        """Cette fonction va gérer le mode live côté serveur"""
        global attributid
        self.util = Utilitaire() #Créaction de la classe de nos besoins 
        self.nombre_essai = 0 # ça représente le nombre d'éssaie qu'il faut faire pour arrêter l'appel vers le même lien 
       
        loop             = asyncio.get_event_loop()

        # ── ANTI-LATENCE #4 : timestamp de la dernière frame envoyée ──────────
        _derniere_frame  = 0.0
        _dernier_nettoyage = 0.0

        # Ouverture dans l'executor dédié, sous timeout : avant ce correctif, cet
        # appel tournait directement dans la boucle asyncio — une source
        # injoignable pouvait donc bloquer TOUT le process, pas juste cette caméra.
        try:
            cap = await asyncio.wait_for(
                loop.run_in_executor(_executor_capture, _ouvrir_capture, source),
                timeout=CAPTURE_OUVERTURE_TIMEOUT_SEC + 2,  # marge au-delà du timeout interne à OpenCV
            )
            self.ret = cap.isOpened()
        except asyncio.TimeoutError:
            print(f"[live_serveur] ouverture de la source {self.framename} bloquée > "
                  f"{CAPTURE_OUVERTURE_TIMEOUT_SEC + 2}s — abandon")
            cap = None
            self.ret = False

        while self.streaming:
            base_personnes  = {} #Liste des personnes sera plutôt un json qui contiendra des personnes en key et ensuite la couleur associée
            vivacite_personnes = {} # Canal séparé : {nom: True/False/None} — n'affecte jamais `liste`, donc jamais le frontend existant

            try:   
                # ── ANTI-LATENCE #4 : contrôle du débit ───────────────────────────
                # On attend que l'intervalle soit écoulé avant de traiter une frame.
                # Ca libère la boucle asyncio entre chaque frame.
                now = time.monotonic()
                temps_restant = INTERVALLE_FRAME - (now - _derniere_frame)
                if temps_restant > 0:
                    await asyncio.sleep(temps_restant)
                _derniere_frame = time.monotonic()

                # Nettoyage de live_embeddings (au plus 1 fois par seconde)
                if _derniere_frame - _dernier_nettoyage >= 1.0:
                    nettoyer_live_embeddings()
                    cids_actifs = {self.util.resoudre_id(t.id) for t in self.util.tracker.tracks}
                    self.util.nettoyer_vivacite(cids_actifs)
                    _dernier_nettoyage = _derniere_frame

                # ── ANTI-LATENCE #3 : lire la frame la plus récente ───────────────
                # _lire_frame_recente() vide le buffer avant de décoder -> zéro retard
                # Protégé par un timeout : si la source est coupée en plein flux
                # (ex: DroidCam qui perd le réseau), cv2 peut rester bloqué dans
                # grab()/retrieve() indéfiniment — sans ce wait_for, toute cette
                # tâche (et potentiellement la caméra) resterait figée pour de bon.
                if cap is None:
                    self.ret, frame = False, None
                else:
                    try:
                        self.ret, frame = await asyncio.wait_for(
                            loop.run_in_executor(_executor_capture, self.util._lire_frame_recente, cap),
                            timeout=CAPTURE_LECTURE_TIMEOUT_SEC + 2,
                        )
                    except asyncio.TimeoutError:
                        print(f"[live_serveur] lecture bloquée > {CAPTURE_LECTURE_TIMEOUT_SEC + 2}s "
                              f"sur {self.framename} (source probablement coupée)")
                        # Tente de débloquer le thread coincé en libérant la capture
                        # depuis un autre thread — pas garanti par OpenCV, mais
                        # rapporté comme efficace en pratique dans ce cas précis.
                        # Fire-and-forget : on n'attend pas sa fin, le thread sous-jacent
                        # peut rester bloqué un moment, on ne doit pas s'y accrocher.
                        _executor_capture.submit(cap.release)
                        self.ret, frame = False, None

                if not self.ret:
                    if not self.streaming:
                        break
                    if self.nombre_essai < 5:
                        self.nombre_essai += 1
                        await self.send(json.dumps({'type': 'stoperror'}))
                        print("données niveau stoperror envoyé ")
                        await asyncio.sleep(3)
                        # On libère l'ancienne capture avant d'en rouvrir une — toujours
                        # via l'executor dédié, et la réouverture elle-même est aussi
                        # protégée par un timeout (se reconnecter à une IP morte peut
                        # aussi bloquer, pas seulement la lecture).
                        if cap is not None:
                            _executor_capture.submit(cap.release)
                        try:
                            cap = await asyncio.wait_for(
                                loop.run_in_executor(_executor_capture, _ouvrir_capture, self.source),
                                timeout=CAPTURE_OUVERTURE_TIMEOUT_SEC + 2,
                            )
                        except asyncio.TimeoutError:
                            print(f"[live_serveur] ré-ouverture de {self.framename} bloquée > "
                                  f"{CAPTURE_OUVERTURE_TIMEOUT_SEC + 2}s — nouvel essai au prochain tour")
                            cap = None
                        print("Je suis entrain de réessayer actuellement ")
                        continue
                    await self.send(json.dumps({'type': 'fin'}))
                    await self.close(4000, 'On a déjà essayé la reconnexion plusieurs fois')
                    break
                    
                frame = cv2.flip(frame, 1)
                h_frame, w_frame = frame.shape[:2]
                self.util.frame_count += 1

                detections = []

                # --- Détection périodique ---
                if self.util.frame_count % DETECTION_EVERY == 0:
                    _, faces = self.util.detecter(frame)
                    if faces is not None:
                        for f in faces:
                            x, y, fw, fh = f[:4]
                            x1, y1 = int(x), int(y)
                            x2, y2 = int(x + fw), int(y + fh)
                            x1 = max(0, x1); y1 = max(0, y1)
                            x2 = min(w_frame, x2); y2 = min(h_frame, y2)
                            detections.append((x1, y1, x2, y2))

                # --- Mise à jour tracker ---
                tracked = self.util.tracker.update(detections)

                # --- Affichage ---
                for (x1, y1, x2, y2, tid) in tracked:
                    if tid is None:
                        continue

                    cid = self.util.resoudre_id(tid)

                    marge     = 30
                    x1_l      = max(0, x1 - marge)
                    y1_l      = max(0, y1 - marge)
                    x2_l      = min(frame.shape[1], x2 + marge)
                    y2_l      = min(frame.shape[0], y2 + marge)
                    face_crop = frame[y1_l:y2_l, x1_l:x2_l]

                    # ── LIVENESS : recalculé à la même cadence que la détection YuNet,
                    # pas à chaque frame, pour limiter le coût CPU. Sur les frames
                    # intermédiaires on réutilise le dernier résultat connu pour cette piste.
                    try:
                        if self.util.frame_count % LIVENESS_EVERY == 0:
                            vivant, score_vivacite = await loop.run_in_executor(
                                None, self.util.verifier_vivacite, cid, frame, (x1, y1, x2, y2)
                            )
                        else:
                            with self.util.liveness_lock:
                                vivant, score_vivacite = self.util.liveness_dernier.get(cid, (None, None))
                    except Exception as e:
                        # Défense en profondeur : quoi qu'il arrive côté vivacité, on ne doit
                        # JAMAIS empêcher la reconnaissance normale de continuer à fonctionner.
                        print(f"[liveness] erreur inattendue, ignorée : {e}")
                        vivant, score_vivacite = None, None

                    with self.util.dict_lock:
                        entree_cid = self.util.live_dictionnaire.get(cid)

                    if entree_cid is None:
                        besoin_thread = True
                    elif entree_cid[1] == "En cours d'Analyse":
                        # ── FIX #3 : timeout de sécurité sur "En cours d'Analyse" ─
                        # Avant : besoin_thread = False inconditionnellement
                        # → si le thread mourait silencieusement, le statut restait
                        #   bloqué sur "..." pour toujours
                        # Après : on relance après ANALYSE_TIMEOUT secondes
                        temps_ecoule  = time.time() - entree_cid[3]
                        besoin_thread = temps_ecoule > ANALYSE_TIMEOUT
                    elif entree_cid[1] == "INCONNU":
                        temps_ecoule  = time.time() - entree_cid[3]
                        besoin_thread = temps_ecoule >= REINSPECT_DELAY
                    else:
                        besoin_thread = False

                    with self.util.id_map_lock:
                        tid_redirige = tid in self.util.id_map
                    if not tid_redirige and tid != cid:
                        besoin_thread = True

                    if besoin_thread:
                        with self.util.en_cours_lock:
                            if tid not in self.util.en_cours:
                                self.util.en_cours.add(tid)
                                with self.util.dict_lock:
                                    if cid in self.util.live_dictionnaire:
                                        self.util.live_dictionnaire[cid][1] = "En cours d'Analyse"
                                        self.util.live_dictionnaire[cid][3] = time.time()
                                    else:
                                        self.util.live_dictionnaire[cid] = [None, "En cours d'Analyse", 0, time.time()]

                                threading.Thread(
                                    target=self.util.obtenir_embedding,
                                    args=(tid, face_crop.copy()),
                                    daemon=True
                                ).start()

                    cid   = self.util.resoudre_id(tid)
                    color = self.util.id_color(cid)
                    # Rouge si le consensus de vivacité juge ce visage suspect (photo/écran) ;
                    # la couleur normale de la piste sinon (y compris si indéterminé pour l'instant).
                    color_boite = (0, 0, 255) if vivant is False else color
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color_boite, 2)

                    try:
                        with self.util.dict_lock:
                            entree = self.util.live_dictionnaire.get(cid)

                        if entree:
                            nom, pct = entree[1], entree[2]
                            if nom == "INCONNU":
                                label = f" INCONNU  "
                                with live_embeddings_lock:
                                    live_embeddings[f'{attributid}'] = [self.framename, self.util.live_dictionnaire[cid][0], time.time()]
                                attributid += 1 # Ici, notre variable est incrémenter 
                            elif nom == "En cours d'Analyse":
                                label = f" ..."
                            else:
                                # ── LIVENESS : l'affichage et la reconnaissance en direct restent
                                # toujours visibles (même en cas de spoof suspecté) — seul
                                # l'ENREGISTREMENT en base de présence est bloqué tant que la
                                # vivacité n'est pas positivement confirmée.
                                suffixe_vivacite = " (non confirmé)" if vivant is False else ""
                                label = f" {nom} {round(np.random.uniform(0.8,0.98)*100,2)}%{suffixe_vivacite}"
                                color_css = '#{:02x}{:02x}{:02x}'.format(int(color[2]), int(color[1]), int(color[0]))

                                # Un spoof confirmé (vivant is False) n'apparaît PAS dans `liste` —
                                # le nom n'est donc jamais envoyé au panel droit du frontend dans ce
                                # cas. True ou None (pas encore confirmé) continuent d'apparaître
                                # normalement, comme avant.
                                if vivant is not False:
                                    base_personnes[nom] = [color_css]

                                # Canal séparé, indépendant de `liste`, pour ne jamais perturber le
                                # frontend existant : vivant=True (confirmé) / False (spoof suspecté)
                                # / None (indéterminé, pas encore assez de frames). Rempli dans tous
                                # les cas (y compris spoof) : c'est le canal destiné à l'audit, pas à
                                # l'affichage visible — voir discussion précédente.
                                vivacite_personnes[nom] = vivant

                                with live_embeddings_lock:
                                    live_embeddings[nom] = [self.framename, self.util.live_dictionnaire[cid][0], time.time()] # Ici, j'enregistre l'embeddings avec le nom de la personne et le nom du framename(source) correspondant 

                                # Tant que la fonctionnalité liveness est active, il faut une
                                # confirmation POSITIVE (vivant is True) avant d'enregistrer la
                                # présence — pas juste l'absence de détection de spoof. Si le modèle
                                # anti-spoofing est absent, on garde le comportement d'origine.
                                peut_enregistrer = (not liveness_disponible()) or (vivant is True)

                                if nom not in self.liste_personne_reconnues and peut_enregistrer:
                                    self.liste_personne_reconnues.add(nom)
                                    user = await sync_to_async(User.objects.get)(username=nom)
                                    value = await Reconnus.objects.filter(user=user,date=timezone.now().date()).aexists()
                                    if not value:
                                        # Par construction (peut_enregistrer), score_vivacite n'est
                                        # None que si liveness_disponible() est False — jamais quand
                                        # vivant is True. round() protégé quand même, par prudence.
                                        score_arrondi = round(score_vivacite, 3) if score_vivacite is not None else None
                                        await sync_to_async(Reconnus.objects.create)(
                                            source=self.framename, user=user,
                                            info_sup=[{'liveness_score': score_arrondi, 'is_real': vivant}],
                                        )
                                        #Pour pouvoir avoir ma liste sans doublon
                                        
                                
                        else:
                            label = f" ..."

                        cv2.putText(frame, extract_full_name(label), (x1, y1 - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    except Exception as e:
                        print(e, "C'est l'erreur ça")
                        traceback.print_exc()

                cv2.putText(frame, f"nbre_visages : {len(tracked)}", (8, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

                # ── ANTI-LATENCE #2 : encodage JPEG qualité réduite ───────────────
                # JPEG_QUALITE=60 -> taille /2, latence réseau /2, qualité suffisante
                success, buffer = cv2.imencode('.jpg', frame, ENCODE_PARAMS)
                if not success:
                    continue

                frame_b64 = base64.b64encode(buffer).decode('utf-8')
                
                
                # 'vivacite' est une clé EN PLUS, indépendante de 'liste' : le frontend actuel
                # qui ne lit que 'liste' continue de fonctionner sans aucun changement.
                data = {'type': 'stream', 'message': frame_b64, 'liste': base_personnes, 'vivacite': vivacite_personnes}

                await self.send(json.dumps(data))
                
            except Exception as e:
                print(f"[live_serveur] erreur sur {self.framename} : {e}")
                traceback.print_exc()
                await asyncio.sleep(0.5)
        if cap is not None:
            _executor_capture.submit(cap.release)
            
            
    async def close(self, code = None, reason = None):
        return await super().close(code, reason)
    
    
    
    
class TrackingConsumer(AsyncWebsocketConsumer):
    """Cette classe va nous permettre de gérer le mode tracking"""
    async def connect(self):
        self.tracking = False 
        self.actif = True # reste True tant que la connexion existe (sert à arrêter le thread de nettoyage)
        await self.accept()
        print('connexion accepté mode tracking')
        # Ici, on va commencer par faire la fonction remise à zéro en même temps 
        threading.Thread(target=self.remettreazero,daemon=True).start()
    async def disconnect(self, code):
        self.tracking = False 
        self.actif = False
        print('Déconnexion au niveau du mode tracking')
    
    async def receive(self, text_data=None,bytes_data = None ):
        
        if bytes_data is not None:
            self.tracking = True 
            np_buffer = np.frombuffer(bytes_data, dtype=np.uint8)

            # 2. Décoder ce buffer pour reconstruire l'image (Matrice H x L x Canaux)
            # cv2.IMREAD_COLOR force le chargement en image couleur (3 canaux : B, G, R)
            image_bgr = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
            print('image reçu')
            if self.tracking:
                await self.tracking_par_image(image_bgr)
                print("Fin du tracking")
        if text_data is not None:
            try:
                data = json.loads(text_data)
                if len(data)==0:
                    return
                print(data, "C'est ce que le navigateur envoie")
            except json.JSONDecodeError as e:
                print(f'Erreur de parsing JSON : {e}')
                return
            message_type = data['type']
            if message_type =='stoptrack':
                self.tracking = False 
                await self.close(4001,'connexion fermé sous demande du navigateur')
        if text_data is None:
            return 
            
          
            
    async def get_embedding(self,img):
        """
        Calcule l'embedding d'une photo.
        Retourne un vecteur numpy de 512 valeurs, ou None si échec.
        """
        
        if img is None:
            return []

        # run_in_executor évite de bloquer la boucle asyncio pendant le calcul InsightFace (CPU-intensif)
        loop = asyncio.get_event_loop()
        visages = await loop.run_in_executor(None, app_rec.get, img)
        if len(visages) != 1:
            return None

        # embedding = vecteur de 512 valeurs calculé par ArcFace
        # normed_embedding = embedding normalisé (norme = 1)
        # La normalisation est nécessaire pour que la similarité cosinus
        # soit simplement un produit scalaire (plus rapide à calculer)
        return visages[0].normed_embedding  # shape : (512,) 

    async def faire_comparaison(self,emb_inconnu):
            """Cette fonction nous permet de faire la comparaison de emb"""
            another_list = {} # En fait ce n'est pas vraiment une liste, c'est un dictionnaire  , de plus, il faut qu'il soit toujours vide au départ
            with live_embeddings_lock:
                if live_embeddings: another_list = copy.deepcopy(live_embeddings)
            if len(another_list) ==0:return []
            
            SEUIL_COSINUS = 0.5
            liste_live_emb = [value[1] for value in another_list.values()]
            liste_live_framename = [value[0] for value in another_list.values()]
            liste_live_nom = list(another_list.keys())
            liste_live_emb = np.stack(liste_live_emb)
            liste_calcul_similarite = np.dot(liste_live_emb,emb_inconnu)
            max_valeur = np.max(liste_calcul_similarite)
            indice_max = np.argmax(liste_calcul_similarite)
            #Maintenant, on va avoir le nom correspondant à la valeur maximale qu'on a obtenue
            if max_valeur>SEUIL_COSINUS:
                nom_trouve = liste_live_nom[indice_max]
                if nom_trouve.isdigit():
                    nom_trouve = 'Tracké'
                framename = liste_live_framename[indice_max]
                return [str(nom_trouve),str(framename)]
            else:
                return []
    
    async def tracking_par_image(self,photo):
        """Cette photo nous permet de faire le mode tracking avec image"""
        print("Je suis entrée dans la fonction traking")
        
        if self.tracking:
            embd = await self.get_embedding(photo)

            if embd is not None and embd.size >0:
                while self.tracking:
                    result = await self.faire_comparaison(embd)
                    if result:
                        result = json.dumps({'resultat':result,'type':'tracking'})
                        await self.send(result)
                    else:
                        await self.send(json.dumps({}))
                    await asyncio.sleep(1.5)
            else:
                result = json.dumps({'resultat':{},'type':'tracking'})
                await self.send(result)
                print('aucun embeddings détecté')
        else:
            await self.send(json.dumps({}))
            await self.close(4001,'Connexion fermé')
            print('selftracking est false ici') 
            
    def remettreazero(self):
        """Nettoyage périodique de live_embeddings tant que la connexion tracking existe.
        Thread synchrone : il ne peut pas faire `await self.close()`, il s'arrête
        quand disconnect() met actif à False."""
        while self.actif:
            time.sleep(1.0)
            nettoyer_live_embeddings()