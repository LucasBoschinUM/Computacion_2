"""Paquete de los 7 analizadores. Cada uno corre en su propio proceso."""

from analizadores.resumen import AnalizadorResumen
from analizadores.memoria import AnalizadorMemoria
from analizadores.fds import AnalizadorFds
from analizadores.threads import AnalizadorThreads
from analizadores.senales import AnalizadorSenales
from analizadores.scheduling import AnalizadorScheduling
from analizadores.sistema import AnalizadorSistema

TODOS = [
    AnalizadorResumen,
    AnalizadorMemoria,
    AnalizadorFds,
    AnalizadorThreads,
    AnalizadorSenales,
    AnalizadorScheduling,
    AnalizadorSistema,
]
