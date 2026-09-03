from django.db import models
from django.contrib.auth.models import User 
# Create your models here.




class Organization(models.Model):
    name = models.CharField(max_length=100)
    admin = models.ForeignKey(User,on_delete=models.CASCADE)


class Profile(models.Model):
    user = models.ForeignKey(User,on_delete=models.CASCADE) 
    organization = models.OneToOneField(Organization,on_delete=models.CASCADE)
    photo = models.ImageField(upload_to='Profile/')