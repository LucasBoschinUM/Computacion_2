"""
Vista 6 — Scheduling: nice, priority, politica, afinidad y context switches.

Distincion importante entre los dos contadores de switches:
  voluntary_ctxt_switches    : el proceso cedio la CPU solo, tipicamente
                               porque se bloqueo en I/O. Alto en procesos
                               I/O-bound.
  nonvoluntary_ctxt_switches : el scheduler lo desalojo al agotarse su
                               time slice o al aparecer una tarea de mayor
                               prioridad. Alto en procesos CPU-bound.
"""

import procfs
from analizadores.base import Analizador


class AnalizadorScheduling(Analizador):
    NOMBRE = "scheduling"

    def extraer(self, pids):
        salida = {}
        for pid in pids:
            st = procfs.leer_stat(pid)
            status = procfs.leer_status(pid)
            if not st or not status:
                continue

            salida[str(pid)] = {
                "pid": pid,
                "comm": st["comm"],
                "nice": st["nice"],
                "priority": st["priority"],
                "politica": procfs.POLITICAS.get(st["policy"], str(st["policy"])),
                "rt_priority": st["rt_priority"],
                "afinidad": status.get("Cpus_allowed_list", "?"),
                "cpu_actual": st["processor"],
                "ctx_vol": procfs.entero(status, "voluntary_ctxt_switches"),
                "ctx_invol": procfs.entero(status, "nonvoluntary_ctxt_switches"),
                "utime_s": round(st["utime"] / procfs.HZ, 2),
                "stime_s": round(st["stime"] / procfs.HZ, 2),
                "sid": st["sid"],
                "pgid": st["pgid"],
            }
        return salida
