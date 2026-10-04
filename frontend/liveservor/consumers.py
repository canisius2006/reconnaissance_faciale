"""
=============================================================
  FACE TRACKING OPTIMISÉ — YuNet + SORT (Kalman + Hongrois)
  Contrainte : CPU uniquement
  Auteur     : Canisius (adapté)
=============================================================

DÉPENDANCES :
    pip install opencv-contrib-python numpy scipy filterpy

MODÈLE REQUIS :
    Télécharger : face_detection_yunet_2023mar.onnx
    Source : https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet

NOTE : le traitement (détection, tracking, identification) est dans pipeline.py,
    la présence dans presence.py. Ce fichier ne contient plus que la partie WebSocket.

NOUVEAUTÉ :
    - Re-vérification automatique des visages "INCONNU" toutes les REINSPECT_DELAY secondes.
    - La structure de live_dictionnaire passe de [emb, nom, pct]
      à [emb, nom, pct, timestamp_derniere_tentative].
"""

# ─── IMPORTS ──────────────────────────────────────────────────────────────────
import asyncio
import base64
import copy
import json
import threading
import time
import traceback

import cv2
import numpy as np

from channels.generic.websocket import AsyncWebsocketConsumer
from .models import Source
from .pipeline import (
    Utilitaire, app_rec,
    live_embeddings, live_embeddings_lock, nettoyer_live_embeddings,
)
from .presence import marquer_present


def extract_full_name(username):
    if not username or not username.strip():
        raise ValueError("Le nom d'utilisateur ne peut pas être vide.")

    return username.strip().replace("-", " ").title()


# ── ANTI-LATENCE #1 : limiter le débit d'envoi ────────────────────────────────
# 15 fps suffit pour la reconnaissance — réduit CPU et taille de la file WebSocket
FPS_CIBLE        = 15
INTERVALLE_FRAME = 1.0 / FPS_CIBLE

# ── ANTI-LATENCE #2 : qualité JPEG réduite ────────────────────────────────────
# 60% = taille divisée par ~2, qualité visuelle acceptable pour surveillance
JPEG_QUALITE     = 60
ENCODE_PARAMS    = [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITE]


# =============================================================
# BOUCLE PRINCIPALE
# =============================================================

class VideoStreamConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.streaming      = False
        self.framename      = self.scope['url_route']['kwargs']['framename']
        print('avant')
        await self.accept()
        print('connexion acceptée')

    async def disconnect(self, code):
        print(code,"Il y a eu déconnexion au niveau de ",self.framename)
        self.streaming = False

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            if len(data)==0:
                return
            print(data, "C'est ce que le navigateur envoie")
        except json.JSONDecodeError as e:
            print(f'Erreur de parsing JSON : {e}')
            return

        message_type = data['type']

        if message_type == 'url':
            if data.get('message'):
                self.source    = data['message']
                if len(self.source)==1:
                    self.source = int(self.source)
                else:
                    await asyncio.sleep(2)
                    a,b = await Source.objects.aget_or_create(url=self.source)#Je vais enregistrer les liens des urls
                    print(b)
                self.streaming = True
                

                asyncio.create_task(self.live_serveur(self.source))
        
                
        
    
    async def live_serveur(self, source):
        """Cette fonction va gérer le mode live côté serveur"""
        self.util = Utilitaire() #Créaction de la classe de nos besoins 
        self.nombre_essai = 0 # ça représente le nombre d'éssaie qu'il faut faire pour arrêter l'appel vers le même lien 
       
        loop             = asyncio.get_event_loop()

        # ── ANTI-LATENCE #4 : timestamp de la dernière frame envoyée ──────────
        _derniere_frame  = 0.0
        _dernier_nettoyage = 0.0

        # Ouverture dans un thread : sur une URL réseau elle peut durer plusieurs secondes
        # et bloquait toute la boucle asyncio (donc toutes les caméras et tous les WebSocket).
        cap = await loop.run_in_executor(None, cv2.VideoCapture, source)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)
        self.ret = cap.isOpened()

        while self.streaming:
            base_personnes  = {} #Liste des personnes sera plutôt un json qui contiendra des personnes en key et ensuite la couleur associée

            try:   
                # ── ANTI-LATENCE #4 : contrôle du débit ───────────────────────────
                # On attend que l'intervalle soit écoulé avant de traiter une frame.
                # Ca libère la boucle asyncio entre chaque frame.
                now = time.monotonic()
                temps_restant = INTERVALLE_FRAME - (now - _derniere_frame)
                if temps_restant > 0:
                    await asyncio.sleep(temps_restant)
                _derniere_frame = time.monotonic()

                # Nettoyage de live_embeddings (au plus 1 fois par seconde)
                if _derniere_frame - _dernier_nettoyage >= 1.0:
                    nettoyer_live_embeddings()
                    _dernier_nettoyage = _derniere_frame

                # ── ANTI-LATENCE #3 : lire la frame la plus récente ───────────────
                # _lire_frame_recente() vide le buffer avant de décoder -> zéro retard
                self.ret, frame = await loop.run_in_executor(None, self.util._lire_frame_recente, cap)
                
                if not self.ret:
                    if not self.streaming:
                        break
                    if self.nombre_essai < 5:
                        self.nombre_essai += 1
                        await self.send(json.dumps({'type': 'stoperror'}))
                        print("données niveau stoperror envoyé ")
                        await asyncio.sleep(3)
                        # On libère l'ancienne capture avant d'en rouvrir une
                        await loop.run_in_executor(None, cap.release)
                        cap = await loop.run_in_executor(None, cv2.VideoCapture, self.source)
                        print("Je suis entrain de réessayer actuellement ")
                        continue
                    await self.send(json.dumps({'type': 'fin'}))
                    await self.close(4000, 'On a déjà essayé la reconnexion plusieurs fois')
                    break
                    
                frame = cv2.flip(frame, 1)
                # Détection, tracking et identification : tout est dans pipeline.traiter_frame(),
                # partagé avec l'agent LiveKit. Ici on ne fait que dessiner et enregistrer la présence.
                resultats = self.util.traiter_frame(frame, self.framename)

                for r in resultats:
                    x1, y1, x2, y2 = r['bbox']
                    color = r['color']
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                    try:
                        nom, pct = r['nom'], r['pct']
                        if r['etat'] == 'inconnu':
                            label = f" INCONNU  "
                        elif r['etat'] == 'analyse':
                            label = f" ..."
                        else:
                            label = f" {nom} {round(pct, 2)}%" # vraie similarité cosinus x 100
                            color_css = '#{:02x}{:02x}{:02x}'.format(int(color[2]), int(color[1]), int(color[0]))
                            base_personnes[nom] = [color_css]
                            # Présence du jour : une seule fois par personne et par jour (voir presence.py)
                            await marquer_present(nom, self.framename)

                        cv2.putText(frame, extract_full_name(label), (x1, y1 - 8),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                    except Exception as e:
                        print(e, "C'est l'erreur ça")
                        traceback.print_exc()

                cv2.putText(frame, f"nbre_visages : {len(resultats)}", (8, 35),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)

                # ── ANTI-LATENCE #2 : encodage JPEG qualité réduite ───────────────
                # JPEG_QUALITE=60 -> taille /2, latence réseau /2, qualité suffisante
                success, buffer = cv2.imencode('.jpg', frame, ENCODE_PARAMS)
                if not success:
                    continue

                frame_b64 = base64.b64encode(buffer).decode('utf-8')
                
                
                data = {'type': 'stream', 'message': frame_b64, 'liste': base_personnes}

                await self.send(json.dumps(data))
                
            except Exception as e:
                print(f"[live_serveur] erreur sur {self.framename} : {e}")
                traceback.print_exc()
                await asyncio.sleep(0.5)
        cap.release()
            
            
    async def close(self, code = None, reason = None):
        return await super().close(code, reason)
    
    
    
    
class TrackingConsumer(AsyncWebsocketConsumer):
    """Cette classe va nous permettre de gérer le mode tracking"""
    async def connect(self):
        self.tracking = False 
        self.actif = True # reste True tant que la connexion existe (sert à arrêter le thread de nettoyage)
        await self.accept()
        print('connexion accepté mode tracking')
        # Ici, on va commencer par faire la fonction remise à zéro en même temps 
        threading.Thread(target=self.remettreazero,daemon=True).start()
    async def disconnect(self, code):
        self.tracking = False 
        self.actif = False
        print('Déconnexion au niveau du mode tracking')
    
    async def receive(self, text_data=None,bytes_data = None ):
        
        if bytes_data is not None:
            self.tracking = True 
            np_buffer = np.frombuffer(bytes_data, dtype=np.uint8)

            # 2. Décoder ce buffer pour reconstruire l'image (Matrice H x L x Canaux)
            # cv2.IMREAD_COLOR force le chargement en image couleur (3 canaux : B, G, R)
            image_bgr = cv2.imdecode(np_buffer, cv2.IMREAD_COLOR)
            print('image reçu')
            if self.tracking:
                await self.tracking_par_image(image_bgr)
                print("Fin du tracking")
        if text_data is not None:
            try:
                data = json.loads(text_data)
                if len(data)==0:
                    return
                print(data, "C'est ce que le navigateur envoie")
            except json.JSONDecodeError as e:
                print(f'Erreur de parsing JSON : {e}')
                return
            message_type = data['type']
            if message_type =='stoptrack':
                self.tracking = False 
                await self.close(4001,'connexion fermé sous demande du navigateur')
        if text_data is None:
            return 
            
          
            
    async def get_embedding(self,img):
        """
        Calcule l'embedding d'une photo.
        Retourne un vecteur numpy de 512 valeurs, ou None si échec.
        """
        
        if img is None:
            return []

        # run_in_executor évite de bloquer la boucle asyncio pendant le calcul InsightFace (CPU-intensif)
        loop = asyncio.get_event_loop()
        visages = await loop.run_in_executor(None, app_rec.get, img)
        if len(visages) != 1:
            return None

        # embedding = vecteur de 512 valeurs calculé par ArcFace
        # normed_embedding = embedding normalisé (norme = 1)
        # La normalisation est nécessaire pour que la similarité cosinus
        # soit simplement un produit scalaire (plus rapide à calculer)
        return visages[0].normed_embedding  # shape : (512,) 

    async def faire_comparaison(self,emb_inconnu):
            """Cette fonction nous permet de faire la comparaison de emb"""
            another_list = {} # En fait ce n'est pas vraiment une liste, c'est un dictionnaire  , de plus, il faut qu'il soit toujours vide au départ
            with live_embeddings_lock:
                if live_embeddings: another_list = copy.deepcopy(live_embeddings)
            if len(another_list) ==0:return []
            
            SEUIL_COSINUS = 0.5
            liste_live_emb = [value[1] for value in another_list.values()]
            liste_live_framename = [value[0] for value in another_list.values()]
            liste_live_nom = list(another_list.keys())
            liste_live_emb = np.stack(liste_live_emb)
            liste_calcul_similarite = np.dot(liste_live_emb,emb_inconnu)
            max_valeur = np.max(liste_calcul_similarite)
            indice_max = np.argmax(liste_calcul_similarite)
            #Maintenant, on va avoir le nom correspondant à la valeur maximale qu'on a obtenue
            if max_valeur>SEUIL_COSINUS:
                nom_trouve = liste_live_nom[indice_max]
                if nom_trouve.isdigit():
                    nom_trouve = 'Tracké'
                framename = liste_live_framename[indice_max]
                return [str(nom_trouve),str(framename)]
            else:
                return []
    
    async def tracking_par_image(self,photo):
        """Cette photo nous permet de faire le mode tracking avec image"""
        print("Je suis entrée dans la fonction traking")
        
        if self.tracking:
            embd = await self.get_embedding(photo)

            if embd is not None and embd.size >0:
                while self.tracking:
                    result = await self.faire_comparaison(embd)
                    if result:
                        result = json.dumps({'resultat':result,'type':'tracking'})
                        await self.send(result)
                    else:
                        await self.send(json.dumps({}))
                    await asyncio.sleep(1.5)
            else:
                result = json.dumps({'resultat':{},'type':'tracking'})
                await self.send(result)
                print('aucun embeddings détecté')
        else:
            await self.send(json.dumps({}))
            await self.close(4001,'Connexion fermé')
            print('selftracking est false ici') 
            
    def remettreazero(self):
        """Nettoyage périodique de live_embeddings tant que la connexion tracking existe.
        Thread synchrone : il ne peut pas faire `await self.close()`, il s'arrête
        quand disconnect() met actif à False."""
        while self.actif:
            time.sleep(1.0)
            nettoyer_live_embeddings()