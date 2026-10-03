"""
Chargement partagé des modèles InsightFace (buffalo_l, CPU).

Avant : 3 copies du modèle en mémoire (consumers, reconnaissance_par_embeddings,
ajouter_une_personne), chacune chargée à l'import.
Maintenant :
  - get_app_rec()         : détection + reconnaissance en 320x320 (temps réel).
                            UNE seule instance, partagée par consumers.py et
                            reconnaissance_par_embeddings.py.
  - get_app_enrolement()  : détection 640x640 pour l'ajout d'une personne.
                            Chargée seulement au premier ajout (paresseux).
"""
import threading

from insightface.app import FaceAnalysis

_verrou = threading.Lock()
_instances = {}


def _obtenir(cle, det_size):
    with _verrou:
        if cle not in _instances:
            app = FaceAnalysis(
                name='buffalo_l',
                providers=['CPUExecutionProvider'],
                allowed_modules=['detection', 'recognition'],
            )
            app.prepare(ctx_id=-1, det_size=det_size)
            _instances[cle] = app
        return _instances[cle]


def get_app_rec():
    """Instance temps réel (det_size 320x320), partagée."""
    return _obtenir('rec', (320, 320))


def get_app_enrolement():
    """Instance pour l'ajout de visages (det_size 640x640), chargée à la demande."""
    return _obtenir('enrolement', (640, 640))