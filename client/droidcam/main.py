from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel

import cv2
import secrets
import uvicorn
import webbrowser
import threading
import socket
import uuid

from datetime import datetime


app = FastAPI()


# ============================================================
# CONFIGURATION
# ============================================================

HOST = "0.0.0.0"
PORT = 8001

TOKEN = secrets.token_urlsafe(16)


# ============================================================
# MODELES
# ============================================================

class CameraConfig(BaseModel):
    source: str = "0"
    width: int = 640
    height: int = 480


class IPRequest(BaseModel):
    ip: str


# ============================================================
# GESTION DES CLIENTS
# ============================================================

class GestionClients:

    def __init__(self):

        # Clients actuellement connectés
        #
        # {
        #     client_id: {
        #         id,
        #         ip,
        #         hostname,
        #         connected_at,
        #         connections,
        #         blocked
        #     }
        # }
        self.connectes = {}

        # Historique des clients déconnectés
        self.deconnectes = []

        # Nombre total de connexions par IP
        #
        # {
        #     "192.168.1.20": 4
        # }
        self.compteurs = {}

        # IP bloquées
        self.blacklist = set()

    # --------------------------------------------------------
    # HOSTNAME
    # --------------------------------------------------------

    def resoudre_hostname(self, ip):

        try:
            return socket.gethostbyaddr(ip)[0]

        except Exception:
            return "Inconnu"

    # --------------------------------------------------------
    # CONNEXION
    # --------------------------------------------------------

    def connecter(self, ip):

        # Une IP blacklistée ne peut pas se connecter
        if self.est_blacklist(ip):
            return None

        client_id = str(uuid.uuid4())

        hostname = self.resoudre_hostname(ip)

        maintenant = datetime.now().isoformat(
            timespec="seconds"
        )

        # Incrémenter le compteur de cette IP
        self.compteurs[ip] = (
            self.compteurs.get(ip, 0) + 1
        )

        client = {
            "id": client_id,
            "ip": ip,
            "hostname": hostname,
            "connected_at": maintenant,
            "connections": self.compteurs[ip],
            "blocked": False,
        }

        self.connectes[client_id] = client

        print(
            f"[+] Connexion | "
            f"IP={ip} | "
            f"Hostname={hostname} | "
            f"Connexion n°{self.compteurs[ip]} | "
            f"Clients actifs={len(self.connectes)}"
        )

        return client_id

    # --------------------------------------------------------
    # DECONNEXION
    # --------------------------------------------------------

    def deconnecter(self, client_id):

        client = self.connectes.pop(
            client_id,
            None
        )

        if client is None:
            return

        client["disconnected_at"] = (
            datetime.now().isoformat(
                timespec="seconds"
            )
        )

        self.deconnectes.append(client)

        # Garder seulement les 100 dernières connexions
        if len(self.deconnectes) > 100:
            self.deconnectes.pop(0)

        print(
            f"[-] Déconnexion | "
            f"IP={client['ip']} | "
            f"Hostname={client['hostname']} | "
            f"Clients actifs={len(self.connectes)}"
        )

    # --------------------------------------------------------
    # BLACKLIST
    # --------------------------------------------------------

    def est_blacklist(self, ip):

        return ip in self.blacklist

    # --------------------------------------------------------
    # EXPULSER + BLOQUER
    # --------------------------------------------------------

    def bloquer(self, ip):

        # Ajouter l'IP à la blacklist
        self.blacklist.add(ip)

        # Marquer les connexions existantes
        # comme bloquées.
        #
        # Le générateur du flux détectera ensuite
        # cette information et coupera la connexion.
        for client in self.connectes.values():

            if client["ip"] == ip:
                client["blocked"] = True

        print(
            f"[!] IP bloquée : {ip}"
        )

    # --------------------------------------------------------
    # DEBLOQUER
    # --------------------------------------------------------

    def debloquer(self, ip):

        self.blacklist.discard(ip)

        print(
            f"[+] IP débloquée : {ip}"
        )

    # --------------------------------------------------------
    # CLIENTS ACTIFS
    # --------------------------------------------------------

    @property
    def nombre_clients(self):

        return len(self.connectes)

    def clients_actuels(self):

        return list(
            self.connectes.values()
        )

    # --------------------------------------------------------
    # CLIENTS DECONNECTES
    # --------------------------------------------------------

    def clients_deconnectes(self):

        return list(
            reversed(self.deconnectes)
        )

    # --------------------------------------------------------
    # STATISTIQUES
    # --------------------------------------------------------

    def statistiques(self):

        return {
            "clients_actuels":
                self.nombre_clients,

            "connexions_totales":
                sum(self.compteurs.values()),

            "ips_connues":
                len(self.compteurs),

            "blacklist":
                list(self.blacklist),
        }


# ============================================================
# GESTION DE LA CAMERA
# ============================================================

class GestionFlux:

    def __init__(self):

        self.camera_is_open = False

        self.source = 0

        self.camera = None

        self.erreur = ""

    # --------------------------------------------------------
    # RESOLUTION DE LA SOURCE
    # --------------------------------------------------------

    def resoudre_source(self, source):

        source = source.strip()

        if source.isdigit():

            return int(source)

        return source

    # --------------------------------------------------------
    # DEFINIR LA SOURCE
    # --------------------------------------------------------

    def set_source(self, valeur):

        self.source = self.resoudre_source(
            valeur
        )

    # --------------------------------------------------------
    # OUVRIR CAMERA
    # --------------------------------------------------------

    def ouvrir_camera(self, config):

        # Fermer l'ancienne caméra
        self.fermer_camera(
            afficher=False
        )

        # Définir la nouvelle source
        self.set_source(
            config.source
        )

        print(
            f"[*] Ouverture de la source : "
            f"{self.source}"
        )

        self.camera = cv2.VideoCapture(
            self.source
        )

        # Vérifier l'ouverture
        if not self.camera.isOpened():

            self.camera = None

            self.camera_is_open = False

            self.erreur = (
                f"Impossible d'ouvrir la source : "
                f"{self.source}"
            )

            print(
                f"[ERREUR] {self.erreur}"
            )

            return False

        # Résolution
        self.camera.set(
            cv2.CAP_PROP_FRAME_WIDTH,
            config.width
        )

        self.camera.set(
            cv2.CAP_PROP_FRAME_HEIGHT,
            config.height
        )

        self.camera_is_open = True

        self.erreur = ""

        print(
            f"[+] Caméra démarrée | "
            f"Source={self.source} | "
            f"Résolution="
            f"{config.width}x{config.height}"
        )

        return True

    # --------------------------------------------------------
    # FERMER CAMERA
    # --------------------------------------------------------

    def fermer_camera(self, afficher=True):

        if self.camera is not None:

            self.camera.release()

            self.camera = None

        self.camera_is_open = False

        if afficher:

            print(
                "[*] Caméra arrêtée"
            )

    # --------------------------------------------------------
    # GENERATION DU FLUX
    # --------------------------------------------------------

    def generate_frames(
        self,
        client_id,
        client_ip,
        gestion_clients
    ):

        try:

            while self.camera_is_open:

                # ------------------------------------------------
                # Vérifier si le client a été blacklisté
                # ------------------------------------------------

                if gestion_clients.est_blacklist(
                    client_ip
                ):

                    print(
                        f"[!] Expulsion immédiate | "
                        f"IP={client_ip}"
                    )

                    break

                # ------------------------------------------------
                # Vérifier la caméra
                # ------------------------------------------------

                if self.camera is None:

                    break

                # ------------------------------------------------
                # Lire une image
                # ------------------------------------------------

                success, frame = (
                    self.camera.read()
                )

                if not success:

                    self.erreur = (
                        "Impossible de lire "
                        "la caméra."
                    )

                    break

                # ------------------------------------------------
                # Encoder en JPEG
                # ------------------------------------------------

                success, buffer = (
                    cv2.imencode(
                        ".jpg",
                        frame
                    )
                )

                if not success:

                    continue

                # ------------------------------------------------
                # Format MJPEG
                # ------------------------------------------------

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + buffer.tobytes()
                    + b"\r\n"
                )

        finally:

            # Lorsque le navigateur ferme la connexion,
            # le client est retiré des clients actifs.
            gestion_clients.deconnecter(
                client_id
            )


# ============================================================
# INSTANCES
# ============================================================

core = GestionFlux()

clients = GestionClients()


# ============================================================
# IP LOCALE
# ============================================================

def obtenir_ip_locale():

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM
    )

    try:

        # Aucun paquet n'est réellement envoyé.
        # Cela permet simplement de déterminer
        # l'interface réseau utilisée.
        sock.connect(
            ("8.8.8.8", 80)
        )

        return sock.getsockname()[0]

    except Exception:

        return "127.0.0.1"

    finally:

        sock.close()


# ============================================================
# PAGE ADMIN
# ============================================================

@app.get(
    "/",
    response_class=HTMLResponse
)
def admin_page():

    ip_locale = obtenir_ip_locale()

    return f"""
<!DOCTYPE html>

<html lang="fr">

<head>

    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>MyDroidCam - Administration</title>

    <style>

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            padding: 30px;

            font-family:
                Arial,
                Helvetica,
                sans-serif;

            background: #f4f4f4;

            color: #222;
        }}

        .container {{
            max-width: 1100px;
            margin: auto;
        }}

        h1 {{
            margin-bottom: 5px;
        }}

        .card {{
            background: white;

            padding: 20px;

            margin-bottom: 20px;

            border-radius: 12px;

            box-shadow:
                0 3px 10px
                rgba(0,0,0,0.08);
        }}

        input {{
            width: 100%;

            padding: 10px;

            margin-top: 5px;
            margin-bottom: 15px;

            border: 1px solid #ccc;

            border-radius: 6px;
        }}

        button {{
            border: none;

            padding: 10px 15px;

            border-radius: 6px;

            cursor: pointer;

            margin-right: 5px;
        }}

        .start {{
            background: #198754;
            color: white;
        }}

        .stop {{
            background: #dc3545;
            color: white;
        }}

        .block {{
            background: #b02a37;
            color: white;
        }}

        .unblock {{
            background: #198754;
            color: white;
        }}

        table {{
            width: 100%;

            border-collapse: collapse;

            margin-top: 15px;
        }}

        th,
        td {{
            padding: 10px;

            border-bottom:
                1px solid #ddd;

            text-align: left;
        }}

        th {{
            background: #f0f0f0;
        }}

        .status {{
            font-weight: bold;
        }}

        .online {{
            color: #198754;
        }}

        .offline {{
            color: #dc3545;
        }}

        code {{
            background: #eee;

            padding: 4px 7px;

            border-radius: 5px;

            word-break: break-all;
        }}

        .danger {{
            color: #dc3545;
        }}

        .success {{
            color: #198754;
        }}

    </style>

</head>


<body>

<div class="container">

    <div class="card">

        <h1>MyDroidCam</h1>

        <p>
            Serveur caméra local
        </p>

        <p>
            IP locale :
            <code>{ip_locale}</code>
        </p>

        <p>
            Serveur :
            <code>
                http://{ip_locale}:{PORT}
            </code>
        </p>

    </div>


    <!-- ================================================= -->
    <!-- CONFIGURATION CAMERA -->
    <!-- ================================================= -->

    <div class="card">

        <h2>Configuration caméra</h2>

        <label>
            Source
        </label>

        <input
            id="source"
            value="0"
            placeholder="0 ou URL caméra"
        >


        <label>
            Largeur
        </label>

        <input
            id="width"
            type="number"
            value="640"
        >


        <label>
            Hauteur
        </label>

        <input
            id="height"
            type="number"
            value="480"
        >


        <button
            class="start"
            onclick="startCamera()"
        >
            Démarrer
        </button>


        <button
            class="stop"
            onclick="stopCamera()"
        >
            Arrêter
        </button>


        <p>

            État :
            <span
                id="camera-status"
                class="status"
            >
                Chargement...
            </span>

        </p>

    </div>


    <!-- ================================================= -->
    <!-- ACCES CLIENT -->
    <!-- ================================================= -->

    <div class="card">

        <h2>Accès client</h2>

        <p>
            Token :
            <code>{TOKEN}</code>
        </p>

        <p>
            URL :
            <code>
                http://{ip_locale}:{PORT}/camera/{TOKEN}
            </code>
        </p>

    </div>


    <!-- ================================================= -->
    <!-- STATISTIQUES -->
    <!-- ================================================= -->

    <div class="card">

        <h2>Statistiques</h2>

        <p>
            Clients actifs :
            <strong id="active-count">
                0
            </strong>
        </p>

        <p>
            Connexions totales :
            <strong id="total-count">
                0
            </strong>
        </p>

        <p>
            IP connues :
            <strong id="ip-count">
                0
            </strong>
        </p>

    </div>


    <!-- ================================================= -->
    <!-- CLIENTS ACTIFS -->
    <!-- ================================================= -->

    <div class="card">

        <h2>
            Clients actuellement connectés
        </h2>

        <div id="clients-container">

            Aucun client connecté.

        </div>

    </div>


    <!-- ================================================= -->
    <!-- HISTORIQUE -->
    <!-- ================================================= -->

    <div class="card">

        <h2>
            Clients déconnectés
        </h2>

        <div id="history-container">

            Aucun historique.

        </div>

    </div>


    <!-- ================================================= -->
    <!-- BLACKLIST -->
    <!-- ================================================= -->

    <div class="card">

        <h2>
            Liste noire
        </h2>

        <div id="blacklist-container">

            Aucune IP bloquée.

        </div>

    </div>

</div>


<script>


// ========================================================
// START CAMERA
// ========================================================

async function startCamera() {{

    const source =
        document.getElementById(
            "source"
        ).value;


    const width =
        parseInt(
            document.getElementById(
                "width"
            ).value
        );


    const height =
        parseInt(
            document.getElementById(
                "height"
            ).value
        );


    const response =
        await fetch(
            "/admin/start",
            {{

                method: "POST",

                headers: {{
                    "Content-Type":
                        "application/json"
                }},

                body: JSON.stringify({{

                    source: source,

                    width: width,

                    height: height

                }})

            }}
        );


    const data =
        await response.json();


    if (!response.ok) {{

        alert(
            data.detail ||
            "Impossible de démarrer la caméra."
        );

        return;
    }}


    chargerStatus();

}}


// ========================================================
// STOP CAMERA
// ========================================================

async function stopCamera() {{

    const response =
        await fetch(
            "/admin/stop",
            {{

                method: "POST"

            }}
        );


    const data =
        await response.json();


    if (!response.ok) {{

        alert(
            data.detail ||
            "Impossible d'arrêter la caméra."
        );

        return;
    }}


    chargerStatus();

}}


// ========================================================
// EXPULSER + BLOQUER
// ========================================================

async function expulserEtBloquer(ip) {{

    const confirmation =
        confirm(
            "Voulez-vous vraiment expulser et bloquer " +
            ip +
            " ?"
        );


    if (!confirmation) {{

        return;

    }}


    const response =
        await fetch(
            "/admin/block",
            {{

                method: "POST",

                headers: {{

                    "Content-Type":
                        "application/json"

                }},

                body: JSON.stringify({{

                    ip: ip

                }})

            }}
        );


    const data =
        await response.json();


    if (!response.ok) {{

        alert(
            data.detail ||
            "Erreur."
        );

        return;
    }}


    chargerStatus();

}}


// ========================================================
// DEBLOQUER
// ========================================================

async function debloquer(ip) {{

    const response =
        await fetch(
            "/admin/unblock",
            {{

                method: "POST",

                headers: {{

                    "Content-Type":
                        "application/json"

                }},

                body: JSON.stringify({{

                    ip: ip

                }})

            }}
        );


    const data =
        await response.json();


    if (!response.ok) {{

        alert(
            data.detail ||
            "Erreur."
        );

        return;
    }}


    chargerStatus();

}}


// ========================================================
// CHARGER STATUS
// ========================================================

async function chargerStatus() {{

    try {{

        const response =
            await fetch(
                "/status"
            );


        const data =
            await response.json();


        // -----------------------------------------------
        // CAMERA
        // -----------------------------------------------

        const cameraStatus =
            document.getElementById(
                "camera-status"
            );


        if (data.camera_active) {{

            cameraStatus.textContent =
                "ACTIVE";

            cameraStatus.className =
                "status online";

        }} else {{

            cameraStatus.textContent =
                "INACTIVE";

            cameraStatus.className =
                "status offline";

        }}


        // -----------------------------------------------
        // STATISTIQUES
        // -----------------------------------------------

        document.getElementById(
            "active-count"
        ).textContent =
            data.statistics.clients_actuels;


        document.getElementById(
            "total-count"
        ).textContent =
            data.statistics.connexions_totales;


        document.getElementById(
            "ip-count"
        ).textContent =
            data.statistics.ips_connues;


        // -----------------------------------------------
        // CLIENTS ACTIFS
        // -----------------------------------------------

        const clientsContainer =
            document.getElementById(
                "clients-container"
            );


        if (data.clients.length === 0) {{

            clientsContainer.innerHTML =
                "Aucun client connecté.";

        }} else {{

            let html = `

                <table>

                    <thead>

                        <tr>

                            <th>IP</th>

                            <th>Nom</th>

                            <th>Connexion</th>

                            <th>Depuis</th>

                            <th>Action</th>

                        </tr>

                    </thead>

                    <tbody>

            `;


            data.clients.forEach(
                client => {{

                    html += `

                        <tr>

                            <td>
                                ${{client.ip}}
                            </td>

                            <td>
                                ${{client.hostname}}
                            </td>

                            <td>
                                #${{client.connections}}
                            </td>

                            <td>
                                ${{client.connected_at}}
                            </td>

                            <td>

                                <button
                                    class="block"
                                    onclick=
                                    "expulserEtBloquer(
                                        '${{client.ip}}'
                                    )"
                                >
                                    Expulser & bloquer
                                </button>

                            </td>

                        </tr>

                    `;

                }}
            );


            html += `

                    </tbody>

                </table>

            `;


            clientsContainer.innerHTML =
                html;

        }}


        // -----------------------------------------------
        // HISTORIQUE
        // -----------------------------------------------

        const historyContainer =
            document.getElementById(
                "history-container"
            );


        if (
            data.disconnected.length === 0
        ) {{

            historyContainer.innerHTML =
                "Aucun historique.";

        }} else {{

            let html = `

                <table>

                    <thead>

                        <tr>

                            <th>IP</th>

                            <th>Nom</th>

                            <th>Connexion</th>

                            <th>Déconnexion</th>

                        </tr>

                    </thead>

                    <tbody>

            `;


            data.disconnected.forEach(
                client => {{

                    html += `

                        <tr>

                            <td>
                                ${{client.ip}}
                            </td>

                            <td>
                                ${{client.hostname}}
                            </td>

                            <td>
                                ${{client.connected_at}}
                            </td>

                            <td>
                                ${{client.disconnected_at}}
                            </td>

                        </tr>

                    `;

                }}
            );


            html += `

                    </tbody>

                </table>

            `;


            historyContainer.innerHTML =
                html;

        }}


        // -----------------------------------------------
        // BLACKLIST
        // -----------------------------------------------

        const blacklistContainer =
            document.getElementById(
                "blacklist-container"
            );


        if (
            data.blacklist.length === 0
        ) {{

            blacklistContainer.innerHTML =
                "Aucune IP bloquée.";

        }} else {{

            let html = `

                <table>

                    <thead>

                        <tr>

                            <th>IP</th>

                            <th>Action</th>

                        </tr>

                    </thead>

                    <tbody>

            `;


            data.blacklist.forEach(
                ip => {{

                    html += `

                        <tr>

                            <td>
                                ${{ip}}
                            </td>

                            <td>

                                <button
                                    class="unblock"
                                    onclick=
                                    "debloquer(
                                        '${{ip}}'
                                    )"
                                >
                                    Débloquer
                                </button>

                            </td>

                        </tr>

                    `;

                }}
            );


            html += `

                    </tbody>

                </table>

            `;


            blacklistContainer.innerHTML =
                html;

        }}


    }} catch (error) {{

        console.error(
            error
        );

    }}

}}


// Actualisation toutes les 2 secondes

setInterval(
    chargerStatus,
    2000
);


// Premier chargement

chargerStatus();


</script>

</body>

</html>
"""


# ============================================================
# DEMARRER LA CAMERA
# ============================================================

@app.post("/admin/start")
def start_camera(config: CameraConfig):

    success = core.ouvrir_camera(
        config
    )

    if not success:

        raise HTTPException(
            status_code=500,
            detail=core.erreur
        )

    return {
        "success": True,
        "message": "Caméra démarrée",
        "source": core.source,
        "width": config.width,
        "height": config.height
    }


# ============================================================
# ARRETER LA CAMERA
# ============================================================

@app.post("/admin/stop")
def stop_camera():

    core.fermer_camera()

    return {
        "success": True,
        "message": "Caméra arrêtée"
    }


# ============================================================
# EXPULSER + BLOQUER
# ============================================================

@app.post("/admin/block")
def block_client(data: IPRequest):

    ip = data.ip.strip()

    if not ip:

        raise HTTPException(
            status_code=400,
            detail="IP invalide."
        )

    clients.bloquer(ip)

    return {
        "success": True,
        "message":
            f"{ip} a été expulsée et bloquée."
    }


# ============================================================
# DEBLOQUER
# ============================================================

@app.post("/admin/unblock")
def unblock_client(data: IPRequest):

    ip = data.ip.strip()

    clients.debloquer(ip)

    return {
        "success": True,
        "message":
            f"{ip} a été débloquée."
    }


# ============================================================
# PAGE CAMERA CLIENT
# ============================================================

@app.get(
    "/camera/{token}",
    response_class=HTMLResponse
)
def camera_page(token: str):

    # Vérification du token
    if token != TOKEN:

        raise HTTPException(
            status_code=403,
            detail="Token invalide."
        )

    return f"""
<!DOCTYPE html>

<html lang="fr">

<head>

    <meta charset="UTF-8">

    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">

    <title>MyDroidCam</title>

    <style>

        body {{

            margin: 0;

            background: #111;

            color: white;

            font-family: Arial;

            display: flex;

            flex-direction: column;

            align-items: center;

        }}

        h1 {{

            margin: 20px;

        }}

        .camera-container {{

            width: 95%;

            max-width: 1000px;

        }}

        img {{

            width: 100%;

            display: block;

            border-radius: 10px;

        }}

    </style>

</head>


<body>

    <h1>MyDroidCam</h1>

    <div class="camera-container">

        <img
            src="/camera/{TOKEN}/stream"
            alt="Flux caméra"
        >

    </div>

</body>

</html>
"""


# ============================================================
# FLUX CAMERA
# ============================================================

@app.get(
    "/camera/{token}/stream"
)
def camera_stream(
    token: str,
    request: Request
):

    # Vérifier le token
    if token != TOKEN:

        raise HTTPException(
            status_code=403,
            detail="Token invalide."
        )

    # Vérifier que la caméra est active
    if not core.camera_is_open:

        raise HTTPException(
            status_code=503,
            detail="Caméra inactive."
        )

    # Adresse IP du client
    ip = request.client.host

    # Vérifier immédiatement la blacklist
    if clients.est_blacklist(ip):

        raise HTTPException(
            status_code=403,
            detail="Votre adresse IP est bloquée."
        )

    # Enregistrer le client
    client_id = clients.connecter(ip)

    if client_id is None:

        raise HTTPException(
            status_code=403,
            detail="Accès refusé."
        )

    # Retourner le flux MJPEG
    return StreamingResponse(

        core.generate_frames(
            client_id,
            ip,
            clients
        ),

        media_type=
            "multipart/x-mixed-replace; boundary=frame"
    )


# ============================================================
# STATUS
# ============================================================

@app.get("/status")
def status():

    return {

        "camera_active":
            core.camera_is_open,

        "source":
            core.source,

        "error":
            core.erreur,

        "clients":
            clients.clients_actuels(),

        "disconnected":
            clients.clients_deconnectes(),

        "blacklist":
            list(clients.blacklist),

        "statistics":
            clients.statistiques()
    }


# ============================================================
# LANCEMENT
# ============================================================

if __name__ == "__main__":

    ip_locale = obtenir_ip_locale()

    print()
    print("=" * 60)

    print(
        "             MyDroidCam"
    )

    print("=" * 60)

    print()

    print(
        f"[ADMIN] "
        f"http://{ip_locale}:{PORT}/"
    )

    print()

    print(
        f"[CLIENT] "
        f"http://{ip_locale}:{PORT}/camera/{TOKEN}"
    )

    print()

    print(
        f"[TOKEN] "
        f"{TOKEN}"
    )

    print()

    print("=" * 60)

    # Ouvrir automatiquement la page admin
    threading.Timer(
        1.5,
        lambda: webbrowser.open(
            f"http://127.0.0.1:{PORT}/"
        )
    ).start()

    # Lancer FastAPI/Uvicorn
    uvicorn.run(
        app,
        host=HOST,
        port=PORT
    )