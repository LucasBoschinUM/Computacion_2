"""Servidor de prueba: responde cuando se le pide, con demora conocida.

Sirve para medir concurrencia sin depender de lo que haga internet.
"""
import asyncio

from fastapi import FastAPI, Response

app = FastAPI()
CUERPO = b'x' * 100_000


@app.get('/')
async def raiz():
    return {'ok': True}


@app.get('/demora/{milisegundos}')
async def demora(milisegundos: int):
    await asyncio.sleep(milisegundos / 1000)
    return Response(CUERPO, media_type='application/octet-stream')
