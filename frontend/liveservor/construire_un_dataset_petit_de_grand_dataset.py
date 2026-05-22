#C'est pouvoir construire un dataset d'image à partir d'un dataset plus grand pour l'extraction des embeddings pour notre model d'insightface 

import tkinter as tk 
from tkinter import filedialog 
from pathlib import Path 
import os 
import shutil
import random 

def choisir_dossier():
    """Ouvre une boîte de dialogue pour choisir un dossier et retourne son chemin."""
    root = tk.Tk()
    root.withdraw()  # Masquer la fenêtre principale
    dossier = filedialog.askdirectory(title="Choisissez le dossier contenant les images")
    root.destroy()
    return Path(dossier)

def choisir_destination_dataset():
    """Ouvre une boîte de dialogue pour choisir un dossier de destination et retourne son chemin."""
    root = tk.Tk()
    root.withdraw()  # Masquer la fenêtre principale
    dossier = filedialog.askdirectory(title="Choisissez le dossier de destination pour le dataset")
    root.destroy()
    return Path(dossier)

def main():
    path_dossier = choisir_dossier()
    path_destination = choisir_destination_dataset()
    
    if not path_dossier.exists() or not path_destination.exists():
        print("Erreur : les dossiers sélectionnés n'existent pas.")
        return
    
    for dossier in os.listdir(path_dossier):
        chemin_dossier = path_dossier / dossier
        if not chemin_dossier.is_dir():
            continue
        
        images = [f for f in os.listdir(chemin_dossier) if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
        if len(images) < 5:
            print(f"  {dossier} a moins de 5 images, ignoré.")
            continue
        
        images_choisies = random.sample(images, 5)
        
        destination_personne = path_destination / dossier
        destination_personne.mkdir(parents=True, exist_ok=True)
        
        for image in images_choisies:
            source = chemin_dossier / image
            destination = destination_personne / image
            shutil.copy(str(source), str(destination))
        print(dossier,' traité.')
        
main() ##Exécution du script 