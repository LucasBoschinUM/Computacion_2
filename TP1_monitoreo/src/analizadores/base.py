"""
base.py — Esqueleto comun de los 7 analizadores.

Cada analizador es un PROCESO independiente (no un thread). Motivo: el
trabajo es leer cientos de archivos de /proc y parsearlos en Python, que es
CPU-bound en bytecode. Con threads el GIL los serializaria y no ganariamos
paralelismo real; con procesos cada uno tiene su propio interprete y su
propio GIL, y el scheduler del kernel los reparte entre cores.

Cada uno tiene su propio ritmo (`intervalo`, un multiprocessing.Value de
tipo double) porque los datos tienen frecuencias de cambio muy distintas:
el CPU% cambia todo el tiempo, la mascara de senales de un proceso no
cambia casi nunca.
"""

import queue
import time

import senales


class Analizador:
    NOMBRE = "base"

    def __init__(self, cola_pids, cola_resultados, intervalo, verbose, apagando):
        self.cola_pids = cola_pids
        self.cola_resultados = cola_resultados
        self.intervalo = intervalo        # Value('d') compartido con la TUI
        self.verbose = verbose            # Value('b') global, toggle SIGUSR2
        self.apagando = apagando          # Event de apagado ordenado
        self.pids = []

    # A implementar por cada subclase.
    def extraer(self, pids):
        raise NotImplementedError

    def correr(self):
        senales.ignorar_en_hijo()
        while not self.apagando.is_set():
            self._refrescar_pids()
            if not self.pids:
                # Todavia no llego la primera lista del recolector.
                time.sleep(0.1)
                continue

            t0 = time.monotonic()
            try:
                datos = self.extraer(self.pids)
            except Exception as e:  # noqa: BLE001
                # Un analizador nunca debe tumbar al monitor: se reporta el
                # error dentro del propio snapshot y se sigue.
                datos = {"_error": repr(e)}

            try:
                self.cola_resultados.put({
                    "seccion": self.NOMBRE,
                    "datos": datos,
                    "ts": time.time(),
                    "ms": round((time.monotonic() - t0) * 1000, 1),
                })
            except (ValueError, OSError):
                break

            self._dormir()

    def _refrescar_pids(self):
        """Se queda con la ULTIMA lista publicada por el recolector.

        Drenar la cola en vez de hacer un get() simple evita trabajar sobre
        listas atrasadas si el analizador es mas lento que el recolector.
        """
        try:
            while True:
                self.pids = self.cola_pids.get_nowait()
        except (queue.Empty, OSError):
            pass

    def _dormir(self):
        restante = float(self.intervalo.value)
        while restante > 0 and not self.apagando.is_set():
            paso = min(0.2, restante)
            time.sleep(paso)
            restante -= paso


def arrancar(clase, *args):
    """Entry point del Process: instancia la clase dentro del hijo.

    Se pasa la CLASE y no la instancia para que el objeto se construya del
    lado del hijo. Con el metodo de arranque 'spawn' una instancia con
    estado interno tendria que pickearse entera; asi solo viajan las
    primitivas de multiprocessing, que saben serializarse solas.
    """
    clase(*args).correr()
