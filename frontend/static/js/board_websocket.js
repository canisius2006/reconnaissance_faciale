// Cet fichier va nous permettre d'implémenter le mode live au niveau du flux video 

export function connexion(name,url){
    // Cette fonction va nous permettre d'établir la connexion niveau websocket, créer le websocket quoi
    const livesocket = new WebSocket(
        'ws://'
        +window.location.host
        +'/ws/'
        +name
    )

    livesocket.onmessage = function(e){
        const data = JSON.parse(e.data)
        console.log(data)
    }
    livesocket.onopen = function(){
        alert("La connexion s'est bien établi")
    }

    livesocket.onclose = function(e){
        console.error("La connexion s'est coupé ")
    }
}