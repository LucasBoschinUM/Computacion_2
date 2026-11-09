"""
main.py — Orquestador del monitor.

Responsabilidades del proceso principal:
  1. Crear las primitivas de IPC compartidas.
  2. Lanzar recolector + 7 analizadores + agregador + display (10 procesos).
  3. Atender las senales del monitor via self-pipe + select.
  4. Apagar todo de forma ordenada.

El proceso principal NO renderiza ni lee /proc: se queda en un select()
esperando eventos de senal, que es lo unico que tiene que hacer.
"""

import argparse
import json
import os
import select
import sys
import time
from multiprocessing import Event, Manager, Pipe, Process, Queue, Value

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agregador
import recolector
import senales as sen
from analizadores import TODOS
from analizadores.base import arrancar

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(RAIZ, "config.json")

CONFIG_DEFAULT = {
    "intervalos": {"resumen": 2.0, "memoria": 3.0, "fds": 5.0, "threads": 2.0,
                   "senales": 10.0, "scheduling": 10.0, "sistema": 2.0,
                   "recolector": 2.0},
    "intervalos_minimos": {"resumen": 0.5, "memoria": 1.0, "fds": 2.0,
                           "threads": 0.5, "senales": 5.0, "scheduling": 5.0,
                           "sistema": 1.0},
    "orden_default": "cpu",
    "filtro_default": "",
}


def cargar_config():
    try:
        with open(CONFIG) as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return dict(CONFIG_DEFAULT)
    fusion = dict(CONFIG_DEFAULT)
    fusion.update(cfg)
    for k in ("intervalos", "intervalos_minimos"):
        base = dict(CONFIG_DEFAULT[k])
        base.update(cfg.get(k, {}))
        fusion[k] = base
    return fusion


class Monitor:
    def __init__(self, args):
        self.args = args
        self.config = cargar_config()

        # Manager.dict para el snapshot: las secciones son diccionarios
        # anidados, heterogeneos y de tamanio variable (un proceso puede
        # tener 3 FDs o 400). Value/Array exigen tipos C de tamanio fijo,
        # asi que no sirven para esto. El costo es que cada acceso es un
        # round-trip IPC contra el proceso del Manager, y por eso el
        # display copia la seccion entera una sola vez por frame.
        self.manager = Manager()
        self.snapshot = self.manager.dict()

        # Event: flag booleano compartido para el apagado ordenado. Todos
        # los loops lo chequean, asi que apagar es set() y esperar joins.
        self.apagando = Event()

        # Queue analizadores -> agregador. Un solo consumidor => patron
        # single-writer sobre el snapshot, sin locks explicitos.
        self.cola_resultados = Queue()

        # Queue recolector -> cada analizador. Acotadas: si un analizador
        # se atrasa preferimos descartar listas viejas antes que bloquear
        # al recolector.
        self.colas_pids = [Queue(maxsize=4) for _ in TODOS]

        # Value('d'): un double por vista. Escribe el display (+/-) y el
        # SIGHUP del main; leen los analizadores. Value trae lock propio.
        self.intervalos = {
            c.NOMBRE: Value("d", float(self.config["intervalos"][c.NOMBRE]))
            for c in TODOS
        }
        self.int_recolector = Value("d",
                                    float(self.config["intervalos"]["recolector"]))
        # Value('b'): flag de verbose, toggle con SIGUSR2.
        self.verbose = Value("b", 0)

        # Pipe bidireccional main <-> display para avisos puntuales
        # (SIGHUP recargo config, SIGUSR1 volco el dump...). Es un canal
        # de mensajes raros y chicos: un Pipe es mas barato que una Queue.
        self.lado_main, self.lado_display = Pipe()

        self.procesos = []

    def lanzar(self):
        self.procesos.append(Process(
            target=recolector.correr, name="recolector",
            args=(self.colas_pids, self.int_recolector, self.apagando)))

        self.procesos.append(Process(
            target=agregador.correr, name="agregador",
            args=(self.cola_resultados, self.snapshot, self.apagando)))

        for clase, cola in zip(TODOS, self.colas_pids):
            self.procesos.append(Process(
                target=arrancar, name=f"analizador:{clase.NOMBRE}",
                args=(clase, cola, self.cola_resultados,
                      self.intervalos[clase.NOMBRE], self.verbose,
                      self.apagando)))

        if not self.args.sin_tui:
            import display
            # Se duplica stdin ANTES del fork porque multiprocessing lo
            # reemplaza por /dev/null en los hijos.
            fd_stdin = os.dup(sys.stdin.fileno())
            self.procesos.append(Process(
                target=display.correr, name="display",
                args=(self.snapshot, self.intervalos,
                      self.config["intervalos_minimos"], self.apagando,
                      self.lado_display, self.config, self.verbose,
                      fd_stdin)))

        for p in self.procesos:
            p.start()

    # ------------------------------------------------------------------

    def atender(self, byte, fd_senales):
        if byte in (b"I", b"T"):
            self.apagando.set()

        elif byte == b"H":
            try:
                nueva = cargar_config()
                for nombre, v in self.intervalos.items():
                    v.value = float(nueva["intervalos"][nombre])
                self.int_recolector.value = float(nueva["intervalos"]["recolector"])
                self.config = nueva
                self.avisar(("config", nueva["intervalos_minimos"]))
                self.avisar(("aviso", "SIGHUP: config.json recargado"))
            except (OSError, ValueError, KeyError) as e:
                self.avisar(("aviso", f"SIGHUP: config invalida ({e})"))

        elif byte == b"1":
            nombre = f"dump_{time.strftime('%Y%m%d_%H%M%S')}.json"
            ruta = os.path.join(RAIZ, nombre)
            try:
                # dict(...) fuerza la copia local del Manager.dict antes de
                # serializar; json no sabe manejar el proxy directamente.
                copia = {k: v for k, v in self.snapshot.items()}
                with open(ruta, "w") as f:
                    json.dump(copia, f, indent=2, default=str)
                self.avisar(("aviso", f"SIGUSR1: snapshot volcado a {nombre}"))
            except (OSError, TypeError) as e:
                self.avisar(("aviso", f"SIGUSR1: error al volcar ({e})"))

        elif byte == b"2":
            self.verbose.value = 0 if self.verbose.value else 1
            estado = "ON" if self.verbose.value else "OFF"
            self.avisar(("aviso", f"SIGUSR2: modo verbose {estado}"))

        elif byte == b"W":
            self.avisar(("repintar", None))

    def avisar(self, mensaje):
        if self.args.sin_tui:
            print(f"[main] {mensaje}", flush=True)
            return
        try:
            self.lado_main.send(mensaje)
        except (OSError, BrokenPipeError):
            pass

    # ------------------------------------------------------------------

    def correr(self):
        self.lanzar()
        fd_senales = sen.instalar()
        inicio = time.monotonic()

        if self.args.sin_tui:
            print(f"[main] monitor corriendo, pid {os.getpid()} "
                  f"({len(self.procesos)} procesos)", flush=True)

        try:
            while not self.apagando.is_set():
                # select() sobre el self-pipe: el proceso principal duerme
                # hasta que llega una senal o vence el timeout. Nada de
                # busy-wait.
                listos, _, _ = select.select([fd_senales], [], [], 0.3)
                if listos:
                    for b in os.read(fd_senales, 128):
                        self.atender(bytes([b]), fd_senales)

                for p in self.procesos:
                    if not p.is_alive():
                        # Si se cae el display, se apaga todo. Si se cae un
                        # analizador, el monitor sigue: su seccion queda
                        # congelada y la TUI lo muestra con la "edad" del
                        # dato creciendo.
                        if p.name == "display":
                            self.apagando.set()

                if self.args.segundos and \
                        time.monotonic() - inicio > self.args.segundos:
                    self.apagando.set()

                if self.args.sin_tui and self.args.volcar:
                    self._volcar_texto()
        finally:
            self.apagar()

    def _volcar_texto(self):
        """Salida minima para modo headless (util para testear sin tty)."""
        sec = self.snapshot.get("sistema")
        if sec and sec["datos"]:
            d = sec["datos"]
            print(f"[sistema] procesos={d.get('procesos_total')} "
                  f"threads={d.get('threads_totales')} "
                  f"zombies={d.get('zombies')} "
                  f"cpu_idle={d.get('cpu_pct', {}).get('idle')}%", flush=True)
        time.sleep(1.0)

    def apagar(self):
        """Apagado ordenado: primero el Event, despues join, despues fuerza."""
        self.apagando.set()

        # Se cierran las queues para que los hijos bloqueados en get()
        # despierten en vez de quedar esperando un put que no va a llegar.
        for q in [self.cola_resultados] + self.colas_pids:
            try:
                q.close()
                q.cancel_join_thread()
            except (OSError, ValueError):
                pass

        for p in self.procesos:
            p.join(timeout=2.0)
        for p in self.procesos:
            if p.is_alive():
                p.terminate()      # SIGTERM
                p.join(timeout=1.0)
        for p in self.procesos:
            if p.is_alive():
                p.kill()           # SIGKILL, ultimo recurso
        try:
            self.manager.shutdown()
        except Exception:  # noqa: BLE001
            pass
        if self.args.sin_tui:
            print("[main] apagado completo", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Monitor de procesos y threads")
    ap.add_argument("--sin-tui", action="store_true",
                    help="corre sin curses (modo headless / debug)")
    ap.add_argument("--segundos", type=float, default=None,
                    help="termina automaticamente tras N segundos")
    ap.add_argument("--volcar", action="store_true",
                    help="con --sin-tui, imprime resumen del sistema cada 1s")
    Monitor(ap.parse_args()).correr()


if __name__ == "__main__":
    main()
