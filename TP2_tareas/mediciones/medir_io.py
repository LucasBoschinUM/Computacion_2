"""Medición 1: 20 descargas secuenciales contra 20 concurrentes.

Es la misma tarea `descargar`; lo único que cambia es `concurrencia`:
con 1 baja de a una, con 20 baja todas a la vez.

Uso (con el contenedor levantado):
    docker compose exec api python -m mediciones.medir_io
"""
import asyncio
import random

import httpx

from mediciones.comun import (
    crear_tarea,
    esperar_tarea,
    guardar,
    proceso_servidor,
    promedio,
    puerto_libre,
    servicio,
    tabla,
)

REPETICIONES = 3
SITIOS = [
    'https://www.python.org/', 'https://docs.python.org/3/',
    'https://fastapi.tiangolo.com/', 'https://www.python-httpx.org/',
    'https://www.wikipedia.org/', 'https://github.com/',
    'https://www.kernel.org/', 'https://www.debian.org/',
    'https://ubuntu.com/', 'https://www.docker.com/',
    'https://www.mozilla.org/', 'https://developer.mozilla.org/',
    'https://www.gnu.org/', 'https://www.postgresql.org/',
    'https://www.sqlite.org/', 'https://www.um.edu.ar/',
    'https://www.argentina.gob.ar/', 'https://www.w3.org/',
    'https://www.ietf.org/', 'https://www.iana.org/',
]


async def una_vez(cliente, url, urls, concurrencia) -> tuple[float, int]:
    tarea_id = await crear_tarea(cliente, url, {
        'tipo': 'descargar', 'urls': urls,
        'concurrencia': concurrencia, 'timeout': 300,
    })
    tarea = await esperar_tarea(cliente, url, tarea_id)
    if tarea['estado'] != 'completada':
        raise RuntimeError(f'La tarea terminó {tarea["estado"]}: {tarea["motivo"]}')
    resultado = tarea['resultado']
    correctas = sum('error' not in d for d in resultado['descargas'])
    return resultado['tiempo_total_s'], correctas


async def comparar(cliente, url, nombre, urls) -> list:
    tiempos = {}
    correctas = len(urls)
    for concurrencia in (1, len(urls)):
        muestras = []
        for vuelta in range(REPETICIONES):
            segundos, ok = await una_vez(cliente, url, urls, concurrencia)
            muestras.append(segundos)
            correctas = min(correctas, ok)
            print(f'  {nombre}: concurrencia={concurrencia:<3} '
                  f'vuelta {vuelta + 1}: {segundos:.2f} s ({ok}/{len(urls)} OK)')
        tiempos[concurrencia] = promedio(muestras)
    secuencial, concurrente = tiempos[1], tiempos[len(urls)]
    return [nombre, f'{correctas}/{len(urls)}', f'{secuencial:.2f}',
            f'{concurrente:.2f}', f'{secuencial / concurrente:.1f}x']


async def main() -> None:
    random.seed(2026)
    demoras = [random.randint(200, 1000) for _ in range(20)]
    filas = []
    puerto = puerto_libre()
    argumentos = ['-m', 'uvicorn', 'mediciones.servidor_lento:app',
                  '--port', str(puerto), '--log-level', 'error']
    async with proceso_servidor(argumentos, {}, puerto) as lento:
        urls = [f'{lento.url}/demora/{d}' for d in demoras]
        async with servicio() as api, httpx.AsyncClient(timeout=30) as cliente:
            filas.append(await comparar(cliente, api.url,
                                        'Servidor local con demoras', urls))
            try:
                filas.append(await comparar(cliente, api.url,
                                            'Internet (20 sitios)', SITIOS))
            except RuntimeError as error:
                print(f'  No se pudo medir contra internet: {error}')

    guardar('medicion_1.md', '\n'.join([
        '### Medición 1: 20 descargas',
        '',
        f'Promedio de {REPETICIONES} corridas. En el servidor local la suma de '
        f'las demoras es {sum(demoras) / 1000:.1f} s y la más lenta tarda '
        f'{max(demoras) / 1000:.1f} s.',
        '',
        tabla(['Escenario', 'URLs OK', 'Secuencial (s)', 'Concurrente (s)',
               'Speedup'], filas),
    ]))


if __name__ == '__main__':
    asyncio.run(main())
