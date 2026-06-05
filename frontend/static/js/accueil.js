const bouton = document.querySelector('.primary')
bouton.addEventListener('click',()=>{
    window.location.pathname='dashboard/'
})

const ajouter = document.querySelector('.secondary')
const destination = document.querySelector('.rf-about')
ajouter.addEventListener('click',()=>{
    destination.scrollIntoView({behavior:"smooth"})
})

window.onbeforeunload = function() {
  window.scrollTo(0, 0);
};
