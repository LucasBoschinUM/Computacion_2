"""
recolector.py — Proceso que descubre los PIDs vivos y reparte el trabajo.

Es el unico componente que hace el listado de /proc. Publica la lista en
una Queue por analizador (fan-out) en vez de que cada analizador liste
/proc por su cuenta: asi todos los analizadores de un mismo ciclo trabajan
sobre el MISMO conjunto de PIDs y las vistas quedan coherentes entre si.

Las queues son de tamanio acotado (maxsize) y se escribe con put_nowait:
si un analizador lento no consume, preferimos descartar la lista vieja
antes que bloquear al recolector y frenar a todos los demas.
"""

import queue
import time

import procfs
import senales


def dormir(intervalo, apagando, paso=0.2):
    """Duerme `intervalo.value` segundos pero chequeando el flag de apagado.

    Se despierta cada 200 ms para que un Ctrl+C no tenga que esperar los
    10 segundos del analizador mas lento. Ademas relee intervalo.value en
    cada vuelta, asi un cambio hecho con '+'/'-' desde la TUI tiene efecto
    casi inmediato.
    """
    restante = float(intervalo.value)
    while restante > 0 and not apagando.is_set():
        time.sleep(min(paso, restante))
        restante -= paso


def correr(colas_pids, intervalo, apagando):
    senales.ignorar_en_hijo()

    while not apagando.is_set():
        pids = procfs.listar_pids()
        for q in colas_pids:
            try:
                q.put_nowait(pids)
            except queue.Full:
                pass
        dormir(intervalo, apagando)
