 // ============================================================
    //  ÉTAT GLOBAL
    // ============================================================
    let currentMethod = 'url';
    let formobjet     = {};
    let webcamStream  = null;

    // Ces deux tableaux sont les sources de vérité.
    // On les alimente via les fonctions globales ci-dessous.
    let allAvailableMembers = [];   // { id, framename, source, lien }
    let guildFamilies       = [];   // { id, memberIds }

    let _nextMemberId = 1; // compteur auto pour les IDs

    // ============================================================
    //  FONCTIONS GLOBALES D'AJOUT
    //  → À appeler depuis n'importe où pour enrichir les panels
    // ============================================================

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
        _rafraichirPanelDroit();
        _rafraichirPanelGauche();
        _rafraichirPanelMilieu();

        return id;
    }

    /**
     * Supprime un membre par son id.
     * @param {number} id
     */
    function supprimerMembre(id) {
        allAvailableMembers = allAvailableMembers.filter(m => m.id !== id);
        guildFamilies.forEach(f => {
            f.memberIds = f.memberIds.filter(mid => mid !== id);
        });
        // Retire les familles vides
        guildFamilies = guildFamilies.filter(f => f.memberIds.length > 0);

        _rafraichirPanelDroit();
        _rafraichirPanelGauche();
        _rafraichirPanelMilieu();
    }

    // ============================================================
    //  RENDU PANEL GAUCHE
    // ============================================================
    function _rafraichirPanelGauche() {
        const container = document.getElementById('channel-items-container');
        container.innerHTML = '';

        allAvailableMembers.forEach(m => {
            const a = document.createElement('a');
            a.href = '#';
            a.className = 'channel-item';
            a.innerHTML = `
                <div class="channel-item-left">
                    <span>${m.framename}</span>
                </div>
                <span class="channel-count">${m.source}</span>
            `;
            container.appendChild(a);
        });
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
                ? `<img class="focus-img" src="${focused.lien}" alt="${focused.framename}">`
                : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f1a;color:#334155;font-size:16px;">${focused.framename}</div>`;

            frame.innerHTML = `
                ${mediaHTML}
                <button class="focus-close-btn" onclick="event.stopPropagation();supprimerMembre(${focused.id})" title="Fermer">${_svgClose(16)}</button>
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
                    ? `<img class="strip-img" src="${m.lien}" alt="${m.framename}">`
                    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f1a;color:#475569;font-size:11px;">${m.framename}</div>`;

                strip.innerHTML = `
                    ${stripMedia}
                    <div class="strip-label">${m.framename}</div>
                    <button class="strip-close-btn" onclick="event.stopPropagation();supprimerMembre(${m.id})" title="Fermer">${_svgClose(10)}</button>
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
                    ? `<img class="video-card-img" src="${m.lien}" alt="${m.framename}">`
                    : `<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;background:#0b0f1a;color:#334155;font-size:13px;">${m.framename}</div>`;

                card.innerHTML = `
                    ${mediaHTML}
                    <button class="card-close-btn" onclick="event.stopPropagation();supprimerMembre(${m.id})" title="Fermer le cadre">${_svgClose(12)}</button>
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
    //  RENDU PANEL DROIT
    // ============================================================
    function _rafraichirPanelDroit() {
        const container  = document.getElementById('members-list-container');
        const countEl    = document.getElementById('members-count');
        container.innerHTML = '';
        countEl.textContent = allAvailableMembers.length;

        guildFamilies.forEach(famille => {
            const familleCard = document.createElement('div');
            familleCard.className = 'family-card';

            familleCard.innerHTML = `
                <div class="family-header">🪟 ${famille.id}</div>
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
                    ? `<img class="member-avatar" src="${m.lien}" alt="${m.framename}">`
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
                        <button class="member-action-close" onclick="supprimerMembre(${m.id})" title="Retirer">
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
    function handleSubmit(event) {
        event.preventDefault();
        const feedback = document.getElementById('feedback-info');
        const camName  = document.getElementById('cam-name').value.trim();

        if (!camName) {
            feedback.style.color = '#ef4444';
            feedback.innerText   = "Veuillez entrer un nom pour la caméra.";
            return;
        }

        let lien = '';

        if (currentMethod === 'url') {
            const urlVal = document.getElementById('url-input').value;
            if (!urlVal) { feedback.style.color='#ef4444'; feedback.innerText="Veuillez entrer une URL."; return; }
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
    //  ENVOI SERVEUR (Django) — optionnel, ne bloque pas l'UI
    // ============================================================
    async function _envoyerServeur() {
        const csrfInput = document.getElementsByName("csrfmiddlewaretoken")[0];
        const csrfToken = csrfInput ? csrfInput.value : '';
        if (!csrfToken) return; // Pas en contexte Django, on skip

        try {
            const formdata = new FormData();
            formdata.append('source', formobjet.source);
            formdata.append('framename', formobjet.camName);

            if (formobjet.source === 'url')   formdata.append('url', formobjet.urlVal);
            if (formobjet.file)               formdata.append('file', formobjet.file);
            console.log(csrfToken)
            const data    = await fetch(`${formobjet.camName}`, { method:'POST', headers:{ 'X-CSRFToken': csrfToken }, body: formdata });
            const reponse = await data.json();
            console.log(reponse)
            // La réponse est sous cette forme
            // return JsonResponse({'name':name,'url':chemin,'source':source,'liste':liste_personnes})
            ajouterMembre({ framename: reponse.name, source:reponse.source, lien:reponse.url })

            console.log('[Serveur]', reponse);
        } catch(e) {
            console.warn('[Serveur] Envoi échoué (mode standalone ?)', e);
            alert("Erreur rencontré lors de la requête")
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
        setTimeout(() => togglePopup(true), 100);
    });



    // ============================================================
    //  INIT
    // ============================================================
    window.addEventListener('load', () => {
        _rafraichirPanelMilieu();
        _rafraichirPanelDroit();
        _rafraichirPanelGauche();
    });