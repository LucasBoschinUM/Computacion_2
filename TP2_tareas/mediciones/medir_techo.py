"""Medición 3: dónde está el techo.

A. Con 8 workers fijos, ¿qué pasa si llegan más tareas que workers?
B. Con tantos workers como tareas, ¿hasta dónde escala la espera?
C. Con tareas de CPU, ¿qué pasa al superar la cantidad de núcleos?
D. La pregunta de la cátedra: 10.000 tareas de golpe con la config normal.

Uso (con el contenedor levantado):
    docker compose exec api python -m mediciones.medir_techo
"""
import asyncio
import json
import math
import os
import time
from collections import Counter

import httpx

from app.calculo import hash_tramo
from mediciones.comun import guardar, percentil, servicio, tabla

CONEXIONES = 64


async def disparar(puerto: int, cuerpos: list[bytes]) -> tuple[Counter, float]:
    """Manda los POST y devuelve cuántos dieron cada código y cuánto tardó.

    Es un cliente HTTP escrito a mano con streams (clase 20) porque httpx
    es bastante más lento que el servidor: el techo que estaríamos midiendo
    sería el del cliente.
    """
    pendientes = iter(cuerpos)
    codigos: Counter = Counter()

    async def conexion() -> None:
        lector, escritor = await asyncio.open_connection('127.0.0.1', puerto)
        # El iterador se comparte sin lock: next() no tiene ningún await.
        for cuerpo in pendientes:
            escritor.write(
                b'POST /tareas HTTP/1.1\r\nHost: x\r\n'
                b'Content-Type: application/json\r\n'
                b'Content-Length: %d\r\n\r\n' % len(cuerpo) + cuerpo)
            await escritor.drain()
            cabeceras = await lector.readuntil(b'\r\n\r\n')
            codigos[int(cabeceras[9:12])] += 1
            for linea in cabeceras.split(b'\r\n'):
                if linea.lower().startswith(b'content-length:'):
                    await lector.readexactly(int(linea.split(b':')[1]))
        escritor.close()
        await escritor.wait_closed()

    inicio = time.perf_counter()
    await asyncio.gather(*(conexion() for _ in range(CONEXIONES)))
    return codigos, time.perf_counter() - inicio


async def correr_lote(api, cuerpo: dict, cantidad: int) -> dict:
    """Manda el lote y sondea /estadisticas hasta que termina todo."""
    datos = json.dumps(cuerpo).encode()
    inicio = time.perf_counter()
    envio = asyncio.create_task(disparar(api.puerto, [datos] * cantidad))
    latencias = []
    async with httpx.AsyncClient(timeout=120) as cliente:
        while True:
            t0 = time.perf_counter()
            r = await cliente.get(api.url + '/estadisticas')
            latencias.append((time.perf_counter() - t0) * 1000)
            e = r.json()
            activas = e['por_estado']['pendiente'] + e['por_estado']['ejecutando']
            if envio.done() and activas == 0:
                break
            await asyncio.sleep(0.05)
    codigos, encolado = envio.result()
    return {
        'total': time.perf_counter() - inicio, 'encolado': encolado,
        'codigos': codigos, 'estadisticas': e,
        'peor_latencia': max(latencias), 'p95': percentil(latencias, 0.95),
    }


async def parte_a() -> str:
    workers, demora = 8, 0.5
    filas = []
    for cantidad in (4, 8, 16, 32, 64):
        print(f'  A: {cantidad} tareas con {workers} workers')
        async with servicio(WORKERS=workers, PROCESOS=1) as api:
            r = await correr_lote(api, {'tipo': 'esperar', 'segundos': demora},
                                  cantidad)
        ideal = math.ceil(cantidad / workers) * demora
        filas.append([cantidad, f'{r["total"]:.2f}', f'{ideal:.2f}',
                      f'{r["estadisticas"]["tiempo_promedio_espera_s"]:.2f}'])
    return ('#### A. 8 workers fijos, tareas `esperar(0.5 s)`\n\n' + tabla(
        ['Tareas', 'Tiempo total (s)', 'Teórico: ⌈tareas/workers⌉ × 0.5 (s)',
         'Espera promedio en cola (s)'], filas))


async def parte_b() -> str:
    filas = []
    for cantidad in (100, 500, 1000, 2000, 5000, 10000, 20000):
        print(f'  B: {cantidad} tareas con {cantidad} workers')
        async with servicio(WORKERS=cantidad, TAMANO_COLA=cantidad,
                            PROCESOS=1, TIMEOUT_DEFECTO=600) as api:
            r = await correr_lote(api, {'tipo': 'esperar', 'segundos': 1.0},
                                  cantidad)
            memoria = api.memoria_mb()
        e = r['estadisticas']
        filas.append([
            cantidad, f'{r["encolado"]:.2f}', f'{cantidad / r["encolado"]:.0f}',
            f'{r["total"]:.2f}', f'{e["tiempo_promedio_ejecucion_s"]:.3f}',
            f'{r["p95"]:.0f}', f'{r["peor_latencia"]:.0f}',
            f'{memoria:.0f}' if memoria else 'n/d',
        ])
    return ('#### B. Tantos workers como tareas, `esperar(1 s)`\n\n' + tabla(
        ['Tareas y workers', 'Tiempo en encolar (s)', 'POST/s',
         'Tiempo total (s)', 'Duración real de esperar(1 s)',
         'GET /estadisticas p95 (ms)', 'Peor GET (ms)', 'Memoria pico (MB)'],
        filas))


def iteraciones_por_segundo() -> int:
    prueba = 200_000
    t0 = time.perf_counter()
    hash_tramo(b'calibrar', prueba)
    return int(prueba / (time.perf_counter() - t0))


async def parte_c() -> str:
    nucleos = os.cpu_count() or 1
    iteraciones = iteraciones_por_segundo()
    filas = []
    base = None
    cantidades = sorted({1, 2, 4, max(1, nucleos // 2), nucleos, nucleos * 2})
    for cantidad in cantidades:
        print(f'  C: {cantidad} tareas de CPU con {nucleos} procesos')
        async with servicio(WORKERS=cantidad, PROCESOS=nucleos,
                            TIMEOUT_DEFECTO=600) as api:
            r = await correr_lote(api, {'tipo': 'hashear', 'texto': 'techo',
                                        'iteraciones': iteraciones}, cantidad)
        if base is None:
            base = r['total']
        filas.append([cantidad, f'{r["total"]:.2f}',
                      f'{cantidad / r["total"]:.2f}',
                      f'{cantidad * base / r["total"]:.2f}',
                      f'{r["p95"]:.0f}'])
    return (f'#### C. Tareas CPU-bound de ~1 s, con {nucleos} procesos '
            f'({nucleos} núcleos lógicos)\n\n' + tabla(
                ['Tareas', 'Tiempo total (s)', 'Tareas por segundo',
                 'Speedup', 'GET /estadisticas p95 (ms)'], filas))


async def parte_d() -> str:
    print('  D: 10.000 tareas de golpe, configuración normal')
    async with servicio(WORKERS=8, TAMANO_COLA=1000, PROCESOS=1) as api:
        r = await correr_lote(api, {'tipo': 'esperar', 'segundos': 0.05}, 10_000)
        memoria = api.memoria_mb()
    codigos = r['codigos']
    return ('#### D. 10.000 POST de golpe (8 workers, cola de 1000)\n\n' + tabla(
        ['Aceptadas (202)', 'Rechazadas (503)', 'Otros códigos',
         'Tiempo en recibirlas (s)', 'Tiempo total (s)', 'Memoria pico (MB)'],
        [[codigos[202], codigos[503],
          sum(v for k, v in codigos.items() if k not in (202, 503)),
          f'{r["encolado"]:.2f}', f'{r["total"]:.2f}',
          f'{memoria:.0f}' if memoria else 'n/d']]))


async def main() -> None:
    partes = [await parte_a(), await parte_b(), await parte_c(), await parte_d()]
    guardar('medicion_3.md', '### Medición 3: techo del pool\n\n'
            + '\n\n'.join(partes))


if __name__ == '__main__':
    asyncio.run(main())
