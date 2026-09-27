# 🎯 Système de Reconnaissance Faciale en Temps Réel

> Pipeline de vision par ordinateur temps réel — **YuNet + InsightFace (ArcFace) + SORT** — servi via **Django Channels + WebSocket**, sans GPU requis.

Développé par **NOBRE Canisius , OLAOGOU Martial et KOTANMI Angèle** · CAEB Natitingou, Bénin

---

## 📌 Présentation

Ce projet est un système complet de **détection, tracking et reconnaissance faciale en temps réel**, entièrement opérationnel sur CPU. Il expose un dashboard web multi-flux qui diffuse les frames annotées via WebSocket, enregistre automatiquement les présences en base de données, et permet d'ajouter de nouvelles personnes à la volée sans réentraîner le moindre modèle.

### Ce que le système fait concrètement

- **Détecte** les visages dans un flux vidéo (webcam locale, URL RTSP, DroidCam) grâce au modèle YuNet (ONNX)
- **Suit** chaque visage d'une frame à l'autre via l'algorithme SORT (Filtre de Kalman + Algorithme Hongrois) pour éviter de réidentifier la même personne à chaque frame
- **Reconnaît** chaque visage par similarité cosinus sur des embeddings ArcFace 512D générés par InsightFace `buffalo_l` — sans softmax, sans classification fermée : un visage inconnu reste "INCONNU"
- **Diffuse** les frames annotées (base64/JPEG) en temps réel au navigateur via Django Channels WebSocket
- **Enregistre** les présences (nom, source, heure, date) en base de données MySQL/SQLite
- **Permet l'ajout** de nouvelles personnes directement depuis l'interface web, avec mise à jour immédiate des embeddings sans redémarrer le serveur
- **Mode tracking cross-caméra** : retrouve une personne vue sur une caméra depuis n'importe quelle autre caméra active à partir de son nom et ou de son image 

---

## 🏗️ Architecture

```
reconnaissance_faciale/
│
├── live_camera.py                  # Version standalone (interface OpenCV, mode bureau)
│
├── amelioration/
│   ├── live_camera.py              # Version standalone améliorée (même pipeline)
│   └── construire_base_de_donnees.py  # Script de création de la base d'embeddings JSON
│
└── frontend/                       # Application Django (serveur web + WebSocket)
    ├── manage.py
    ├── frontend/                   # Configuration Django (settings, urls, asgi)
    │   ├── settings.py
    │   ├── urls.py
    │   └── asgi.py                 # Point d'entrée ASGI (Daphne)
    │
    └── liveservor/                 # App Django principale
        ├── consumers.py            # WebSocket consumers (VideoStream + Tracking)
        ├── views.py                # Vues HTTP (dashboard, présences, ajout, téléchargement)
        ├── models.py               # Modèles BDD (Reconnus, Profile, ImageTraite, Source)
        ├── routing.py              # Routes WebSocket
        ├── urls.py                 # Routes HTTP
        ├── ajouter_une_personne.py # Logique d'ajout en temps réel dans la base
        ├── reconnaissance_par_embeddings.py  # Reconnaissance sur image statique
        ├── creactionfichier.py     # Export Excel des présences
        │
        ├── static/
        │   ├── model/
        │   │   ├── face_detection_yunet_2023mar.onnx  # Modèle de détection
        │   │   └── embeddings.json                    # Base des visages connus
        │   ├── js/
        │   │   ├── dashboard.js    # Logique WebSocket côté navigateur
        │   │   ├── ajouter.js      # Interface d'ajout de photos
        │   │   └── accueil.js
        │   └── css/
        │
        └── templates/
            ├── dashboard.html      # Interface multi-flux principale
            ├── ajouter.html        # Formulaire d'ajout de personne
            └── accueil.html        # Page d'accueil
```

---

## 🔬 Pipeline de reconnaissance (cœur du système)

### 1. Détection — YuNet (ONNX)

```
Frame vidéo → cv2.FaceDetectorYN → Boîtes englobantes (x, y, w, h)
```

YuNet est un détecteur léger basé sur un réseau de neurones exporté en ONNX. Il tourne directement via OpenCV sans dépendance externe lourde. Paramètres clés :

| Paramètre         | Valeur | Rôle                                        |
|---                |---     |---                                          |
| `SCORE_THRESHOLD` | 0.75   | Seuil de confiance de détection             |
| `NMS_THRESHOLD`   | 0.3    | Suppression des détections redondantes      |
| `DETECTION_EVERY` | 2      | Détection une frame sur deux (économie CPU) |

### 2. Tracking — SORT (Simple Online and Realtime Tracking)

Implémenté from scratch. Chaque visage détecté reçoit un identifiant stable (`track_id`) qui persiste entre les frames, même si la détection rate une frame.

**Filtre de Kalman** (8 états : position x1,y1,x2,y2 + vitesses) — prédit la position à la frame suivante.

**Algorithme Hongrois** (`scipy.linear_sum_assignment`) — associe optimalement les détections aux tracks existants via la matrice IoU.

```
Détections frame N → Matrice IoU avec prédictions → Algorithme Hongrois
→ Tracks mis à jour / nouveaux tracks / tracks supprimés
```

| Paramètre | Valeur | Rôle |
|---|---|---|
| `MAX_AGE` | 5–20 | Frames avant suppression d'un track perdu |
| `MIN_HITS` | 2–3 | Frames minimum avant d'afficher un track |
| `IOU_THRESHOLD` | 0.15–0.25 | Seuil d'association détection↔track |

### 3. Reconnaissance — InsightFace ArcFace 512D

Pas de classification fermée. Chaque visage est représenté par un **vecteur de 512 valeurs** (embedding normalisé). La reconnaissance se fait par **similarité cosinus** entre l'embedding du visage en live et ceux de la base.

```python
similarite = np.dot(embedding_live, embedding_base)  # = cosinus car vecteurs normalisés
nom = base_noms[argmax(similarites)] if max(similarites) >= SEUIL_COSINUS else "INCONNU"
```

**Avantage majeur** : un visage jamais vu reste "INCONNU". Ajouter une nouvelle personne = calculer son embedding moyen et l'insérer dans le JSON. Zéro réentraînement.

### 4. Anti-duplication inter-frames (id_map)

Quand un nouveau track apparaît, un thread calcule son embedding et vérifie s'il ressemble déjà à un track existant dans `live_dictionnaire`. Si oui → redirection (`id_map[tid] = id_canonique`). Cela évite qu'une même personne soit comptée deux fois après une occlusion brève.

### 5. Re-vérification périodique des inconnus

Les visages marqués "INCONNU" sont re-analysés toutes les `REINSPECT_DELAY` secondes. Si entre-temps la personne a été ajoutée à la base, elle sera reconnue automatiquement.

---

## 🌐 Architecture WebSocket (Django Channels)

```
Navigateur                     Django Channels (ASGI/Daphne)
    │                                    │
    │──── WS connect /ws/video/<name> ──►│  VideoStreamConsumer.connect()
    │──── { type: "url", message: "..." }►│  VideoStreamConsumer.receive()
    │                                    │  └── asyncio.create_task(live_serveur())
    │                                    │       ├── cap = cv2.VideoCapture(source)
    │                                    │       ├── Détection YuNet (run_in_executor)
    │                                    │       ├── SORT Tracker.update()
    │                                    │       ├── Thread InsightFace (daemon)
    │                                    │       └── cv2.imencode → base64
    │◄── { type:"stream", message:b64 } ─│  self.send(json.dumps(data))
    │◄── { type:"stoperror" } ───────────│  (reconnexion automatique ×5)
    │◄── { type:"fin" } ─────────────────│  (abandon après 5 échecs)
```

**Anti-latence** (4 mécanismes implémentés) :
1. **Contrôle FPS** : `INTERVALLE_FRAME = 1/15` — libère la boucle asyncio entre frames
2. **JPEG qualité réduite** : `JPEG_QUALITE = 60` — taille divisée par ~2
3. **Buffer vidange** : `cap.grab(); cap.grab(); cap.retrieve()` — élimine les frames en retard
4. **`run_in_executor`** pour OpenCV — ne bloque pas la boucle asyncio

---

## 📷 Mode Tracking Cross-Caméra (`TrackingConsumer`)

Ce second consumer WebSocket permet de **retrouver une personne inconnue** parmi toutes les caméras actives.

```
Photo envoyée (bytes) → TrackingConsumer.receive()
  → get_embedding(photo) via InsightFace
  → faire_comparaison(emb) : dot product contre live_embeddings (dict partagé)
  → Réponse : { nom, framename (caméra où elle se trouve) }
```

`live_embeddings` est un dictionnaire partagé entre tous les consumers, alimenté par `VideoStreamConsumer` en temps réel. Un thread de nettoyage (`remettreazero`) supprime les personnes absentes depuis `DELAI_EXPIRATION = 5` secondes.

---

## 🗄️ Base de données

### Modèles Django

| Modèle | Champs | Rôle |
|---|---|---|
| `Reconnus` | `nom`, `source`, `heure`, `date` | Enregistrement automatique des présences |
| `Profile` | `nom`, `photo` | Personnes enregistrées dans le système |
| `ImageTraite` | `name_frame`, `image`, `date` | Historique des images traitées |
| `Source` | `url` | Sources vidéo mémorisées |

### Base d'embeddings (`embeddings.json`)

```json
{
  "Jean_Dupont": [0.023, -0.145, ..., 0.312],   // vecteur 512D normalisé
  "Marie_Kokou": [0.198, 0.067, ..., -0.089]
}
```

La base est rechargée automatiquement à chaque ajout (détection de modification via `os.path.getmtime`).

---

## 🚀 Installation

### Prérequis

- Python 3.10+
- Redis (pour Django Channels)
- MySQL ou SQLite
- Modèles à télécharger séparément (voir ci-dessous)

### 1. Cloner le dépôt

```bash
git clone https://github.com/canisius2006/reconnaissance_faciale.git
cd reconnaissance_faciale
```

### 2. Environnement virtuel & dépendances

```bash
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

pip install -r requirements.txt
```


## 🐛 Débogage

### Problèmes de Reconnaissance Faciale

Pour les visages "INCONNU" persistants:
1. Augmenter le délai `REINSPECT_DELAY`
2. Baisser le `SEUIL_COSINUS` (moins strict)
3. Vérifier la qualité des photos dans la base

Pour améliorer la détection:
1. Augmenter `SCORE_THRESHOLD`
2. Réduire `DETECTION_EVERY` (plus fréquent)
3. Augmenter `MIN_HITS` (plus stricte)

### 🔧 Problèmes d'Installation InsightFace

InsightFace nécessite un compilateur C++ pour compiler les extensions natives. **C'est souvent la cause principale d'échec !**

#### **PRÉREQUIS: Installer un compilateur C++**

##### **Sur Windows**
InsightFace refuse souvent d'installer sans compilateur C++.

**Solution:** Installer **Visual Studio Build Tools** (gratuit)
*
#### **Télécharger depuis**:
#### [https://visualstudio.microsoft.com/downloads/](https://visualstudio.microsoft.com/downloads/)
#### "Tools for Visual Studio" → "Build Tools for Visual Studio"

#### Après installation, redémarrer et relancer pip install insightface
```
pip install insightface
```

**Alternative rapide (ligne de commande):**
```bash
# Installer via chocolatey si disponible
choco install visualstudio2022-workload-nativedesktop

# Ou via Windows Package Manager
winget install Microsoft.VisualStudio.2022.Community
```

##### **Sur Linux (Ubuntu/Debian)**
```bash
# Installer les outils de compilation et cmake
sudo apt-get update
sudo apt-get install -y \
    build-essential \
    cmake \
    git \
    python3-dev \
    dlib-dev

# Pour une meilleure compatibilité:
sudo apt-get install -y libopenblas-dev liblapack-dev

# Puis installer insightface
pip install insightface
```

##### **Sur Linux (Fedora/RedHat)**
```bash
sudo yum groupinstall -y "Development Tools"
sudo yum install -y cmake dlib-devel python3-devel
pip install insightface
```

##### **Sur macOS**
```bash
# Installer Xcode Command Line Tools
xcode-select --install

# Puis installer insightface
pip install insightface
```

---

#### **Erreur: `pip install insightface` échoue**

**Solution 1 - Installation depuis Git (recommandé)**
```bash
pip uninstall insightface -y
pip install git+https://github.com/deepinsight/insightface.git
```

**Solution 2 - Compiler depuis source**
```bash
git clone https://github.com/deepinsight/insightface.git
cd insightface/python-package
python setup.py install
```

**Solution 3 - Pré-construire avec wheel**
```bash
pip install --upgrade pip setuptools wheel
pip install insightface --no-cache-dir --force-reinstall
```

#### **Erreur: `ModuleNotFoundError: No module named 'insightface'`**

```bash
# Vérifier l'installation
python -c "import insightface; print(insightface.__version__)"

# Si pas trouvé, réinstaller complètement
pip uninstall insightface mxnet onnxruntime -y
pip install insightface onnxruntime-gpu  # ou onnxruntime pour CPU
```

#### **Erreur: Téléchargement du modèle buffalo_l échoue**

Le modèle se télécharge automatiquement. Si ça échoue:

```bash
# Télécharger manuellement
mkdir -p ~/.insightface/models
cd ~/.insightface/models

# Télécharger buffalo_l (remplacer URL si nécessaire)
wget https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
unzip buffalo_l.zip
```

**Ou en Python:**
```python
from insightface.app import FaceAnalysis

# Force le téléchargement
app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'], 
                   root='./models')  # Custom path
app.prepare(ctx_id=-1, det_size=(320, 320))
```

#### **Erreur: `Illegal instruction` (CPU incompatible)**

Si vous avez une très vieille CPU:

```bash
# Installer une version compatible
pip uninstall onnxruntime -y
pip install onnxruntime==1.14.1  # Version plus ancienne

# Ou utiliser CPU minimal
pip install insightface --no-binary :all:
```

#### **Erreur: OutOfMemory (RAM insuffisante)**

```python
# Réduire la taille du modèle
app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'],
                   allowed_modules=['detection', 'recognition'])
app.prepare(ctx_id=-1, det_size=(160, 160))  # Plus petit que (320, 320)
```


### 3. Télécharger le modèle YuNet


#### Placer ici : frontend/static/model/face_detection_yunet_2023mar.onnx
#### Source officielle :
#### [https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)
```

### 4. Télécharger InsightFace buffalo_l

InsightFace télécharge `buffalo_l` automatiquement au premier lancement dans `~/.insightface/models/`.

### 5. Démarrer Redis

```bash
# Linux
sudo service redis start

# Windows (via WSL ou Docker)
docker run -p 6379:6379 redis
```

### 6. Configuration Django

```bash
cd frontend
python manage.py migrate
```

### 7. Lancer le serveur

```bash
# Développement (avec Daphne pour ASGI)
daphne -p 8000 frontend.asgi:application

# Ou avec Django runserver (WebSocket supporté depuis Django 3.1+) (recommandé)
python manage.py runserver
```

---

## 🗂️ Construire la base d'embeddings

Avant de lancer le système, il faut créer le fichier `embeddings.json` à partir de photos.
### 1-Ajouter une personne via l'interface web (Recommandé)

L'interface `/ajouter/` permet d'uploader des photos directement depuis le navigateur. L'embedding est calculé et ajouté au JSON sans redémarrer le serveur.


### 2- (Ajouter Manuellement) Structure du dataset

```
dataset/
├── Jean_Dupont/
│   ├── photo1.jpg
│   ├── photo2.jpg
│   └── photo3.jpg
├── Marie_Kokou/
│   └── photo1.jpg
└── ...
```

### Lancer le script

```bash
python amelioration/construire_base_de_donnees.py
# Une boîte de dialogue s'ouvre → sélectionner le dossier dataset
# Résultat : ownmodel/embeddings/embeddings.json
```

Copier ensuite le fichier généré vers `frontend/static/model/embeddings.json`.


---

## 🖥️ Utilisation

### Interface Dashboard (`/dashboard/`)

1. Saisir un **nom de flux** (identifiant de la caméra) et une **source** :
   - URL RTSP → `rtsp://...`
   - URL DroidCam → `http://192.168.x.x:4747/video`
2. Le flux démarre automatiquement via WebSocket
3. Les personnes reconnues apparaissent dans le panneau latéral avec leur couleur d'identification
4. Les présences sont enregistrées en base de données en temps réel

### Mode Image (reconnaissance sur photo)

Envoyer une requête POST à `/reconnaissance/<name>/` avec un fichier image. Retourne l'image annotée + la liste des personnes détectées.

### Export des présences

- `GET /presence/?date=2026-06-05` → JSON des présences du jour
- `GET /telecharger/?date=2026-06-05` → Fichier Excel téléchargeable
- `GET /presence/?date=all` → Toutes les présences

---

## ⚙️ Configuration avancée

### Paramètres principaux (`consumers.py`)

```python
SEUIL_COSINUS   = 0.5     # Seuil de reconnaissance (0.4 = plus permissif, 0.6 = plus strict)
REINSPECT_DELAY = 1.5     # Secondes avant de re-tenter l'identification d'un inconnu
FPS_CIBLE       = 15      # Frames par seconde envoyées au navigateur
JPEG_QUALITE    = 60      # Qualité JPEG (40–80 selon bande passante)
MAX_AGE         = 5       # Frames avant suppression d'un track perdu
MIN_HITS        = 3       # Frames minimum pour valider un nouveau track
IOU_THRESHOLD   = 0.15    # Seuil d'association détection↔track
```

### WebSocket routes

| URL | Consumer | Rôle |
|---|---|---|
| `ws/video/<framename>` | `VideoStreamConsumer` | Flux vidéo en temps réel |
| `ws/tracking/` | `TrackingConsumer` | Recherche cross-caméra |

---

## 📦 Stack technique

| Composant | Technologie |
|---|---|
| Backend web | Django 6.0 + Django REST Framework |
| WebSocket | Django Channels 4 + Daphne (ASGI) |
| Channel layer | Redis + channels_redis |
| Détection visages | OpenCV YuNet (`face_detection_yunet_2023mar.onnx`) |
| Embeddings | InsightFace `buffalo_l` (ArcFace 512D) |
| Tracking | SORT custom (Kalman Filter + Algorithme Hongrois) |
| Calcul similitude | NumPy (produit scalaire vectorisé) |
| Base de données | MySQL (mysqlclient) / SQLite |
| Export | openpyxl (Excel) |
| Frontend | Vanilla JS (fetch, WebSocket, DOM) |
| Déploiement GPU | NVIDIA Jetson Nano / RTX 3060+ (optionnel) |

---

## 🔧 Mode standalone (sans serveur web)

Pour tester le pipeline directement avec OpenCV sans Django :

```bash
# Version simple (interface fenêtre OpenCV)
python live_camera.py

# Version améliorée
python amelioration/live_camera.py
```

Au premier lancement, une boîte de dialogue demande de sélectionner :
- Le fichier `embeddings.json` (base de visages)
- Le modèle YuNet `.onnx`

Les chemins sont mémorisés dans `nom_model.txt` et `MODEL_PATH.txt`.

Touche `q` pour quitter.

---

Nb: Veillez à bien configurer les chemins en cas d'utilisations sans serveur 

## 📐 Choix techniques — Pourquoi pas softmax ?

Les approches classiques (MobileNetV2 + softmax) ont une limitation critique : elles ne peuvent pas rejeter un visage inconnu — elles forcent toujours une prédiction parmi les classes connues.

Ce système utilise une approche par **métrique** :
- Chaque personne = un point dans l'espace vectoriel à 512 dimensions
- La reconnaissance = mesurer la distance cosinus entre deux points
- Un inconnu = aucun point connu n'est suffisamment proche (seuil configurable)
- Ajouter une personne = ajouter un point dans l'espace, sans réentraînement

---

## 🚧 Limitations connues

- Performances optimales avec une carte GPU (NVIDIA RTX 3060 minimum recommandé pour la production) — le mode CPU est fonctionnel mais plus lent sur des flux multiples
- La précision diminue en conditions d'éclairage faible ou d'occlusion partielle du visage
- Le seuil cosinus (`0.5`) peut nécessiter un ajustement selon la qualité des photos d'enregistrement

---

## 👤 Auteurs

**NOBRE Canisius , OLAOGOU Martial et KOTANMI Angèle** — CAEB de Natitingou, Bénin  
Projet développé dans le cadre d'une journée de restitution de projet sur l'intelligence artificielle au CAEB Natitingou.

---

*Contact: [canisiusnobre@gmail.com](mailto:canisiusnobre@gmail.com)*
