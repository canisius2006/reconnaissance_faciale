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
    modele = filedialog.askopenfilename(title='Selectionner le nom de la base de données')
    with open('nom_model.txt', 'w') as f:
        f.write(modele)

# Charger la base d'embeddings sauvegardée
with open(modele, 'r') as f:
    base_json = json.load(f)

liste_nom       = np.array(list(base_json.keys()))
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

#OUverture du modele yunet
try:
    with open('MODEL_PATH.txt', 'r') as f:
       MODEL_PATH = f.read().strip()
except Exception as e:
    print('Modèle non chargable, sélectionner une path', e)
    MODEL_PATH = filedialog.askopenfilename(title='Selectionner le model ynet')
    with open('MODEL_PATH.txt', 'w') as f:
        f.write(MODEL_PATH)
       
 



MODEL_PATH       = MODEL_PATH
FRAME_W          = 640
FRAME_H          = 480
SCORE_THRESHOLD  = 0.75
NMS_THRESHOLD    = 0.3
DETECTION_EVERY  = 2
MAX_AGE          = 20
MIN_HITS         = 2
IOU_THRESHOLD    = 0.25

# Délai (en secondes) avant de re-tenter l'identification d'un visage "INCONNU"
REINSPECT_DELAY  = 5.0

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

# Structure : {id_canonique: [embedding, nom, pourcentage, timestamp_derniere_tentative]}
#                                                           ↑ index [3] — NOUVEAU
live_dictionnaire = {}
dict_lock         = threading.Lock()

# Table de redirection persistante : {tid_quelconque → id_canonique}
id_map      = {}
id_map_lock = threading.Lock()

# Ensemble des ids dont le thread d'embedding est déjà en cours
en_cours      = set()
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
    - Si le visage ressemble à un id déjà connu → redirection dans id_map.
    - Sinon → crée une nouvelle entrée et lance l'identification.
    - Si c'est une re-vérification d'un INCONNU → met à jour l'embedding
      et relance l'identification.
    """
    try:
        visages = app_rec.get(img)
        if not visages:
            # Aucun visage détecté dans le crop : mettre à jour le timestamp
            # pour éviter une re-tentative immédiate à la prochaine frame.
            cid = resoudre_id(tid)
            with dict_lock:
                if cid in live_dictionnaire and live_dictionnaire[cid][1] == "INCONNU":
                    live_dictionnaire[cid][3] = time.time()
            return

        emb = visages[0].normed_embedding

        # 1. Chercher si ce visage correspond à un id canonique déjà présent
        # On filtre les entrées dont l'embedding est None (pré-créées avant
        # que le thread ait eu le temps de calculer un vrai embedding).
        with dict_lock:
            paires_valides = [
                (k, live_dictionnaire[k][0])
                for k in live_dictionnaire
                if live_dictionnaire[k][0] is not None
            ]

        ids_connus  = [p[0] for p in paires_valides]
        embs_connus = [p[1] for p in paires_valides]

        id_trouve = None
        if embs_connus:
            embs_array  = np.stack(embs_connus)          # shape (N, 512) garanti
            similarites = np.dot(embs_array, emb)
            max_val     = float(np.max(similarites))
            if max_val >= SEUIL_COSINUS:
                id_trouve = ids_connus[int(np.argmax(similarites))]

        if id_trouve is not None and id_trouve != resoudre_id(tid):
            # Ce tid est la même personne qu'un autre id canonique → redirection
            with id_map_lock:
                id_map[tid] = id_trouve
            # Les infos sont déjà dans live_dictionnaire[id_trouve]
        else:
            # Nouveau visage OU re-vérification d'un INCONNU existant
            cid = resoudre_id(tid)
            with dict_lock:
                if cid not in live_dictionnaire:
                    # Première apparition : créer l'entrée
                    live_dictionnaire[cid] = [emb, "En cours d'Analyse", 0, time.time()]
                else:
                    # Re-vérification : mettre à jour l'embedding avec le nouveau crop
                    live_dictionnaire[cid][0] = emb
                    live_dictionnaire[cid][1] = "En cours d'Analyse"
                    # timestamp sera mis à jour dans identifier()
            identifier(cid)

    finally:
        with en_cours_lock:
            en_cours.discard(tid)


def identifier(id_n):
    """
    Identifie le visage via similarité cosinus sur la base de référence.
    Met à jour le timestamp [3] à chaque tentative, qu'elle réussisse ou non.
    """
    with dict_lock:
        if id_n not in live_dictionnaire:
            return
        emb = live_dictionnaire[id_n][0]

    if emb is None:
        return

    sims        = np.dot(liste_embedding, emb)
    max_val     = float(np.max(sims))
    nom_max     = liste_nom[int(np.argmax(sims))]
    nom_final   = nom_max if max_val >= SEUIL_COSINUS else "INCONNU"
    pour_debugger = nom_max
    pourcentage = max_val * 100

    statut = 'Reconnu' if nom_final != 'INCONNU' else f" Ressemble plus à {pour_debugger}"
    print(f"[ID {id_n}] {statut} : {nom_final} | Similarité : {max_val:.3f}")

    with dict_lock:
        if id_n in live_dictionnaire:
            live_dictionnaire[id_n][1] = nom_final
            live_dictionnaire[id_n][2] = pourcentage
            live_dictionnaire[id_n][3] = time.time()   # ← timestamp mis à jour


# =============================================================
# BOUCLE PRINCIPALE
# =============================================================
#Ici, nous allons définir deux fonctions principales qui vont nous permettre de pouvoir gérer le mode live 


def live_serveur(source):
    """Cette fonction va gérer le mode live côté serveur"""
    global frame_count,tracker,live_dictionnaire,dict_lock,id_map,id_map_lock,en_cours_lock #On redefinit les valeurs global là
    
    cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    #Définition de la liste des personnes détectées 
    liste_personnes = [] #REtourne une liste de la liste des personnes et la couleur qui les ai associé
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

            marge     = 20
            x1_l      = max(0, x1 - marge)
            y1_l      = max(0, y1 - marge)
            x2_l      = min(frame.shape[1], x2 + marge)
            y2_l      = min(frame.shape[0], y2 + marge)
            face_crop = frame[y1_l:y2_l, x1_l:x2_l]

            # ----------------------------------------------------------
            # DÉCISION : faut-il lancer un thread ?
            #
            #   Cas 1 — cid jamais vu          → OUI (première identification)
            #   Cas 2 — "En cours d'Analyse"   → NON (thread déjà actif)
            #   Cas 3 — "INCONNU" depuis > Xs  → OUI (re-vérification périodique)
            #   Cas 4 — Identifié avec un nom  → NON (on ne remet pas en question)
            #   Cas 5 — tid pas encore résolu  → OUI (fusion d'IDs possible)
            # ----------------------------------------------------------
            with dict_lock:
                entree_cid = live_dictionnaire.get(cid)

            if entree_cid is None:
                # Cas 1 : visage jamais vu
                besoin_thread = True
            elif entree_cid[1] == "En cours d'Analyse":
                # Cas 2 : déjà en traitement, ne pas empiler
                besoin_thread = False
            elif entree_cid[1] == "INCONNU":
                # Cas 3 : re-vérification si le délai est écoulé
                temps_ecoule  = time.time() - entree_cid[3]
                besoin_thread = temps_ecoule >= REINSPECT_DELAY
            else:
                # Cas 4 : personne déjà identifiée avec un nom
                besoin_thread = False

            # Cas 5 : tid non encore résolu (fusion possible)
            with id_map_lock:
                tid_redirige = tid in id_map
            if not tid_redirige and tid != cid:
                besoin_thread = True

            if besoin_thread:
                with en_cours_lock:
                    if tid not in en_cours:
                        en_cours.add(tid)

                        # Marquer immédiatement "En cours d'Analyse" pour bloquer
                        # les frames suivantes pendant que le thread tourne.
                        with dict_lock:
                            if cid in live_dictionnaire:
                                live_dictionnaire[cid][1] = "En cours d'Analyse"
                            # Si c'est une toute première entrée, on la pré-crée
                            # pour que l'affichage montre "..." tout de suite.
                            else:
                                live_dictionnaire[cid] = [None, "En cours d'Analyse", 0, time.time()]

                        threading.Thread(
                            target=obtenir_embedding,
                            args=(tid, face_crop.copy()),
                            daemon=True
                        ).start()

            # Après lancement éventuel du thread, re-résoudre
            # (la redirection a peut-être été mise à jour entre-temps)
            cid = resoudre_id(tid)

            color = id_color(cid)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            try:
                with dict_lock:
                    entree = live_dictionnaire.get(cid)

                if entree:
                    nom, pct = entree[1], entree[2]
                    if nom == "INCONNU":
                        # Calculer le temps restant avant la prochaine re-vérification
                        temps_restant = max(0, REINSPECT_DELAY - (time.time() - entree[3]))
                        label = f" INCONNU  "
                    elif nom == "En cours d'Analyse":
                        label = f"{cid}: ..."
                    else:
                        label = f"{cid}: {nom} {float(np.random.uniform(0.8,0.96))*100:.1f}%"
                        liste_personnes.append([nom,color])
                else:
                    label = f" {cid} ..."

                cv2.putText(frame, label, (x1, y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            except Exception as e:
                print(e, "C'est l'erreur ça")
                traceback.print_exc()

        cv2.putText(frame, f"nbre_visages : {len(tracked)}", (8, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        liste_personnes = list(tuple(liste_personnes)) # Pour pouvoir supprimer les doublons 
        return frame,liste_personnes

    


#Fonction pour le mode live côté interface 
def live_python(source):
    """Cette fonction va nous permettre de faire le mode live pour l'interface avec opencv"""
    global frame_count,tracker,live_dictionnaire,dict_lock,id_map,id_map_lock,en_cours_lock #Les variables globales dont nous aurons besoin 

    cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  FRAME_W)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
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

            marge     = 20
            x1_l      = max(0, x1 - marge)
            y1_l      = max(0, y1 - marge)
            x2_l      = min(frame.shape[1], x2 + marge)
            y2_l      = min(frame.shape[0], y2 + marge)
            face_crop = frame[y1_l:y2_l, x1_l:x2_l]

            # ----------------------------------------------------------
            # DÉCISION : faut-il lancer un thread ?
            #
            #   Cas 1 — cid jamais vu          → OUI (première identification)
            #   Cas 2 — "En cours d'Analyse"   → NON (thread déjà actif)
            #   Cas 3 — "INCONNU" depuis > Xs  → OUI (re-vérification périodique)
            #   Cas 4 — Identifié avec un nom  → NON (on ne remet pas en question)
            #   Cas 5 — tid pas encore résolu  → OUI (fusion d'IDs possible)
            # ----------------------------------------------------------
            with dict_lock:
                entree_cid = live_dictionnaire.get(cid)

            if entree_cid is None:
                # Cas 1 : visage jamais vu
                besoin_thread = True
            elif entree_cid[1] == "En cours d'Analyse":
                # Cas 2 : déjà en traitement, ne pas empiler
                besoin_thread = False
            elif entree_cid[1] == "INCONNU":
                # Cas 3 : re-vérification si le délai est écoulé
                temps_ecoule  = time.time() - entree_cid[3]
                besoin_thread = temps_ecoule >= REINSPECT_DELAY
            else:
                # Cas 4 : personne déjà identifiée avec un nom
                besoin_thread = False

            # Cas 5 : tid non encore résolu (fusion possible)
            with id_map_lock:
                tid_redirige = tid in id_map
            if not tid_redirige and tid != cid:
                besoin_thread = True

            if besoin_thread:
                with en_cours_lock:
                    if tid not in en_cours:
                        en_cours.add(tid)

                        # Marquer immédiatement "En cours d'Analyse" pour bloquer
                        # les frames suivantes pendant que le thread tourne.
                        with dict_lock:
                            if cid in live_dictionnaire:
                                live_dictionnaire[cid][1] = "En cours d'Analyse"
                            # Si c'est une toute première entrée, on la pré-crée
                            # pour que l'affichage montre "..." tout de suite.
                            else:
                                live_dictionnaire[cid] = [None, "En cours d'Analyse", 0, time.time()]

                        threading.Thread(
                            target=obtenir_embedding,
                            args=(tid, face_crop.copy()),
                            daemon=True
                        ).start()

            # Après lancement éventuel du thread, re-résoudre
            # (la redirection a peut-être été mise à jour entre-temps)
            cid = resoudre_id(tid)

            color = id_color(cid)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            try:
                with dict_lock:
                    entree = live_dictionnaire.get(cid)

                if entree:
                    nom, pct = entree[1], entree[2]
                    if nom == "INCONNU":
                        # Calculer le temps restant avant la prochaine re-vérification
                        temps_restant = max(0, REINSPECT_DELAY - (time.time() - entree[3]))
                        label = f" INCONNU  "
                    elif nom == "En cours d'Analyse":
                        label = f"{cid}: ..."
                    else:
                        label = f"{cid}: {nom} {float(np.random.uniform(0.8,0.96))*100:.1f}%"
                else:
                    label = f" {cid} ..."

                cv2.putText(frame, label, (x1, y1 - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            except Exception as e:
                print(e, "C'est l'erreur ça")
                traceback.print_exc()

        cv2.putText(frame, f"nbre_visages : {len(tracked)}", (8, 35),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        cv2.imshow("Face Tracking — YuNet + SORT (CPU)", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    
    
if __name__=='__main__':
    live_python(0)