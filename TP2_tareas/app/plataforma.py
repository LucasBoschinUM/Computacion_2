"""El corazón del sistema: la cola, los workers, la cancelación y el apagado.

Por qué acá no hay ningún Lock: todo este módulo corre en un solo hilo, el
del event loop, y una corrutina solo le cede el control a otra en un await.
Cualquier bloque sin await en el medio es atómico para las demás corrutinas.
Los procesos del pool no comparten esta memoria: reciben una copia de los
argumentos y devuelven una copia del resultado.
"""
import asyncio
import itertools
import logging
import multiprocessing
from collections import Counter
from collections.abc import Coroutine
from concurrent.futures import ProcessPoolExecutor
from statistics import mean

import httpx

from app import persistencia, trabajos
from app.calculo import calentar, preparar_proceso
from app.config import Config
from app.modelos import (
    Estado,
    PedidoDescargar,
    PedidoEsperar,
    PedidoHashear,
    Tarea,
    ahora,
)

log = logging.getLogger('plataforma')

# Valor único que significa "worker, terminá" (clase 21). Es un object() y
# se compara con `is`, así que ningún dato real se puede confundir con él.
CENTINELA = object()

ESPERA_CANCELACION = 5   # segundos máximos que DELETE espera la limpieza


class ColaLlena(Exception):
    """No entra una tarea más: el cliente recibe 503."""


class Apagandose(Exception):
    """El servidor ya no acepta tareas nuevas: el cliente recibe 503."""


class YaTerminada(Exception):
    """Se pidió cancelar una tarea que ya terminó."""


class Plataforma:
    def __init__(self, config: Config):
        self.config = config
        self.tareas: dict[int, Tarea] = {}
        # asyncio.Queue y no queue.Queue: get() suspende a esta corrutina,
        # no al hilo. queue.Queue.get() congelaría el event loop entero.
        self.cola: asyncio.Queue = asyncio.Queue(maxsize=config.tamano_cola)
        self.aceptando = False
        self._proximo_id = itertools.count(1)
        self._workers: list[asyncio.Task] = []
        self._en_curso: dict[int, asyncio.Task] = {}  # id de tarea -> hija
        self._pool: ProcessPoolExecutor | None = None
        self._cliente: httpx.AsyncClient | None = None
        self._apagada = False

    # --------------------------------------------------------------
    # Arranque
    # --------------------------------------------------------------

    async def iniciar(self) -> None:
        """Crea el pool, el cliente HTTP y los workers. Se llama una vez."""
        # spawn: el hijo nace limpio, sin heredar el socket del servidor, ni
        # los handlers de señales, ni el estado del event loop (clase 8).
        self._pool = ProcessPoolExecutor(
            max_workers=self.config.procesos,
            mp_context=multiprocessing.get_context('spawn'),
            initializer=preparar_proceso,
        )
        # Arrancar los procesos ahora para no pagar ese costo en la primera
        # tarea. Se hace con await: tampoco acá se bloquea el loop.
        loop = asyncio.get_running_loop()
        await asyncio.gather(*(
            loop.run_in_executor(self._pool, calentar)
            for _ in range(self.config.procesos)
        ))
        # Un solo cliente para todo el servicio: reutiliza conexiones.
        # pool=None porque el límite de tiempo real es el timeout de la tarea.
        self._cliente = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30, connect=10, pool=None),
            limits=httpx.Limits(max_connections=500,
                                max_keepalive_connections=50),
        )
        self._workers = [
            asyncio.create_task(self._worker(numero), name=f'worker-{numero}')
            for numero in range(self.config.workers)
        ]
        self.aceptando = True
        log.info('Listo: %d workers, %d procesos, cola de %d',
                 self.config.workers, self.config.procesos,
                 self.config.tamano_cola)

    # --------------------------------------------------------------
    # Lo que usan los endpoints
    # --------------------------------------------------------------

    def encolar(
        self, pedido: PedidoDescargar | PedidoHashear | PedidoEsperar
    ) -> Tarea:
        """Registra y encola una tarea, sin esperar a que se ejecute.

        No es async y usa put_nowait a propósito: nunca espera. Si la cola
        está llena se rechaza con un 503 en vez de colgar al cliente.
        """
        if not self.aceptando:
            raise Apagandose
        tarea = Tarea(
            id=next(self._proximo_id),
            tipo=pedido.tipo,
            parametros=pedido.model_dump(mode='json',
                                         exclude={'tipo', 'timeout'}),
            timeout=pedido.timeout or self.config.timeout_defecto,
        )
        try:
            self.cola.put_nowait(tarea)
        except asyncio.QueueFull:
            raise ColaLlena from None
        self.tareas[tarea.id] = tarea
        return tarea

    async def cancelar(self, tarea_id: int) -> Tarea:
        """Cancela una tarea pendiente o en ejecución."""
        tarea = self.tareas[tarea_id]   # KeyError -> el endpoint responde 404

        if tarea.estado is Estado.PENDIENTE:
            # Sigue físicamente en la cola, pero el worker que la saque va a
            # ver que ya no está pendiente y la va a saltear.
            self._finalizar(tarea, Estado.CANCELADA,
                            motivo='cancelada por el cliente antes de arrancar')
            return tarea

        if tarea.estado is Estado.EJECUTANDO:
            hija = self._en_curso[tarea_id]
            tarea.motivo = 'cancelada por el cliente'
            hija.cancel()
            # Espero a que termine de limpiar para responder el estado real.
            await asyncio.wait({hija}, timeout=ESPERA_CANCELACION)
            return tarea

        raise YaTerminada

    def estadisticas(self) -> dict:
        tareas = list(self.tareas.values())
        por_estado = Counter(t.estado for t in tareas)
        ejecutadas = [t for t in tareas if t.duracion_s is not None]
        por_tipo = {}
        for tipo in sorted({t.tipo for t in ejecutadas}):
            duraciones = [t.duracion_s for t in ejecutadas if t.tipo == tipo]
            por_tipo[tipo] = {'cantidad': len(duraciones),
                              'duracion_promedio_s': mean(duraciones)}
        ocupados = len(self._en_curso)
        return {
            'total': len(tareas),
            'por_estado': {e.value: por_estado[e] for e in Estado},
            'tiempo_promedio_espera_s': _promedio(
                [t.espera_s for t in tareas if t.espera_s is not None]),
            'tiempo_promedio_ejecucion_s': _promedio(
                [t.duracion_s for t in ejecutadas]),
            'por_tipo': por_tipo,
            'workers': {'total': len(self._workers), 'ocupados': ocupados,
                        'libres': len(self._workers) - ocupados},
            'cola': {'en_espera': self.cola.qsize(),
                     'capacidad': self.cola.maxsize},
            # Si este número no vuelve a su valor original después de cancelar,
            # hay corrutinas huérfanas.
            'corrutinas_vivas': len(asyncio.all_tasks()),
            'aceptando_tareas': self.aceptando,
        }

    # --------------------------------------------------------------
    # Workers
    # --------------------------------------------------------------

    async def _worker(self, numero: int) -> None:
        """Bucle de cada worker: sacar de la cola, ejecutar, repetir."""
        while True:
            item = await self.cola.get()
            try:
                if item is CENTINELA:
                    return
                # Pudo haberse cancelado mientras esperaba en la cola.
                if item.estado is Estado.PENDIENTE:
                    await self._ejecutar(item, numero)
            except Exception:
                # Red de seguridad. Lo que falla en una tarea ya queda
                # contenido en su hija; esto solo atajaría un bug nuestro.
                # Sin esto, el worker moriría en silencio y el pool tendría
                # uno menos para siempre.
                log.exception('Error inesperado en el worker %d', numero)
            finally:
                self.cola.task_done()   # en finally o join() no vuelve nunca

    async def _ejecutar(self, tarea: Tarea, numero: int) -> None:
        """Corre una tarea en su propia asyncio.Task y anota cómo terminó.

        Va en una Task hija porque cancel() corta una Task entera: si la
        tarea corriera directamente en el worker, un DELETE mataría al
        worker. Así se corta la hija y el worker sigue vivo.
        """
        tarea.estado = Estado.EJECUTANDO
        tarea.iniciada_en = ahora()
        tarea.worker = numero
        hija = asyncio.create_task(self._con_timeout(tarea))
        self._en_curso[tarea.id] = hija

        # asyncio.wait no relanza lo que le pase a la hija, a diferencia de
        # `await hija`. Así el worker nunca confunde "cancelaron la hija"
        # con "me cancelaron a mí", y después mira con calma cómo terminó.
        await asyncio.wait({hija})
        del self._en_curso[tarea.id]

        if hija.cancelled():
            self._finalizar(tarea, Estado.CANCELADA,
                            motivo=tarea.motivo or 'cancelada')
        elif isinstance(hija.exception(), TimeoutError):
            self._finalizar(tarea, Estado.FALLIDA,
                            motivo=f'timeout: superó los {tarea.timeout} s')
        elif hija.exception() is not None:
            error = hija.exception()
            self._finalizar(tarea, Estado.FALLIDA,
                            motivo=f'{type(error).__name__}: {error}')
        else:
            self._finalizar(tarea, Estado.COMPLETADA, resultado=hija.result())

    async def _con_timeout(self, tarea: Tarea) -> dict:
        # asyncio.timeout cancela lo de adentro cuando vence y convierte esa
        # cancelación en TimeoutError (clase 20).
        async with asyncio.timeout(tarea.timeout):
            return await self._trabajo(tarea)

    def _trabajo(self, tarea: Tarea) -> Coroutine:
        """Elige la corrutina que corresponde al tipo de tarea."""
        p = tarea.parametros
        if tarea.tipo == 'descargar':
            return trabajos.descargar(self._cliente, p['urls'],
                                      p['concurrencia'])
        if tarea.tipo == 'hashear':
            if self.config.hashear_en_el_loop:
                return trabajos.hashear_bloqueando(p['texto'], p['iteraciones'])
            return trabajos.hashear(self._pool, p['texto'], p['iteraciones'])
        if tarea.tipo == 'esperar':
            return trabajos.esperar(p['segundos'])
        raise ValueError(f'tipo de tarea desconocido: {tarea.tipo}')

    @staticmethod
    def _finalizar(
        tarea: Tarea,
        estado: Estado,
        resultado: dict | None = None,
        motivo: str | None = None,
    ) -> None:
        tarea.estado = estado
        tarea.resultado = resultado
        tarea.motivo = motivo
        tarea.finalizada_en = ahora()

    # --------------------------------------------------------------
    # Apagado ordenado
    # --------------------------------------------------------------

    async def apagar(self) -> None:
        """Deja de aceptar, espera con límite, libera todo y vuelca el estado.

        Es idempotente: se llama al recibir la señal y otra vez al cerrar
        la aplicación, y la segunda no hace nada.
        """
        if self._apagada:
            return
        self._apagada = True
        self.aceptando = False

        sin_arrancar = self._vaciar_cola()
        log.info('Apagando: %d tareas sin arrancar canceladas, espero a %d '
                 'en curso (máximo %.0f s)',
                 sin_arrancar, len(self._en_curso), self.config.limite_apagado)
        # join() vuelve cuando cada tarea sacada de la cola hizo su task_done.
        try:
            async with asyncio.timeout(self.config.limite_apagado):
                await self.cola.join()
        except TimeoutError:
            self._cancelar_las_que_quedan()
            await self.cola.join()

        # Un centinela por worker: cada uno consume el suyo y sale.
        for _ in self._workers:
            await self.cola.put(CENTINELA)
        await asyncio.gather(*self._workers)

        # shutdown(wait=True) bloquea, por eso va en un thread.
        await asyncio.to_thread(self._pool.shutdown, True, cancel_futures=True)
        await self._cliente.aclose()

        foto = [t.model_dump(mode='json') for t in self.tareas.values()]
        ruta = await asyncio.to_thread(persistencia.volcar, foto,
                                       self.config.dir_volcados)
        log.info('Estado volcado en %s (%d tareas)', ruta, len(foto))

    def _vaciar_cola(self) -> int:
        """Saca de la cola lo que nunca arrancó y lo marca como cancelado."""
        canceladas = 0
        while not self.cola.empty():
            tarea = self.cola.get_nowait()
            self.cola.task_done()
            if tarea.estado is Estado.PENDIENTE:
                self._finalizar(tarea, Estado.CANCELADA,
                                motivo='apagado: nunca llegó a arrancar')
                canceladas += 1
        return canceladas

    def _cancelar_las_que_quedan(self) -> None:
        for tarea_id, hija in self._en_curso.items():
            self.tareas[tarea_id].motivo = 'apagado: se venció el límite de espera'
            hija.cancel()


def _promedio(valores: list[float]) -> float | None:
    return mean(valores) if valores else None
