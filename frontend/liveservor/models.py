from django.db import models
from django.utils import timezone
# Create your models here.

class ImageTraite(models.Model):
    name_frame = models.TextField(max_length=25)
    image = models.FileField(upload_to='traitement/')
    date = models.TimeField(auto_now=True)
    def __str__(self):
        return f"{self.name_frame} à {self.date}"



class Reconnus(models.Model):
    date = models.DateField(auto_now=True)
    source = models.TextField(max_length=25)
    nom = models.TextField(max_length=25)
    heure = models.TimeField(auto_now=True)
    def __str__(self):
        return f"{self.nom} à {self.date} {self.heure}"
    

class Profile(models.Model):
    nom = models.TextField(max_length=25)
    photo = models.ImageField(upload_to='image/')
    def __str__(self):
        return self.nom 
    
class ListePresence(models.Model):
    nom = models.TextField(max_length=25,default=None)
    date = models.DateField()
    file = models.FileField(upload_to='Fichier/')
    def __str__(self):
        return self.date