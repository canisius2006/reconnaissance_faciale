// ─────────────────────────────────────────────
//  STATE
// ─────────────────────────────────────────────
const MAX_PHOTOS = 5;
let photos = [];
let currentSource = 'camera';
let stream = null;
let droidcamUrl = null;
let droidcamRetryCount = 0;
const MAX_DC_RETRIES = 3;

// FIX 3 — Timer global pour showTempAlert (évite les conflits entre onglets)
let tempAlertTimer = null;

// ─────────────────────────────────────────────
//  SOURCE SELECTION
// ─────────────────────────────────────────────
function selectSource(src) {
  currentSource = src;
  document.querySelectorAll('.src-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-src="${src}"]`).classList.add('active');

  stopStream();

  // FIX 3 — Annuler tout timer de temp-alert en cours avant de changer d'onglet
  if (tempAlertTimer) { clearTimeout(tempAlertTimer); tempAlertTimer = null; }
  hideAlert('droidAlert');
  document.getElementById('droidTips').classList.add('hidden');

  document.getElementById('droidcamConfig').classList.toggle('hidden', src !== 'droidcam');
  document.getElementById('fileLabel').classList.toggle('hidden', src !== 'file');

  const video      = document.getElementById('videoEl');
  const dcImg      = document.getElementById('droidcamImg');
  const imgPrev    = document.getElementById('imgPreviewEl');
  const placeholder= document.getElementById('placeholderMsg');
  const captureBtn = document.getElementById('captureBtn');

  video.style.display   = 'none'; video.srcObject = null;
  dcImg.style.display   = 'none'; dcImg.src = '';
  imgPrev.style.display = 'none'; imgPrev.src = '';
  placeholder.style.display = '';
  captureBtn.disabled = true;
  captureBtn.classList.remove('done');
  document.getElementById('captureBtnText').textContent = 'Capturer';

  if (src === 'camera') {
    startCamera();
  } else if (src === 'droidcam') {
    placeholder.innerHTML = `<span class="big">URL</span>Entrez l'IP DroidCam<br>et appuyez sur <b>Connecter</b>`;
  } else {
    placeholder.innerHTML = `<span class="big">Photos</span>Cliquez sur <b>Choisir image(s)</b><br>pour sélectionner jusqu'à ${MAX_PHOTOS - photos.length} photo(s)`;
  }
}

// ─────────────────────────────────────────────
//  CAMERA
// ─────────────────────────────────────────────
async function startCamera() {
  const video      = document.getElementById('videoEl');
  const placeholder= document.getElementById('placeholderMsg');
  const captureBtn = document.getElementById('captureBtn');
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    video.srcObject = stream;
    video.style.display = 'block';
    placeholder.style.display = 'none';
    captureBtn.disabled = photos.length >= MAX_PHOTOS;
  } catch (err) {
    placeholder.innerHTML = `<span class="big">Stop</span>Accès caméra refusé<br><small style="color:var(--muted)">${err.message}</small>`;
  }
}

function stopStream() {
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  const dcImg = document.getElementById('droidcamImg');
  dcImg.src = ''; dcImg.style.display = 'none';
  droidcamUrl = null;
}

// ─────────────────────────────────────────────
//  DROIDCAM
// ─────────────────────────────────────────────
async function connectDroidCam() {
  const ip   = document.getElementById('dcIp').value.trim();
  const port = document.getElementById('dcPort').value.trim() || '4747';

  // FIX 3 — Annuler le timer temp-alert avant toute nouvelle alerte
  if (tempAlertTimer) { clearTimeout(tempAlertTimer); tempAlertTimer = null; }
  hideAlert('droidAlert');
  document.getElementById('droidTips').classList.add('hidden');
  droidcamRetryCount = 0;

  if (!ip) { showAlert('droidAlert','warn',' Veuillez entrer une adresse IP.'); return; }
  if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) {
    showAlert('droidAlert','warn',` Format d'IP invalide : "${ip}". Exemple : 192.168.1.42`); return;
  }

  stopStream();
  droidcamUrl = `http://${ip}:${port}/video`;

  const placeholder = document.getElementById('placeholderMsg');
  placeholder.innerHTML = `<span class="big"></span>Connexion à ${droidcamUrl}…`;
  placeholder.style.display = '';
  showAlert('droidAlert','info',` Tentative de connexion à ${droidcamUrl}…`);

  attemptDroidCamConnect();
}

function attemptDroidCamConnect() {
  const placeholder= document.getElementById('placeholderMsg');
  const captureBtn = document.getElementById('captureBtn');

  droidcamRetryCount++;

  // FIX 2 — Remplacer le <img> par un clone propre à chaque tentative
  // Cela coupe toute connexion réseau pendante du navigateur sur l'ancienne tentative
  const old = document.getElementById('droidcamImg');
  const dcImg = old.cloneNode(false);
  dcImg.id = 'droidcamImg';
  old.parentNode.replaceChild(dcImg, old);

  let loaded = false;
  const probeTimeout = setTimeout(() => {
    if (!loaded) {
      dcImg.onload  = null;
      dcImg.onerror = null;
      dcImg.src = '';
      handleDCError('timeout');
    }
  }, 8000);

  dcImg.onload = () => {
    if (loaded) return;
    loaded = true;
    clearTimeout(probeTimeout);
    placeholder.style.display = 'none';
    dcImg.style.display = 'block';
    captureBtn.disabled = photos.length >= MAX_PHOTOS;
    hideAlert('droidAlert');
    showAlert('droidAlert','info',' Connecté à DroidCam. Flux MJPEG actif.');
    document.getElementById('droidTips').classList.add('hidden');
    droidcamRetryCount = 0;

    dcImg.onerror = () => {
      if (currentSource === 'droidcam') handleDCError('stream_lost');
    };
  };

  dcImg.onerror = () => {
    if (loaded) return;
    clearTimeout(probeTimeout);
    handleDCError('unreachable');
  };

  // Cache-buster pour forcer une nouvelle requête HTTP
  dcImg.src = droidcamUrl + '?t=' + Date.now();
}

function handleDCError(type) {
  const placeholder= document.getElementById('placeholderMsg');
  const dcImg      = document.getElementById('droidcamImg');
  const captureBtn = document.getElementById('captureBtn');

  dcImg.style.display = 'none'; dcImg.src = '';
  captureBtn.disabled = true;
  placeholder.style.display = '';
  document.getElementById('droidTips').classList.remove('hidden');

  const msgs = {
    timeout:     ' Délai dépassé — L\'appareil ne répond pas.',
    unreachable: ' Impossible de joindre DroidCam — Vérifiez l\'IP et le Wi-Fi.',
    stream_lost: ' Flux interrompu — La connexion a été perdue.',
  };
  const msg = msgs[type] || ' Erreur inconnue.';

  if (droidcamRetryCount < MAX_DC_RETRIES) {
    showAlert('droidAlert','warn',
      `${msg}<br><small>Tentative ${droidcamRetryCount}/${MAX_DC_RETRIES} dans 3 s…</small>`);
    placeholder.innerHTML = `<span class="big"></span>Reconnexion…`;
    setTimeout(attemptDroidCamConnect, 3000);
  } else {
    showAlert('droidAlert','error',
      `${msg}<br><small>Échec après ${MAX_DC_RETRIES} tentatives.</small>`);
    placeholder.innerHTML = `<span class="big"></span>Connexion DroidCam échouée`;
  }
}

// ─────────────────────────────────────────────
//  FILE SELECT
// ─────────────────────────────────────────────
function handleFileSelect(event) {
  const files     = Array.from(event.target.files);
  if (!files.length) return;
  const remaining = MAX_PHOTOS - photos.length;
  const toAdd     = files.slice(0, remaining);
  if (files.length > remaining)
    showTempAlert(` ${files.length} fichiers — seulement ${remaining} ajouté(s).`, 'info');

  const last = toAdd[toAdd.length - 1];
  if (last) {
    const imgPrev    = document.getElementById('imgPreviewEl');
    const placeholder= document.getElementById('placeholderMsg');
    const r = new FileReader();
    r.onload = e => { imgPrev.src = e.target.result; imgPrev.style.display = 'block'; placeholder.style.display = 'none'; };
    r.readAsDataURL(last);
  }
  toAdd.forEach(f => addPhotoFromFile(f));
  event.target.value = '';
}

function addPhotoFromFile(file) {
  if (photos.length >= MAX_PHOTOS) return;
  const r = new FileReader();
  r.onload = e => addPhoto(e.target.result, dataURItoBlob(e.target.result), file.name);
  r.readAsDataURL(file);
}

// ─────────────────────────────────────────────
//  CAPTURE
// ─────────────────────────────────────────────
function capturePhoto() {
  if (photos.length >= MAX_PHOTOS) return;
  if (currentSource === 'file') { document.getElementById('fileInput').click(); return; }

  const source = currentSource === 'droidcam'
    ? document.getElementById('droidcamImg')
    : document.getElementById('videoEl');

  // FIX 1 — Pour DroidCam (<img> MJPEG), naturalWidth peut valoir 0
  // si le premier frame n'est pas encore entièrement rendu.
  // On utilise les dimensions du conteneur comme fallback fiable.
  const container = document.getElementById('videoContainer');
  const w = (source.videoWidth  || source.naturalWidth)  || container.clientWidth  || 640;
  const h = (source.videoHeight || source.naturalHeight) || container.clientHeight || 480;

  // Vérification : si les dimensions sont toujours nulles, le flux n'est pas prêt
  if (currentSource === 'droidcam' && source.naturalWidth === 0) {
    showTempAlert(' Le flux DroidCam n\'est pas encore prêt. Attendez que l\'image s\'affiche.', 'warn');
    return;
  }

  const canvas = document.createElement('canvas');
  canvas.width  = w;
  canvas.height = h;
  canvas.getContext('2d').drawImage(source, 0, 0, w, h);

  const dataUrl = canvas.toDataURL('image/jpeg', 0.92);
  const label   = `${currentSource === 'droidcam' ? 'DroidCam' : 'Caméra'} · Photo ${photos.length + 1}`;
  addPhoto(dataUrl, dataURItoBlob(dataUrl), label);
}

// ─────────────────────────────────────────────
//  ADD PHOTO
// ─────────────────────────────────────────────
function addPhoto(dataUrl, blob, label) {
  if (photos.length >= MAX_PHOTOS) return;
  photos.push({ dataUrl, blob, label: label || `Photo ${photos.length + 1}` });
  renderGallery();
  updateProgress();
  if (photos.length >= MAX_PHOTOS) {
    const btn = document.getElementById('captureBtn');
    btn.disabled = true; btn.classList.add('done');
    document.getElementById('captureBtnText').textContent = '5/5 Complet ✓';
  }
}

// ─────────────────────────────────────────────
//  GALLERY
// ─────────────────────────────────────────────
function renderGallery() {
  for (let i = 0; i < MAX_PHOTOS; i++) {
    const slot = document.getElementById(`slot${i}`);
    const fresh = slot.cloneNode(false);
    slot.parentNode.replaceChild(fresh, slot);
    fresh.id = `slot${i}`;

    if (photos[i]) {
      fresh.classList.add('filled');
      fresh.innerHTML = `
        <img src="${photos[i].dataUrl}" alt="Photo ${i+1}" draggable="false" />
        <div class="zoom-overlay">🔍</div>
        <button class="thumb-del" data-idx="${i}" title="Supprimer cette photo">✕</button>
      `;

      fresh.querySelector('.thumb-del').addEventListener('click', function(e) {
        e.stopPropagation();
        deletePhoto(parseInt(this.dataset.idx));
      });

      fresh.addEventListener('click', function(e) {
        if (e.target.classList.contains('thumb-del')) return;
        openLightbox(i);
      });
    } else {
      fresh.classList.remove('filled');
      fresh.innerHTML = `<span class="thumb-num">0${i+1}</span>`;
    }
  }

  const gs = document.getElementById('galleryStatus');
  gs.className = 'status-indicator ' + (photos.length > 0 ? 'live' : '');
}

function deletePhoto(idx) {
  photos.splice(idx, 1);
  renderGallery();
  updateProgress();

  if (photos.length < MAX_PHOTOS) {
    const btn = document.getElementById('captureBtn');
    const ready = (currentSource === 'camera' && stream) ||
                  (currentSource === 'droidcam' && droidcamUrl &&
                   document.getElementById('droidcamImg').style.display !== 'none');
    btn.disabled = !ready;
    btn.classList.remove('done');
    document.getElementById('captureBtnText').textContent = 'Capturer';
  }
  document.getElementById('sendBtn').disabled = photos.length < 5;
}

function clearAll() {
  if (!photos.length) return;
  if (!confirm('Effacer toutes les photos ?')) return;
  photos = [];
  renderGallery();
  updateProgress();
  const btn = document.getElementById('captureBtn');
  btn.classList.remove('done');
  document.getElementById('captureBtnText').textContent = 'Capturer';
  const ready = (currentSource === 'camera' && stream) ||
                (currentSource === 'droidcam' && droidcamUrl &&
                 document.getElementById('droidcamImg').style.display !== 'none');
  btn.disabled = !ready;
  document.getElementById('sendBtn').disabled = true;
  if (currentSource === 'file') {
    const ip = document.getElementById('imgPreviewEl');
    ip.style.display = 'none'; ip.src = '';
    document.getElementById('placeholderMsg').style.display = '';
  }
}

// ─────────────────────────────────────────────
//  PROGRESS
// ─────────────────────────────────────────────
function updateProgress() {
  document.getElementById('progressText').textContent = `${photos.length} / ${MAX_PHOTOS} photos`;
  for (let i = 0; i < MAX_PHOTOS; i++)
    document.getElementById(`dot${i}`).classList.toggle('filled', i < photos.length);
  document.getElementById('sendBtn').disabled = photos.length < 5;
}

// ─────────────────────────────────────────────
//  LIGHTBOX
// ─────────────────────────────────────────────
function openLightbox(idx) {
  document.getElementById('lbImg').src = photos[idx].dataUrl;
  document.getElementById('lbLabel').textContent = `Photo ${idx+1} · ${photos[idx].label}`;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
}
function closeLightboxOverlay(e) {
  if (e.target === document.getElementById('lightbox')) closeLightbox();
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

// ─────────────────────────────────────────────
//  SEND TO DJANGO
// ─────────────────────────────────────────────
async function sendPhotos() {
  const nom = sanitizeName(document.querySelector('#people-name').value);
  const url      = '';
  const statusEl = document.getElementById('sendStatus');
  if (!nom) { statusEl.style.color='var(--warn)'; statusEl.textContent=' Entrez Votre nom.'; return; }
  if (!photos.length) { statusEl.style.color='var(--warn)'; statusEl.textContent=' Aucune photo.'; return; }

  const sendBtn = document.getElementById('sendBtn');
  sendBtn.disabled = true;
  statusEl.style.color = 'var(--muted)';
  statusEl.textContent = '⏳ Envoi en cours…';

  try {
    const fd = new FormData();
    photos.forEach((p,i) => fd.append('photos', p.blob, `photo_${i+1}.jpg`));
    fd.append('count', photos.length);
    fd.append('source', currentSource);
    fd.append('nom', nom);

    const res = await fetch(url, { method: 'POST', headers: {
      'X-CSRFToken': document.getElementsByName('csrfmiddlewaretoken')[0].value
    }, body: fd });

    if (res.ok) {
      statusEl.style.color = 'var(--green)';
      statusEl.textContent = `✅ ${photos.length} photo(s) envoyée(s) avec succès !`;
      setTimeout(() => { window.location.pathname = 'dashboard/'; }, 1000);
    } else {
      const txt = await res.text().catch(() => '');
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = `❌ Erreur "Photo mal prise, Veuillez reessayer la capture" : `;
    }
  } catch(err) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = `❌ Impossible de contacter le serveur : ${err.message}`;
  } finally {
    sendBtn.disabled = false;
  }
}

// ─────────────────────────────────────────────
//  HELPERS
// ─────────────────────────────────────────────
function dataURItoBlob(dataURI) {
  const bytes  = atob(dataURI.split(',')[1]);
  const mime   = dataURI.split(',')[0].split(':')[1].split(';')[0];
  const ab     = new ArrayBuffer(bytes.length);
  const ia     = new Uint8Array(ab);
  for (let i = 0; i < bytes.length; i++) ia[i] = bytes.charCodeAt(i);
  return new Blob([ab], { type: mime });
}

function showAlert(id, type, html) {
  const el = document.getElementById(id);
  el.className = `alert ${type} show`; el.innerHTML = html;
}

function hideAlert(id) {
  const el = document.getElementById(id);
  el.className = 'alert'; el.innerHTML = '';
}

// FIX 3 — showTempAlert utilise le timer global pour éviter les conflits
function showTempAlert(msg, type) {
  if (tempAlertTimer) { clearTimeout(tempAlertTimer); tempAlertTimer = null; }
  showAlert('droidAlert', type, msg);
  tempAlertTimer = setTimeout(() => {
    hideAlert('droidAlert');
    tempAlertTimer = null;
  }, 4000);
}

function sanitizeName(name) {
  return name
    .normalize("NFD")
    .trim()
    .replace(/[^\p{L}\s\-'\d_]/gu, '')
}

// ─────────────────────────────────────────────
//  INIT
// ─────────────────────────────────────────────
window.addEventListener('DOMContentLoaded', () => {
  selectSource('camera');
  renderGallery();
  updateProgress();
});