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
from .insight import get_app_enrolement
from tkinter import filedialog
from pathlib import Path 
from .models import Embedding 
from django.contrib.auth.models import User
import re,unicodedata 
BASE_DIR = Path(__file__).resolve().parent.parent
chemin_base = BASE_DIR/'static/model/embeddings.json'


def extract_username(full_name):
    words = full_name.strip()

    if not words:
        raise ValueError("Le nom complet ne peut pas être vide.")
    text = unicodedata.normalize("NFKD", words)
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )

    return re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()


# buffalo_l = grand modèle, plus précis que buffalo_sc
# À utiliser ici car on fait ça une seule fois (pas en temps réel)
# Le modèle (det 640x640) n'est plus chargé à l'import mais au premier ajout d'une personne

# ─────────────────────────────────────────────────────────────
def get_embedding(chemin_photo):
    """
    Calcule l'embedding d'une photo.
    Retourne un vecteur numpy de 512 valeurs, ou None si échec.
    """
    img = cv2.imread(chemin_photo)
    if img is None:
        return None

    visages = get_app_enrolement().get(img)
    if len(visages) != 1:
        return None

    # embedding = vecteur de 512 valeurs calculé par ArcFace
    # normed_embedding = embedding normalisé (norme = 1)
    # La normalisation est nécessaire pour que la similarité cosinus
    # soit simplement un produit scalaire (plus rapide à calculer)
    return visages[0].normed_embedding  # shape : (512,)

#---------------------Extraction du json à partir de la base de données------------
try:
    base_json = {
        f"{embedding.user.username}":embedding.data 
        for embedding in Embedding.objects.all()
    }
except:
    base_json = {}

#Charger le modèle d'abord (ancienne méthode)
# with open(chemin_base,'r') as f:
#     base_json = json.load(f)

# ─────────────────────────────────────────────────────────────
def construire_base(dossier_propre:Path):
    """
    Parcourt le dataset propre et calcule un embedding
    moyen par personne.

    Retourne un dictionnaire :
    {'Jean_Dupont': array(512,), 'Marie_Kokou': array(512,), ...}
    """
    embeddings = []
    for personne in sorted(os.listdir(dossier_propre)):
        print(personne)
        if not personne.lower().endswith(('.jpg','.jpeg','.png')):
            continue
        emb = get_embedding(os.path.join(dossier_propre, personne))
        if emb is not None:
            embeddings.append(emb)

    if len(embeddings) == 0:
        print(f"  Aucun embedding pour {personne} — vérifier les photos")
        return None
    # Calculer la moyenne de tous les embeddings de cette personne
    # np.mean(..., axis=0) fait la moyenne valeur par valeur
    # puis on renormalise pour que la norme reste = 1
    emb_moyen = np.mean(embeddings, axis=0)
    emb_moyen = emb_moyen / np.linalg.norm(emb_moyen)  # renormaliser

    print(f" {personne:25s} → {len(embeddings)} embeddings calculés")
    return dossier_propre.name, emb_moyen


def enregister_dans_la_base(base_json: dict):
    """Enregistre les données dans la base de données."""

    for key, data in base_json.items():
        username = extract_username(key)

        user, _ = User.objects.get_or_create(
            username=username
        )

        Embedding.objects.update_or_create(
            user=user,
            defaults={
                "data": data
            }
        )


#Charger et mettre à jour la base de données 

def ajouter(chemin):
    """Cette fonction est en quelque sorte le main qui va nous permettre d'ajouter le chemin du fichier """

    resultat = construire_base(chemin)
    if resultat is None:
        print('Personne non enregistrée')
        return False
    base_json[resultat[0]] = resultat[1].tolist() #De array en liste

    # Sauvegarder : 
    enregister_dans_la_base(base_json)

    print(f"\n Base de {len(base_json)} personnes sauvegardée")
    return True