from django.db import models
from django.utils import timezone
# Create your models here.

class ImageTraite(models.Model):
    name_frame = models.CharField(max_length=25)
    image = models.FileField(upload_to='traitement/')
    date = models.TimeField(auto_now=True)
    def __str__(self):
        return f"{self.name_frame} à {self.date}"



class Reconnus(models.Model):
    date = models.DateField(auto_now=True)
    source = models.CharField(max_length=25)
    nom = models.CharField(max_length=25)
    heure = models.TimeField(auto_now=True)
    def __str__(self):
        return f"{self.nom} à {self.date} {self.heure}"
    

class Profile(models.Model):
    nom = models.CharField(max_length=25)
    photo = models.ImageField(upload_to='image/')
    def __str__(self):
        return self.nom 
    
class Source(models.Model):
    """Cette classe nous permettra de pouvoir enregistrer les sources souvent utilisé par leurs utilisateurs et de se connecter"""
    url = models.CharField(max_length=39)
    def __str__(self):
        return self.url