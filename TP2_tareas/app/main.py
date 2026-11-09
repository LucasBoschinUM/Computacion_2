"""La API HTTP y el arranque del servicio.

Todos los endpoints son `async def` y ninguno bloquea: corren dentro del
event loop y solo tocan estructuras en memoria.
"""
import asyncio
import logging
import signal
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from app import persistencia
from app.config import cargar_config
from app.modelos import Estado, PedidoTarea, Tarea
from app.plataforma import Apagandose, ColaLlena, Plataforma, YaTerminada

log = logging.getLogger('api')


def _instalar_senales(app: FastAPI) -> None:
    """SIGTERM y SIGINT arrancan el apagado ordenado.

    Con loop.add_signal_handler el callback corre entre tareas, en un momento
    seguro, y no interrumpe en cualquier lado como signal.signal (clase 20).
    Reemplaza el handler de uvicorn: así la API sigue contestando los GET
    mientras se drena, y un POST recibe 503.
    """
    servidor = getattr(app.state, 'servidor', None)
    if servidor is None:     # tests, o lanzado con `uvicorn app.main:app`
        return

    plataforma: Plataforma = app.state.plataforma
    loop = asyncio.get_running_loop()

    async def apagar_y_salir() -> None:
        try:
            await plataforma.apagar()
        except Exception:
            log.exception('Falló el apagado ordenado')
        finally:
            servidor.should_exit = True    # recién ahora se cierra uvicorn

    def al_recibir(senal: signal.Signals) -> None:
        log.info('Recibí %s', senal.name)
        if app.state.apagado is None:      # una segunda señal no hace nada
            app.state.apagado = asyncio.create_task(apagar_y_salir())

    app.state.apagado = None
    for senal in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(senal, al_recibir, senal)
        except NotImplementedError:   # Windows sin Docker: queda uvicorn
            log.warning('add_signal_handler no existe en este sistema')
            return


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    config = cargar_config()
    previo = await asyncio.to_thread(persistencia.describir_volcado_previo,
                                     config.dir_volcados)
    if previo:
        log.info('Volcado previo encontrado: %s', previo)
    else:
        log.info('No hay volcados previos')

    plataforma = Plataforma(config)
    await plataforma.iniciar()
    app.state.plataforma = plataforma
    _instalar_senales(app)
    yield
    await plataforma.apagar()     # si ya se apagó por señal, no hace nada


app = FastAPI(
    title='Plataforma de tareas distribuidas',
    description='TP2 de Computación II: cola asyncio, workers y executor.',
    lifespan=ciclo_de_vida,
)


class TareaCreada(BaseModel):
    id: int
    estado: Estado


def _plataforma(request: Request) -> Plataforma:
    return request.app.state.plataforma


@app.get('/')
async def raiz() -> dict:
    return {'servicio': 'plataforma de tareas', 'documentacion': '/docs'}


@app.post('/tareas', response_model=TareaCreada, status_code=202)
async def crear_tarea(
    request: Request,
    pedido: Annotated[PedidoTarea, Body()],
) -> Tarea:
    """Encola y responde al instante. 202 = aceptada, se procesa después."""
    try:
        return _plataforma(request).encolar(pedido)
    except ColaLlena:
        raise HTTPException(503, 'La cola está llena, probá más tarde',
                            headers={'Retry-After': '1'}) from None
    except Apagandose:
        raise HTTPException(503, 'El servidor se está apagando') from None


@app.get(
    '/tareas',
    response_model=list[Tarea],
    response_model_exclude={'__all__': {'parametros', 'resultado'}},
)
async def listar_tareas(
    request: Request,
    estado: Estado | None = Query(default=None),
    limite: int = Query(default=100, ge=1, le=1000),
) -> list[Tarea]:
    """Las últimas `limite` tareas. El límite existe porque armar el JSON
    gasta CPU del event loop, y sin tope un listado enorme lo trabaría."""
    tareas = list(_plataforma(request).tareas.values())
    if estado is not None:
        tareas = [t for t in tareas if t.estado is estado]
    return tareas[-limite:]


@app.get('/tareas/{tarea_id}', response_model=Tarea)
async def obtener_tarea(request: Request, tarea_id: int) -> Tarea:
    try:
        return _plataforma(request).tareas[tarea_id]
    except KeyError:
        raise HTTPException(404, 'No existe esa tarea') from None


@app.delete('/tareas/{tarea_id}', response_model=Tarea)
async def cancelar_tarea(request: Request, tarea_id: int) -> Tarea:
    try:
        return await _plataforma(request).cancelar(tarea_id)
    except KeyError:
        raise HTTPException(404, 'No existe esa tarea') from None
    except YaTerminada:
        raise HTTPException(409, 'La tarea ya terminó') from None


@app.get('/estadisticas')
async def estadisticas(request: Request) -> dict:
    return _plataforma(request).estadisticas()


# Esta guarda es obligatoria. Con spawn (macOS, Windows, y acá porque lo
# pedimos explícito) cada proceso hijo vuelve a importar este módulo; sin
# ella, cada hijo levantaría otro servidor y crearía otro pool.
if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
    )
    ajustes = cargar_config()
    servidor_http = uvicorn.Server(uvicorn.Config(
        app,
        host='0.0.0.0',
        port=ajustes.puerto,
        loop='asyncio',            # el selector de asyncio, sobre epoll
        access_log=ajustes.log_acceso,
        timeout_graceful_shutdown=5,
    ))
    app.state.servidor = servidor_http
    servidor_http.run()
