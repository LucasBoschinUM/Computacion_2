"""
senales.py — Manejo de senales del proceso principal con el patron self-pipe.

Por que self-pipe y no hacer el trabajo dentro del handler:
un handler de senal corre de forma asincrona, interrumpiendo al proceso en
cualquier punto. Si adentro del handler abrimos archivos, tomamos locks o
tocamos el Manager, podemos re-entrar en una funcion que ya estaba a medio
ejecutar (por ejemplo malloc) y dejar el proceso en un estado inconsistente
o directamente deadlockeado.

La unica primitiva async-signal-safe que usamos en el handler es write(2)
sobre el extremo de escritura de un pipe: escribimos UN byte que identifica
la senal. El loop principal hace select() sobre el extremo de lectura y
procesa el evento de forma sincrona, en un punto seguro del programa.
"""

import os
import signal

_fd_escritura = None

# Un byte por senal. Se usa un byte y no una estructura porque write() de
# hasta PIPE_BUF bytes es atomico y no necesitamos sincronizacion.
CODIGOS = {
    signal.SIGINT: b"I",
    signal.SIGTERM: b"T",
    signal.SIGHUP: b"H",
    signal.SIGUSR1: b"1",
    signal.SIGUSR2: b"2",
    signal.SIGWINCH: b"W",
}


def _handler(signum, frame):
    # Unico syscall permitido aca: write. Sin prints, sin locks, sin I/O
    # bufferizada.
    try:
        os.write(_fd_escritura, CODIGOS.get(signum, b"?"))
    except (OSError, TypeError):
        # EAGAIN si el pipe se lleno (imposible en la practica con 1 byte)
        # o TypeError si todavia no se instalo. Se descarta silenciosamente.
        pass


def instalar():
    """Instala los handlers y devuelve el fd de lectura del self-pipe."""
    global _fd_escritura
    r, w = os.pipe()
    # No bloqueante en ambos extremos: el handler nunca debe quedarse
    # esperando, y el loop principal lee solo cuando select lo despierta.
    os.set_blocking(r, False)
    os.set_blocking(w, False)
    _fd_escritura = w
    for sig in CODIGOS:
        signal.signal(sig, _handler)
    return r


def ignorar_en_hijo():
    """Configuracion de senales para los procesos hijos.

    Los hijos ignoran SIGINT: cuando el usuario hace Ctrl+C, la senal se
    entrega a TODO el grupo de procesos del foreground. Si cada hijo la
    manejara por su cuenta moririan en desorden, sin flushear las queues.
    En cambio la maneja solo el padre, que ordena el apagado via el Event
    compartido. SIGTERM si se deja por default para poder matarlos a mano.
    """
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    signal.signal(signal.SIGUSR2, signal.SIG_IGN)
