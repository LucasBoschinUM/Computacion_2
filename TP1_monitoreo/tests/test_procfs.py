"""
Tests del parseo de /proc.

Se corren con:  python3 -m pytest tests/ -q
o sin pytest:   python3 tests/test_procfs.py

Los tests que dependen del sistema usan el propio proceso de test, que
siempre existe. Los que verifican parseo puro usan muestras fijas.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

import procfs  # noqa: E402


# --------------------------------------------------------------------------
# Parseo puro
# --------------------------------------------------------------------------

def test_mascara_vacia():
    assert procfs.decodificar_mascara("0000000000000000") == []


def test_mascara_sigterm():
    # bit 14 (0-indexado) => senal 15 => SIGTERM
    assert procfs.decodificar_mascara(f"{1 << 14:016x}") == ["SIGTERM"]


def test_mascara_multiple():
    # SIGHUP(1) + SIGINT(2) + SIGQUIT(3)
    valor = (1 << 0) | (1 << 1) | (1 << 2)
    assert procfs.decodificar_mascara(f"{valor:016x}") == \
        ["SIGHUP", "SIGINT", "SIGQUIT"]


def test_mascara_invalida():
    assert procfs.decodificar_mascara("no-es-hex") == []
    assert procfs.decodificar_mascara(None) == []


def test_mascara_con_limite():
    valor = (1 << 0) | (1 << 1) | (1 << 2) | (1 << 3)
    r = procfs.decodificar_mascara(f"{valor:016x}", maximo=2)
    assert r == ["SIGHUP", "SIGINT", "..."]


def test_tipo_fd():
    assert procfs.tipo_fd("socket:[12345]") == "socket"
    assert procfs.tipo_fd("pipe:[999]") == "pipe"
    assert procfs.tipo_fd("/dev/pts/3") == "tty"
    assert procfs.tipo_fd("/dev/null") == "dev"
    assert procfs.tipo_fd("/home/lisandro/a.log") == "file"
    assert procfs.tipo_fd("anon_inode:[eventfd]") == "anon"


def test_stat_con_parentesis_en_comm(tmpdir=None):
    """El caso patologico: comm con espacios y parentesis adentro.

    Un parser que hiciera split() a secas rompe aca. El nuestro recorta
    usando el ULTIMO ')'.
    """
    linea = ("4242 (mi (raro) proc) S 1 4242 4242 0 -1 4194304 100 0 0 0 "
             "10 20 0 0 20 0 3 0 5000 12345 67 " + " ".join(["0"] * 20))
    ini = linea.find("(")
    fin = linea.rfind(")")
    assert linea[ini + 1:fin] == "mi (raro) proc"
    resto = linea[fin + 2:].split()
    assert resto[0] == "S"          # campo 3
    assert int(resto[14 - 3]) == 10  # utime
    assert int(resto[15 - 3]) == 20  # stime


# --------------------------------------------------------------------------
# Contra el sistema real
# --------------------------------------------------------------------------

def test_listar_pids_incluye_el_propio():
    assert os.getpid() in procfs.listar_pids()


def test_stat_propio():
    st = procfs.leer_stat(os.getpid())
    assert st is not None
    assert st["pid"] == os.getpid()
    assert st["ppid"] == os.getppid()
    assert st["estado"] in procfs.ESTADOS
    assert st["num_threads"] >= 1


def test_status_propio():
    status = procfs.leer_status(os.getpid())
    assert status is not None
    assert procfs.kb(status, "VmRSS") > 0
    assert procfs.entero(status, "Threads") >= 1
    uid, gid = procfs.uid_gid(status)
    assert uid == os.getuid()


def test_stat_de_pid_inexistente():
    """Un PID que no existe devuelve None, no explota."""
    inexistente = 4194303
    assert procfs.leer_stat(inexistente) is None
    assert procfs.leer_status(inexistente) is None


def test_tids_propios():
    tids = procfs.listar_tids(os.getpid())
    assert os.getpid() in tids   # el thread lider tiene TID == PID


def test_fds_propios():
    lista, total = procfs.leer_fds(os.getpid())
    assert total >= 3            # al menos stdin/stdout/stderr
    assert all("tipo" in fd for fd in lista)


def test_maps_propio():
    seg = procfs.leer_maps(os.getpid())
    assert seg["regiones"] > 0
    assert seg["text"] > 0       # siempre hay codigo mapeado


def test_meminfo_y_loadavg():
    mem = procfs.leer_meminfo()
    assert mem["MemTotal"] > 0
    load = procfs.leer_loadavg()
    assert "1" in load
    assert procfs.leer_uptime() > 0


def test_stat_global():
    g = procfs.leer_stat_global()
    assert "cpu" in g["cpus"]
    assert g["cpus"]["cpu"]["total"] > 0
    assert g["btime"] > 0


def test_cmdline_propio():
    cmd = procfs.leer_cmdline(os.getpid())
    assert cmd and "python" in cmd.lower()


if __name__ == "__main__":
    fallos = 0
    for nombre, fn in sorted(globals().items()):
        if nombre.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {nombre}")
            except Exception as e:  # noqa: BLE001
                fallos += 1
                print(f"  FALLA {nombre}: {e!r}")
    print(f"\n{'TODO OK' if not fallos else f'{fallos} fallo(s)'}")
    sys.exit(1 if fallos else 0)
