from django.db import models
from django.utils import timezone
# Create your models here.
class ImageTraite(models.Model):
    name_frame = models.TextField(max_length=25)
    image = models.FileField(upload_to='traitement/')
    date = models.TimeField(auto_now=True)
    def __str__(self):
        return f"{self.name_frame} à {self.date}"