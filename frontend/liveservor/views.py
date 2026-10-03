from django.shortcuts import render,redirect
from django.http import HttpRequest,HttpResponse,JsonResponse,FileResponse
import io 
import numpy as np
import cv2,time,json ,os
from . import reconnaissance_par_embeddings as rpe
from pathlib import Path
from django.conf import settings 
import datetime 
from django.utils import timezone
from django.core.files.base import ContentFile 
from .models import ImageTraite,Reconnus,Profile,Source
from .creactionfichier import enregistrer_presence
from django.core.files.storage import default_storage
from .models import Embedding 
from . import ajouter_une_personne as aj 
from django.contrib.auth.models import User
import re,unicodedata 
# Create your views here.
BASE_DIR = Path(__file__).resolve().parent.parent

# chemin_base = BASE_DIR/'static/model/embeddings.json'


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


def extract_full_name(username):
    if not username or not username.strip():
        raise ValueError("Le nom d'utilisateur ne peut pas être vide.")

    return username.strip().replace("-", " ").title()


def accueil(request):
    return render(request,'accueil.html') 

def dashboard(request:HttpRequest)->HttpResponse: #Ici, nous allons recupérer la liste des noms des personnes dans la base de données et l'envoyer dans le template 
    """la vue des caméras """
    if request.method=='POST':
        source = request.POST.get('source')
        print(source)
        if source=='url':
            framename = request.POST.get('framename')
            url = request.POST.get('url')
            print(framename,url)
            return JsonResponse({'framename':framename,'url':url})
    #Nouvelle méthode pour obtenir les embeddings 
    base:dict = {
        f"{embedding.user.username}":embedding.data 
            for embedding in Embedding.objects.all()
    }
    # with open(chemin_base,'r') as f:
    #     base:dict = json.load(f)
    liste = list(base.keys())
        
    return render(request,'dashboard_1.html',{'liste':liste})

def reconnaissance_faciale_image(request:HttpRequest,name):
    """Pour pouvoir faire la reconnaissance pour l'image envoyé sous forme de requête post"""
    if request.method=='POST' :
        source = request.POST.get('source')
        if source =='image':
            # 1. Récupérer le fichier depuis la requête
            image_file = request.FILES.get('file')
            # 2. Lire les octets du fichier (le transformer en buffer)
            file_bytes = np.frombuffer(image_file.read(), np.uint8)
            # 3. Décoder le buffer pour obtenir une image OpenCV (format BGR)
            img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
            
            image_traite,liste_personnes = rpe.identifier_serveur_image(img) #Retourne l'image sous forme de numpy et la liste des personnes détectées
            #Le nom n'est juste que l'heure à laquelle la photo a été traité 
            nom = str(timezone.now())
            # file:///C:/projet_django/rf_projet/frontend/traitements/2026-05-22%2015:48:43.647347+00:00.jpg
            
            nom = name+'_'+nom.replace(' ','_').replace(':','_').replace('+','_').replace('-','_').replace('.','_')
            
            # On encode l'image en format JPEG en mémoire
            _, buffer = cv2.imencode('.jpg', image_traite)
            
            # 3. Création d'un objet ContentFile pour Django
            image_data = ContentFile(buffer.tobytes(),f'{nom}.jpg')
            
            image = ImageTraite.objects.create(frame_image=name,image=image_data) #Ici, on crée un image dans le dossier traitement pour l'historique
            
            #On accède au chemin de notre fichier 
            chemin = ImageTraite.objects.get(id=image.id).image.url
            
            #On retourne la réponse sous forme  de json avec le path de la photo à l'intérieur de ça 
            return JsonResponse({'name':name,'url':chemin,'source':source,'liste':liste_personnes})
        else:
            return JsonResponse({'name':name,'source':source})
    else:
        return JsonResponse({'name':name,'url':"static/img/flux_stop.png",'source':'image','liste':None})
    
def presence(request):
    if request.method=='GET':
        date = request.GET.get('date')
        if date =='all':
            liste = Reconnus.objects.all().order_by('-date','-heure').values('source', 'user__username', 'heure', 'date','user__profile__photo')
        else:
            liste = Reconnus.objects.filter(date=date).order_by('-heure').values('source','user__username','heure','date','user__profile__photo')
        liste = list(liste)
        for item in liste:
            if item['heure']:
                item['heure'] = item['heure'].strftime('%H:%M:%S')
    print(liste)
    return JsonResponse({'personnes':liste})


def telecharger(request):
    """Cette fonction va nous permettre de pouvoir télécharger la liste """
    if request.method=='GET':
        date = request.GET.get('date')
        if date=='all':
            liste = Reconnus.objects.all().order_by('-date','-heure').values('source', 'user__username', 'heure', 'date')
            
            liste = list(liste)
            nom_fichier = 'Toute_les_listes_de_presences'+'.xlsx'
        else:
            liste = Reconnus.objects.filter(date=date).order_by('-heure').values('source','user__username','heure','date')
            liste = list(liste)
            nom_fichier = date.replace(':','_')+'.xlsx'
        for item in liste:
            if item['heure']:
                item['heure'] = item['heure'].strftime('%H:%M:%S')
            if item['date']:
                item['date'] = item['date'].strftime("%d-%m-%Y")
        
        wb = enregistrer_presence(liste)
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        
        buffer.seek(0)
        
        return FileResponse(buffer,as_attachment=True,filename=nom_fichier)

def ajouter(request:HttpRequest)->HttpResponse:
    """La vue pour pouvoir ajouter de nouveaux visages """
    if request.method == 'POST':
        photos = request.FILES.getlist('photos')
        nom = (request.POST.get('nom') or '').strip()
        if not nom:
            return JsonResponse({'message':"Le nom est obligatoire",'status':400},status=400)
        if not photos:
            return JsonResponse({'message':"Ajoutez au moins une photo",'status':400},status=400)
        photo_de_profil = photos[0]
        for photo in photos:
            default_storage.save(f'photos/{nom}/{photo.name}',photo)

        # Le dossier est calculé avec le MÊME stockage que celui qui vient d'enregistrer les photos
        # (avant : 'media/photos/<nom>', relatif au dossier depuis lequel le serveur est lancé)
        chemin = Path(default_storage.path(f'photos/{nom}'))

        #Après avoir fait ça , nous allons faire de l'ajout dans la base de données 
        valeur = aj.ajouter(chemin)
        if valeur:
            #Je vais procéder à l'enregistrememt du nom de la personne dans ma base de données 
            username = extract_username(nom)

            user = User.objects.get_or_create(username=username)[0]
            photo_de_profil.seek(0) # le fichier a déjà été lu par default_storage.save
            # La photo est dans defaults : avant, elle était dans le lookup, donc ré-ajouter
            # une personne existante tentait de créer un 2e Profile (IntegrityError)
            Profile.objects.update_or_create(user=user,defaults={'photo':photo_de_profil})
            return JsonResponse({'message':f'{nom} Ajouté dans la base de données avec succès','status':200})

        else:
            return JsonResponse({'message':f"Aucun visage exploitable pour {nom} : utilisez des photos nettes avec un seul visage.",'status':400},status=400)
    return render(request,'ajouter.html')


def liste_source(request:HttpRequest)->JsonResponse:
    """Cette fonction nous permet d'enregistrer les sources de l'utilisateur et de s'y connecter quand il revient vers les sources"""
    liste = Source.objects.values_list('url',flat=True)
    # liste = Source.objects.all().values('url').exclude(id=None)
    liste = list(liste)
    print(liste)
    return JsonResponse({'liste':liste})