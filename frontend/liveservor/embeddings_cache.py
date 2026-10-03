"""
Cache partagé des embeddings de référence (un par personne enregistrée).

Avant :
  - chaque thread de reconnaissance relisait toute la table Embedding (2 requêtes par visage),
    avec deux requêtes qui n'avaient pas le même ordre (risque de noms décalés) ;
  - reconnaissance_par_embeddings.py figeait la liste à l'import (personne ajoutée = invisible
    avant redémarrage).
Maintenant :
  - UNE requête ordonnée donne les noms et la matrice d'embeddings, toujours alignés ;
  - rechargée toutes les TTL secondes (filet de sécurité : suppression en admin, autre processus...)
    et tout de suite après invalider() (appelée quand une personne est ajoutée).

Les tableaux renvoyés ne doivent PAS être modifiés par l'appelant (ils sont remplacés en bloc
au rechargement, jamais modifiés sur place : lecture sûre depuis plusieurs threads).
"""
import threading
import time
import traceback

import numpy as np
from django.db import connection

from .models import Embedding

TTL = 30.0   # secondes avant rechargement automatique
DIM = 512    # taille d'un embedding buffalo_l

_verrou = threading.Lock()
_etat = {
    'noms': np.array([], dtype=str),
    'matrice': np.zeros((0, DIM), dtype=np.float32),
    'charge_a': 0.0,
    'valide': False,
}


def invalider():
    """Force le rechargement au prochain appel de obtenir()."""
    with _verrou:
        _etat['valide'] = False


def _charger():
    lignes = Embedding.objects.order_by('id').values_list('user__username', 'data')
    noms, vecteurs = [], []
    for nom, data in lignes:
        if isinstance(data, list) and len(data) == DIM:
            noms.append(nom)
            vecteurs.append(data)
        else:
            print(f"[embeddings_cache] embedding ignoré pour {nom} (format invalide)")
    if vecteurs:
        return np.array(noms, dtype=str), np.array(vecteurs, dtype=np.float32)
    return np.array([], dtype=str), np.zeros((0, DIM), dtype=np.float32)


def obtenir(fermer_connexion=False):
    """Retourne (noms, matrice) : noms[i] correspond à matrice[i].
    Si matrice est vide (aucune personne enregistrée), matrice.shape[0] == 0.

    fermer_connexion=True : à passer depuis un thread créé à la main (pas une requête Django),
    pour fermer la connexion DB que ce thread vient d'ouvrir."""
    with _verrou:
        if not _etat['valide'] or time.time() - _etat['charge_a'] >= TTL:
            try:
                noms, matrice = _charger()
                _etat.update(noms=noms, matrice=matrice, charge_a=time.time(), valide=True)
            except Exception:
                traceback.print_exc()
                # On garde l'ancien contenu et on réessaie dans 5 s (sans marteler la base)
                _etat['charge_a'] = time.time() - TTL + 5
                _etat['valide'] = True
            finally:
                if fermer_connexion:
                    connection.close()
        return _etat['noms'], _etat['matrice']