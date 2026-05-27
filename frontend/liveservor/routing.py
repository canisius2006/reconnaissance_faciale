from django.urls import path 
from . import consumers 

websocket_urlpatterns = [
    path('ws/video/<str:framename>',consumers.VideoStreamConsumer.as_asgi()),
]