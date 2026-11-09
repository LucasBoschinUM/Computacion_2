"""
Vista 4 — Threads (LWPs).

En Linux un thread es un LWP: una tarea planificable mas, con su propio TID.
Por eso aparece como un directorio en /proc/<pid>/task/<tid>/ con la misma
estructura que un proceso. El "proceso" es el thread group: su TGID coincide
con el TID del thread principal, y eso es lo que ve el usuario como PID.
"""

import time

import procfs
from analizadores.base import Analizador


class AnalizadorThreads(Analizador):
    NOMBRE = "threads"

    def __init__(self, *args):
        super().__init__(*args)
        self.previo = {}     # (pid, tid) -> jiffies
        self.t_previo = None

    def extraer(self, pids):
        ahora = time.monotonic()
        dt = (ahora - self.t_previo) if self.t_previo else None
        self.t_previo = ahora

        nuevo_previo = {}
        salida = {}

        for pid in pids:
            tids = procfs.listar_tids(pid)
            if not tids:
                continue

            hilos = []
            for tid in tids:
                st = procfs.leer_stat(pid, tid)
                if not st:
                    continue
                jiffies = st["utime"] + st["stime"]
                clave = (pid, tid)
                nuevo_previo[clave] = jiffies

                cpu = 0.0
                ant = self.previo.get(clave)
                if dt and dt > 0 and ant is not None and jiffies >= ant:
                    cpu = ((jiffies - ant) / procfs.HZ) / dt * 100.0

                comm = procfs._leer(f"/proc/{pid}/task/{tid}/comm")
                status = procfs.leer_status(pid, tid) or {}

                hilos.append({
                    "tid": tid,
                    "principal": tid == pid,      # el thread lider del grupo
                    "nombre": (comm or st["comm"]).strip(),
                    "estado": st["estado"],
                    "estado_txt": procfs.ESTADOS.get(st["estado"], st["estado"]),
                    "cpu": round(cpu, 1),
                    "utime": st["utime"],
                    "stime": st["stime"],
                    "cpu_actual": st["processor"],
                    "ctx_vol": procfs.entero(status, "voluntary_ctxt_switches"),
                    "ctx_invol": procfs.entero(status, "nonvoluntary_ctxt_switches"),
                })

            hilos.sort(key=lambda h: h["cpu"], reverse=True)
            st_padre = procfs.leer_stat(pid)
            salida[str(pid)] = {
                "pid": pid,
                "comm": st_padre["comm"] if st_padre else "?",
                "cantidad": len(hilos),
                "hilos": hilos if self.verbose.value else hilos[:32],
            }

        self.previo = nuevo_previo
        return salida
