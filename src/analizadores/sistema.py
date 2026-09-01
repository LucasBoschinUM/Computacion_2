"""
Vista 7 — Sistema global: CPU, memoria, load average y totales.

Los porcentajes de CPU de /proc/stat son contadores ACUMULADOS desde el
boot, no instantaneos. Para sacar un porcentaje util hay que calcular el
delta contra la lectura anterior y dividir por el delta total.
"""

import time

import procfs
from analizadores.base import Analizador


class AnalizadorSistema(Analizador):
    NOMBRE = "sistema"

    def __init__(self, *args):
        super().__init__(*args)
        self.cpu_previo = None

    def extraer(self, pids):
        glob = procfs.leer_stat_global() or {"cpus": {}, "btime": 0}
        agregado = glob["cpus"].get("cpu", {})

        porcentajes = {}
        if agregado and self.cpu_previo:
            dt = agregado["total"] - self.cpu_previo["total"]
            if dt > 0:
                for k in ("user", "nice", "system", "idle", "iowait",
                          "irq", "softirq", "steal"):
                    delta = agregado[k] - self.cpu_previo.get(k, 0)
                    porcentajes[k] = round(delta / dt * 100.0, 1)
        if agregado:
            self.cpu_previo = agregado

        # Recuento por estado. Se recorre /proc una vez mas aca porque el
        # analizador de resumen puede ir a otro ritmo y queremos que la
        # vista Sistema sea internamente consistente.
        por_estado = {}
        threads_totales = 0
        for pid in pids:
            st = procfs.leer_stat(pid)
            if not st:
                continue
            por_estado[st["estado"]] = por_estado.get(st["estado"], 0) + 1
            threads_totales += st["num_threads"]

        mem = procfs.leer_meminfo()
        uptime = procfs.leer_uptime()

        return {
            "cpu_pct": porcentajes,
            "nucleos": max(0, len(glob["cpus"]) - 1),
            "load": procfs.leer_loadavg(),
            "mem_total": mem.get("MemTotal", 0),
            "mem_libre": mem.get("MemFree", 0),
            "mem_disponible": mem.get("MemAvailable", 0),
            "buffers": mem.get("Buffers", 0),
            "cached": mem.get("Cached", 0),
            "swap_total": mem.get("SwapTotal", 0),
            "swap_libre": mem.get("SwapFree", 0),
            "procesos_total": len(pids),
            "por_estado": por_estado,
            "zombies": por_estado.get("Z", 0),
            "threads_totales": threads_totales,
            "boot_time": glob["btime"],
            "uptime_s": uptime,
            "ahora": time.time(),
        }
