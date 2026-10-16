from django.urls import path 
from . import views 
from .views import (
    OrganizationListView,
    OrganizationCreateView, 
    OrganizationUpdateView, 
    OrganizationDeleteView,
)
urlpatterns = [
    path('',views.accueil,name='accueil'),
    path('dashboard/',views.dashboard,name='dashboard'),
    path('dashboard/<str:name>',views.reconnaissance_faciale_image,name='imagereconnaissance'),
    path('dashboard/presence/',views.presence,name='presence'),
    path('dashboard/telecharger/',views.telecharger,name='telecharger'),
    path('dashboard/listesource/',views.liste_source,name='listesource'),
    path('ajouter/',views.ajouter,name='ajouter'),
    path('list/', OrganizationListView.as_view(), name='organization_list'),
    path('create/', OrganizationCreateView.as_view(), name='organization_create'),
    path('<int:pk>/edit/', OrganizationUpdateView.as_view(), name='organization_edit'),
    path('<int:pk>/delete/', OrganizationDeleteView.as_view(), name='organization_delete'),
]
