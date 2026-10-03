import asyncio
import websockets

async def client():
    async with websockets.connect("ws://localhost:8765") as ws:

        while True:
            message = input("> ")

            await ws.send(message)

            response = await ws.recv()

            print("Serveur :", response)


asyncio.run(client())