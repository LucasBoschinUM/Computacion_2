"""Vista 1 — Resumen: estado, CPU%, RSS, threads, comando."""

import time

import procfs
from analizadores.base import Analizador


class AnalizadorResumen(Analizador):
    NOMBRE = "resumen"

    def __init__(self, *args):
        super().__init__(*args)
        # Estado privado del proceso analizador: jiffies de la lectura
        # anterior por PID. Vive solo aca, no hace falta compartirlo.
        self.previo = {}
        self.t_previo = None

    def extraer(self, pids):
        ahora = time.monotonic()
        dt = (ahora - self.t_previo) if self.t_previo else None
        self.t_previo = ahora

        nuevo_previo = {}
        salida = {}

        for pid in pids:
            st = procfs.leer_stat(pid)
            if not st:
                continue  # murio entre el listado y la lectura
            status = procfs.leer_status(pid)
            if not status:
                continue

            jiffies = st["utime"] + st["stime"]
            nuevo_previo[pid] = jiffies

            # CPU% = (delta de jiffies / HZ) / (segundos transcurridos) * 100
            # Es el mismo calculo que hace top: uso relativo a UN core, asi
            # que un proceso multihilo puede pasar el 100%.
            cpu = 0.0
            anterior = self.previo.get(pid)
            if dt and dt > 0 and anterior is not None and jiffies >= anterior:
                cpu = ((jiffies - anterior) / procfs.HZ) / dt * 100.0

            uid, gid = procfs.uid_gid(status)
            salida[str(pid)] = {
                "pid": pid,
                "ppid": st["ppid"],
                "uid": uid,
                "gid": gid,
                "usuario": procfs.usuario(uid),
                "estado": st["estado"],
                "estado_txt": procfs.ESTADOS.get(st["estado"], st["estado"]),
                "comm": st["comm"],
                "cmdline": procfs.leer_cmdline(pid) or f"[{st['comm']}]",
                "cpu": round(cpu, 1),
                "cpu_jiffies": jiffies,
                "threads": procfs.entero(status, "Threads", st["num_threads"]),
                "rss_kb": procfs.kb(status, "VmRSS"),
                "vsz_kb": procfs.kb(status, "VmSize"),
            }

        self.previo = nuevo_previo
        return salida
