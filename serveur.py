import asyncio
import websockets

async def serveur(ws):
    print("Client connecté")

    try:
        async for message in ws:
            print("Client :", message)
            await ws.send("Message reçu")

    except websockets.ConnectionClosed:
        print("Client déconnecté")


async def main():
    async with websockets.serve(serveur, "0.0.0.0", 8765):
        print("Serveur lancé")
        await asyncio.Future()

asyncio.run(main())