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

    let incrementation = 1

    let traking = false ; // Cette variable va nous permettre de savoir si le mode traking est lancé ou pas 

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
function connectStream(framename,lien) {
    // Concernant le mode caméra, on va juste se concentrer sur le fait qu'il aura un lenght , le lien.lenght ==1
    chemin = `ws://${domain}/ws/video/${framename}`
    const ws = new WebSocket(chemin);
    donnees[framename] = {} // On crée le diction pour framename
  ws.onopen = ()=>{
    data = {'type':'url','message':lien,'framename':framename}
    data = JSON.stringify(data)
    ws.send(data)
    console.log('Connexion ouverte, donnée envoyée')

    liendatabase[framename]=lien // Enregistrement dans la base de données des liens des images 

  }
  

  ws.onmessage = (e) => {
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
        
        const todeleteframename = allAvailableMembers.filter(m  => m.id===id )
        // Supprimer la liste d'affichage des noms 
        document.querySelector(`.sndcontainer.${todeleteframename[0].framename}`).remove()

        listeframename = listeframename.filter(m  => m!==todeleteframename[0].framename)  // Ceci me permet de supprimer framename si la fenêtre est parti ou a été supprimé 
        
        allAvailableMembers = allAvailableMembers.filter(m => m.id !== id);
        
        guildFamilies.forEach(f => {
            f.memberIds = f.memberIds.filter(mid => mid !== id);
            listeframename = listeframename.filter(fn => fn !== f.framename);
        });
        // Retire les familles vides
        guildFamilies = guildFamilies.filter(f => f.memberIds.length > 0);

        _rafraichirPanelGauche();
        _rafraichirPanelMilieu();
        AjouterPanelDroit()
    }

    function supprimermembrewithname(framename){
        try{
        //Cette fonction va nous permettre de pouvoir supprimer une fenêtre grâce au nom de la caméra 
        document.querySelector(`.sndcontainer.${framename}`).remove() // Supprimer la liste d'affichage des noms 
        listeframename = listeframename.filter(m  => m!==framename)  // Ceci me permet de supprimer framename si la fenêtre est parti ou a été supprimé 
        monid = allAvailableMembers.filter(m => m.framename===framename )[0].id
        allAvailableMembers = allAvailableMembers.filter(m => m.framename !== framename);
        

         guildFamilies.forEach(f => {
            f.memberIds = f.memberIds.filter(mid => mid !== monid);
            listeframename = listeframename.filter(fn => fn !== f.framename);
        });
        // Retire les familles vides
        guildFamilies = guildFamilies.filter(f => f.memberIds.length > 0);

        _rafraichirPanelGauche();
        _rafraichirPanelMilieu();
        AjouterPanelDroit()}
        catch(e){

        }

    }

    // ============================================================
    //  RENDU PANEL GAUCHE # Droite maintenant
    // ============================================================

    function ajoutersurpaneldroit(framename, src, liste) {
    if (!liste) return;
    const container = document.getElementById('members-list-container');

    if (!document.querySelector(`.sndcontainer.${framename}`)) {
        const sndcontainer = document.createElement('div')
        const trdcontainer = document.createElement('div')
        const spannamme = document.createElement('span')
        spannamme.classList.add('infos')
        spannamme.textContent = framename
        trdcontainer.classList.add('trdcontainer')
        sndcontainer.classList.add('sndcontainer', `${framename}`)
        const frameavatar = document.createElement('img')
        frameavatar.classList.add('frameavatar')
        frameavatar.src = src
        trdcontainer.appendChild(frameavatar)
        trdcontainer.appendChild(spannamme)
        sndcontainer.appendChild(trdcontainer)
        container.appendChild(sndcontainer)
    }

    const existant = []

    for (const [nom, couleur] of Object.entries(liste)) {
        existant.push(nom)
        // ✅ Utilise data-nom au lieu de la classe
        if (!document.querySelector(`.sndcontainer.${framename} .divpersonnecontainer[data-nom="${CSS.escape(nom)}"]`)) {
            const divpersonnecontainer = document.createElement('div')
            const avatarpersonne = document.createElement('img')
            avatarpersonne.src = `${STATIC_URL}img/live.png`
            const infos = document.createElement('span')
            divpersonnecontainer.style.border = `2px solid ${couleur}`
            infos.textContent = nom
            // ✅ data-nom à la place du nom en classe
            divpersonnecontainer.classList.add('divpersonnecontainer')
            divpersonnecontainer.dataset.nom = nom
            avatarpersonne.classList.add('avatarpersonne')
            infos.classList.add('infos')
            divpersonnecontainer.append(avatarpersonne)
            divpersonnecontainer.append(infos)
            document.querySelector(`.sndcontainer.${framename}`).append(divpersonnecontainer)
        }
    }

    const enfants = document.querySelectorAll(`.sndcontainer.${framename} .divpersonnecontainer`)
    // ✅ Suppression correcte via data-nom
    const absents = Array.from(enfants).filter(enfant => !existant.includes(enfant.dataset.nom))
    absents.forEach(absent => absent.remove())
}


function AjouterPanelDroit() {
    listeframename.forEach(framename => {
        const container = document.getElementById('members-list-container');

        if (!document.querySelector(`.sndcontainer.${framename}`)) {
            const sndcontainer = document.createElement('div')
            const trdcontainer = document.createElement('div')
            const spannamme = document.createElement('span')
            spannamme.classList.add('infos')
            spannamme.textContent = framename
            trdcontainer.classList.add('trdcontainer')
            sndcontainer.classList.add('sndcontainer', `${framename}`)
            const frameavatar = document.createElement('img')
            frameavatar.classList.add('frameavatar')
            frameavatar.src = donnees[framename].src
            trdcontainer.appendChild(frameavatar)
            trdcontainer.appendChild(spannamme)
            sndcontainer.appendChild(trdcontainer)
            container.appendChild(sndcontainer)
        }

        if (!donnees[framename].liste) return

        const existant = []
        const liste_personnes = donnees[framename].liste

        for (const [nom, couleur] of Object.entries(liste_personnes)) {
            existant.push(nom)
            // ✅ Utilise data-nom au lieu de la classe
            if (!document.querySelector(`.sndcontainer.${framename} .divpersonnecontainer[data-nom="${CSS.escape(nom)}"]`)) {
                const divpersonnecontainer = document.createElement('div')
                const avatarpersonne = document.createElement('img')
                avatarpersonne.src = `${STATIC_URL}img/live.png`
                const infos = document.createElement('span')
                divpersonnecontainer.style.border = `2px solid ${couleur}`
                infos.textContent = nom
                // ✅ data-nom à la place du nom en classe
                divpersonnecontainer.classList.add('divpersonnecontainer')
                divpersonnecontainer.dataset.nom = nom
                avatarpersonne.classList.add('avatarpersonne')
                infos.classList.add('infos')
                divpersonnecontainer.append(avatarpersonne)
                divpersonnecontainer.append(infos)
                document.querySelector(`.sndcontainer.${framename}`).append(divpersonnecontainer)
            }
        }

        const enfants = document.querySelectorAll(`.sndcontainer.${framename} .divpersonnecontainer`)
        // ✅ Suppression correcte via data-nom
        const absents = Array.from(enfants).filter(enfant => !existant.includes(enfant.dataset.nom))
        absents.forEach(absent => absent.remove())
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
        const countEl    = document.getElementById('members-count');
        container.innerHTML = '';
        countEl.textContent = allAvailableMembers.length;

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
        const camName  = sanitizeName(document.getElementById('cam-name').value);

        if (!camName) {
            feedback.style.color = '#ef4444';
            feedback.innerText   = "Veuillez entrer un nom pour la caméra.";
            return;
        }
        if (listeframename.includes(camName)){
            feedback.style.color = '#ef4444';
            feedback.innerText   = "Nom de frame déjà existant.";
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
                delete checker ; // Je ne veux pas avoir d'erreur bizarre après 
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
                        const attendre = setInterval(() => {
                            if (donnees[cam] && donnees[cam].src) { 
                                 
                                clearInterval(attendre)
                                ajouterMembre({ framename: cam, source: 'url', lien: donnees[cam].src })
                            }
                        }, 200)

                        listeframename.push(cam)
            }

            if (formobjet.source === 'webcam'){    
                   // — accède à la clé dynamique
                        const cam = formobjet.camName
                        listeframename.push(cam)
                        // J'arrête la caméra au niveau de chrome d'abord 
                        stopWebcam();
                        // Ici, j'établis la connexion 
                        connectStream(cam,'0')

                        // Attendre que la première frame arrive et aussi on va considérer que la caméra est un lien, parce que ça proviendra du serveur, le lien d'analyse 
                        const attendre = setInterval(() => {
                            if (donnees[cam] && donnees[cam].src) {   
                                clearInterval(attendre)
                                ajouterMembre({ framename: cam, source: 'url', lien: donnees[cam].src })
                            }
                        }, 200)
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
            // console.log('[Serveur] Envoi échoué (mode standalone ?)', e);
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
    let isResizingLeft = false, isResizingRight = false;

    resizer1.addEventListener('pointerdown', e => { isResizingLeft=true;  resizer1.classList.add('resizing'); document.body.style.cursor='col-resize'; e.preventDefault(); });
    resizer2.addEventListener('pointerdown', e => { isResizingRight=true; resizer2.classList.add('resizing'); document.body.style.cursor='col-resize'; e.preventDefault(); });

    document.addEventListener('pointermove', e => {
        if (isResizingLeft) {
            const w = e.clientX;
            if (w >= 180 && w <= 380) leftPanel.style.width = w + 'px';
        } else if (isResizingRight) {
            const w = window.innerWidth - e.clientX;
            if (w >= 220 && w <= 480) rightPanel.style.width = w + 'px';
        }
    });

    document.addEventListener('pointerup', () => {
        isResizingLeft = isResizingRight = false;
        resizer1.classList.remove('resizing');
        resizer2.classList.remove('resizing');
        document.body.style.cursor = 'default';
    });

    // ============================================================
    //  BOUTON AJOUTER SOURCE
    // ============================================================
    document.getElementById('btn-ajouter-source').addEventListener('click', () => {
        document.getElementById('cam-name').value =incrementer()
        document.getElementById('cam-name').addEventListener('focus',(e)=>{e.target.select()})
        setTimeout(() => togglePopup(true), 100);
        setTimeout(()=>{document.getElementById('cam-name').focus(),500})
        
    });



function sanitizeName(name) {
  // 1. .trim() enlève les espaces inutiles au début et à la fin
  // 2. .replace() utilise une Regex pour ne garder que les caractères autorisés
  // \p{L} : toutes les lettres de toutes les langues (Unicode)
  // \s : espaces
  // -' : tirets et apostrophes
  
  return name
  .normalize("NFD")
    .trim()
    .replace(/[^\p{L}\s\-'\d_]/gu, '') // Remplace tout ce qui n'est pas autorisé par rien
    .replace(' ',''); // Remplace l'espace par rien
}

async function checkLink(url) {
    try {

        const response = await fetch(url, { method: 'HEAD', mode: 'no-cors' });
        // 'no-cors' est important pour éviter l'erreur de blocage 
        // si le serveur ne renvoie pas d'en-tête CORS spécifique.
        
        // Si on arrive ici, le serveur a répondu (même avec une erreur 404).
        // Note: avec 'no-cors', response.ok sera toujours false, 
        // mais le fait qu'il n'y ait pas d'erreur réseau est un bon indicateur.
        return true; 
    } catch (error) {
    
        return false;
    }
}

    function incrementer(){
        // Cette fonction va me permettre de pouvoir donner de façon automatique un nom de frame pour notre source 
        const imgs = document.querySelectorAll(`img.Source_${incrementation}`)
        if (document.querySelectorAll('img').length===0) {incrementation=1} 
        else if (imgs.length>0){incrementation = incrementation+1}
        
        return 'Source_'+incrementation
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
  personnes = data.personnes || [];
  const presents  = personnes.filter(p => p.present).length;

  labelStats.textContent = `${personnes.length} Personnes`

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
    const initiales = p.nom.split(' ').map(n => n[0]).join('').toUpperCase().slice(0,2);
    const source       = p.source;
    const heure     = p.heure ? `<span class="heure">${p.heure}</span>` : '';
    return `
      <div class="row" title=${!document.getElementById('cb1-6').checked ? p.date:"Toutes les dates"}>
        <div class="avatar present">${initiales}</div>
        <div class="row-info">
          <div class="row-nom">${p.nom}</div>
        </div>
        <div class="row-right">
          ${heure}
          ${ document.getElementById('cb1-6').checked ? `<span class='badge' style='color:blue' >${p.date}</span>`:''}
          <span class="badge ">${source}</span>
        </div>
      </div>
    `;
  }).join('');
}

function intervalajouter(){
    // Cette fonction permet d'ajouter les gens sans recharger la page 
    listeContent.innerHTML = personnes.map(p => {
    const initiales = p.nom.split(' ').map(n => n[0]).join('').toUpperCase().slice(0,2);
    const source       = p.source;
    const heure     = p.heure ? `<span class="heure">${p.heure}</span>` : '';
    return `
      <div class="row" title=${!document.getElementById('cb1-6').checked ? p.date:"Toutes les dates"}>
        <div class="avatar present">${initiales}</div>
        <div class="row-info">
          <div class="row-nom">${p.nom}</div>
        </div>
        <div class="row-right">
          ${heure}
          ${ document.getElementById('cb1-6').checked ? `<span class='badge' style='color:blue' >${p.date}</span>`:''}
          <span class="badge ">${source}</span>
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

document.querySelector('.date-picker-wrap').addEventListener('click',()=>{
    const a = document.querySelector('#date-input')
    a.showPicker()
})

// Fonction du bouton retour 
document.querySelector('.retour').addEventListener('click',()=>{window.location.pathname=''})

// Afficher le menu
let encours 
document.querySelector('.menu-t').addEventListener('click',()=>{
    document.querySelector('.overlay').style.display='flex'
    mettreAJour()
    encours = setInterval(intervalajouter,800)
})
 document.getElementById('cb1-6').addEventListener('change',()=>{
    mettreAJour()
 })
// Fermer la fenêtre de téléchargement 
document.querySelector('.overlay').addEventListener('click',(e)=>{
    if (e.target==document.querySelector('.overlay')){
        document.querySelector('.overlay').style.display='none';
        document.getElementById('cb1-6').checked = false
        clearInterval(encours)
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
    const attendre = setInterval(() => {
        if (donnees[framename] && donnees[framename].src) {   
            clearInterval(attendre)
            ajouterMembre({ framename: framename, source: 'url', lien: donnees[framename].src })
        }
    }, 200)
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
})

let traker = {} // On crée un objet pour pouvoir enregistrer les valeurs des gens 

// Donnees  = {framename:{src:lien,liste:{'canisius',couleur}}}
function rechercher_par_nom(noms) {
  // Correction 1 — split + map + filter en une seule chaîne
  const searching = noms
    .split('+')
    .map(m => m.toLowerCase().trim())
    .filter(m => m.length > 0);

  const frametrouve = [];
  if (!donnees) return;

  for (let [key, value] of Object.entries(donnees)) {
    document.querySelectorAll(`img.${key}, video.${key}`)
      .forEach(el => el.classList.remove('actifs'));

    const liste = Object.keys(value?.liste ?? {}).map(e => e.toLowerCase());

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



// Maintenant, nous allons commen



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
        tr.classList.add(`${personne}`)

    tr.innerHTML = `
        <td>${sourcename}</td>
        <td>${personne}</td>
        <td>${heure}</td>
    `;

    tbody.prepend(tr);
}

/* =====================================
    TEST
===================================== */




function updateroradd(sourcename,personne,heure){
    if (traker.length===0)return
    const ligne = document.querySelector(`tr.${personne}`)
    if (ligne){
        
        ligne.querySelectorAll('td')[0].textContent = traker[personne].source = sourcename
        ligne.querySelectorAll('td')[2].textContent = traker[personne].temps = heure
        tbody.prepend(ligne)
    }
    else{
        ajouterPersonne(sourcename,personne,heure)
    }
}



    // ============================================================
    //  INIT
    // ============================================================
    window.addEventListener('load', () => {
        // J'arrête la caméra au niveau de chrome d'abord 
        stopWebcam();
        _rafraichirPanelMilieu();
        _rafraichirPanelGauche();
        AjouterPanelDroit()
        setTimeout(()=>{ouvrirsourcefirst(incrementer(),'0')},500) // Cette fonction permet d'ouvrir la caméra d'abord
        connexionlimite()
    });
