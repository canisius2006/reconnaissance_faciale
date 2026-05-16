// Cette liste nous permettra de connaitre les noms des frames existants
let ListeFrame = []
// Cet objet contiendra la liste des des ctx pour chaque frame
let ListeCtx = {}


function randomVividColor() {
    // permet de générer des couleurs vives 
    const hue = Math.floor(Math.random() * 360);

    return `hsl(${hue}, 90%, 55%)`;
}


// Gestion de la page pop-up
const page = document.querySelector('.pop-up')
const bouton = document.querySelector('.ajouter')
bouton.addEventListener('click',()=>{
    page.style.visibility = 'visible';
    document.getElementsByName('name')[0].focus()
})
page.addEventListener('click',(e)=>{
    if (e.target===page){
    page.style.visibility = 'hidden'
    reset()
}
    
})

// NB: Fermer la fenêtre agrandir grâce au bouton

function FermerAgrandir(){
    const selection = document.querySelector('.agrandir').getElementsByClassName('camera-frame')[0]
    if (selection){
    document.querySelector('.center').prepend(selection)
    selection.style.minHeight='70vh'
    selection.getElementsByClassName('entrer')[0].style.visibility = 'visible'
}
    document.querySelector('.agrandir').style.visibility='hidden'
    // On a pas besoin de selection==true pour fermer la fenêtre
    

}

// Ici, le bindage de l'évèvenement sur la bouton close 
document.querySelector('.close').addEventListener('click',FermerAgrandir)


//Gestion de la validation du bouton valider

const input1 = document.getElementsByName('name')[0]

const input2 = document.getElementsByName('url')[0]

input1.addEventListener('keydown',(e)=>{
    
    if (e.key==='Enter'){
        input2.focus()
    }
})


// Ici, on gère l'affichage de l'image téléversé dans la balise image reservé 
const preview_div = document.querySelector('.preview_div')
const image_div = document.querySelector('.preview')
const plus = document.querySelector('#plus')

preview_div.addEventListener('click',()=>{
    input2.click()
})

function afficher_preview(){
    image_div.style.display='flex' 
    plus.style.display = 'none'
    const Newimg = URL.createObjectURL(input2.files[0])
    image_div.src=Newimg 
}
// Attribution de la méthode à notre élément file 
input2.addEventListener('change',afficher_preview)


function valider(){
    const label = document.querySelector('.info')
    if (!(input1.value && input2.value)){
        label.textContent = 'Remplissez le formulaire'
        return false;
    }
    else if(ListeFrame.includes(input1.value)){
        label.textContent = 'Nom déjà pris'
        return false
    }
    else{
        ListeFrame.push(input1.value)
        return true;
    }
}

function reset(){
    // Cetter fonction permet de remettre les inputs à zéro
    input1.value='';
    input2.value=''
    image_div.style.display='none'
    plus.style.display='flex'
    plus.style.margin='auto'
    image_div.src=''
    document.querySelector('.info').textContent=''
}

function afficher(name,url){
    page.style.visibility = 'hidden'
    const ListeCam = document.querySelector('.left')
    const central = document.querySelector('.center')
    const nouveau = document.createElement('div')
    nouveau.classList.add('frame-name')
    nouveau.textContent = name 
    document.querySelector('.ajouter').before(nouveau)
    //Ici, on va afficher la frame de la caméra 
    const div1 = document.createElement('div')
    const div2 = document.createElement('div')
    const canva = document.createElement('canvas') // Pour le dessin
    const imgnew = document.createElement('img')
    const button1 = document.createElement('button')
    const button2 = document.createElement('button')
    const spannew = document.createElement('span')
    
    div1.classList.add('camera-frame')
    canva.classList.add('canva')
    imgnew.classList.add('source')
    imgnew.src=url
    div2.classList.add('options')
    button1.classList.add('entrer', 'bouton')
    button1.textContent = 'Voir'
    spannew.classList.add('nom-room')
    spannew.textContent=name 
    button2.classList.add('fermer' ,'bouton')
    button2.textContent = 'Fermer'


    // ajouter les éléments au dom 

    central.append(div1)
    div1.append(canva)
    div1.append(div2)
    div2.append(button1)
    div2.append(spannew)
    div2.append(button2)
    // Fonction du bouton entrer
    button1.addEventListener('click',()=>{
        const newpage = document.querySelector('.agrandir')
        newpage.style.visibility='visible'
        newpage.prepend(div1)
        div1.style.minHeight='98vh'
        button1.style.visibility = 'hidden'})
    // Fonction du bouton fermer 
    button2.addEventListener('click',()=>{
        ListeFrame = ListeFrame.filter(i=> i !==nouveau.textContent)
        imgnew.src=''
        div1.remove()
        nouveau.remove()
        }
    ) 
    // Fonction du bouton name affichant le nom de la cam 
    nouveau.addEventListener('click',()=>{
        const newpage = document.querySelector('.agrandir')
        newpage.style.visibility='visible'
        newpage.prepend(div1)
        div1.style.minHeight='98vh'
        button1.style.visibility = 'hidden'
    })

    const ctx = canva.getContext('2d')
     // On enregistre le name dans le dictionnaire 
    imgnew.onload=()=>{ //On attend que l'image se charge d'abord
    
    ctx.drawImage(imgnew,0,0,canva.width,canva.height)
    imgnew.height = canva.height 
    imgnew.width = canva.width 
    ListeCtx[name] = [ctx,imgnew.width,imgnew.height]
     // On dessine l'image dans notre canvas
    }

    // Réintialisation 
    reset()

}

// Fonction afficher les noms de ceux qui ont été détecté 

function afficher_nom(person_name,pourcentage,couleur){
    if (person_name!=='INCONNU'){
        
        const element = document.createElement('div')
        element.textContent = `${person_name} | ${pourcentage}%`
        element.classList.add('person')
        element.style.border=`2px solid ${couleur}`
        document.querySelector('.right').append(element)}
    }

// Une fonction qui va nous permettre d'afficher le cadre sur les visages 

function dessiner(data,room_name){
    
    const ctx = ListeCtx[room_name][0]
    const img_width = ListeCtx[room_name][1]
    const img_height = ListeCtx[room_name][2]
    const liste = data.infos
    
    for (dictio of liste){
        const cadre = dictio.cadre
        const person_name = dictio.nom 
        const pourcentage = dictio.score 
        const x = cadre.x1*img_width 
        const y = cadre.y1*img_height 
        const hauteur = (cadre.y2 - cadre.y1)*img_height 
        const largeur = (cadre.x2 - cadre.x1)*img_width 

        ctx.beginPath()
        ctx.roundRect(x,y,largeur,hauteur,10)
        ctx.lineWidth = 1;
        
        if (person_name!=='INCONNU'){
            ctx.strokeStyle = 'green'}
        else{
            ctx.strokeStyle = 'red'
        }
        const couleur = randomVividColor()
        ctx.fillStyle= couleur 
        
        ctx.fillText(`${person_name} ${pourcentage}%`,x,y-10)
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(x+largeur/2,y)
        ctx.lineTo(x+largeur/2,y-7)
        ctx.stroke()

        afficher_nom(person_name,pourcentage,couleur)
    }
    
}


token = document.getElementsByName('csrfmiddlewaretoken')[0].value

async function EnvoyerRoom(Notre_nom,Notre_image){
// Cette fonction va nous permettre d'envoyer les fichiers au serveur
    const form_data = new FormData()
    
    form_data.append('image',Notre_image)
    // Animation Ici 
    document.querySelector('.chargement').style.visibility = 'visible'
    try{
    const reponse = await fetch(Notre_nom,
        {method:'POST',
        headers:{
            // 'Content-Type':'application/json', interdit when formdata
            'X-CSRFToken':token 
        },
        body:form_data
        })
        data = await reponse.json()  
        console.log(data.temps)
    
    dessiner(data,Notre_nom)
    
    }
    catch(e){
        console.log(e)
    }
    finally{
        document.querySelector('.chargement').style.visibility = 'hidden'
    }
}
// Attribution des fonctions au bouton confirmer

document.querySelector('.confirmer').addEventListener('click',async ()=>{
    if(valider()){
            const Notre_nom = input1.value
            const Notre_image = input2.files[0]
            afficher(Notre_nom,URL.createObjectURL(Notre_image));

            await EnvoyerRoom(Notre_nom,Notre_image)
        reset()
        }
})





