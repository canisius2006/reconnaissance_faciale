from django.urls import path 
from . import views 
urlpatterns = [
    path('',views.accueil,name='accueil'),
    path('dashboard/',views.dashboard,name='dashboard'),
    path('dashboard/<str:name>',views.reconnaissance_faciale_image,name='imagereconnaissance')
]
