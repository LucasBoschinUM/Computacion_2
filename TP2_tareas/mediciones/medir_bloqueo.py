"""Medición 2: cuánto tarda GET /tareas durante un cálculo pesado.

Se corre dos veces: con el bug a propósito (HASHEAR_EN_EL_LOOP=1, sin
executor) y con el código normal (ProcessPoolExecutor).

Uso (con el contenedor levantado):
    docker compose exec api python -m mediciones.medir_bloqueo
"""
import argparse
import asyncio
import time

import httpx

from mediciones.comun import (
    TERMINALES,
    crear_tarea,
    guardar,
    percentil,
    promedio,
    servicio,
    tabla,
)


async def latencia_get(cliente, url) -> float:
    t0 = time.perf_counter()
    r = await cliente.get(url + '/tareas')
    r.raise_for_status()
    return (time.perf_counter() - t0) * 1000


async def medir(nombre: str, iteraciones: int, **ajustes) -> list:
    print(f'  {nombre}...')
    async with servicio(**ajustes) as api, \
            httpx.AsyncClient(timeout=120) as cliente:
        en_reposo = [await latencia_get(cliente, api.url) for _ in range(30)]

        # El POST va en su propia tarea porque, con el loop bloqueado, puede
        # tardar en volver. Mientras, sondeo GET /tareas.
        pedido = {'tipo': 'hashear', 'texto': 'medicion',
                  'iteraciones': iteraciones, 'timeout': 600}
        creacion = asyncio.create_task(crear_tarea(cliente, api.url, pedido))
        durante = []
        while True:
            durante.append(await latencia_get(cliente, api.url))
            if creacion.done():
                tarea = (await cliente.get(
                    f'{api.url}/tareas/{creacion.result()}')).json()
                if tarea['estado'] in TERMINALES:
                    break
            await asyncio.sleep(0.05)

    return [nombre, f'{tarea["duracion_s"]:.2f}',
            f'{promedio(en_reposo):.1f}', len(durante),
            f'{percentil(durante, 0.5):.1f}', f'{max(durante):.1f}']


async def main() -> None:
    argumentos = argparse.ArgumentParser()
    argumentos.add_argument('--iteraciones', type=int, default=10_000_000)
    iteraciones = argumentos.parse_args().iteraciones

    filas = [
        await medir('Sin executor (en el loop)', iteraciones,
                    HASHEAR_EN_EL_LOOP=1),
        await medir('Con ProcessPoolExecutor', iteraciones,
                    HASHEAR_EN_EL_LOOP=0),
    ]
    guardar('medicion_2.md', '\n'.join([
        '### Medición 2: el event loop bloqueado',
        '',
        f'Una tarea `hashear` de {iteraciones:,} iteraciones mientras se '
        'sondea `GET /tareas`.',
        '',
        tabla(['Modo', 'Duración del cálculo (s)', 'GET en reposo (ms)',
               'GET respondidos durante el cálculo', 'Mediana (ms)',
               'Peor GET (ms)'], filas),
    ]))


if __name__ == '__main__':
    asyncio.run(main())
