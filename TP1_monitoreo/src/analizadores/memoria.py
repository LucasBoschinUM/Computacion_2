"""Vista 2 — Memoria: campos Vm*, page faults y segmentos de /proc/<pid>/maps."""

import procfs
from analizadores.base import Analizador


class AnalizadorMemoria(Analizador):
    NOMBRE = "memoria"

    def extraer(self, pids):
        salida = {}
        for pid in pids:
            status = procfs.leer_status(pid)
            st = procfs.leer_stat(pid)
            if not status or not st:
                continue

            d = {
                "pid": pid,
                "comm": st["comm"],
                "vm_size": procfs.kb(status, "VmSize"),
                "vm_rss": procfs.kb(status, "VmRSS"),
                "vm_hwm": procfs.kb(status, "VmHWM"),   # pico historico de RSS
                "vm_data": procfs.kb(status, "VmData"),
                "vm_stk": procfs.kb(status, "VmStk"),
                "vm_exe": procfs.kb(status, "VmExe"),
                "vm_lib": procfs.kb(status, "VmLib"),
                "vm_swap": procfs.kb(status, "VmSwap"),
                "minflt": st["minflt"],   # sin acceso a disco (COW, page cache)
                "majflt": st["majflt"],   # requirio I/O de disco
                "cminflt": st["cminflt"],
                "cmajflt": st["cmajflt"],
                "segmentos": None,
            }

            # /proc/<pid>/maps es caro: un proceso grande puede tener miles
            # de lineas. Se parsea siempre para el proceso, pero el limite
            # real lo pone el intervalo de 3s de esta vista.
            d["segmentos"] = procfs.leer_maps(pid)
            salida[str(pid)] = d
        return salida
