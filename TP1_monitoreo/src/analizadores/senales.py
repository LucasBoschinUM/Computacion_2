"""
Vista 5 — Senales.

Los campos Sig* de /proc/<pid>/status son mascaras hexadecimales de 64 bits.
El bit N-1 prendido significa que la senal N esta en ese conjunto.

  SigPnd : pendientes para el thread
  ShdPnd : pendientes para todo el thread group (shared)
  SigBlk : bloqueadas por sigprocmask
  SigIgn : con disposicion SIG_IGN
  SigCgt : con un handler propio instalado (caught)

SIGKILL y SIGSTOP nunca aparecen en SigIgn ni en SigCgt: el kernel no
permite bloquearlas, ignorarlas ni capturarlas.
"""

import procfs
from analizadores.base import Analizador

CAMPOS = ("SigPnd", "ShdPnd", "SigBlk", "SigIgn", "SigCgt")


class AnalizadorSenales(Analizador):
    NOMBRE = "senales"

    def extraer(self, pids):
        limite = None if self.verbose.value else 12
        salida = {}
        for pid in pids:
            status = procfs.leer_status(pid)
            st = procfs.leer_stat(pid)
            if not status or not st:
                continue

            d = {"pid": pid, "comm": st["comm"]}
            for campo in CAMPOS:
                crudo = status.get(campo, "0")
                d[campo] = {
                    "hex": crudo,
                    "nombres": procfs.decodificar_mascara(crudo, maximo=limite),
                }
            salida[str(pid)] = d
        return salida
