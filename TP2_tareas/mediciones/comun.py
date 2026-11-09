"""Herramientas compartidas por las tres mediciones.

Cada medición levanta su propia copia del servicio con la configuración que
necesita, en un puerto libre. Así no depende de lo que tengas corriendo y
los volcados de prueba no ensucian tu carpeta volcados/.
"""
import asyncio
import contextlib
import os
import signal
import socket
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

RESULTADOS = Path(__file__).parent / 'resultados'
TERMINALES = ('completada', 'fallida', 'cancelada')


@dataclass
class Instancia:
    url: str
    puerto: int
    pid: int

    def memoria_mb(self) -> float | None:
        """Pico de memoria del proceso, leído de /proc (como en el TP1)."""
        try:
            with open(f'/proc/{self.pid}/status', encoding='ascii') as f:
                for linea in f:
                    if linea.startswith('VmHWM:'):
                        return int(linea.split()[1]) / 1024
        except OSError:
            pass
        return None


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


async def esperar_que_levante(url: str, limite: float = 30) -> None:
    async with httpx.AsyncClient() as cliente:
        fin = time.monotonic() + limite
        while time.monotonic() < fin:
            with contextlib.suppress(httpx.HTTPError):
                await cliente.get(url + '/', timeout=1)
                return
            await asyncio.sleep(0.05)
    raise RuntimeError(f'El servidor {url} no levantó en {limite} s')


@contextlib.asynccontextmanager
async def proceso_servidor(argumentos: list[str], entorno: dict, puerto: int):
    url = f'http://127.0.0.1:{puerto}'
    with tempfile.TemporaryFile() as errores:
        proceso = await asyncio.create_subprocess_exec(
            sys.executable, *argumentos,
            env={**os.environ, **{k: str(v) for k, v in entorno.items()}},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=errores,
        )
        try:
            try:
                await esperar_que_levante(url)
            except RuntimeError:
                errores.seek(0)
                print(errores.read().decode(errors='replace'), file=sys.stderr)
                raise
            yield Instancia(url, puerto, proceso.pid)
        finally:
            proceso.send_signal(signal.SIGTERM)
            await proceso.wait()


@contextlib.asynccontextmanager
async def servicio(**ajustes):
    """Levanta el servicio con estas variables de entorno y lo apaga al salir."""
    puerto = puerto_libre()
    with tempfile.TemporaryDirectory() as volcados:
        entorno = {'PUERTO': puerto, 'LOG_ACCESO': '0', 'LIMITE_APAGADO': 1,
                   'DIR_VOLCADOS': volcados, **ajustes}
        async with proceso_servidor(['-m', 'app.main'], entorno, puerto) as i:
            yield i


async def crear_tarea(cliente: httpx.AsyncClient, url: str, cuerpo: dict) -> int:
    r = await cliente.post(url + '/tareas', json=cuerpo)
    r.raise_for_status()
    return r.json()['id']


async def esperar_tarea(
    cliente: httpx.AsyncClient, url: str, tarea_id: int
) -> dict:
    while True:
        r = await cliente.get(f'{url}/tareas/{tarea_id}')
        r.raise_for_status()
        tarea = r.json()
        if tarea['estado'] in TERMINALES:
            return tarea
        await asyncio.sleep(0.05)


def percentil(valores: list[float], p: float) -> float:
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(len(ordenados) * p))]


def tabla(encabezados: list[str], filas: list[list]) -> str:
    lineas = ['| ' + ' | '.join(encabezados) + ' |',
              '|' + '---|' * len(encabezados)]
    lineas += ['| ' + ' | '.join(str(c) for c in f) + ' |' for f in filas]
    return '\n'.join(lineas)


def guardar(nombre: str, texto: str) -> None:
    print('\n' + texto)
    RESULTADOS.mkdir(exist_ok=True)
    (RESULTADOS / nombre).write_text(texto + '\n', encoding='utf-8')
    print(f'\nGuardado en mediciones/resultados/{nombre}')


promedio = statistics.mean
