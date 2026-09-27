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
"""
import traceback
import cv2
import numpy as np
from tkinter import filedialog
import time, tkinter, math
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter
import insightface
import threading, os, json
from insightface.app import FaceAnalysis
import matplotlib.pyplot as plt
from pathlib import Path

# Ouverture du modèle de reconnaissance json
try:
    with open('nom_model.txt', 'r') as f:
        modele = f.read().strip()
except Exception as e:
    print('Modèle non chargable, sélectionner une path', e)
    modele = filedialog.askopenfilename(title='Selectionner le nom du model')
    with open('nom_model.txt', 'w') as f:
        f.write(modele)

# Charger la base d'embeddings sauvegardée
with open(modele, 'r') as f:
    base_json = json.load(f)

liste_nom = np.array(list(base_json.keys()))
liste_embedding = np.array(list(base_json.values()))

# Reconvertir les listes en arrays numpy
BASE_EMBEDDINGS = {nom: np.array(emb) for nom, emb in base_json.items()}

# Initialiser InsightFace pour la détection en temps réel
app_rec = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'],
                       allowed_modules=['detection', 'recognition'])
app_rec.prepare(ctx_id=-1, det_size=(320, 320))

# Seuil de décision cosinus
SEUIL_COSINUS = 0.5

# =============================================================
# CONFIG
# =============================================================

MODEL_PATH      = r"C:\projet_django\rf_projet\amelioration\face_detection_yunet_2023mar.onnx"
FRAME_W         = 640
FRAME_H         = 480
SCORE_THRESHOLD = 0.75
NMS_THRESHOLD   = 0.3
DETECTION_EVERY = 2
MAX_AGE         = 20
MIN_HITS        = 2
IOU_THRESHOLD   = 0.25

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

cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

detector = cv2.FaceDetectorYN.create(
    model=MODEL_PATH,
    config="",
    input_size=(FRAME_W, FRAME_H),
    score_threshold=SCORE_THRESHOLD,
    nms_threshold=NMS_THRESHOLD,
    top_k=100
)

tracker     = SORTTracker()
frame_count = 0


def id_color(tid):
    np.random.seed(tid * 7 + 13)
    return tuple(int(c) for c in np.random.randint(100, 255, 3))


# =============================================================
# DÉTECTION / RECONNAISSANCE EN ARRIÈRE-PLAN
# =============================================================

live_dictionnaire = {}   # {id_canonique: [embedding, nom, pourcentage]}
dict_lock         = threading.Lock()

# Table de redirection persistante : {tid_quelconque → id_canonique}
# Une fois qu'on sait que tid=5 est la même personne que tid=2,
# on enregistre id_map[5] = 2 et on ne le remet jamais en question.
id_map     = {}
id_map_lock = threading.Lock()

# Ensemble des ids dont le thread d'embedding est déjà en cours
# (pour ne pas lancer plusieurs threads en parallèle pour le même tid)
en_cours     = set()
en_cours_lock = threading.Lock()


def resoudre_id(tid):
    """Retourne l'id canonique associé à tid (suit la chaîne de redirections)."""
    with id_map_lock:
        visited = set()
        cid = tid
        while cid in id_map and cid not in visited:
            visited.add(cid)
            cid = id_map[cid]
        return cid


def obtenir_embedding(tid, img: np.ndarray):
    """
    Extrait l'embedding du visage cropé.
    - Si le visage ressemble à un id déjà connu → enregistre la redirection dans id_map
      et ne crée PAS de nouvelle entrée dans live_dictionnaire.
    - Sinon → crée une nouvelle entrée et lance l'identification.
    """
    try:
        visages = app_rec.get(img)
        if not visages:
            return
        emb = visages[0].normed_embedding

        # 1. Chercher si ce visage correspond à un id canonique déjà présent
        with dict_lock:
            ids_connus   = list(live_dictionnaire.keys())
            embs_connus  = [live_dictionnaire[k][0] for k in ids_connus]

        id_trouve = None
        if embs_connus:
            embs_array  = np.array(embs_connus)
            similarites = np.dot(embs_array, emb)
            max_val     = float(np.max(similarites))
            if max_val >= SEUIL_COSINUS:
                id_trouve = ids_connus[int(np.argmax(similarites))]

        if id_trouve is not None:
            # Ce tid est la même personne que id_trouve → redirection permanente
            with id_map_lock:
                id_map[tid] = id_trouve
            # Pas besoin de re-identifier, les infos sont déjà dans live_dictionnaire
        else:
            # Nouveau visage : créer l'entrée et identifier
            with dict_lock:
                # Vérifier une dernière fois qu'un thread concurrent n'a pas déjà créé tid
                if tid not in live_dictionnaire:
                    live_dictionnaire[tid] = [emb, "En cours d'Analyse", 0]
                else:
                    # Entrée déjà créée par un autre thread, on met juste l'embedding à jour
                    if live_dictionnaire[tid][1] == "En cours d'Analyse":
                        live_dictionnaire[tid][0] = emb
            identifier(tid)
    finally:
        with en_cours_lock:
            en_cours.discard(tid)


def identifier(id_n):
    """Identifie le visage via similarité cosinus sur la base de référence."""
    with dict_lock:
        if id_n not in live_dictionnaire:
            return
        emb = live_dictionnaire[id_n][0]

    if emb is None:
        return

    sims       = np.dot(liste_embedding, emb)
    max_val    = float(np.max(sims))
    nom_max    = liste_nom[int(np.argmax(sims))]
    nom_final  = nom_max if max_val >= SEUIL_COSINUS else "INCONNU"
    pourcentage = max_val * 100

    print(f"{'Reconnu' if nom_final != 'INCONNU' else 'Inconnu'} : "
          f"{nom_final} | Similarité : {max_val:.3f}")

    with dict_lock:
        if id_n in live_dictionnaire:
            live_dictionnaire[id_n] = [live_dictionnaire[id_n][0], nom_final, pourcentage]


# =============================================================
# BOUCLE PRINCIPALE
# =============================================================

while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.flip(frame, 1)
    frame_count += 1

    detections = []

    # --- Détection périodique ---
    if frame_count % DETECTION_EVERY == 0:
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
    tracked = tracker.update(detections)

    # --- Affichage ---
    for (x1, y1, x2, y2, tid) in tracked:
        if tid is None:
            continue

        # Résoudre l'id canonique AVANT tout traitement
        cid = resoudre_id(tid)

        marge    = 20
        x1_l     = max(0, x1 - marge);          y1_l = max(0, y1 - marge)
        x2_l     = min(frame.shape[1], x2 + marge); y2_l = min(frame.shape[0], y2 + marge)
        face_crop = frame[y1_l:y2_l, x1_l:x2_l]

        # Lancer le thread SEULEMENT si :
        #   - cid n'est pas encore dans le dictionnaire (nouveau visage), OU
        #   - tid n'est pas encore dans id_map (on n'a pas encore confirmé la fusion)
        #   - ET aucun thread n'est déjà en cours pour ce tid
        with dict_lock:
            cid_connu = cid in live_dictionnaire and \
                        live_dictionnaire[cid][1] not in ("En cours d'Analyse",)
        with id_map_lock:
            tid_redirige = tid in id_map

        besoin_thread = (not cid_connu) or (not tid_redirige and tid != cid)

        if besoin_thread:
            with en_cours_lock:
                if tid not in en_cours:
                    en_cours.add(tid)
                    threading.Thread(target=obtenir_embedding,
                                     args=(tid, face_crop.copy()), daemon=True).start()

        # Après le thread, re-résoudre (la redirection a peut-être été mise à jour)
        cid = resoudre_id(tid)

        color = id_color(cid)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

        try:
            with dict_lock:
                entree = live_dictionnaire.get(cid)

            if entree:
                nom, pct = entree[1], entree[2]
                if nom == "INCONNU":
                    label = f"ID {cid} : INCONNU"
                elif nom == "En cours d'Analyse":
                    label = f"ID {cid} : ..."
                else:
                    label = f"ID {cid} : {nom} {pct:.1f}%"
            else:
                label = f"ID {cid} : ..."

            cv2.putText(frame, label, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
        except Exception as e:
            print(e, "C'est l'erreur ça")
            traceback.print_exc()

    cv2.putText(frame, f"Faces : {len(tracked)}", (15, 65),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
    cv2.imshow("Face Tracking — YuNet + SORT (CPU)", frame)

    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()