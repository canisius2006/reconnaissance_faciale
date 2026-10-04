# ─────────────────────────────────────────────────────────────
# RECONNAISSANCE PAR EMBEDDINGS
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
from .insight import get_app_rec
# tkinter et matplotlib ne sont importés que dans identifier() et dans le bloc __main__ (outils de test local) :
# sur un serveur Linux sans Tk, les importer ici empêchait Django de démarrer.
from pathlib import Path
from . import embeddings_cache

BASE_DIR = Path(__file__).resolve().parent.parent
chemin_modele = BASE_DIR/'static/model/face_detection_yunet_2023mar.onnx'
chemin_base = BASE_DIR/'static/model/embeddings.json'

base = chemin_base


# Les embeddings de référence viennent de embeddings_cache : ils sont rechargés dynamiquement
# (avant : lus une seule fois à l'import, donc une personne ajoutée restait invisible
# jusqu'au redémarrage du serveur).

# Initialiser InsightFace pour la détection en temps réel
# buffalo_sc suffit pour la détection (on n'a pas besoin de buffalo_l
# car l'embedding est déjà dans les attributs retournés)
# Même instance que celle de consumers.py (une seule copie du modèle en mémoire)
app_rec = get_app_rec()

# Seuil de décision
# 0.5 est une bonne valeur de départ
# Tu peux ajuster : plus haut = plus strict (moins de faux positifs)
#                   plus bas  = plus permissif (moins de faux négatifs)
SEUIL_COSINUS = 0.5

# ─────────────────────────────────────────────────────────────
def identifier(chemin_photo):
    import matplotlib.pyplot as plt # import local : outil de test, pas utilisé par le serveur
    """
    Identifie la personne sur une photo.
    Retourne (nom, similarité) ou ('INCONNU', similarité_max)
    """
    img = cv2.imread(chemin_photo)
    if img is None:
        print(" Image illisible"); return

    visages = app_rec.get(img)
    
    if len(visages) == 0:
        print(" Aucun visage détecté"); return

    #Ici, je cherche la taille de l'image 
    height,width = img.shape[:2]
    print(f"Hauteur : {height} , largeur : {width}")
    #Ici, je définis un score pour m'assurer qu'au moins c'est forcément un visage 
    score_min = 0.5
    # Prendre le visage avec le meilleur score de détection
    visages = [visage for visage in visages if visage.det_score>score_min]
    liste_nom, liste_embedding = embeddings_cache.obtenir()
    for visage in visages:
        emb_inconnu = visage.normed_embedding
        if len(liste_nom) == 0:
            max_valeur, nom_max = 0.0, "INCONNU" # aucune personne enregistrée
        else:
            liste_calcul_similarite = np.dot(liste_embedding,emb_inconnu)
            max_valeur = np.max(liste_calcul_similarite)
            indice_max = np.argmax(liste_calcul_similarite) #Ceci nous permet de connaitre l'indice de la valeur maximale afin d'avoir le nom correspondant
            nom_max = liste_nom [indice_max]

        # Décision selon le seuil
        if max_valeur >= SEUIL_COSINUS:
            nom_final = nom_max 
            couleur   = (0, 255, 0)
            couleur_css = "#FF0000"
            
            print(f"Reconnu : {nom_final} | Similarité : {max_valeur:.3f}")
        else:
            nom_final = "INCONNU"
            couleur   = (0, 0, 255)
            couleur_css =  "#00FF00"
            print(f" Inconnu | Meilleure correspondance : {nom_max} ({max_valeur:.3f})")

        # Afficher le résultat
        x1, y1, x2, y2 = visage.bbox.astype(int)
        
        cv2.rectangle(img, (x1,y1), (x2,y2), couleur, 2)
        cv2.putText(img, f"{nom_final}", (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.3, couleur, 3)
    plt.figure(figsize=(6,6))
    plt.imshow(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    plt.axis('off')
    plt.show()



def identifier_serveur_image(img):
    """Cette fonction va nous permettre d'indentifier un visage mais en passant par le serveur avec le mode image
    Retourne (nom, similarité) ou ('INCONNU', similarité_max)"""
    personnes = {} #Un dictionnaire  vide pour récueillir les noms des personnes reconnues à la fin 
    #Maintenant, on fait la reconnaissance faciale  
    if img is None:
        print(" Image illisible")
        lisible=0 #Pour dire null

    visages = app_rec.get(img)
    
    if len(visages) == 0:
        print(" Aucun visage détecté")
        visage = 0
    #Ici, je cherche la taille de l'image 
    height,width = img.shape[:2]
    height = int(height)
    width = int(width)
    #Ici, je définis un score pour m'assurer qu'au moins c'est forcément un visage 
    score_min = 0.5
    # Prendre le visage avec le meilleur score de détection
    visages = [visage for visage in visages if visage.det_score>score_min]
    liste_nom, liste_embedding = embeddings_cache.obtenir()
    for visage in visages:
        emb_inconnu = visage.normed_embedding
        if len(liste_nom) == 0:
            max_valeur, nom_max = 0.0, "INCONNU" # aucune personne enregistrée
        else:
            liste_calcul_similarite = np.dot(liste_embedding,emb_inconnu)
            max_valeur = np.max(liste_calcul_similarite)
            indice_max = np.argmax(liste_calcul_similarite) #Ceci nous permet de connaitre l'indice de la valeur maximale afin d'avoir le nom correspondant
            nom_max = liste_nom [indice_max]
        #Conversion des np en normal, int et str 
        nom_max = str(nom_max)
        max_valeur = float(max_valeur)

        # Décision selon le seuil
        if max_valeur >= SEUIL_COSINUS:
            nom_final = nom_max 
            couleur   = (0, 255, 0)
            couleur_css =  "#00FF00"
            print(f"Reconnu : {nom_final} | Similarité : {max_valeur:.3f}")
            valeur = max_valeur*100 # vraie similarité cosinus x 100 (avant : valeur inventée entre 80 et 96)
            personnes[nom_final] = [couleur_css] #Je répète nom_final parce que son format doit se rapporter à celui de listes personnes de websocket 

        else:
            nom_final = "INCONNU"
            couleur   = (0, 0, 255)
            couleur_css =  "#FF0000"
            valeur = 0
            print(f" Inconnu | Meilleure correspondance : {nom_max} ({max_valeur:.3f})")
            

        # Afficher le résultat
        x1, y1, x2, y2 = visage.bbox.astype(int)
        cv2.rectangle(img, (x1,y1), (x2,y2), couleur, 4)
        cv2.putText(img, f"{nom_final} {valeur:.1f}%", (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.4, couleur, 3)
    return img,personnes
       

if __name__=='__main__':
    # Tester
    print("Uploade une photo de test :")
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    uploaded =filedialog.askopenfilename()
    root.destroy()
    print(uploaded)
    identifier(Path(uploaded))