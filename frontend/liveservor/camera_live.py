# ─────────────────────────────────────────────────────────────
# RECONNAISSANCE PAR EMBEDDINGS (mode camera live)
#
# La similarité cosinus mesure l'angle entre deux vecteurs.
# Résultat entre -1 et 1 :
#   1.0  → vecteurs identiques → même personne
#   0.5  → seuil de décision recommandé
#   0.0  → vecteurs perpendiculaires → personnes très différentes
#  -1.0  → vecteurs opposés (rare en pratique)
# ─────────────────────────────────────────────────────────────

import numpy as np
import cv2, json
from insightface.app import FaceAnalysis
from tkinter import filedialog 
import tkinter
import matplotlib.pyplot as plt
from pathlib import Path
import threading 
import multiprocessing,os
import onnxruntime as ort 


#Ici, je vais essayer de maximiser le nombre de coeurs pour l'exécution de mon script 


# Charger la base d'embeddings sauvegardée

try:
    with open('nom_model.txt','r') as f:
        modele = f.read()
except Exception as e:
    print('Modèle non chargable, sélectionner une path',e)
    modele = filedialog.askopenfilename(title='Selectionner le nom du model')
    with open('nom_model.txt','w') as f:
        f.write(modele)


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
app_rec = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'],allowed_modules=['detection', 'recognition']
                  )
# Ne charger que le détecteur et le reconnaissance (embedding)

app_rec.prepare(ctx_id=-1, det_size=(160,160))

# Seuil de décision
# 0.5 est une bonne valeur de départ
# Tu peux ajuster : plus haut = plus strict (moins de faux positifs)
#                   plus bas  = plus permissif (moins de faux négatifs)
SEUIL_COSINUS = 0.5

# ─────────────────────────────────────────────────────────────

resultat = {}

def afficher(resultat:dict,frame):
    for data in resultat.items():
        nom_final = data[0] 
        x1, y1, x2, y2 = data[1]['bbox']
        max_valeur = data[1]['pourcentage']
        couleur = (0, 255, 0) if nom_final != "INCONNU" else (0, 0, 255)
        cv2.rectangle(frame, (x1,y1), (x2,y2), couleur, 2)
        cv2.putText(frame, f"{nom_final} ({max_valeur:.3f}) %", (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, couleur, 2)
        


def identifier(frame):
    """
    Identifie la personne sur une photo.
    Retourne (nom, similarité) ou ('INCONNU', similarité_max)
    """
    data = {}
    if frame is None:
        print(" Image illisible");return 

    visages = app_rec.get(frame)
    
    if len(visages) == 0:
        print(" Aucun visage détecté"); return

    #Ici, je cherche la taille de l'image 
    height,width = frame.shape[:2]
    #Ici, je définis un score pour m'assurer qu'au moins c'est forcément un visage 
    score_min = 0.5
    # Prendre le visage avec le meilleur score de détection
    visages = [visage for visage in visages if visage.det_score>score_min]
    for visage in visages:
        emb_inconnu = visage.normed_embedding
        liste_calcul_similarite = np.dot(liste_embedding,emb_inconnu)
        max_valeur = np.max(liste_calcul_similarite)
        indice_max = np.argmax(liste_calcul_similarite) #Ceci nous permet de connaitre l'indice de la valeur maximale afin d'avoir le nom correspondant
        nom_max = liste_nom [indice_max]

        # Décision selon le seuil
        if max_valeur >= SEUIL_COSINUS:
            nom_final = nom_max 
            couleur   = (0, 255, 0)
            print(f"Reconnu : {nom_final} | Similarité : {100*max_valeur:.3f}")
        else:
            nom_final = "INCONNU"
            couleur   = (0, 0, 255)
            print(f" Inconnu | Meilleure correspondance : {nom_max} ({100*max_valeur:.3f})")

        # Afficher le résultat
        x1, y1, x2, y2 = visage.bbox.astype(int)
        data['pourcentage'] = 100*max_valeur
        data['bbox'] = (x1, y1, x2, y2)
        
    resultat[nom_final]=data

cap = cv2.VideoCapture(0)

while True:
   
    if not cap.isOpened():
        break
    value,frame = cap.read()
    if not value:
        break
    identifier(frame)
    afficher(resultat, frame)
    cv2.imshow('Frame',frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break
    
cap.release()
cv2.destroyAllWindows()