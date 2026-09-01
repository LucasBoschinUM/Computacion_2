"""Vista 3 — File descriptors: /proc/<pid>/fd y el destino de cada symlink."""

import procfs
from analizadores.base import Analizador


class AnalizadorFds(Analizador):
    NOMBRE = "fds"

    def extraer(self, pids):
        # En modo verbose (toggle con SIGUSR2) se guardan mas FDs por
        # proceso. Sin el toggle se recorta a 24 para no inflar el snapshot
        # compartido: cada entrada viaja pickleada al Manager.
        limite = 200 if self.verbose.value else 24

        salida = {}
        for pid in pids:
            st = procfs.leer_stat(pid)
            if not st:
                continue
            lista, total = procfs.leer_fds(pid, limite=limite)

            por_tipo = {}
            for fd in lista:
                por_tipo[fd["tipo"]] = por_tipo.get(fd["tipo"], 0) + 1

            salida[str(pid)] = {
                "pid": pid,
                "comm": st["comm"],
                "total": total,
                "mostrados": len(lista),
                "por_tipo": por_tipo,
                "lista": lista,
                # total == 0 con lista vacia normalmente significa EACCES:
                # es un proceso de otro usuario y no somos root.
                "accesible": bool(lista) or total > 0,
            }
        return salida
