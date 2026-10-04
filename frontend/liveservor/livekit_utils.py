"""
Tokens LiveKit (côté serveur Django).

Django ne fait PAS transiter de vidéo : il signe seulement des jetons (JWT) qui disent
« ce participant peut rejoindre cette room avec ces droits ».

Deux rôles :
  - publisher : une caméra. Peut publier SA caméra, ne peut ni recevoir ni envoyer de données.
                Peut déclencher l'envoi de l'agent de reconnaissance dans la room (dispatch explicite).
  - viewer    : un spectateur (dashboard, /live/<cam>/). Peut recevoir, ne peut rien publier.

Réglages (settings.py ou variables d'environnement) :
  LIVEKIT_API_KEY, LIVEKIT_API_SECRET   clés du serveur LiveKit (en dev : devkey / secret)
  LIVEKIT_PUBLIC_URL                    URL que le NAVIGATEUR utilise (ex. ws://192.168.1.10:7880).
                                        Par défaut : ws(s)://<hôte de la page>:7880
  LIVEKIT_AGENT_NAME                    nom de l'agent à envoyer quand une caméra publie
                                        (vide = aucun agent, par défaut ; 'face-rec' à la phase 2)
  LIVEKIT_TOKEN_TTL                     durée de validité d'un token en secondes (3600 par défaut)
"""
import json
import os
import re
import unicodedata
import uuid
from datetime import timedelta

from django.conf import settings
from livekit import api


class LiveKitNonConfigure(Exception):
    """Les clés LiveKit sont absentes."""


def _reglage(nom, defaut=''):
    return getattr(settings, nom, None) or os.environ.get(nom, defaut)


def nom_room(cam):
    """Nom de room sûr à partir du nom de caméra saisi (accents retirés, tirets).
    Le résultat est renvoyé au navigateur : personne d'autre n'a besoin de recalculer ce nom."""
    texte = unicodedata.normalize("NFKD", str(cam).strip())
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    room = re.sub(r"[^a-zA-Z0-9]+", "-", texte).strip("-").lower()[:64]
    if not room:
        raise ValueError("Nom de caméra vide")
    return room


def url_publique(request):
    """URL du serveur LiveKit à donner au navigateur."""
    explicite = _reglage('LIVEKIT_PUBLIC_URL')
    if explicite:
        return explicite
    hote = request.get_host()
    hote = hote[:hote.index(']') + 1] if hote.startswith('[') else hote.split(':')[0]
    return f"{'wss' if request.is_secure() else 'ws'}://{hote}:7880"


def creer_token(role, cam):
    """Retourne {'token', 'room', 'identity'} pour le rôle demandé sur la caméra `cam`."""
    cle, secret = _reglage('LIVEKIT_API_KEY'), _reglage('LIVEKIT_API_SECRET')
    if not cle or not secret:
        raise LiveKitNonConfigure("LIVEKIT_API_KEY / LIVEKIT_API_SECRET manquants")
    room = nom_room(cam)

    if role == 'publisher':
        identity = f"cam-{room}"   # fixe : une 2e page qui publie la même caméra remplace la 1re
        grants = api.VideoGrants(
            room_join=True, room=room,
            can_publish=True, can_subscribe=False, can_publish_data=False,
            can_publish_sources=['camera'],
        )
    elif role == 'viewer':
        identity = f"viewer-{uuid.uuid4().hex[:8]}"
        grants = api.VideoGrants(
            room_join=True, room=room,
            can_publish=False, can_subscribe=True, can_publish_data=False,
        )
    else:
        raise ValueError(f"Rôle inconnu : {role}")

    token = (api.AccessToken(cle, secret)
             .with_identity(identity)
             .with_name(str(cam).strip()[:100])
             .with_grants(grants)
             .with_ttl(timedelta(seconds=int(_reglage('LIVEKIT_TOKEN_TTL', 3600)))))

    agent = _reglage('LIVEKIT_AGENT_NAME')
    if role == 'publisher' and agent:
        # Dispatch explicite : l'agent n'est envoyé que quand une caméra publie
        token = token.with_room_config(api.RoomConfiguration(
            agents=[api.RoomAgentDispatch(
                agent_name=agent,
                metadata=json.dumps({'cam': str(cam).strip(), 'room': room}),
            )]
        ))
    return {'token': token.to_jwt(), 'room': room, 'identity': identity}