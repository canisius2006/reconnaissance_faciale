from django.urls import path 
from . import views 
urlpatterns = [
    path('',views.accueil,name='accueil'),
    path('dashboard/',views.dashboard,name='dashboard'),
    path('dashboard/<str:name>',views.reconnaissance_faciale_image,name='imagereconnaissance'),
    path('dashboard/presence/',views.presence,name='presence'),
    path('dashboard/telecharger/',views.telecharger,name='telecharger'),
    path('dashboard/listesource/',views.liste_source,name='listesource'),
    path('ajouter/',views.ajouter,name='ajouter')
]
