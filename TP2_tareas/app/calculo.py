"""Lo que se ejecuta adentro de los procesos del pool.

Tienen que ser funciones de nivel módulo: ProcessPoolExecutor manda la
función y sus argumentos con pickle, y una lambda o una función anidada
no se pueden picklear (clase 22).
"""
import hashlib
import signal
import time


def preparar_proceso() -> None:
    """Corre una vez al nacer cada proceso del pool.

    Ctrl+C le manda SIGINT a todo el grupo de procesos de la terminal
    (clase 6). Si los hijos se murieran con esa señal, el pool se rompería
    justo cuando el padre está intentando apagarse en orden. Por eso el
    hijo la ignora y el único que decide cuándo terminar es el padre.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def calentar() -> None:
    """Tarea vacía para obligar a que los procesos arranquen de entrada."""
    time.sleep(0.05)


def hash_tramo(datos: bytes, iteraciones: int) -> bytes:
    """Aplica SHA-256 `iteraciones` veces. CPU puro: no cede nada a nadie."""
    for _ in range(iteraciones):
        datos = hashlib.sha256(datos).digest()
    return datos
