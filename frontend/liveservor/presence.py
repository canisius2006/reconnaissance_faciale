"""
Enregistrement de la présence du jour (table Reconnus).

Utilisé par le VideoStreamConsumer et, plus tard, par l'agent LiveKit.

Avant (dans consumers.py) :
  - chaque consumer gardait un set "déjà vu" valable pour toute sa durée de vie : un traitement qui
    tourne sur plusieurs jours n'aurait enregistré la présence que le premier jour ;
  - test d'existence puis création en deux temps : deux caméras pouvaient créer un doublon ;
  - la date testée (UTC, timezone.now().date()) n'était pas celle que le champ enregistre
    (DateField(auto_now=True) -> datetime.date.today(), date locale).
Maintenant :
  - mémoire "déjà vu" PAR JOUR et par personne, partagée par tout le processus ;
  - get_or_create sous verrou ;
  - la date de recherche est exactement celle que le champ enregistre.
Il n'y a toujours pas de contrainte d'unicité en base : elle protège contre deux PROCESSUS qui
écriraient en même temps (Django + agent) ; elle demanderait une migration et le nettoyage des
doublons existants.
"""
import datetime
import threading
import time

from asgiref.sync import sync_to_async
from django.contrib.auth.models import User

from .models import Reconnus

_verrou = threading.Lock()
_vus = {}        # nom -> date du jour pour laquelle la présence est déjà enregistrée
_absents = {}    # nom -> instant (time.monotonic) jusqu'auquel on ne réessaie pas (aucun User de ce nom)
DELAI_ABSENT = 30


def _inutile(nom):
    """True si on sait déjà qu'il n'y a rien à faire pour ce nom (pas de requête, pas de thread)."""
    with _verrou:
        return (_vus.get(nom) == datetime.date.today()
                or _absents.get(nom, 0) > time.monotonic())


def marquer_present_sync(nom, source):
    """Enregistre la présence du jour de `nom`, vue pour la première fois par la caméra `source`.
    Retourne True si une ligne Reconnus a été créée. Appel bloquant (ORM)."""
    aujourdhui = datetime.date.today()   # même fonction que DateField(auto_now=True)
    with _verrou:
        if _vus.get(nom) == aujourdhui or _absents.get(nom, 0) > time.monotonic():
            return False
        try:
            user = User.objects.get(username=nom)
        except User.DoesNotExist:
            _absents[nom] = time.monotonic() + DELAI_ABSENT
            return False
        _, cree = Reconnus.objects.get_or_create(
            user=user, date=aujourdhui, defaults={'source': source})
        _vus[nom] = aujourdhui
        return cree


async def marquer_present(nom, source):
    """Version asynchrone : ne quitte la boucle asyncio que si la présence reste à enregistrer."""
    if _inutile(nom):
        return False
    return await sync_to_async(marquer_present_sync)(nom, source)