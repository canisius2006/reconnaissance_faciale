"""
=============================================================
  PIPELINE DE RECONNAISSANCE — YuNet + SORT (Kalman + Hongrois) + InsightFace
  Contrainte : CPU uniquement
=============================================================

Code de traitement PARTAGÉ entre :
  - consumers.py  (VideoStreamConsumer : sources URL, caméra du serveur)
  - l'agent LiveKit (à venir)

Ce module ne dépend NI de Channels NI du WebSocket : il reçoit une frame (numpy BGR) et
retourne des résultats (boîtes, noms, similarité). Le dessin, l'encodage JPEG et l'envoi
restent à la charge de l'appelant.
Il suppose que Django est configuré (embeddings_cache lit la table Embedding) :
un programme séparé (agent) doit appeler django.setup() AVANT de l'importer.

Structure de live_dictionnaire : [emb, nom, pct, timestamp_derniere_tentative]
Les visages "INCONNU" sont re-vérifiés toutes les REINSPECT_DELAY secondes.
"""

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment

from .insight import get_app_rec
from . import embeddings_cache

# =============================================================
# CHEMINS
# =============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
chemin_modele = BASE_DIR/'static/model/face_detection_yunet_2023mar.onnx'

# ── InsightFace ────────────────────────────────────────────────────────────────
# Instance partagée : une seule copie du modèle en mémoire pour tout le processus
app_rec = get_app_rec()

SEUIL_COSINUS = 0.5

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
# SETUP DÉTECTEUR
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


# Compteur des clés des visages inconnus dans live_embeddings ("0", "1", ...)
_attributid = 0
_attributid_lock = threading.Lock()


def ajouter_inconnu_live(framename, emb):
    """Enregistre l'embedding d'un visage INCONNU sous une clé numérique (mode tracking)."""
    global _attributid
    if emb is None:
        return
    with _attributid_lock:
        cle = f'{_attributid}'
        _attributid += 1
    with live_embeddings_lock:
        live_embeddings[cle] = [framename, emb, time.time()]


def enregistrer_live(nom, framename, emb):
    """Enregistre l'embedding d'une personne reconnue, avec le nom de la source (framename)."""
    if emb is None:
        return
    with live_embeddings_lock:
        live_embeddings[nom] = [framename, emb, time.time()]


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
    # TRAITEMENT D'UNE FRAME (partagé consumer / agent)
    # =============================================================

    def traiter_frame(self, frame, framename):
        """
        Détection (une frame sur DETECTION_EVERY) -> tracking SORT -> lancement des threads
        d'identification -> état de chaque visage suivi.

        `frame` : image numpy BGR (déjà retournée si l'appelant le souhaite).
        `framename` : nom de la source, enregistré dans live_embeddings.
        Ne dessine rien et n'écrit rien en base. Retourne une liste de dict, un par visage :
            {'tid', 'cid', 'bbox': (x1, y1, x2, y2), 'nom', 'pct', 'etat', 'color'}
        - etat : 'reconnu' (nom = username, pct = similarité cosinus x 100),
                 'inconnu', ou 'analyse' (identification en cours, nom = None)
        - color : couleur BGR stable pour ce visage
        """
        h_frame, w_frame = frame.shape[:2]
        self.frame_count += 1

        detections = []

        # --- Détection périodique ---
        if self.frame_count % DETECTION_EVERY == 0:
            _, faces = self.detecter(frame)
            if faces is not None:
                for f in faces:
                    x, y, fw, fh = f[:4]
                    x1, y1 = int(x), int(y)
                    x2, y2 = int(x + fw), int(y + fh)
                    x1 = max(0, x1); y1 = max(0, y1)
                    x2 = min(w_frame, x2); y2 = min(h_frame, y2)
                    detections.append((x1, y1, x2, y2))

        # --- Mise à jour tracker ---
        tracked = self.tracker.update(detections)

        resultats = []
        for (x1, y1, x2, y2, tid) in tracked:
            if tid is None:
                continue

            cid = self.resoudre_id(tid)

            marge     = 30
            x1_l      = max(0, x1 - marge)
            y1_l      = max(0, y1 - marge)
            x2_l      = min(frame.shape[1], x2 + marge)
            y2_l      = min(frame.shape[0], y2 + marge)
            face_crop = frame[y1_l:y2_l, x1_l:x2_l]

            with self.dict_lock:
                entree_cid = self.live_dictionnaire.get(cid)

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

            with self.id_map_lock:
                tid_redirige = tid in self.id_map
            if not tid_redirige and tid != cid:
                besoin_thread = True

            if besoin_thread:
                with self.en_cours_lock:
                    if tid not in self.en_cours:
                        self.en_cours.add(tid)
                        with self.dict_lock:
                            if cid in self.live_dictionnaire:
                                self.live_dictionnaire[cid][1] = "En cours d'Analyse"
                                self.live_dictionnaire[cid][3] = time.time()
                            else:
                                self.live_dictionnaire[cid] = [None, "En cours d'Analyse", 0, time.time()]

                        threading.Thread(
                            target=self.obtenir_embedding,
                            args=(tid, face_crop.copy()),
                            daemon=True
                        ).start()

            cid   = self.resoudre_id(tid)
            color = self.id_color(cid)

            nom, pct, etat = None, 0, 'analyse'
            try:
                with self.dict_lock:
                    entree = self.live_dictionnaire.get(cid)

                if entree:
                    nom_e, pct_e, emb_e = entree[1], entree[2], entree[0]
                    if nom_e == "INCONNU":
                        etat, nom, pct = 'inconnu', None, 0
                        ajouter_inconnu_live(framename, emb_e)
                    elif nom_e == "En cours d'Analyse":
                        etat, nom, pct = 'analyse', None, 0
                    else:
                        etat, nom, pct = 'reconnu', nom_e, pct_e
                        # Ici, j'enregistre l'embeddings avec le nom de la personne et le nom de la source
                        enregistrer_live(nom_e, framename, emb_e)
            except Exception as e:
                print(e, "[traiter_frame] erreur sur un visage")
                traceback.print_exc()

            resultats.append({
                'tid': tid, 'cid': cid, 'bbox': (x1, y1, x2, y2),
                'nom': nom, 'pct': pct, 'etat': etat, 'color': color,
            })

        return resultats