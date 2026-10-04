// ============================================================
    //  ÉTAT GLOBAL
    // ============================================================
    let currentMethod = 'url';
    let formobjet     = {};
    let webcamStream  = null;

    let listeframename = [] // Cet tableau pour avoir tous les éléments framename existants 

    let liendatabase = {} // Cet tableau regroupe tous les liens avec les framenames correspondants 

    basepersonnesdetectee = {} // Ceci est une base de données qui va avoir tous les personnes détectés de tous les caméras 

    // Ces deux tableaux sont les sources de vérité.
    // On les alimente via les fonctions globales ci-dessous.
    let allAvailableMembers = [];   // { id, framename, source, lien }
    let guildFamilies       = [];   // { id, memberIds }

    let _nextMemberId = 1; // compteur auto pour les IDs
    let base_url = window.STATIC_URL // Chemin d'accès aux fichiers statiques 

    let nomAutoPropose = ''; // dernier nom proposé automatiquement dans le popup d'ajout

    let traking = false ; // Cette variable va nous permettre de savoir si le mode traking est lancé ou pas 

    // ============================================================
    //  UTILITAIRES — AFFICHAGE DES NOMS
    //  "nobre-canisius" → "Nobre Canisius"  (affichage uniquement :
    //  les clés internes / data-nom restent les noms bruts du serveur)
    // ============================================================
    function formatNom(brut) {
        return String(brut ?? '')
            .replace(/[-_]+/g, ' ')
            .replace(/\s+/g, ' ')
            .trim()
            .split(' ')
            .map(m => m ? m.charAt(0).toUpperCase() + m.slice(1).toLowerCase() : m)
            .join(' ');
    }

    // Clé de comparaison : insensible à la casse et au tiret / underscore
    function cleNom(brut) {
        return String(brut ?? '').toLowerCase().replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim();
    }

    function initialesNom(brut) {
        return formatNom(brut).split(' ').filter(Boolean).map(m => m[0]).join('').toUpperCase().slice(0, 2) || '?';
    }

    function echapperHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    // Construit l'URL d'une photo de profil à partir du chemin relatif renvoyé par le backend
    function urlPhoto(chemin) {
        if (!chemin) return '';
        if (/^(https?:)?\/\//.test(chemin) || chemin.startsWith('/') || chemin.startsWith('data:')) return chemin;
        const base = (window.MEDIA_URL || '/media/').replace(/\/?$/, '/');
        return base + chemin;
    }

    // ============================================================
    //  BULLE D'APERÇU DE PHOTO (partagée : tracking par image + liste de présence)
    //  cote 'bas'    : sous l'élément, pointe vers le haut
    //  cote 'gauche' : à gauche de la carte, pointe vers la droite (ne cache pas la suite)
    // ============================================================
    const bulleApercu = document.createElement('div')
    bulleApercu.className = 'preview-bubble'
    bulleApercu.innerHTML = '<img alt="Aperçu">'
    document.body.appendChild(bulleApercu)
    const bulleImg = bulleApercu.querySelector('img')

    function montrerBulle(src, ancre, cote = 'bas') {
        if (!src || !ancre) return
        bulleImg.src = src
        bulleApercu.classList.toggle('a-gauche', cote === 'gauche')
        const marge = 8
        const L = bulleApercu.offsetWidth
        const H = bulleApercu.offsetHeight
        const r = ancre.getBoundingClientRect()

        if (cote === 'gauche') {
            // À gauche de la carte de présence ; s'il n'y a pas la place, à gauche de la photo
            const carte = ancre.closest('.card') || ancre
            let gauche = carte.getBoundingClientRect().left - L - 14
            if (gauche < marge) gauche = Math.max(marge, r.left - L - 14)
            const centreY = r.top + r.height / 2
            const haut = Math.max(marge, Math.min(centreY - H / 2, window.innerHeight - H - marge))
            bulleApercu.style.left = gauche + 'px'
            bulleApercu.style.top  = haut + 'px'
            // La pointe reste alignée sur la photo survolée
            bulleApercu.style.setProperty('--fleche-y', (centreY - haut) + 'px')
        } else {
            let gauche = r.left + r.width / 2 - L / 2
            gauche = Math.max(marge, Math.min(gauche, window.innerWidth - L - marge))
            bulleApercu.style.left = gauche + 'px'
            bulleApercu.style.top  = (r.bottom + 12) + 'px'
            bulleApercu.style.setProperty('--fleche-x', (r.left + r.width / 2 - gauche) + 'px')
        }
        bulleApercu.classList.add('visible')
    }

    function cacherBulle() {
        bulleApercu.classList.remove('visible')
    }

    // ============================================================
    //  FONCTIONS GLOBALES D'AJOUT
    //  → À appeler depuis n'importe où pour enrichir les panels
    // ============================================================

    // Ici, je vais ajouter ma fonction qui va permettre de pouvoir établir les connexions websockets  
// Ici, je vais définir le innerHtml de mon bouton pour pouvoir me permettre de faire le loading page 

let bouton = document.getElementsByClassName('confirmer')[0]
let oldbouton = bouton.innerHTML 

// De la façon dont cette fonction est faite, il pourra accepter plusieurs connexions 

donnees = {} //Dictionnaire qui contient les framenames avec la source correspondantes ainsi que la liste des personnes 
domain = window.location.host
// ws:// sur une page HTTP, wss:// sur une page HTTPS (un navigateur bloque ws:// depuis une page HTTPS)
const wsProto = window.location.protocol === 'https:' ? 'wss' : 'ws'

// ------------------------------------------------------------
//  GESTION DES NOMS DE CADRES ET DES CONNEXIONS
// ------------------------------------------------------------
const sockets = {} // framename -> WebSocket actif (pour pouvoir le fermer proprement)

// Un nom est "pris" s'il est en cours de connexion (listeframename) ou déjà affiché (allAvailableMembers).
// Comparaison insensible à la casse.
function nomsPris() {
    const pris = new Set(listeframename.map(n => n.toLowerCase()))
    allAvailableMembers.forEach(m => pris.add(m.framename.toLowerCase()))
    return pris
}

function nomEstPris(nom) {
    return nomsPris().has(String(nom).toLowerCase())
}

// Plus petit "Source_N" libre (réutilise les trous : si Source_2 est supprimée, elle redevient disponible)
function incrementer() {
    const pris = nomsPris()
    let n = 1
    while (pris.has('source_' + n)) n++
    return 'Source_' + n
}

function fermerSocket(framename) {
    const ws = sockets[framename]
    delete sockets[framename] // les handlers de cette socket deviennent "périmés" et s'ignorent
    if (ws) { try { ws.close(1000, 'cadre fermé') } catch (e) {} }
    if (donnees[framename]) donnees[framename].liste = {} // plus personne à suivre sur ce cadre
}

// Libère complètement un nom : état, socket, carte du panel droit, panels gauche/milieu
function nettoyerSource(framename) {
    listeframename = listeframename.filter(m => m !== framename)
    fermerSocket(framename)

    const carte = document.querySelector(`.sndcontainer.${framename}`)
    if (carte) carte.remove()

    const membre = allAvailableMembers.find(m => m.framename === framename)
    allAvailableMembers = allAvailableMembers.filter(m => m.framename !== framename)
    if (membre) guildFamilies.forEach(f => { f.memberIds = f.memberIds.filter(mid => mid !== membre.id) })
    guildFamilies = guildFamilies.filter(f => f.memberIds.length > 0)

    _rafraichirPanelGauche()
    _rafraichirPanelMilieu()
    AjouterPanelDroit()
}

// Attend la première frame d'un cadre puis l'ajoute aux panels.
// Si le cadre est supprimé entre-temps, ou qu'aucune frame n'arrive en 45 s,
// on abandonne et on libère le nom (sinon il restait "pris" sans source visible).
function attendrePremiereFrame(framename, source = 'url') {
    const debut = Date.now()
    const attendre = setInterval(() => {
        if (!listeframename.includes(framename)) { clearInterval(attendre); return }
        if (donnees[framename] && donnees[framename].src) {
            clearInterval(attendre)
            ajouterMembre({ framename, source, lien: donnees[framename].src })
        } else if (Date.now() - debut > 45000) {
            clearInterval(attendre)
            nettoyerSource(framename)
        }
    }, 200)
}
function connectStream(framename,lien) {
    // Concernant le mode caméra, on va juste se concentrer sur le fait qu'il aura un lenght , le lien.lenght ==1
    chemin = `${wsProto}://${domain}/ws/video/${framename}`
    const ws = new WebSocket(chemin);
    sockets[framename] = ws
    donnees[framename] = {} // On crée le diction pour framename
  ws.onopen = ()=>{
    data = {'type':'url','message':lien,'framename':framename}
    data = JSON.stringify(data)
    ws.send(data)
    console.log('Connexion ouverte, donnée envoyée')

    liendatabase[framename]=lien // Enregistrement dans la base de données des liens des images 

  }
  

  ws.onmessage = (e) => {
    if (sockets[framename] !== ws) return // socket d'un cadre supprimé ou remplacé
    data = JSON.parse(e.data)
    if (data.type==='stoperror'){
        // Ici, ce sera comme pour dire si le lien s'est arrêté parce que le flux n'existe plus, il faut faire ceci 
        donnees[framename].src = `${base_url}img/flux_stop.png`
        // Trouver l'img correspondante à ce framename et la mettre à jour
        const imgs = document.querySelectorAll(`img.${framename}`)
        for (img of imgs){
        if (img) img.src = donnees[framename].src
                }
    }

    else if(data.type==='fin'){
        supprimermembrewithname(framename) // Pour fermer l'onglet ou la frame de la camera quand les dix essaies de tentative de reconnexion sont épuisés 
        donnees[framename].liste = []
        ws.close(4001,'erreur de fin')
    }

    else if (data.type==='stream'){
      src = 'data:image/jpeg;base64,' + data.message;
      const base_personnes = data.liste  // Ici, nous avons plutôt la base des id comme keys et en valeur une liste de la couleur et aussi du nom de la personne 
      if (listeframename.includes(framename)){
            donnees[framename].src=src 
             
            // Trouver l'img correspondante à ce framename et la mettre à jour
            const imgs = document.querySelectorAll(`img.${framename}`)
            for (img of imgs){
            if (img) img.src = 'data:image/jpeg;base64,' + data.message
                 }
            try{
                donnees[framename].liste= base_personnes
                
                }
               
            catch(e){ }
            ajoutersurpaneldroit(framename,donnees[framename].src,data.liste)
    }
      else{
        ws.close(4000,"La frame n'existe plus ")
        donnees[framename].liste = []
        try{
            listeframename.filter(m =>m!==framename)
            
        }
        catch(e){
        
        }
      }
    }
    
  };

  ws.onclose = () => {
   if (sockets[framename] !== ws) return // socket d'un cadre supprimé ou remplacé
   // ON met un photo à l'écran pour dire que le flux s'est coupé 
    donnees[framename].src = `${base_url}img/flux_stop.png`
    // Trouver l'img correspondante à ce framename et la mettre à jour
    donnees[framename].liste = []
    const imgs = document.querySelectorAll(`img.${framename}`)
    for (img of imgs){
    if (img) img.src = donnees[framename].src
            }
    console.log("Connexion coupée")
  };

  ws.onerror = (err) => console.error(`Cam ${framename} erreur:`, err);
}

document.querySelector('.people_add').addEventListener('click',()=>{
    window.location.pathname='ajouter/'
})


    async function geturllist(){ // Cette function permet d'avoir la liste des urls
    try{
    const response = await fetch('listesource/',{method:'GET',headers:{
        'Content-Type':'application/json',
        'X-CSRFToken':csrfToken
    }})
    const data= await response.json()
    //console.log(data)
    const liste = data.liste || []
    //console.log(liste)
    return liste
    }
    
    catch(e){
        
    }
  }


    /**
     * Ajoute un membre (cadre) dans le panel droit ET dans le panel gauche.
     * @param {Object} opts
     * @param {string} opts.framename  - Nom affiché
     * @param {string} opts.source     - 'url' | 'image' | 'video' | 'webcam'
     * @param {string} [opts.lien]     - URL de l'image/flux (optionnel)
     * @returns {number} id du membre créé
     */
    function ajouterMembre({ framename, source, lien = '' }) {
        const id = _nextMemberId++;
        const membre = { id, framename, source, lien };

        allAvailableMembers.push(membre);

        // Crée ou réutilise une famille portant le nom du cadre
        const familleId = framename.toLowerCase().replace(/\s+/g, '-');
        let famille = guildFamilies.find(f => f.id === familleId);
        if (!famille) {
            famille = { id: familleId, memberIds: [] };
            guildFamilies.push(famille);
        }
        famille.memberIds.push(id);

        // Met à jour les deux panels
        _rafraichirPanelGauche();
        _rafraichirPanelMilieu();
        AjouterPanelDroit()
        return id;
    }

    /**
     * Supprime un membre par son id.
     * @param {number} id
     */
    function supprimerMembre(id) {
        const membre = allAvailableMembers.find(m => m.id === id)
        if (!membre) return
        nettoyerSource(membre.framename)
    }

    // Supprime une fenêtre grâce au nom de la caméra (ex. fin des tentatives de reconnexion)
    function supprimermembrewithname(framename){
        nettoyerSource(framename)
    }

    // ============================================================
    //  RENDU PANEL GAUCHE # Droite maintenant
    // ============================================================

    // Avatar d'une personne reconnue : photo si disponible, sinon initiales.
    // (Quand le backend enverra la photo pour ce panel, il suffira de passer son URL ici.)
    function creerAvatarPersonne(nom, photo) {
        const wrap = document.createElement('div')
        wrap.classList.add('avatarpersonne')
        const initiales = () => { wrap.textContent = initialesNom(nom) }
        if (photo) {
            const img = document.createElement('img')
            img.src = urlPhoto(photo)
            img.alt = formatNom(nom)
            img.onerror = () => { img.remove(); initiales() }
            wrap.appendChild(img)
        } else {
            initiales()
        }
        return wrap
    }

    // Crée la carte d'un cadre dans le panel droit (si elle n'existe pas encore)
    function creerCarteCadre(framename, src) {
        if (document.querySelector(`.sndcontainer.${framename}`)) return
        const container = document.getElementById('members-list-container')

        const sndcontainer = document.createElement('div')
        sndcontainer.classList.add('sndcontainer', framename)

        const trdcontainer = document.createElement('div')
        trdcontainer.classList.add('trdcontainer')

        // IMPORTANT : la classe framename permet à connectStream() de rafraîchir
        // cette image comme toutes les autres (img.${framename})
        const frameavatar = document.createElement('img')
        frameavatar.classList.add('frameavatar', framename)
        frameavatar.src = src

        const titre = document.createElement('div')
        titre.classList.add('cadre-titre')
        const nomCadre = document.createElement('span')
        nomCadre.classList.add('infos')
        nomCadre.textContent = framename
        const sous = document.createElement('span')
        sous.classList.add('cadre-sous')
        sous.innerHTML = `<span class="cadre-count">0</span> reconnue(s)`
        titre.append(nomCadre, sous)

        trdcontainer.append(frameavatar, titre)

        const liste = document.createElement('div')
        liste.classList.add('persons-list')

        sndcontainer.append(trdcontainer, liste)
        container.appendChild(sndcontainer)
    }

    // Synchronise les personnes reconnues d'un cadre (ajoute / retire sans tout reconstruire)
    function synchroniserPersonnes(framename, liste) {
        const carte = document.querySelector(`.sndcontainer.${framename}`)
        if (!carte || !liste) return
        const zone = carte.querySelector('.persons-list')
        const existant = []

        for (const [nom, couleur] of Object.entries(liste)) {
            existant.push(nom)
            let ligne = Array.from(zone.children).find(el => el.dataset.nom === nom)
            if (!ligne) {
                ligne = document.createElement('div')
                ligne.classList.add('divpersonnecontainer')
                ligne.dataset.nom = nom
                const infos = document.createElement('span')
                infos.classList.add('infos')
                infos.textContent = formatNom(nom)
                const pastille = document.createElement('i')
                pastille.classList.add('personne-pastille')
                ligne.append(creerAvatarPersonne(nom), infos, pastille)
                zone.appendChild(ligne)
            }
            ligne.style.setProperty('--c', couleur || '#64748b')
        }

        Array.from(zone.children)
            .filter(el => !existant.includes(el.dataset.nom))
            .forEach(el => el.remove())

        carte.querySelector('.cadre-count').textContent = zone.children.length
    }

    function ajoutersurpaneldroit(framename, src, liste) {
        if (!liste) return;
        creerCarteCadre(framename, src)
        synchroniserPersonnes(framename, liste)
    }

    function AjouterPanelDroit() {
        listeframename.forEach(framename => {
            if (!donnees[framename] || !donnees[framename].src) return // pas encore de frame
            creerCarteCadre(framename, donnees[framename].src)
            if (!donnees[framename].liste) return
            synchroniserPersonnes(framename, donnees[framename].liste)
        })
    }


    // ============================================================
    //  RENDU PANEL MILIEU
    //  activeFocusId = null  → mode grille égale
    //  activeFocusId = N     → mode focus : grande frame + bande bas
    // ============================================================
    let activeFocusId = null;

    function _rafraichirPanelMilieu() {
        const mainVideoArea = document.getElementById('main-video-area');
        const bottomStrip   = document.getElementById('bottom-strip');
        mainVideoArea.innerHTML = '';
        bottomStrip.innerHTML  = '';

        if (allAvailableMembers.length === 0) {
            activeFocusId = null;
            bottomStrip.classList.add('hidden');
            mainVideoArea.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">📭</div>
                    <h3>Aucune source active</h3>
                    <p>Ajoutez une source via le bouton "Ajouter +" dans le panel gauche.</p>
                </div>`;
            return;
        }

        // Si le focus pointe vers un membre supprimé, on reset
        if (activeFocusId !== null && !allAvailableMembers.find(m => m.id === activeFocusId)) {
            activeFocusId = null;
        }

        if (activeFocusId !== null) {
            // -------- MODE FOCUS --------
            bottomStrip.classList.remove('hidden');
            const focused = allAvailableMembers.find(m => m.id === activeFocusId);

            // Grande frame
            const frame = document.createElement('div');
            frame.className = 'focus-frame';

            const mediaHTML = focused.lien
                ? `<img class="focus-img ${focused.framename}" src="${donnees[focused.framename].src}" alt="${focused.framename}">`
                : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f1a;color:#334155;font-size:16px;">${focused.framename}</div>`; 

            frame.innerHTML = `
                ${mediaHTML}
                <button class="focus-close-btn ${focused.framename} " onclick="event.stopPropagation();supprimerMembre(${focused.id})" title="Fermer">${_svgClose(16)}</button>
                <div class="focus-label">
                    <span class="card-label-dot" style="background:#23a55a"></span>
                    <span class="focus-label-name">${focused.framename}</span>
                </div>
            `;

            // Clic sur la grande frame → retour grille
            frame.addEventListener('click', () => {
                activeFocusId = null;
                _rafraichirPanelMilieu();
            });

            mainVideoArea.appendChild(frame);

            // Miniatures bande du bas
            allAvailableMembers.forEach(m => {
                const isActive = m.id === activeFocusId;
                const strip = document.createElement('div');
                strip.className = 'strip-card' + (isActive ? ' active' : '');

                const stripMedia = m.lien
                    ? `<img class="strip-img ${m.framename}" src="${donnees[m.framename].src}" alt="${m.framename}">`
                    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f1a;color:#475569;font-size:11px;">${m.framename}</div>`;

                strip.innerHTML = `
                    ${stripMedia}
                    <div class="strip-label">${m.framename}</div>
                    <button class="strip-close-btn ${m.framename}" onclick="event.stopPropagation();supprimerMembre(${m.id})" title="Fermer">${_svgClose(10)}</button>
                `;

                strip.addEventListener('click', () => {
                    activeFocusId = isActive ? null : m.id;
                    _rafraichirPanelMilieu();
                });

                bottomStrip.appendChild(strip);
            });

        } else {
            // -------- MODE GRILLE ÉGALE --------
            bottomStrip.classList.add('hidden');

            const grid = document.createElement('div');
            const count = Math.min(allAvailableMembers.length, 6);
            grid.className = `video-grid count-${count}`;

            allAvailableMembers.forEach(m => {
                const card = document.createElement('div');
                card.className = 'video-card';
                card.dataset.id = m.id;

                const mediaHTML = m.lien
                    ? `<img class="video-card-img ${m.framename}" src="${donnees[m.framename].src}" alt="${m.framename}">`
                    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f1a;color:#334155;font-size:13px;">${m.framename}</div>`;

                card.innerHTML = `
                    ${mediaHTML}
                    <button class="card-close-btn ${m.framename}" onclick="event.stopPropagation();supprimerMembre(${m.id})" title="Fermer le cadre">${_svgClose(12)}</button>
                    <div class="card-label">
                        <span class="card-label-dot" style="background:#23a55a"></span>
                        <span class="card-label-name">${m.framename}</span>
                    </div>
                `;

                // Clic sur une card → passer en mode focus
                card.addEventListener('click', () => {
                    activeFocusId = m.id;
                    _rafraichirPanelMilieu();
                });

                grid.appendChild(card);
            });

            mainVideoArea.appendChild(grid);
        }
    }

    // ============================================================
    //  RENDU PANEL DROIT # Gauche maintenant
    // ============================================================
    function _rafraichirPanelGauche() {
        const container  = document.getElementById('channel-items-container'); 
        
        container.innerHTML = '';
        

        guildFamilies.forEach(famille => {
            const familleCard = document.createElement('div');
            familleCard.className = 'family-card';

            familleCard.innerHTML = `
                <div class="family-header">Cadre</div>
                <div class="family-members-sub" id="sub-${famille.id}"></div>
            `;
            container.appendChild(familleCard);

            const sub = document.getElementById(`sub-${famille.id}`);

            famille.memberIds.forEach(mid => {
                const m = allAvailableMembers.find(x => x.id === mid);
                if (!m) return;

                const el = document.createElement('div');
                el.className = 'member-card';
                el.id = `member-card-${m.id}`;

                const avatarHTML = m.lien
                    ? `<img class="member-avatar ${m.framename}" src="${donnees[m.framename].src}" alt="${m.framename}">`
                    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#1e293b;color:#94a3b8;font-size:11px;font-weight:700;">${m.framename[0]}</div>`;

                el.innerHTML = `
                    <div class="member-left">
                        <div class="member-avatar-wrap">${avatarHTML}</div>
                        <div class="member-info">
                            <div class="member-name">${m.framename}</div>
                            <div class="member-status-text">${m.source}</div>
                        </div>
                    </div>
                    <div class="member-right">
                        <button class="member-action-close ${m.framename}" onclick="supprimerMembre(${m.id})" title="Retirer">
                            ${_svgClose(14)}
                        </button>
                    </div>
                `;

                sub.appendChild(el);
            });
        });
    }

    // ============================================================
    //  UTILITAIRE SVG
    // ============================================================
    function _svgClose(size) {
        return `<svg width="${size}" height="${size}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2.5" d="M6 18L18 6M6 6l12 12"></path>
        </svg>`;
    }

    // ============================================================
    //  POPUP — TOGGLE
    // ============================================================
    function togglePopup(show) {
        const popup    = document.getElementById('camera-popup');
        const feedback = document.getElementById('feedback-info');
        if (show) {
            popup.classList.add('montrer');
            feedback.innerText = '';
            // Le popup est en display:none jusqu'à cet instant : on attend le rendu pour pouvoir
            // donner le focus, puis on sélectionne le nom pour le remplacer en tapant directement.
            const champ = document.getElementById('cam-name');
            requestAnimationFrame(() => {
                champ.focus({ preventScroll: true });
                champ.select();
            });
        } else {
            popup.classList.remove('montrer');
            stopWebcam();
            resetForm();
        }
    }

    // ============================================================
    //  POPUP — ONGLETS
    // ============================================================
    function switchMethod(method) {
        currentMethod = method;
        stopWebcam();

        const tabs   = { url:'tab-url',    image:'tab-image',   video:'tab-video',   webcam:'tab-webcam' };
        const fields = { url:'field-url',  image:'field-image', video:'field-video', webcam:'field-webcam' };

        Object.keys(tabs).forEach(key => {
            document.getElementById(tabs[key]).classList.remove('actif');
            document.getElementById(fields[key]).classList.add('hidden');
        });

        document.getElementById(tabs[method]).classList.add('actif');
        document.getElementById(fields[method]).classList.remove('hidden');

        const urlInput   = document.getElementById('url-input');
        const imgInput   = document.getElementById('file-image-input');
        const videoInput = document.getElementById('file-video-input');

        urlInput.removeAttribute('required');
        imgInput.removeAttribute('required');
        videoInput.removeAttribute('required');

        if (method === 'url')        urlInput.setAttribute('required','required');
        else if (method === 'image') imgInput.setAttribute('required','required');
        else if (method === 'video') videoInput.setAttribute('required','required');
    }

    // ============================================================
    //  POPUP — PREVIEWS
    // ============================================================
    function previewImage(input) {
        const preview  = document.getElementById('image-preview');
        const plusIcon = document.getElementById('plus');
        if (input.files && input.files[0]) {
            const reader = new FileReader();
            reader.onload = e => {
                preview.src = e.target.result;
                preview.style.display = 'block';
                plusIcon.style.opacity = '0';
            };
            reader.readAsDataURL(input.files[0]);
        }
    }

    function previewVideo(input) {
        const preview     = document.getElementById('video-preview');
        const placeholder = document.getElementById('plus-video-container');
        if (input.files && input.files[0]) {
            preview.src = URL.createObjectURL(input.files[0]);
            preview.style.display = 'block';
            placeholder.style.display = 'none';
            preview.play();
        }
    }

    // ============================================================
    //  POPUP — WEBCAM
    // ============================================================
    async function toggleWebcam(start) {
        const videoEl     = document.getElementById('webcam-view');
        const placeholder = document.getElementById('webcam-placeholder');
        const badge       = document.getElementById('webcam-active-badge');
        const feedback    = document.getElementById('feedback-info');

        if (start) {
            try {
                feedback.innerText = '';
                webcamStream = await navigator.mediaDevices.getUserMedia({ video: { width:640, height:480 } });
                videoEl.srcObject = webcamStream;
                videoEl.style.display = 'block';
                placeholder.style.display = 'none';
                badge.classList.remove('hidden');
            } catch(err) {
                feedback.style.color = '#ef4444';
                feedback.innerText   = "Accès caméra refusé ou non disponible.";
            }
        } else {
            stopWebcam();
        }
    }

    function stopWebcam() {
        const videoEl     = document.getElementById('webcam-view');
        const placeholder = document.getElementById('webcam-placeholder');
        const badge       = document.getElementById('webcam-active-badge');
        if (webcamStream) { webcamStream.getTracks().forEach(t => t.stop()); webcamStream = null; }
        if (videoEl)      { videoEl.srcObject = null; videoEl.style.display = 'none'; }
        if (placeholder)  placeholder.style.display = 'flex';
        if (badge)        badge.classList.add('hidden');
    }

    // ============================================================
    //  POPUP — RESET
    // ============================================================
    function resetForm() {
        document.getElementById('cam-form').reset();
        const imgPreview = document.getElementById('image-preview');
        imgPreview.src = ''; imgPreview.style.display = 'none';
        document.getElementById('plus').style.opacity = '1';
        const videoPreview = document.getElementById('video-preview');
        videoPreview.src = ''; videoPreview.style.display = 'none';
        document.getElementById('plus-video-container').style.display = 'flex';
        switchMethod('url');
        formobjet = {};
    }

    // ============================================================
    //  POPUP — SOUMISSION
    //  handleSubmit valide, stocke dans formobjet, puis appelle
    //  ajouterMembre() pour injecter dans les panels.
    // ============================================================
    async function handleSubmit(event) {
        event.preventDefault();
        const feedback = document.getElementById('feedback-info');
        const champNom = document.getElementById('cam-name');
        let camName    = sanitizeName(champNom.value);
        // Le nom proposé automatiquement a pu être pris entre-temps (connexion auto) : on prend le suivant libre
        if (champNom.value === nomAutoPropose && camName && nomEstPris(camName)) {
            camName = incrementer();
            champNom.value = camName;
        }

        if (!camName) {
            feedback.style.color = '#ef4444';
            feedback.innerText   = "Veuillez entrer un nom pour la caméra.";
            return;
        }
        if (nomEstPris(camName)){
            feedback.style.color = '#ef4444';
            feedback.innerText   = `Le nom « ${camName} » est déjà utilisé. Essayez « ${incrementer()} ».`;
            return;
        }

        let lien = '';

        if (currentMethod === 'url') {
            const urlVal = document.getElementById('url-input').value;
            if (!urlVal) { feedback.style.color='#ef4444'; feedback.innerText="Veuillez entrer une URL."; return; }
            // Configuration pour pouvoir faire un loading page ici 
            bouton.innerHTML = ''
            bouton.classList.remove('confirmer')
            bouton.classList.add('loader')
            bouton.disabled = true
            feedback.innerText=""

            try{const checker = await checkLink(urlVal)
            if (!checker) {feedback.style.color='#ef4444'; feedback.innerText="L'URL entrée n'est pas valide "; return;}
            } 
            catch(e){log(e)
                donnees
            }
            finally{
                bouton.classList.remove('loader') 
                bouton.classList.add('confirmer')
                bouton.innerHTML = oldbouton
                bouton.disabled = false 
                // delete checker ; // Je ne veux pas avoir d'erreur bizarre après 
            }
            
            
            lien = urlVal;
            formobjet = { camName, urlVal, source: 'url' };

        } else if (currentMethod === 'image') {
            const file = document.getElementById('file-image-input').files[0];
            if (!file) { feedback.style.color='#ef4444'; feedback.innerText="Veuillez sélectionner une image."; return; }
            lien = URL.createObjectURL(file);
            formobjet = { camName, file, source: 'image' };

        } else if (currentMethod === 'video') {
            const file = document.getElementById('file-video-input').files[0];
            if (!file) { feedback.style.color='#ef4444'; feedback.innerText="Veuillez sélectionner une vidéo."; return; }
            lien = URL.createObjectURL(file);
            formobjet = { camName, file, source: 'video' };

        } else if (currentMethod === 'webcam') {
            if (!webcamStream) { feedback.style.color='#ef4444'; feedback.innerText="Veuillez d'abord activer la caméra."; return; }
            formobjet = { camName, source: 'webcam' };
        }

        // Feedback succès
        feedback.style.color = '#10b981';
        feedback.innerText   = `Cadre "${camName}" configuré avec succès !`;

        // Envoi serveur (si Django disponible)
        _envoyerServeur();

        setTimeout(() => togglePopup(false), 1500);
    }

    // ============================================================
    //  ENVOI SERVEUR (Django) — ne bloque pas l'UI
    // ============================================================
    let csrfInput = document.getElementsByName("csrfmiddlewaretoken")[0];
    let csrfToken = csrfInput ? csrfInput.value : '';
    async function _envoyerServeur() {
        const feedback = document.getElementById('feedback-info');
        feedback.innerText=""
        try {
        if (!csrfToken) return; // Pas en contexte Django, on skip
        // Ici, le loading page 
            bouton.innerHTML = ''
            bouton.classList.remove('confirmer')
            bouton.classList.add('loader')
            bouton.disabled = true
            const formdata = new FormData();

            if (formobjet.source === 'url'){    
                formdata.append('url', formobjet.urlVal);
                   // — accède à la clé dynamique
                        const cam = formobjet.camName
                        
                        // Ici, j'établis la connexion 
                        connectStream(cam,formobjet.urlVal)

                        // Attendre que la première frame arrive
                        attendrePremiereFrame(cam, 'url')

                        listeframename.push(cam)
            }

            if (formobjet.source === 'webcam'){    
                        stopWebcam()
                   // — accède à la clé dynamique
                        const cam = formobjet.camName
                        listeframename.push(cam)
                        // J'arrête la caméra au niveau de chrome d'abord 
                        stopWebcam();
                        // Ici, j'établis la connexion 
                        connectStream(cam,'0')

                        // Attendre que la première frame arrive et aussi on va considérer que la caméra est un lien, parce que ça proviendra du serveur, le lien d'analyse 
                        attendrePremiereFrame(cam, 'url')
            }


            if (formobjet.file) formdata.append('file', formobjet.file);
        if (currentMethod==='image'){
            formdata.append('source','image')
            const data    = await fetch(`${formobjet.camName}`, { method:'POST', headers:{ 'X-CSRFToken': csrfToken }, body: formdata });
            
            
            const reponse = await data.json();

            // La réponse est sous cette forme
            // return JsonResponse({'name':name,'url':chemin,'source':source,'liste':liste_personnes})
            listeframename.push(reponse.name)
            donnees[reponse.name] = donnees[reponse.name]||{}
            donnees[reponse.name].liste = reponse.liste 
            donnees[reponse.name].src=reponse.url

           

            ajouterMembre({ framename: reponse.name, source:reponse.source, lien:reponse.url })

            console.log('[Serveur]', reponse);
            }
        } catch(e) {
            console.log('[Serveur] Envoi échoué (mode standalone ?)', e);
            // alert("Erreur rencontré lors de la requête")
        }
        finally{
            bouton.classList.remove('loader')
            bouton.classList.add('confirmer')
            bouton.innerHTML = oldbouton
            bouton.disabled = false 
        }
    }

    // ============================================================
    //  RESIZERS
    // ============================================================
    const leftPanel  = document.getElementById('left-panel');
    const rightPanel = document.getElementById('right-panel');
    const resizer1   = document.getElementById('resizer1');
    const resizer2   = document.getElementById('resizer2');
    const resizer3   = document.getElementById('resizer3');
    const resultWindow = document.getElementById('window');
    let isResizingLeft = false, isResizingRight = false, isResizingWindow = false;

    resizer1.addEventListener('pointerdown', e => { isResizingLeft=true;  resizer1.classList.add('resizing'); document.body.style.cursor='col-resize'; e.preventDefault(); });
    resizer2.addEventListener('pointerdown', e => { isResizingRight=true; resizer2.classList.add('resizing'); document.body.style.cursor='col-resize'; e.preventDefault(); });
    resizer3.addEventListener('pointerdown', e => { isResizingWindow=true; resizer3.classList.add('resizing'); document.body.style.cursor='row-resize'; e.preventDefault(); });

    document.addEventListener('pointermove', e => {
        if (isResizingLeft) {
            const w = e.clientX;
            if (w >= 180 && w <= 380) leftPanel.style.width = w + 'px';
        } else if (isResizingRight) {
            const w = window.innerWidth - e.clientX;
            if (w >= 220 && w <= 480) rightPanel.style.width = w + 'px';
        } else if (isResizingWindow) {
            // .window est ancrée en bas (bottom:3px), donc on calcule la hauteur
            // à partir de la position verticale de la souris jusqu'au bas de l'écran.
            const bottomOffset = 3;   // doit correspondre à "bottom:3px" dans le CSS de .window
            const headerHeight = 48;  // hauteur de #app-header (voir calc(100vh - 48px))
            const minHeight = 100;    // hauteur mini du panneau de résultats
            const maxHeight = window.innerHeight - headerHeight - 120; // on garde toujours de la place pour la vidéo

            let h = window.innerHeight - e.clientY - bottomOffset;
            h = Math.min(Math.max(h, minHeight), Math.max(maxHeight, minHeight));

            resultWindow.style.height = h + 'px';

            // On resynchronise #app-container pour que la zone vidéo ne soit jamais
            // masquée derrière le panneau de résultats (même logique que le clic sur la loupe).
            if (traking) {
                document.getElementById('app-container').style.height = `calc(100vh - ${h + bottomOffset + 10}px)`;
            }
        }
    });

    document.addEventListener('pointerup', () => {
        isResizingLeft = isResizingRight = isResizingWindow = false;
        resizer1.classList.remove('resizing');
        resizer2.classList.remove('resizing');
        resizer3.classList.remove('resizing');
        document.body.style.cursor = 'default';
    });

    // ============================================================
    //  BOUTON AJOUTER SOURCE
    // ============================================================
    document.getElementById('btn-ajouter-source').addEventListener('click', () => {
        nomAutoPropose = incrementer()
        document.getElementById('cam-name').value = nomAutoPropose
        togglePopup(true)
    });



function sanitizeName(name) {
  // Le nom sert aussi de classe CSS (img.NomDuCadre) : il doit rester "sûr".
  let n = String(name ?? '')
    .normalize('NFD')
    .replace(/\p{M}/gu, '')        // retire les accents
    .trim()
    .replace(/\s+/g, '')           // pas d'espaces
    .replace(/[^\p{L}\d_-]/gu, '') // lettres, chiffres, _ et - uniquement (plus d'apostrophe)
    .replace(/^-+/, '');           // ne commence pas par un tiret
  if (/^\d/.test(n)) n = 'Source_' + n; // une classe CSS ne peut pas commencer par un chiffre
  return n;
}

async function checkLink(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 3000); // 3 secondes max
    try {
        await fetch(url, { method: 'HEAD', mode: 'no-cors', signal: controller.signal });
        return true;
    } catch (error) {
        return false;
    } finally {
        clearTimeout(timer); // nettoyage du timer dans tous les cas
    }
}

    // Les fonctions pour mon téléchargement de la liste excel des présents 

    
const input       = document.getElementById('date-input');
const labelDate   = document.getElementById('label-date');
const labelStats  = document.getElementById('label-stats');
let listeContent = document.getElementById('liste-content');

const jours = ['Dimanche','Lundi','Mardi','Mercredi','Jeudi','Vendredi','Samedi'];
const mois  = ['janvier','février','mars','avril','mai','juin','juillet','août','septembre','octobre','novembre','décembre'];

function formatDate(d) {
  return `${jours[d.getDay()]} ${d.getDate()} ${mois[d.getMonth()]} ${d.getFullYear()}`;
}

function dateToStr(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,'0');
  const j = String(d.getDate()).padStart(2,'0');
  return `${y}-${m}-${j}`;
}

function changerJour(delta) {
  const d = new Date(input.value + 'T00:00:00');
  d.setDate(d.getDate() + delta);
  input.value = dateToStr(d);
  mettreAJour();
}

function allerAujourdhui() {
  input.value = dateToStr(new Date());
  mettreAJour();
}

function mettreAJour() {
  cacherBulle();
  const d = new Date(input.value + 'T00:00:00');
  labelDate.textContent = `${!document.getElementById('cb1-6').checked ? formatDate(d):"Toutes les dates"}`;
  labelStats.textContent = '';

  listeContent.innerHTML = `
    <div class="etat-center">
      <svg class="spin" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-6.219-8.56"/>
      </svg>
      <p>Chargement…</p>
    </div>
  `;
    if ( document.getElementById('cb1-6').checked){
        chargerPresence('all');
    }
    else{
  chargerPresence(input.value);}
}
let dataactuelle 
function chargerPresence(dateStr) {
    dataactuelle = dateStr
  fetch(`presence/?date=${dateStr}`, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' }
  })
  .then(r => { if (!r.ok) throw new Error(); return r.json(); })
  .then(data => afficherPresence(data))
  .catch(() => afficherErreur());
}
let personnes
function afficherPresence(data) {
  cacherBulle();
  // Nouveau format : liste d'objets { source, user__username, heure, date, user__profile__photo }
  personnes = Array.isArray(data) ? data : (data.personnes || data.liste || data.data || []);
  const toutesDates = document.getElementById('cb1-6').checked;

  const uniques = new Set(personnes.map(p => p.user__username));
  labelStats.textContent = `${uniques.size} ${uniques.size > 1 ? 'Personnes' : 'Personne'}`;

  if (personnes.length === 0) {
    listeContent.innerHTML = `
      <div class="etat-center">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true">
          <circle cx="12" cy="8" r="4"/><path d="M6 20v-1a6 6 0 0 1 12 0v1"/>
          <line x1="17" y1="17" x2="22" y2="22"/>
        </svg>
        <p>Aucune donnée pour cette date.</p>
      </div>
    `;
    return;
  }

  listeContent.innerHTML = personnes.map(p => {
    const nom       = formatNom(p.user__username);
    const initiales = initialesNom(p.user__username);
    const photo     = urlPhoto(p.user__profile__photo);
    const source    = echapperHtml(p.source ?? p.Source ?? '');
    const heure     = p.heure ? `<span class="heure">${echapperHtml(p.heure)}</span>` : '';
    const avatar    = photo
      ? `<div class="avatar present avatar-photo"><img src="${echapperHtml(photo)}" alt="${echapperHtml(nom)}" loading="lazy" onerror="this.parentNode.classList.remove('avatar-photo');this.parentNode.textContent='${initiales}'"></div>`
      : `<div class="avatar present">${initiales}</div>`;
    return `
      <div class="row" title="${toutesDates ? 'Toutes les dates' : echapperHtml(p.date)}">
        ${avatar}
        <div class="row-info">
          <div class="row-nom">${echapperHtml(nom)}</div>
        </div>
        <div class="row-right">
          ${heure}
          ${toutesDates ? `<span class="badge badge-date">${echapperHtml(p.date)}</span>` : ''}
          <span class="badge">${source}</span>
        </div>
      </div>
    `;
  }).join('');
}



function afficherErreur() {
  labelStats.textContent = 'Erreur réseau';
  listeContent.innerHTML = `
    <div class="etat-center">
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="1.5" stroke-linecap="round" aria-hidden="true">
        <line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"/><path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"/>
        <path d="M10.71 5.05A16 16 0 0 1 22.56 9"/><path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/>
      </svg>
      <p>Impossible de contacter le serveur.</p>
    </div>
  `;
}

// Survol d'une photo dans la liste de présence : même bulle que le tracking, décalée à gauche
listeContent.addEventListener('mouseover', e => {
    const av = e.target.closest('.avatar-photo')
    if (!av) return
    const img = av.querySelector('img')
    if (img) montrerBulle(img.currentSrc || img.src, av, 'gauche')
})
listeContent.addEventListener('mouseout', e => {
    const av = e.target.closest('.avatar-photo')
    if (av && !av.contains(e.relatedTarget)) cacherBulle()
})
listeContent.addEventListener('scroll', cacherBulle)

document.querySelector('.date-picker-wrap').addEventListener('click',()=>{
    const a = document.querySelector('#date-input')
    a.showPicker()
})

// Fonction du bouton retour 
document.querySelector('.retour').addEventListener('click',()=>{window.location.pathname=''})

// Afficher le menu

document.querySelector('.menu-t').addEventListener('click',()=>{
    document.querySelector('.overlay').style.display='flex'
    mettreAJour()
   
})
 document.getElementById('cb1-6').addEventListener('change',()=>{
    mettreAJour()
 })
// Fermer la fenêtre de téléchargement 
document.querySelector('.overlay').addEventListener('click',(e)=>{
    if (e.target==document.querySelector('.overlay')){
        document.querySelector('.overlay').style.display='none';
        cacherBulle();
        document.getElementById('cb1-6').checked = false
       
    }
})

document.querySelector('.telecharger').addEventListener('click',()=>{
    value = document.getElementById('cb1-6').checked 
    if (value){dataactuelle='all'}
    window.location.href=`telecharger/?date=${dataactuelle}`
})

input.addEventListener('change', mettreAJour);
input.value = dateToStr(new Date());
// mettreAJour();

// Ici, nous allons faire notre fonction pour pouvoir ouvrire les sources et  la caméra en même temps que l'on vient sur la page 

function ouvrirsourcefirst(framename,url){
    
    listeframename.push(framename)
    
    // Ici, j'établis la connexion 
    connectStream(framename,url)

    // Attendre que la première frame arrive et aussi on va considérer que la caméra est un lien, parce que ça proviendra du serveur, le lien d'analyse 
    attendrePremiereFrame(framename, 'url')
}


// async function connexionautomatique(){
//     // Cette fonction me permet de faire le fecth, ensuite la connexion automatique aux urls déjà enregistré

//     const pause = (ms) => new Promise(resolve => setTimeout(resolve, ms));
//     try{
//         const urlgetter = await geturllist()||[]
//         const listeurl = Object.values(liendatabase)
//     if(urlgetter.length===0) return ; // S'il n'y a pas de liste, alors on continue notre chemin 
//     for (const url of urlgetter){ 
//         const checker = await checkLink(url)
//         if(!checker) continue; // Dans ce cas, on ne fait plus l'ouverture , on saute en même temps l'étape 
//         if (listeurl.includes(url)) continue  // Si l'url est déjà enregistré parmi ceux déjà checké, alors on passe notre chemin
//         ouvrirsourcefirst(incrementer(),url)
        
//         await pause(2000) // Attends 2s d'abord
//     }
//     }
//     catch(e){
//         console.log(e)
//     }

// }



let _connexionEnCours = false; // verrou

async function connexionautomatique() {
    if (_connexionEnCours) return; // déjà en train de tourner → on ignore
    _connexionEnCours = true;

    const pause = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    try {
        const listeurl = Object.values(liendatabase)
        const urlgetter = await geturllist() || [];
        if (urlgetter.length === 0) return;

        for (const url of urlgetter) {
            if (listeurl.includes(url)) continue;

            const checker = await checkLink(url);
            if (!checker) continue;

            ouvrirsourcefirst(incrementer(), url);
            listeurl.push(url);
            await pause(5000);
        }
    } catch (e) {
        
    } finally {
        _connexionEnCours = false; // libère le verrou dans tous les cas
        console.log("J'ai déjà fini l'essai avec cette une source")
    }
    
}


function connexionlimite(){
    const trylimit = setInterval(connexionautomatique,6000)
    setTimeout(()=>{
        clearInterval(trylimit),console.log('Les 30s sont terminés')
    },60000)
}



// Ici, les fonctions pour la méthode rechercher 
const namerechercher = document.querySelector('.mid-header-left-text')
const filerechercher = document.querySelector('.mid-header-left-file')
const illusion = document.querySelector('.illusion')
const stoptraking = document.querySelector('.mid-header-stop')
const conteneur = document.querySelector('#video-stage-container')

// Cette fonction va me permettre de ne pas afficher tous le datalist lorsqu'on clique sur l'input namerechercher

// 1. Au clic ou au focus : on retire l'attribut pour bloquer l'affichage automatique
namerechercher.addEventListener('focus', () => {
    namerechercher.removeAttribute('list');
});

// 2. Dès que l'utilisateur tape une touche : on remet l'attribut pour filtrer
namerechercher.addEventListener('input', () => {
    // On ne remet la liste que si l'input n'est pas vide
    if (namerechercher.value.trim() !== "") {
        namerechercher.setAttribute('list', 'suggestions');
    } else {
        namerechercher.removeAttribute('list');
    }
});

// 3. Si l'utilisateur clique en dehors (blur) : sécurité pour nettoyer l'état
namerechercher.addEventListener('blur', () => {
    namerechercher.removeAttribute('list');
});



let trakingnumber
illusion.addEventListener('click',()=>{
    illusion.style.display='none'
    namerechercher.style.display='block'
    filerechercher.style.display='block'
    stoptraking.style.display = 'block'
    document.querySelector('.window').style.display='block'
    document.querySelector('#app-container').style.height='75vh'
    traking = true 
    trakingnumber = setInterval(()=>{
        
        rechercher_par_nom(namerechercher.value.trim())
        
        
    },500)
})

stoptraking.addEventListener('click',()=>{
    traking = false 
    clearInterval(trakingnumber)
    illusion.style.display = 'block'
    namerechercher.style.display='none'
    filerechercher.style.display='none'
    stoptraking.style.display = 'none'
    document.querySelector('.window').style.display='none'
    document.querySelector('#app-container').style.height='100vh'
    document.querySelectorAll('img').forEach(m=>{m.classList.remove('actifs')})
    namerechercher.value=''
    document.getElementsByClassName('uploadbox-input')[0].value=''
    document.querySelector('.uploadbox-preview').src=''
    televerser.src = ancienne_image
    cacherBulle()
})


let traker = {} // On crée un objet pour pouvoir enregistrer les valeurs des gens 

// Donnees  = {framename:{src:lien,liste:{'canisius',couleur}}}
function rechercher_par_nom(noms) {
  // Correction 1 — split + map + filter en une seule chaîne
  const searching = noms
    .split('+')
    .map(m => cleNom(m))
    .filter(m => m.length > 0);
    if( searching.length===0) return
    // Une fois qu'il y a une données chercher par l'utilisateur, alors l'input file de recherche par image devient null 
    
  const frametrouve = [];
  if (!donnees) return;

  for (let [key, value] of Object.entries(donnees)) {
    document.querySelectorAll(`img.${key}, video.${key}`)
      .forEach(el => el.classList.remove('actifs'));

    const liste = Object.keys(value?.liste ?? {}).map(e => cleNom(e));

    // Correction 2 — for...of au lieu de for...in
    for (const nom of searching) {
      if (liste.includes(nom)) {
        traker[nom] = {}
        // Correction 3 — éviter les doublons
        if (!frametrouve.includes(key)) {
          frametrouve.push(key);
          const dii = new Date()
          traker[nom].source = key // Enregistrement dans traker
          traker[nom].temps = dii.toLocaleTimeString()
          // Maintenant, on fait l'ajout sur le tableau des trakers
          updateroradd(traker[nom].source,nom,traker[nom].temps)
        }
        break; // un match suffit pour cette frame
      }
    }
  }

  frametrouve.forEach(framekey => {
    document.querySelectorAll(`img.${framekey}, video.${framekey}`)
      .forEach(el => el.classList.add('actifs'));
  });
}

namerechercher.addEventListener('input',()=>{
    document.getElementsByClassName('uploadbox-input')[0].value==''
    document.querySelector('.uploadbox-preview').src=''
    televerser.src = ancienne_image
    cacherBulle()
})

// Maintenant, nous allons commencer par faire le tracking avec mode image 

const input_image_track = document.querySelector('.uploadbox')
const declencheur = document.querySelector('.mid-header-left-file')

declencheur.addEventListener('click',()=>{
    input_image_track.style.display='block'
    namerechercher.value=''
})


// On va initier la connexion avec websocket sur cette image 
function trackerimage(){
    const con = new WebSocket(`${wsProto}://${domain}/ws/tracking/`)
    if (!traking) con.close()
    image = document.getElementsByClassName('uploadbox-input')[0].files[0]
    
    con.onopen =() =>{
        console.log('Connexion au mode tracking')
        con.send(image)
        console.log('image envoyé')
    }
    con.onmessage = (e)=>{
        if (!traking) con.close()
        if  (!document.getElementsByClassName('uploadbox-input')[0].value) con.close()
        if (namerechercher.value) con.close() 
        document.querySelectorAll(`img, video`)
        .forEach(el => el.classList.remove('actifs')); // On remove les couleurs
        const resultat = JSON.parse(e.data)
        console.log(resultat)
        if(resultat.length===0){ {console.log('Aucune personne trouvée')}; return }
        // Ici, nous allons colorier la frame correspondate 
        try{
        const framename = resultat.resultat[1]
        const name = resultat.resultat[0]
        document.querySelectorAll(`img.${framename} , video.${framename}`).forEach(el=>{el.classList.add('actifs')}) // On colore la frame en vert
        const temps = new Date()
        updateroradd(framename,name,temps.toLocaleTimeString())
    }
    catch(e){
        console.log("resultat est vide actuellement")
    }
}

    con.onclose = ()=>{
        console.log('Connexion coupé ')
        document.getElementsByClassName('uploadbox-input')[0].value=''
    }
    con.onerror = (e)=>{
        console.log(e)
    }
}


/* =====================================
    VARIABLES
===================================== */

const windowDiv = document.getElementById('window');
const header = document.getElementById('headers');

/* =====================================
    CLOSE
===================================== */


/* =====================================
    AJOUT DYNAMIQUE
===================================== */

const tbody =
    document.getElementById('tbody');

function ajouterPersonne( // Cette fonction permet d'ajouter une personne à la base des personnes recherchés 
    sourcename,
    personne,
    heure
){

    const tr =
        document.createElement('tr');
        tr.dataset.nom = cleNom(personne)

    tr.innerHTML = `
        <td>${echapperHtml(sourcename)}</td>
        <td>${echapperHtml(formatNom(personne))}</td>
        <td>${echapperHtml(heure)}</td>
    `;

    tbody.prepend(tr);
}

/* =====================================
    TEST
===================================== */
const uploadboxDropzone =
document.querySelector(".uploadbox-dropzone");

const uploadboxInput =
document.querySelector(".uploadbox-input");

const uploadboxPreview =
document.querySelector(".uploadbox-preview");

const uploadboxPlaceholder =
document.querySelector(".uploadbox-placeholder");

const uploadboxConfirm =
document.querySelector(".uploadbox-confirm");

const televerser = document.querySelector('.mid-header-left-file')
const ancienne_image = televerser.src 

const uploadboxCancel =
document.querySelector(".uploadbox-cancel");

let imageChoisie = null;

// ------------------------------------------------------------
//  Bulle d'aperçu de l'image choisie (survol du bouton de choix d'image)
// ------------------------------------------------------------
function imageChoisieActive(){
    // Une image est "active" si elle a été confirmée (le bouton affiche alors l'image à la place de l'icône)
    return !!document.querySelector('.uploadbox-preview').getAttribute('src') && televerser.src !== ancienne_image
}

televerser.addEventListener('mouseenter', () => {
    if (imageChoisieActive()) montrerBulle(televerser.src, televerser, 'bas')
})
televerser.addEventListener('mouseleave', cacherBulle)
televerser.addEventListener('click', cacherBulle)



function afficherImage(file){

    if(!file || !file.type.startsWith("image/")){
        return;
    }

    imageChoisie = file;

    const reader = new FileReader();

    reader.onload = e => {

        uploadboxPreview.src = e.target.result;

        uploadboxPreview.style.display = "block";

        uploadboxPlaceholder.style.display = "none";

        uploadboxConfirm.disabled = false;
    };

    reader.readAsDataURL(file);
}


uploadboxDropzone.addEventListener("click", () => {

    uploadboxInput.click();

});


uploadboxInput.addEventListener("change", () => {

    afficherImage(uploadboxInput.files[0]);

});


uploadboxDropzone.addEventListener("dragover", e => {

    e.preventDefault();

    uploadboxDropzone.classList.add("dragover");

});


uploadboxDropzone.addEventListener("dragleave", () => {

    uploadboxDropzone.classList.remove("dragover");

});


uploadboxDropzone.addEventListener("drop", e => {

    e.preventDefault();

    uploadboxDropzone.classList.remove("dragover");

    afficherImage(
        e.dataTransfer.files[0]
    );

});


uploadboxCancel.addEventListener("click", () => {

    imageChoisie = null;

    uploadboxInput.value = "";

    uploadboxPreview.src = "";

    uploadboxPreview.style.display = "none";

    uploadboxPlaceholder.style.display = "block";

    uploadboxConfirm.disabled = true;

    televerser.src = ancienne_image 

});


uploadboxConfirm.addEventListener("click", () => {
    trackerimage()
    console.log(imageChoisie);
    document
    .querySelector(".uploadbox")
    .style.display='none';
    televerser.src = document.querySelector('.uploadbox-preview').src 
});





document
.querySelector(".uploadbox-close")
.addEventListener("click", () => {

    document
    .querySelector(".uploadbox")
    .style.display='none';

});




function updateroradd(sourcename,personne,heure){
    if (traker.length===0)return
    const cle = cleNom(personne)
    const ligne = Array.from(tbody.children).find(tr => tr.dataset.nom === cle)
    if (ligne){
        traker[personne] = traker[personne] || {}
        ligne.querySelectorAll('td')[0].textContent = traker[personne].source = sourcename
        ligne.querySelectorAll('td')[2].textContent = traker[personne].temps = heure
        tbody.prepend(ligne)
    }
    else{
        ajouterPersonne(sourcename,personne,heure)
    }
}

function serveurounon(){
    // Cette fonction va me permettre de savoir si je suis sur l'ordinateur qui gère le serveur ou pas 
    const host = window.location.hostname;
    const tabe = document.querySelector('#tab-webcam')
    if (host === 'localhost' || host === '127.0.0.1') {
        console.log("Même PC que le serveur"); 
    } else {
        console.log("PC différent, IP du serveur :", host);
        tabe.style.display = 'none' 
    }
}


document.querySelector('.uploadbox-confirm')



// Ici, je vais configurer le lien qui nous permet d'ajouter une personne dans la base de données pour que ça nous permet de pouvoir fermer la caméra avant d'aller sur la nouvelle page html 

document.querySelector('.people_add').addEventListener('click',()=>{stopWebcam()})

    // ============================================================
    //  INIT
    // ============================================================
    window.addEventListener('load', () => {
        // Suggestions de recherche : noms sans tiret, avec majuscules
        document.querySelectorAll('#suggestions option').forEach(o => { o.value = formatNom(o.value) });
        // J'arrête la caméra au niveau de chrome d'abord 
        stopWebcam();
        serveurounon()
        _rafraichirPanelMilieu();
        _rafraichirPanelGauche();
        AjouterPanelDroit()
        //setTimeout(()=>{ouvrirsourcefirst(incrementer(),'0')},500) // Cette fonction permet d'ouvrir la caméra d'abord
        setTimeout(connexionlimite,1000)
    });