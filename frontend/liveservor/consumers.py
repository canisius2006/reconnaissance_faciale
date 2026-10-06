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

NOUVEAUTÉ :
    - Re-vérification automatique des visages "INCONNU" toutes les REINSPECT_DELAY secondes.
    - La structure de live_dictionnaire passe de [emb, nom, pct]
      à [emb, nom, pct, timestamp_derniere_tentative].
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

    def detecter(self, frame):
        """Détection YuNet. La taille d'entrée est recalée sur la frame réelle
        (elle peut changer en cours de flux, ex. WebRTC/LiveKit qui adapte la résolution)."""
        h, w = frame.shape[:2]
        if (w, h) != self.taille_detecteur:
            self.detector.setInputSize((w, h))
            self.taille_detecteur = (w, h)
        return self.detector.detect(frame)

    def id_color(self, tid):
        rng = np.random.default_rng(int(tid) * 7 + 13)
        return tuple(int(c) for c in rng.integers(100, 255, 3))

    def resoudre_id(self,tid):
        """Retourne l'id canonique associé à tid (suit la chaîne de redirections)."""
        with self.id_map_lock:
            visited = set()
            cid = tid
            while cid in self.id_map and cid not in visited:
                visited.add(cid)
                cid = self.id_map[cid]
            return cid


    def obtenir_embedding(self, tid, img: np.ndarray):
        """
        Extrait l'embedding et identifie le visage.

        Cette fonction reste synchrone et est exécutée dans un thread dédié.
        Aucun accès Django ORM direct n'est effectué ici.
        """
        try:
            noms, matrice = embeddings_cache.obtenir(fermer_connexion=True)

            if img is None or img.size == 0:
                raise ValueError("Crop visage vide")

            visages = app_rec.get(img)
            if not visages:
                cid = self.resoudre_id(tid)
                with self.dict_lock:
                    entree = self.live_dictionnaire.get(cid)
                    if entree is not None:
                        entree[1] = "INCONNU"
                        entree[3] = time.time()
                return

            emb = np.asarray(
                visages[0].normed_embedding,
                dtype=np.float32,
            )

            if emb.ndim != 1 or emb.size == 0:
                raise ValueError("Embedding InsightFace invalide")

            # Essaie de rattacher cette piste à une piste déjà connue.
            with self.dict_lock:
                paires_valides = [
                    (k, value[0])
                    for k, value in self.live_dictionnaire.items()
                    if value[0] is not None and k != self.resoudre_id(tid)
                ]

            id_trouve = None
            if paires_valides:
                ids_connus = [p[0] for p in paires_valides]
                embs_connus = np.stack([p[1] for p in paires_valides])
                similarites = np.dot(embs_connus, emb)
                meilleur = int(np.argmax(similarites))
                if float(similarites[meilleur]) >= SEUIL_COSINUS:
                    id_trouve = ids_connus[meilleur]

            if id_trouve is not None:
                with self.id_map_lock:
                    self.id_map[tid] = id_trouve
                return

            cid = self.resoudre_id(tid)

            with self.dict_lock:
                if cid not in self.live_dictionnaire:
                    self.live_dictionnaire[cid] = [
                        emb, "En cours d'Analyse", 0.0, time.time()
                    ]
                else:
                    self.live_dictionnaire[cid][0] = emb
                    self.live_dictionnaire[cid][1] = "En cours d'Analyse"
                    self.live_dictionnaire[cid][3] = time.time()

            self.identifier(cid, noms, matrice)

        except Exception as exc:
            print(
                f"[obtenir_embedding] ERREUR tid={tid} : {exc}"
            )
            traceback.print_exc()

            cid = self.resoudre_id(tid)
            with self.dict_lock:
                entree = self.live_dictionnaire.get(cid)
                if entree is not None:
                    entree[1] = "INCONNU"
                    entree[3] = time.time()

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

        matrice = np.asarray(matrice, dtype=np.float32)
        if matrice.ndim != 2 or matrice.shape[0] == 0:
            max_val, nom_max = 0.0, "INCONNU"
        else:
            sims = np.dot(matrice, emb)
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
        self.streaming = False
        self.live_task = None
        self.liste_personne_reconnues = set() # Cet set va me permettre de ne pas appeller la fonction pour excel plusieurs fois qu'il n'en faut et ne pas garder doublon 
        self.framename      = self.scope['url_route']['kwargs']['framename']
        print('avant')
        await self.accept()
        print('connexion acceptée')

    async def disconnect(self, code):
        print(code, "Il y a eu déconnexion au niveau de ", self.framename)
        self.streaming = False

        task = getattr(self, 'live_task', None)
        current = asyncio.current_task()
        if task is not None and task is not current and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if len(data)==0:
                return
            # Debug volontairement silencieux : évite de polluer la console.
        except json.JSONDecodeError as e:
            print(f'Erreur de parsing JSON : {e}')
            return

        message_type = data.get('type')

        if message_type == 'url':
            if data.get('message') is not None:
                self.source = data['message']
                if isinstance(self.source, str) and len(self.source) == 1 and self.source.isdigit():
                    self.source = int(self.source)
                else:
                    await asyncio.sleep(2)
                    a,b = await Source.objects.aget_or_create(url=self.source)#Je vais enregistrer les liens des urls
                    print(b)
                self.streaming = True
                

                if self.live_task is not None and not self.live_task.done():
                    self.live_task.cancel()
                    try:
                        await self.live_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass

                self.live_task = asyncio.create_task(
                    self.live_serveur(self.source)
                )
        
                
        
    
    async def live_serveur(self, source):
        """Cette fonction va gérer le mode live côté serveur"""
        global attributid
        self.util = Utilitaire() #Créaction de la classe de nos besoins 
        self.nombre_essai = 0 # ça représente le nombre d'éssaie qu'il faut faire pour arrêter l'appel vers le même lien 
       
        loop = asyncio.get_running_loop()

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
                # Copie sans annotations pour extraire les visages à reconnaître.
                # au fil de la boucle, ce qui pollue le crop du 2e visage s'il est proche du 1er.
                frame_brut = frame.copy() if tracked else frame
                for (x1, y1, x2, y2, tid) in tracked:
                    if tid is None:
                        continue

                    cid = self.util.resoudre_id(tid)

                    marge     = 30
                    x1_l      = max(0, x1 - marge)
                    y1_l      = max(0, y1 - marge)
                    x2_l      = min(frame.shape[1], x2 + marge)
                    y2_l      = min(frame.shape[0], y2 + marge)
                    face_crop = frame_brut[y1_l:y2_l, x1_l:x2_l]


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
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                    try:
                        with self.util.dict_lock:
                            entree = self.util.live_dictionnaire.get(cid)

                        if entree:
                            nom, pct = entree[1], entree[2]
                            if nom == "INCONNU":
                                label = " INCONNU"
                                embedding_live = entree[0]
                                if embedding_live is not None:
                                    cle_track = f"TRACK:{self.framename}:{cid}"
                                    with live_embeddings_lock:
                                        live_embeddings[cle_track] = [
                                            self.framename,
                                            embedding_live,
                                            time.time(),
                                        ]
                            elif nom == "En cours d'Analyse":
                                label = f" ..."
                            else:
                                label = f" {nom} {pct:.1f}%"
                                color_css = '#{:02x}{:02x}{:02x}'.format(int(color[2]), int(color[1]), int(color[0]))

                                base_personnes[nom] = [color_css]

                                with live_embeddings_lock:
                                    live_embeddings[nom] = [self.framename, self.util.live_dictionnaire[cid][0], time.time()] # Ici, j'enregistre l'embeddings avec le nom de la personne et le nom du framename(source) correspondant 

                                if nom not in self.liste_personne_reconnues:
                                    try:
                                        user = await sync_to_async(
                                            User.objects.get
                                        )(username=nom)

                                        today = timezone.localtime().date()
                                        value = await Reconnus.objects.filter(
                                            user=user,
                                            date=today,
                                        ).aexists()

                                        if not value:
                                            await sync_to_async(
                                                Reconnus.objects.create
                                            )(
                                                source=self.framename,
                                                user=user,
                                                info_sup=[],
                                            )

                                        # On ne bloque plus les tentatives futures si
                                        # une requête ORM a échoué.
                                        self.liste_personne_reconnues.add(nom)

                                    except User.DoesNotExist:
                                        print(
                                            f"[DB] utilisateur introuvable : {nom}"
                                        )
                                    except Exception as exc:
                                        print(
                                            f"[DB] erreur présence {nom} : {exc}"
                                        )
                                        
                                
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
                
                
                data = {'type': 'stream', 'message': frame_b64, 'liste': base_personnes}

                await self.send(json.dumps(data))
                
            except Exception as e:
                print(f"[live_serveur] erreur sur {self.framename} : {e}")
                traceback.print_exc()
                await asyncio.sleep(0.5)
        if cap is not None:
            _executor_capture.submit(cap.release)

        if getattr(self, 'live_task', None) is asyncio.current_task():
            self.live_task = None

    async def close(self, code=None, reason=None):
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
            if image_bgr is None:
                await self.send(json.dumps({
                    'resultat': {},
                    'type': 'tracking',
                    'error': 'image_invalide',
                }))
                return

            if self.tracking:
                await self.tracking_par_image(image_bgr)
                print("Fin du tracking")
        if text_data is not None:
            try:
                data = json.loads(text_data)
                if len(data)==0:
                    return
                # Debug volontairement silencieux : évite de polluer la console.
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
        loop = asyncio.get_running_loop()
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
            valeurs_valides = [
                (key, value)
                for key, value in another_list.items()
                if value[1] is not None
            ]
            if not valeurs_valides:
                return []

            liste_live_nom = [key for key, _ in valeurs_valides]
            liste_live_emb = [value[1] for _, value in valeurs_valides]
            liste_live_framename = [value[0] for _, value in valeurs_valides]
            liste_live_emb = np.stack(liste_live_emb).astype(np.float32)
            emb_inconnu = np.asarray(emb_inconnu, dtype=np.float32)
            if emb_inconnu.ndim != 1 or emb_inconnu.size == 0:
                return []

            liste_calcul_similarite = np.dot(liste_live_emb, emb_inconnu)
            max_valeur = float(np.max(liste_calcul_similarite))
            indice_max = int(np.argmax(liste_calcul_similarite))
            #Maintenant, on va avoir le nom correspondant à la valeur maximale qu'on a obtenue
            if max_valeur >= SEUIL_COSINUS:
                nom_trouve = liste_live_nom[indice_max]
                if str(nom_trouve).startswith('TRACK:') or str(nom_trouve).isdigit():
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
