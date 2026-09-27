from django.contrib import admin
from .models import ImageTraite,Reconnus,Profile,Source,Embedding,Organization
# Register your models here.

@admin.register(ImageTraite)
class ImageTraiteAdmin(admin.ModelAdmin):
    list_display = ['source','frame_image','image','date']

@admin.register(Reconnus)
class ReconnusAdmin(admin.ModelAdmin):
    list_display = ['date','source','profil','heure','info_sup']

@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = ['organization','url','is_actif','is_webcam','info_sup']

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['user','organization','photo','bio','info_sup']

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ['name','logo','admin','info_sup','created_at','updated_at']
    
admin.site.register(Embedding)
