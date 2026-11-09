"""Los tres tipos de tarea. Ninguno sabe nada de colas ni de estados."""
import asyncio
import time
from concurrent.futures import ProcessPoolExecutor

import httpx

from app.calculo import hash_tramo

# Cada cuántas iteraciones el cálculo vuelve al event loop. Es el punto donde
# una cancelación o un timeout pueden entrar. Un tramo tarda menos de 0.1 s.
TAMANO_TRAMO = 50_000


# ------------------------------------------------------------------
# descargar: I/O-bound, se resuelve con await
# ------------------------------------------------------------------

async def _bajar_una(cliente: httpx.AsyncClient, url: str) -> dict:
    """Baja una URL y mide. Si falla, lo informa sin tirar abajo a las otras."""
    inicio = time.perf_counter()
    try:
        tamano = 0
        # stream: cuento los bytes a medida que llegan, sin guardarlos.
        # Si me cancelan acá, el `async with` cierra la conexión.
        async with cliente.stream('GET', url) as respuesta:
            async for bloque in respuesta.aiter_bytes():
                tamano += len(bloque)
        return {
            'url': url,
            'estado_http': respuesta.status_code,
            'tamano_bytes': tamano,
            'tiempo_s': round(time.perf_counter() - inicio, 3),
        }
    except Exception as error:
        # CancelledError NO entra acá: hereda de BaseException (clase 20).
        return {
            'url': url,
            'estado_http': None,
            'tamano_bytes': None,
            'tiempo_s': round(time.perf_counter() - inicio, 3),
            'error': f'{type(error).__name__}: {error}',
        }


async def descargar(
    cliente: httpx.AsyncClient, urls: list[str], concurrencia: int
) -> dict:
    """Baja todas las URLs a la vez, como mucho `concurrencia` en simultáneo."""
    semaforo = asyncio.Semaphore(concurrencia)

    async def acotada(url: str) -> dict:
        async with semaforo:
            return await _bajar_una(cliente, url)

    inicio = time.perf_counter()
    # TaskGroup: si cancelan esta tarea, cancela todas las descargas y no
    # sale del bloque hasta que hayan terminado. No quedan huérfanas.
    async with asyncio.TaskGroup() as grupo:
        hijas = [grupo.create_task(acotada(url)) for url in urls]
    descargas = [hija.result() for hija in hijas]

    if all('error' in d for d in descargas):
        raise RuntimeError(f'fallaron las {len(descargas)} URLs: '
                           f'{descargas[0]["error"]}')
    return {
        'descargas': descargas,
        'tiempo_total_s': round(time.perf_counter() - inicio, 3),
        'suma_de_tiempos_s': round(sum(d['tiempo_s'] for d in descargas), 3),
    }


# ------------------------------------------------------------------
# hashear: CPU-bound, se va a otro proceso
# ------------------------------------------------------------------

async def hashear(
    pool: ProcessPoolExecutor, texto: str, iteraciones: int
) -> dict:
    """Calcula el hash iterado en otro proceso, de a tramos.

    Un proceso que ya está calculando no se puede interrumpir: cancelar el
    Future solo hace que dejemos de esperarlo, y el cálculo seguiría
    ocupando un núcleo. Partiéndolo en tramos, entre uno y otro hay un
    await donde la cancelación entra, y lo máximo que se desperdicia es
    un tramo.
    """
    loop = asyncio.get_running_loop()
    datos = texto.encode()
    hechas = 0
    while hechas < iteraciones:
        tramo = min(TAMANO_TRAMO, iteraciones - hechas)
        datos = await loop.run_in_executor(pool, hash_tramo, datos, tramo)
        hechas += tramo
    return {'hash': datos.hex(), 'iteraciones': iteraciones}


async def hashear_bloqueando(texto: str, iteraciones: int) -> dict:
    """BUG A PROPÓSITO, para la medición 2: calcula dentro del event loop.

    No tiene ningún await, así que mientras corre no se atiende a nadie.
    Solo se usa si se arranca el servidor con HASHEAR_EN_EL_LOOP=1.
    """
    datos = hash_tramo(texto.encode(), iteraciones)
    return {'hash': datos.hex(), 'iteraciones': iteraciones}


# ------------------------------------------------------------------
# esperar: I/O simulado, para probar cancelación y timeouts
# ------------------------------------------------------------------

async def esperar(segundos: float) -> dict:
    await asyncio.sleep(segundos)
    return {'dormi_s': segundos}
