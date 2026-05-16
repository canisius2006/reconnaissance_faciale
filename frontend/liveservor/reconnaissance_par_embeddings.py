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
from insightface.app import FaceAnalysis
from tkinter import filedialog 
import tkinter
import matplotlib.pyplot as plt
from pathlib import Path

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
def identifier(chemin_photo):
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
            print(f"Reconnu : {nom_final} | Similarité : {max_valeur:.3f}")
        else:
            nom_final = "INCONNU"
            couleur   = (0, 0, 255)
            print(f" Inconnu | Meilleure correspondance : {nom_max} ({max_valeur:.3f})")

        # Afficher le résultat
        x1, y1, x2, y2 = visage.bbox.astype(int)
        
        cv2.rectangle(img, (x1,y1), (x2,y2), couleur, 2)
        cv2.putText(img, nom_final, (x1, y1-10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, couleur, 2)
    plt.figure(figsize=(6,6))
    plt.imshow(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    plt.axis('off')
    plt.show()



def identifier_serveur_image(arg):
    """Cette fonction va nous permettre d'indentifier un visage mais en passant par le serveur avec le mode image
    Retourne (nom, similarité) ou ('INCONNU', similarité_max)"""
    data = [] #Une liste vide pour récueillir les résultats à la fin et pour connaitre le nombre 
    lisible = 1;visible = 1 #Par défaut, on dira que ces deux là sont tous=1
    
    # 2. Lire les octets du fichier (le transformer en buffer)
    file_bytes = np.frombuffer(arg.read(), np.uint8)

    # 3. Décoder le buffer pour obtenir une image OpenCV (format BGR)
    img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)

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
    for visage in visages:
        emb_inconnu = visage.normed_embedding
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
            print(f"Reconnu : {nom_final} | Similarité : {max_valeur:.3f}")
            
        else:
            nom_final = "INCONNU"
            couleur   = (0, 0, 255)
            print(f" Inconnu | Meilleure correspondance : {nom_max} ({max_valeur:.3f})")

        # Afficher le résultat
        x1, y1, x2, y2 = visage.bbox.astype(int)
        #On va calculer la position des nouvelles coordonnées pour mieux faire la scalabilité au niveau du frontend
        x1=x1/width #640 est notre taille ici 
        x2 = x2/width 
        y1 = y1/height 
        y2 = y2/height
        dictionnaire = {'nom':nom_final,'lisible':lisible,'visible':visible,'score':round(max_valeur,2),'cadre':{"x1":x1,"x2":x2,"y1":y1,"y2":y2}}
        data.append(dictionnaire)
    print(len(data))
    return data 
       

if __name__=='__main__':
    # Tester
    print("Uploade une photo de test :")
    root = tkinter.Tk()
    root.withdraw()
    uploaded =filedialog.askopenfilename()
    root.destroy()
    print(uploaded)
    identifier(Path(uploaded))
