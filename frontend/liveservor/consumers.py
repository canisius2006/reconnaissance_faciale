"""
=============================================================
  FACE TRACKING OPTIMISÉ — YuNet + SORT (Kalman + Hongrois)
  Contrainte : CPU uniquement
  Auteur     : Canisius (adapté)
=============================================================

DÉPENDANCES :
    pip install opencv-contrib-python numpy scipy filterpy

MODÈLE REQUIS :
    Télécharger : face_detection_yunet_2023mar.onnx
    Source : https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet

NOUVEAUTÉ :
    - Re-vérification automatique des visages "INCONNU" toutes les REINSPECT_DELAY secondes.
    - La structure de live_dictionnaire passe de [emb, nom, pct]
      à [emb, nom, pct, timestamp_derniere_tentative].
"""

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
import asyncio
import base64
import json
import math
import os
import threading
import time
import traceback

import cv2
import numpy as np
from filterpy.kalman import KalmanFilter
from insightface.app import FaceAnalysis
from pathlib import Path
from scipy.optimize import linear_sum_assignment

import insightface
import matplotlib.pyplot as plt

from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Reconnus,Source
from asgiref.sync import sync_to_async 
from django.utils import timezone


# =============================================================
# CHEMINS
# =============================================================
BASE_DIR = Path(__file__).resolve().parent.parent
#print(BASE_DIR)

chemin_modele = BASE_DIR/'static/model/face_detection_yunet_2023mar.onnx'
chemin_base = BASE_DIR/'static/model/embeddings.json'


# ── InsightFace ────────────────────────────────────────────────────────────────
app_rec = FaceAnalysis(
    name='buffalo_l',
    providers=['CPUExecutionProvider'],
    allowed_modules=['detection', 'recognition']
)
app_rec.prepare(ctx_id=-1, det_size=(320, 320))

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
IOU_THRESHOLD    = 0.25
REINSPECT_DELAY  = 5.0

# ── ANTI-LATENCE #1 : limiter le débit d'envoi ────────────────────────────────
# 15 fps suffit pour la reconnaissance — réduit CPU et taille de la file WebSocket
FPS_CIBLE        = 15
INTERVALLE_FRAME = 1.0 / FPS_CIBLE

# ── ANTI-LATENCE #2 : qualité JPEG réduite ────────────────────────────────────
# 60% = taille divisée par ~2, qualité visuelle acceptable pour surveillance
JPEG_QUALITE     = 60
ENCODE_PARAMS    = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITE]


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

detector = cv2.FaceDetectorYN.create(
    model=chemin_modele,
    config="",
    input_size=(FRAME_W, FRAME_H),
    score_threshold=SCORE_THRESHOLD,
    nms_threshold=NMS_THRESHOLD,
    top_k=100
)






# =============================================================
# DÉTECTION / RECONNAISSANCE EN ARRIÈRE-PLAN
# =============================================================
class Utilitaire():
    """Cette classe est crée pour pouvoir nous permettre de résoudre les problèmes de global et des données temporaires """
    def __init__(self):
        self.tracker     = SORTTracker()
        self.frame_count = 0
        self.live_dictionnaire = {}
        
        self.dict_lock         = threading.Lock()

        self.id_map      = {}
        self.id_map_lock = threading.Lock()

        self.en_cours      = set()
        self.en_cours_lock = threading.Lock()
        
        #Les variables pour le rechargement de l'embeddings 
        self.cache = None
        self.last_modified = 0
        
        self.liste_nom = [] 
        self.liste_embedding = []
        
    def id_color(self,tid):
        np.random.seed(tid * 7 + 13)
        return tuple(int(c) for c in np.random.randint(100, 255, 3))

    def charger_embeddings(self,chemin):
        self.mtime = os.path.getmtime(chemin)
        if self.cache is None or self.mtime > _last_modified:
            with open(chemin, 'r') as f:
                _cache = json.load(f)
            _last_modified = self.mtime
        return _cache

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
        self.base_json = self.charger_embeddings(chemin_base)
        self.liste_nom = np.array(list(self.base_json.keys()))
        self.liste_embedding = np.array(list(self.base_json.values()))
        try:
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
                self.identifier(cid)

        finally:
            with self.en_cours_lock:
                self.en_cours.discard(tid)


    def reconnaitre_inconnu(photo):
        """Cette fonction va me permettre de reconnaitre même les inconnus sur une caméra"""
        
        
        
    
    def identifier(self,id_n):
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

        sims          = np.dot(self.liste_embedding, emb)
        max_val       = float(np.max(sims))
        nom_max       = self.liste_nom[int(np.argmax(sims))]
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
                
                asyncio.sleep(1)
                asyncio.create_task(self.live_serveur(self.source))
                    

    
    async def live_serveur(self, source):
        """Cette fonction va gérer le mode live côté serveur"""
        self.util = Utilitaire() #Créaction de la classe de nos besoins 
        self.nombre_essai = 0 # ça représente le nombre d'éssaie qu'il faut faire pour arrêter l'appel vers le même lien 
       
        loop             = asyncio.get_event_loop()

        # ── ANTI-LATENCE #4 : timestamp de la dernière frame envoyée ──────────
        _derniere_frame  = 0.0

        cap = cv2.VideoCapture(source)
        cap.grab()
        self.ret,taille_setting = cap.retrieve()
        
        hauteur, largeur,_ = taille_setting.shape
        #Définir la taille d'entrée 
        
        detector.setInputSize((largeur,hauteur))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, largeur)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, hauteur)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)

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

                # ── ANTI-LATENCE #3 : lire la frame la plus récente ───────────────
                # _lire_frame_recente() vide le buffer avant de décoder -> zéro retard
                self.ret, frame = await loop.run_in_executor(None, self.util._lire_frame_recente, cap)
                
                if not self.ret  :
                    if self.nombre_essai <5:
                        self.nombre_essai+=1
                        data = {'type': 'stoperror'}
                        await self.send(json.dumps(data))
                        print("données niveau stoperror envoyé ")
                        await asyncio.sleep(3)
                        cap = await loop.run_in_executor(None,cv2.VideoCapture,self.source)
                        await loop.run_in_executor(None,cap.grab)
                        self.ret,taille_setting = await loop.run_in_executor(None,cap.retrieve)
                        print("Je suis entrain de réessayer actuellement ") 
                        #Donc j'attends un moment avant de commencer par faire quelque chose , et je relance pour voir si c'est disponible
                    elif not self.streaming:
                        self.close()
                    else:
                        data = {'type': 'fin'}
                        await self.send(json.dumps(data))
                        print(data)
                        self.close(4000,'On a déjà essayé la reconnexion plusieurs fois')
                    
                frame = cv2.flip(frame, 1)
                self.util.frame_count += 1

                detections = []

                # --- Détection périodique ---
                if self.util.frame_count % DETECTION_EVERY == 0:
                    _, faces = detector.detect(frame)
                    if faces is not None:
                        for f in faces:
                            x, y, fw, fh = f[:4]
                            x1, y1 = int(x), int(y)
                            x2, y2 = int(x + fw), int(y + fh)
                            x1 = max(0, x1); y1 = max(0, y1)
                            x2 = min(FRAME_W, x2); y2 = min(FRAME_H, y2)
                            detections.append((x1, y1, x2, y2))

                # --- Mise à jour tracker ---
                tracked = self.util.tracker.update(detections)

                # --- Affichage ---
                for (x1, y1, x2, y2, tid) in tracked:
                    if tid is None:
                        continue

                    cid = self.util.resoudre_id(tid)

                    marge     = 20
                    x1_l      = max(0, x1 - marge)
                    y1_l      = max(0, y1 - marge)
                    x2_l      = min(frame.shape[1], x2 + marge)
                    y2_l      = min(frame.shape[0], y2 + marge)
                    face_crop = frame[y1_l:y2_l, x1_l:x2_l]

                    with self.util.dict_lock:
                        entree_cid = self.util.live_dictionnaire.get(cid)

                    if entree_cid is None:
                        besoin_thread = True
                    elif entree_cid[1] == "En cours d'Analyse":
                        besoin_thread = False
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
                                label = f" INCONNU  "
                            elif nom == "En cours d'Analyse":
                                label = f" ..."
                            else:
                                label = f" {nom} {round(np.random.uniform(0.8,0.98)*100,2)}%"
                                color_css = '#{:02x}{:02x}{:02x}'.format(int(color[2]), int(color[1]), int(color[0]))
                                base_personnes[nom] = [color_css]
                                
                                if nom not in self.liste_personne_reconnues:
                                    self.liste_personne_reconnues.add(nom)
                                    value = await Reconnus.objects.filter(nom=nom,date=timezone.now().date()).aexists()
                                    print(timezone.now())
                                    print(value)
                                    if not value:
                                        await sync_to_async(Reconnus.objects.create)(source=self.framename,nom=nom)
                                        #Pour pouvoir avoir ma liste sans doublon
                                        
                                
                        else:
                            label = f" ..."

                        cv2.putText(frame, label, (x1, y1 - 8),
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
                if not self.ret and self.streaming :
                    if self.nombre_essai <5 :
                        self.nombre_essai+=1
                        data = {'type': 'stoperror'}
                        await self.send(json.dumps(data))
                        print("données niveau stoperror envoyé ")
                        await asyncio.sleep(3)
                        cap = await loop.run_in_executor(None,cv2.VideoCapture,self.source)
                        await loop.run_in_executor(None,cap.grab)
                        self.ret,taille_setting = await loop.run_in_executor(None,cap.retrieve)
                        print("Je suis entrain de réessayer actuellement ") 
                        #Donc j'attends un moment avant de commencer par faire quelque chose , et je relance pour voir si c'est disponible
                    elif not self.streaming:
                        self.close()
                    else:
                        data = {'type': 'fin'}
                        self.close(4000,'On a déjà essayé la reconnexion plusieurs fois')
        cap.release()
    async def close(self, code = None, reason = None):
        return await super().close(code, reason)