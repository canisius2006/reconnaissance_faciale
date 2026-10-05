import socket

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    # Se connecte à une adresse externe (aucun paquet n'est réellement envoyé)
    s.connect(("8.8.8.8", 80))
    adresse_ip = s.getsockname()[0]
except Exception:
    adresse_ip = "127.0.0.1"
finally:
    s.close()

print(f"Adresse IP locale active : {adresse_ip}")
