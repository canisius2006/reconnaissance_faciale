# ─────────────────────────────────────────────────────────────
# CONSTRUCTION DE LA BASE D'EMBEDDINGS
#
# Pour chaque personne du dataset_propre :
# 1. Lire toutes ses photos
# 2. Calculer l'embedding de chaque photo
# 3. Faire la moyenne → un seul vecteur représente cette personne
# 4. Sauvegarder
#
# Pas d'entraînement. InsightFace buffalo_l est déjà entraîné
# sur des millions de visages de célébrités.
# ─────────────────────────────────────────────────────────────

import numpy as np
import cv2, os, json
from insightface.app import FaceAnalysis
import tkinter as tk 
from tkinter import filedialog

# buffalo_l = grand modèle, plus précis que buffalo_sc
# À utiliser ici car on fait ça une seule fois (pas en temps réel)
app = FaceAnalysis(
    name='buffalo_l',
    providers=['CPUExecutionProvider']
)
app.prepare(ctx_id=-1)

# ─────────────────────────────────────────────────────────────
def get_embedding(chemin_photo):
    """
    Calcule l'embedding d'une photo.
    Retourne un vecteur numpy de 512 valeurs, ou None si échec.
    """
    img = cv2.imread(chemin_photo)
    if img is None:
        return None

    visages = app.get(img)
    if len(visages) != 1:
        return None

    # embedding = vecteur de 512 valeurs calculé par ArcFace
    # normed_embedding = embedding normalisé (norme = 1)
    # La normalisation est nécessaire pour que la similarité cosinus
    # soit simplement un produit scalaire (plus rapide à calculer)
    return visages[0].normed_embedding  # shape : (512,)

# ─────────────────────────────────────────────────────────────
def construire_base(dossier_propre):
    """
    Parcourt le dataset propre et calcule un embedding
    moyen par personne.

    Retourne un dictionnaire :
    {'Jean_Dupont': array(512,), 'Marie_Kokou': array(512,), ...}
    """
    base = {}

    for personne in sorted(os.listdir(dossier_propre)):
        dossier = os.path.join(dossier_propre, personne)
        if not os.path.isdir(dossier):
            continue

        embeddings = []

        for fichier in os.listdir(dossier):
            if not fichier.lower().endswith(('.jpg', '.jpeg', '.png')):
                continue
            emb = get_embedding(os.path.join(dossier, fichier))
            if emb is not None:
                embeddings.append(emb)

        if len(embeddings) == 0:
            print(f"  Aucun embedding pour {personne} — vérifier les photos")
            continue

        # Calculer la moyenne de tous les embeddings de cette personne
        # np.mean(..., axis=0) fait la moyenne valeur par valeur
        # puis on renormalise pour que la norme reste = 1
        emb_moyen = np.mean(embeddings, axis=0)
        emb_moyen = emb_moyen / np.linalg.norm(emb_moyen)  # renormaliser

        base[personne] = emb_moyen
        print(f" {personne:25s} → {len(embeddings)} embeddings calculés")

    return base

# Construire et sauvegarder la base
chemin = filedialog.askdirectory(title="Choisissez le dossier contenant les images pour construire la base d'embeddings")
BASE_EMBEDDINGS = construire_base(chemin)



# Sauvegarder : convertir les arrays numpy en listes Python pour JSON
base_json = {nom: emb.tolist() for nom, emb in BASE_EMBEDDINGS.items()}
with open(r'ownmodel\embeddings\embeddings.json', 'w') as f:
    json.dump(base_json, f)

print(f"\n Base de {len(BASE_EMBEDDINGS)} personnes sauvegardée")