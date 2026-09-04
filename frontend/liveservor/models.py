from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User 
from django.utils.text import slugify

class Organization(models.Model):
    name = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='Organization/',null=True,blank=True)
    admin = models.ForeignKey(User,on_delete=models.CASCADE)
    info_sup = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class Profile(models.Model):
    user = models.ForeignKey(User,on_delete=models.CASCADE) 
    organization = models.OneToOneField(Organization,on_delete=models.CASCADE)
    photo = models.ImageField(upload_to='Profile/')
    bio = models.TextField(null=True,blank=True)
    info_sup = models.JSONField(default=list)


class Reconnus(models.Model):
    date = models.DateField(auto_now=True)
    source = models.CharField(max_length=25)
    profil = models.ForeignKey(Profile,on_delete=models.CASCADE)
    heure = models.TimeField(auto_now=True)
    info_sup = models.JSONField(default=list)
    def __str__(self):
        return f"{self.nom} à {self.date} {self.heure}"
    
    
class Source(models.Model):
    """Cette classe nous permettra de pouvoir enregistrer les sources souvent utilisé par leurs utilisateurs et de se connecter"""
    organization = models.ForeignKey(Organization,on_delete=models.CASCADE)
    url = models.CharField(max_length=39,blank=True,null=True)
    is_actif = models.BooleanField(default=False)
    is_webcam = models.BooleanField(default=False)
    info_sup = models.JSONField(default=list)
    def __str__(self):
        return self.url
    
class Embedding(models.Model):
    user = models.OneToOneField(User,on_delete=models.CASCADE)
    data = models.JSONField(default=list)
    def __str__(self):
        return self.user 

class ImageTraite(models.Model):
    source = models.ForeignKey(Source,null=True,blank=True,on_delete=models.CASCADE)
    frame_image = models.CharField(max_length=100)
    image = models.FileField(upload_to='traitement/')
    date = models.TimeField(auto_now=True)
    info_sup = models.JSONField(default=list)
    def __str__(self):
        return f"{self.name_frame} à {self.date}"