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
import time,tkinter,math
from scipy.optimize import linear_sum_assignment
from filterpy.kalman import KalmanFilter
import insightface 
import threading , os,json 
from insightface.app import FaceAnalysis
import matplotlib.pyplot as plt
from pathlib import Path

#Ouverture du modèle de reconnaissance json

try:
    with open('nom_model.txt','r') as f:
        modele = f.read()
except Exception as e:
    print('Modèle non chargable, sélectionner une path',e)
    modele = filedialog.askopenfilename(title='Selectionner le nom du model')
    with open('nom_model.txt','w') as f:
        f.write(modele)

# Charger la base d'embeddings sauvegardée

with open(modele, 'r') as f:
    base_json = json.load(f)
#On aura besoin de numpy pour pouvoir faire des arrays afin de profiter de la puissance de numpy 
liste_nom = np.array(list(base_json.keys()))
liste_embedding = np.array(list(base_json.values()))

# Reconvertir les listes en arrays numpy
BASE_EMBEDDINGS = {nom: np.array(emb) for nom, emb in base_json.items()}

# Initialiser InsightFace pour la détection en temps réel
# buffalo_sc suffit pour la détection (on n'a pas besoin de buffalo_l
# car l'embedding est déjà dans les attributs retournés)
app_rec = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'],allowed_modules=['detection', 'recognition'])
# Ne charger que le détecteur et le reconnaissance (embedding)

app_rec.prepare(ctx_id=-1, det_size=(320,320))

# Seuil de décision
# 0.5 est une bonne valeur de départ
# Tu peux ajuster : plus haut = plus strict (moins de faux positifs)
#                   plus bas  = plus permissif (moins de faux négatifs)
SEUIL_COSINUS = 0.5

# ─────────────────────────────────────────────────────────────

# =============================================================
# CONFIG
# =============================================================

MODEL_PATH      = "face_detection_yunet_2023mar.onnx"
FRAME_W         = 640
FRAME_H         = 480
SCORE_THRESHOLD = 0.75       # Confiance minimale de détection
NMS_THRESHOLD   = 0.3
DETECTION_EVERY = 2          # Détecter 1 frame sur 2 (équilibre vitesse/stabilité)
MAX_AGE         = 20         # Frames avant suppression d'un track perdu
MIN_HITS        = 2          # Hits minimum avant d'afficher un track
IOU_THRESHOLD   = 0.25       # Seuil d'association IoU

# =============================================================
# KALMAN FILTER PAR TRACK
# Modèle d'état : [x, y, w, h, vx, vy, vw, vh]
# Mesure       : [x, y, w, h]
# =============================================================

def create_kalman(bbox):
    """
    Crée un filtre de Kalman pour un nouveau visage détecté.
    bbox : (x1, y1, x2, y2)
    """
    kf = KalmanFilter(dim_x=8, dim_z=4)

    # Matrice de transition (mouvement linéaire)
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

    # Matrice d'observation (on observe x,y,w,h)
    kf.H = np.array([
        [1,0,0,0, 0,0,0,0],
        [0,1,0,0, 0,0,0,0],
        [0,0,1,0, 0,0,0,0],
        [0,0,0,1, 0,0,0,0],
    ], dtype=float)

    kf.R[2:, 2:] *= 10.   # Bruit de mesure
    kf.P[4:, 4:] *= 1000. # Incertitude initiale sur la vitesse
    kf.P          *= 10.
    kf.Q[-1, -1]  *= 0.01
    kf.Q[4:, 4:]  *= 0.01

    x1, y1, x2, y2 = bbox
    kf.x[:4] = np.array([[x1], [y1], [x2], [y2]])

    return kf


def bbox_from_kalman(kf):
    """Extrait le bbox prédit depuis l'état Kalman."""
    state = kf.x[:4].flatten()
    x1, y1, x2, y2 = state
    return (int(x1), int(y1), int(x2), int(y2))


# =============================================================
# IOu UTILS
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
    """Matrice IoU entre toutes les détections et prédictions."""
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
        self.id       = track_id
        self.kf       = create_kalman(bbox)
        self.age      = 0        # frames depuis la dernière association
        self.hits     = 1        # associations réussies
        self.active   = False    # visible à l'écran ?

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
        """
        detections : liste de (x1, y1, x2, y2)
        Retourne   : liste de (x1, y1, x2, y2, track_id) pour tracks actifs
        """
        # --- Prédiction Kalman pour chaque track ---
        predictions = []
        for t in self.tracks:
            predictions.append(t.predict())

        # --- Association Hongrois si on a détections ET tracks ---
        matched      = []
        unmatched_d  = list(range(len(detections)))
        unmatched_t  = list(range(len(self.tracks)))

        if len(detections) > 0 and len(self.tracks) > 0:
            iou_mat = iou_matrix(detections, predictions)
            # Maximiser IoU → minimiser coût négatif
            row_ind, col_ind = linear_sum_assignment(-iou_mat)

            for r, c in zip(row_ind, col_ind):
                if iou_mat[r, c] >= IOU_THRESHOLD:
                    matched.append((r, c))
                    if r in unmatched_d: unmatched_d.remove(r)
                    if c in unmatched_t: unmatched_t.remove(c)

        # --- Mise à jour des tracks associés ---
        for det_idx, trk_idx in matched:
            self.tracks[trk_idx].update(detections[det_idx])

        # --- Nouveaux tracks pour détections non associées ---
        for idx in unmatched_d:
            new_track = Track(self.next_id, detections[idx])
            self.next_id += 1
            self.tracks.append(new_track)

        # --- Suppression des tracks trop vieux ---
        self.tracks = [t for t in self.tracks if t.age <= MAX_AGE]

        # --- Résultats : tracks actifs seulement ---
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
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)   # Réduit la latence

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


# Couleurs par ID (pour distinguer visuellement chaque visage)
def id_color(tid):
    np.random.seed(tid * 7 + 13)
    return tuple(int(c) for c in np.random.randint(100, 255, 3))

# =============================================================
# BOUCLE PRINCIPALE
# =============================================================


#ici, on va s'arranger de détecter les visages, d'extraire leurs embeddings, d'enregistrer ça dans un dictionnaire en temps réel, à chaque fois qu'on a un nouveau visage, on l'ajoute au dictionnaire 
#Après , on faire la comparaison avec les embeddings déjà présents dans le dictionnaire à chaque fois pour voir s'il n'y a pas de nouveau visage, 
# ON prend le visage obtenu, on lance un thread d'arrière plan pour faire la reconnaissance avec insightface ,et après on sort le résultat dans l'image 





#---------Fonctionnalités principale------
#Detection des embeddings et reconnaissance 
live_dictionnaire  = {} #Format {id:[embedding,nom,pourcentage]}
pret = None
def obtenir_embedding(id_n,img:np.ndarray):
    """Cette fonction va nous permettre d'obtenir l'embedding d'un visage à l'aide de l'array de l'image accompagné de son id pour pouvoir faire le lien avec le dictionnaire en temps réel"""
    visages = app_rec.get(img)
    if visages:
        emb = visages[0].normed_embedding
        live_dictionnaire[id_n]=[emb,"En cours d'Analyse",0]
        global pret 
        pret= True


def identifier(id_n):
    """Une fonction qui va faire le calcul par la similarité cosinus de l'embedding et retourner le nom de la personne ou inconnu à partir de l'embedding"""
    if live_dictionnaire=={}:
        return 
    emb = live_dictionnaire[id_n][0]
    if emb is None:
        print(" Image illisible"); return 
    liste_calcul_similarite = np.dot(liste_embedding,emb)
    max_valeur = np.max(liste_calcul_similarite)
    indice_max = np.argmax(liste_calcul_similarite) #Ceci nous permet de connaitre l'indice de la valeur maximale afin d'avoir le nom correspondant
    nom_max = liste_nom [indice_max]
    # Décision selon le seuil
    if max_valeur >= SEUIL_COSINUS:
        nom_final = nom_max 
        print(f"Reconnu : {nom_final} | Similarité : {max_valeur:.3f}")
    else:
        nom_final = "INCONNU"
        print(f" Inconnu | Meilleure correspondance : {nom_max} ({max_valeur:.3f})")
    pourcentage = max_valeur*100
    print(nom_final,pourcentage)
    live_dictionnaire[id_n]=[emb,nom_final,pourcentage]

def checker_existence(tid):
    if live_dictionnaire == {} or tid not in live_dictionnaire:
        return tid

    emb = live_dictionnaire[tid][0]
    if emb is None:
        return tid

    liste_live_id        = list(live_dictionnaire.keys())
    liste_live_embedding = np.array([live_dictionnaire[k][0] for k in liste_live_id])

    similarites = np.dot(liste_live_embedding, emb)
    indice_max  = int(np.argmax(similarites))
    max_valeur  = float(similarites[indice_max])
    id_n        = liste_live_id[indice_max]

    if max_valeur < SEUIL_COSINUS or tid == id_n:
        return tid

    statut_id_n = live_dictionnaire[id_n][1]

    # Cas 1 : id_n est encore en cours d'analyse → pas de fusion hâtive
    if statut_id_n == "En cours d'Analyse":
        return tid

    # Cas 2 : id_n est INCONNU → on fusionne et on re-identifie
    if statut_id_n == "INCONNU":
        live_dictionnaire[tid] = live_dictionnaire[id_n]
        live_dictionnaire.pop(id_n)
        return id_n   # on retourne id_n pour relancer l'identification

    # Cas 3 : id_n est une personne connue → fusion propre, on garde son entrée
    live_dictionnaire[tid] = live_dictionnaire[id_n]
    live_dictionnaire.pop(id_n)
    return id_n   # tid hérite du nom et de l'ID canonique de id_n
   



while True:
    ret, frame = cap.read()
    if not ret:
        break

    frame = cv2.flip(frame, 1)
    frame_count += 1

    detections = []

    # --- Détection périodique (1 frame sur DETECTION_EVERY) ---
    if frame_count % DETECTION_EVERY == 0:
        _, faces = detector.detect(frame)

        if faces is not None:
            for f in faces:
                
                x, y, fw, fh = f[:4]
                x1, y1 = int(x), int(y)
                x2, y2 = int(x + fw), int(y + fh)
                # Clamp dans les limites de la frame
                x1 = max(0, x1); y1 = max(0, y1)
                x2 = min(FRAME_W, x2); y2 = min(FRAME_H, y2)
                detections.append((x1, y1, x2, y2))

    # --- Mise à jour tracker (même sans nouvelles détections) ---
    tracked = tracker.update(detections)

    # --- Affichage ---
    for (x1, y1, x2, y2, tid) in tracked:
        if tid:
            marge = 20  

            # 2. On applique la marge tout en bloquant les valeurs pour ne pas sortir de l'image (grâce à max et min)
            x1_large = max(0, x1 - marge)
            y1_large = max(0, y1 - marge)

            x2_large = min(frame.shape[1], x2 + marge)
            y2_large = min(frame.shape[0], y2 + marge)

            # 3. Découpage final agrandi et totalement sécurisé
            face_crop = frame[y1_large:y2_large, x1_large:x2_large]
            # Extraire le visage pour l'extraction de l'embedding 
            pret = False
            threading.Thread(target=obtenir_embedding, args=(tid,face_crop)).start() #Lancer un thread pour obtenir l'embedding du visage
            
            #Maintenant, on checke si le visage était déjà identifié et reconnu auparavant en live pour éviter de faire des calculs inutiles
            new_id = checker_existence(tid)
            if pret:  
                pass
            else:
                new_id = tid
            if new_id != tid:
                threading.Thread(target=identifier, args=(tid,)).start() #Lancer un thread pour identifier le visage à partir de son embedding
            else:
                pass
            color = id_color(new_id)
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            try:
                if live_dictionnaire[new_id][1]=='INCONNU':
                    label = f"ID {new_id}:{live_dictionnaire[new_id][1]}%"
                    cv2.putText(frame, label, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                    cv2.putText(frame, f"Faces : {len(tracked)}",   (15, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,200,255), 2)
                else:
                    label = f"ID {new_id}:{live_dictionnaire[new_id][1]} {live_dictionnaire[new_id][2]:.1f}%"
                    cv2.putText(frame, label, (x1, y1 - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
                    cv2.putText(frame, f"Faces : {len(tracked)}",   (15, 65),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,200,255), 2)
            except Exception as e:
                print(e,"C'est l'erreur ça")
                traceback.print_exc()    
        else:
            pass

        cv2.imshow("Face Tracking — YuNet + SORT (CPU)", frame)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

cap.release()
cv2.destroyAllWindows()