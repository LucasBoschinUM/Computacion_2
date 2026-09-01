"""
procfs.py — Lectura y parseo manual de /proc.

Este modulo es la unica capa que toca el filesystem virtual del kernel.
No se usa psutil ni subprocess: todo se lee y se parsea a mano, que es
justamente el objetivo del TP.

Convenciones:
  - Toda funcion que lee un proceso puede devolver None si el proceso
    murio entre el listado de PIDs y la lectura (race inevitable con /proc).
  - Los tamanios de memoria se manejan siempre en KB.
"""

import os

PROC = "/proc"

# Jiffies por segundo. En Linux x86 normalmente 100, pero se consulta al
# sistema porque el kernel se puede compilar con CONFIG_HZ distinto.
HZ = os.sysconf("SC_CLK_TCK")
PAGE_KB = os.sysconf("SC_PAGE_SIZE") // 1024

ESTADOS = {
    "R": "Running",
    "S": "Sleeping",
    "D": "Disk-wait",
    "T": "Stopped",
    "t": "Tracing",
    "Z": "Zombie",
    "X": "Dead",
    "I": "Idle",
}

# Politicas de scheduling: valores de sched_setscheduler(2), campo 41 de stat.
POLITICAS = {
    0: "OTHER",
    1: "FIFO",
    2: "RR",
    3: "BATCH",
    4: "ISO",
    5: "IDLE",
    6: "DEADLINE",
}

# Nombres de senales indexados por numero (1..64). Se arma desde el modulo
# signal para no hardcodear valores que cambian entre arquitecturas.
import signal as _signal

NOMBRES_SENAL = {}
for _s in _signal.Signals:
    NOMBRES_SENAL.setdefault(int(_s), _s.name)

# Las senales 32 y 33 las reserva glibc para la implementacion de pthreads
# (setxid y cancel) y Python no las expone. De 34 en adelante son las de
# tiempo real, que se nombran relativas a SIGRTMIN.
NOMBRES_SENAL.setdefault(32, "SIGRT-2(glibc)")
NOMBRES_SENAL.setdefault(33, "SIGRT-1(glibc)")
for _n in range(34, 65):
    NOMBRES_SENAL.setdefault(_n, f"SIGRTMIN+{_n - 34}")


# --------------------------------------------------------------------------
# Lectura cruda
# --------------------------------------------------------------------------

def _leer(path):
    """Lee un archivo de /proc devolviendo texto, o None si no se puede.

    Los errores esperables son:
      - FileNotFoundError / ProcessLookupError: el proceso murio.
      - PermissionError: proceso de otro usuario (ej. /proc/1/fd sin root).
      - OSError generico: ESRCH o EINVAL al leer entradas volatiles.
    """
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return None


def listar_pids():
    """Devuelve la lista de PIDs vivos leyendo los directorios numericos."""
    try:
        return sorted(int(n) for n in os.listdir(PROC) if n.isdigit())
    except OSError:
        return []


def listar_tids(pid):
    """LWPs (threads) del proceso: cada uno es un directorio en /proc/<pid>/task."""
    try:
        return sorted(int(n) for n in os.listdir(f"{PROC}/{pid}/task") if n.isdigit())
    except OSError:
        return []


# --------------------------------------------------------------------------
# /proc/<pid>/stat
# --------------------------------------------------------------------------

def leer_stat(pid, tid=None):
    """Parsea /proc/<pid>/stat (o /proc/<pid>/task/<tid>/stat).

    El campo 2 (comm) viene entre parentesis y puede contener espacios y
    hasta parentesis, asi que se recorta usando el ULTIMO ')' antes de
    hacer el split. Este es el bug clasico de los parsers ingenuos.

    Devuelve un dict con los campos que usa el monitor. Los numeros de
    campo del comentario son los de proc(5), 1-indexados.
    """
    raw = _leer(f"{PROC}/{pid}/stat" if tid is None
                else f"{PROC}/{pid}/task/{tid}/stat")
    if not raw:
        return None

    ini = raw.find("(")
    fin = raw.rfind(")")
    if ini < 0 or fin < 0:
        return None

    comm = raw[ini + 1:fin]
    # resto[0] == campo 3. Para el campo N usar resto[N - 3].
    resto = raw[fin + 2:].split()
    if len(resto) < 40:
        return None

    def num(campo, default=0):
        try:
            return int(resto[campo - 3])
        except (IndexError, ValueError):
            return default

    return {
        "pid": int(raw[:ini].strip()),
        "comm": comm,
        "estado": resto[0],                      # campo 3
        "ppid": num(4),
        "pgid": num(5),                          # process group
        "sid": num(6),                           # sesion
        "minflt": num(10),
        "cminflt": num(11),
        "majflt": num(12),
        "cmajflt": num(13),
        "utime": num(14),                        # jiffies en user mode
        "stime": num(15),                        # jiffies en kernel mode
        "priority": num(18),
        "nice": num(19),
        "num_threads": num(20),
        "starttime": num(22),
        "vsize": num(23),
        "rss_pag": num(24),
        "processor": num(39, -1),                # ultima CPU en la que corrio
        "rt_priority": num(40),
        "policy": num(41),
    }


# --------------------------------------------------------------------------
# /proc/<pid>/status
# --------------------------------------------------------------------------

def leer_status(pid, tid=None):
    """Parsea /proc/<pid>/status a un dict clave -> valor (str crudo)."""
    raw = _leer(f"{PROC}/{pid}/status" if tid is None
                else f"{PROC}/{pid}/task/{tid}/status")
    if not raw:
        return None
    d = {}
    for linea in raw.splitlines():
        if ":" in linea:
            k, _, v = linea.partition(":")
            d[k.strip()] = v.strip()
    return d


def kb(status, clave):
    """Extrae un campo tipo 'VmRSS: 12345 kB' como entero de KB."""
    v = status.get(clave)
    if not v:
        return 0
    try:
        return int(v.split()[0])
    except (IndexError, ValueError):
        return 0


def entero(status, clave, default=0):
    v = status.get(clave)
    if not v:
        return default
    try:
        return int(v.split()[0])
    except (IndexError, ValueError):
        return default


# --------------------------------------------------------------------------
# Usuarios: /etc/passwd (no se usa pwd para poder cachear barato)
# --------------------------------------------------------------------------

_usuarios = None


def usuario(uid):
    """Traduce UID -> nombre leyendo /etc/passwd una sola vez por proceso."""
    global _usuarios
    if _usuarios is None:
        _usuarios = {}
        raw = _leer("/etc/passwd") or ""
        for linea in raw.splitlines():
            partes = linea.split(":")
            if len(partes) > 2 and partes[2].isdigit():
                _usuarios[int(partes[2])] = partes[0]
    return _usuarios.get(uid, str(uid))


def uid_gid(status):
    """De 'Uid: real efectivo saved fs' devuelve (uid_real, gid_real)."""
    try:
        uid = int(status.get("Uid", "0").split()[0])
    except (IndexError, ValueError):
        uid = -1
    try:
        gid = int(status.get("Gid", "0").split()[0])
    except (IndexError, ValueError):
        gid = -1
    return uid, gid


# --------------------------------------------------------------------------
# cmdline
# --------------------------------------------------------------------------

def leer_cmdline(pid):
    """Comando completo. Los argumentos vienen separados por NUL.

    Si viene vacio es un kernel thread (no tiene espacio de usuario), y se
    muestra el comm entre corchetes, igual que hace ps.
    """
    raw = _leer(f"{PROC}/{pid}/cmdline")
    if raw is None:
        return None
    partes = [p for p in raw.split("\0") if p]
    if partes:
        return " ".join(partes)
    st = leer_stat(pid)
    return f"[{st['comm']}]" if st else ""


# --------------------------------------------------------------------------
# File descriptors
# --------------------------------------------------------------------------

def tipo_fd(destino):
    """Infiere el tipo de un FD a partir del target del symlink."""
    if destino.startswith("socket:"):
        return "socket"
    if destino.startswith("pipe:"):
        return "pipe"
    if destino.startswith("anon_inode:"):
        return "anon"
    if destino.startswith("/dev/pts/") or destino in ("/dev/tty", "/dev/console"):
        return "tty"
    if destino.startswith("/dev/"):
        return "dev"
    if destino.startswith("/memfd:"):
        return "memfd"
    return "file"


def leer_fds(pid, limite=64):
    """Lista los FDs abiertos con su destino y tipo.

    Devuelve (lista, total). La lista puede venir recortada por `limite`
    porque un proceso puede tener miles de FDs y no queremos inflar el
    snapshot compartido.
    """
    ruta = f"{PROC}/{pid}/fd"
    try:
        nombres = sorted(os.listdir(ruta), key=lambda n: int(n) if n.isdigit() else 0)
    except OSError:
        return [], 0

    total = len(nombres)
    salida = []
    for n in nombres[:limite]:
        try:
            destino = os.readlink(f"{ruta}/{n}")
        except OSError:
            destino = "?"
        salida.append({"fd": n, "destino": destino, "tipo": tipo_fd(destino)})
    return salida, total


# --------------------------------------------------------------------------
# maps: segmentos de memoria agrupados
# --------------------------------------------------------------------------

def leer_maps(pid):
    """Agrupa /proc/<pid>/maps en categorias y suma tamanios en KB.

    Criterio de agrupacion:
      heap / stack        -> por el nombre pseudo entre corchetes
      text  (r-x)         -> codigo ejecutable mapeado
      data  (rw- privado) -> datos privados
      shared (p == 's')   -> mapeos compartidos
      ro    (r--)         -> solo lectura (rodata, mapeos de archivo)
    """
    raw = _leer(f"{PROC}/{pid}/maps")
    if raw is None:
        return None

    grupos = {"text": 0, "data": 0, "heap": 0, "stack": 0, "shared": 0, "ro": 0}
    regiones = 0
    for linea in raw.splitlines():
        partes = linea.split(None, 5)
        if len(partes) < 2:
            continue
        rango, perms = partes[0], partes[1]
        nombre = partes[5].strip() if len(partes) > 5 else ""
        try:
            ini, fin = rango.split("-")
            tam = (int(fin, 16) - int(ini, 16)) // 1024
        except ValueError:
            continue
        regiones += 1

        if nombre == "[heap]":
            grupos["heap"] += tam
        elif nombre.startswith("[stack"):
            grupos["stack"] += tam
        elif perms[3] == "s":
            grupos["shared"] += tam
        elif perms[2] == "x":
            grupos["text"] += tam
        elif perms[1] == "w":
            grupos["data"] += tam
        else:
            grupos["ro"] += tam

    grupos["regiones"] = regiones
    return grupos


# --------------------------------------------------------------------------
# Senales: mascaras hexadecimales de 64 bits
# --------------------------------------------------------------------------

def decodificar_mascara(hexstr, maximo=None):
    """Convierte una mascara tipo '0000000180014a03' a nombres de senal.

    El bit 0 corresponde a la senal 1 (SIGHUP), el bit 1 a la 2 (SIGINT),
    etc. Las senales 33-64 son las de tiempo real (SIGRT*).
    """
    try:
        valor = int(hexstr, 16)
    except (TypeError, ValueError):
        return []
    nombres = []
    for bit in range(64):
        if valor & (1 << bit):
            num = bit + 1
            nombres.append(NOMBRES_SENAL.get(num, f"SIG{num}"))
            if maximo and len(nombres) >= maximo:
                nombres.append("...")
                break
    return nombres


# --------------------------------------------------------------------------
# Stats globales del sistema
# --------------------------------------------------------------------------

def leer_stat_global():
    """Linea agregada 'cpu' de /proc/stat + btime, en jiffies acumulados."""
    raw = _leer(f"{PROC}/stat")
    if not raw:
        return None
    salida = {"btime": 0, "cpus": {}}
    for linea in raw.splitlines():
        if linea.startswith("cpu"):
            campos = linea.split()
            nombre = campos[0]
            vals = [int(x) for x in campos[1:11]] if len(campos) >= 11 else []
            if not vals:
                continue
            salida["cpus"][nombre] = {
                "user": vals[0], "nice": vals[1], "system": vals[2],
                "idle": vals[3], "iowait": vals[4], "irq": vals[5],
                "softirq": vals[6], "steal": vals[7],
                "total": sum(vals),
            }
        elif linea.startswith("btime"):
            salida["btime"] = int(linea.split()[1])
    return salida


def leer_meminfo():
    raw = _leer(f"{PROC}/meminfo")
    if not raw:
        return {}
    d = {}
    for linea in raw.splitlines():
        k, _, v = linea.partition(":")
        try:
            d[k.strip()] = int(v.split()[0])
        except (IndexError, ValueError):
            pass
    return d


def leer_loadavg():
    raw = _leer(f"{PROC}/loadavg")
    if not raw:
        return {}
    c = raw.split()
    try:
        corriendo, total = c[3].split("/")
        return {
            "1": float(c[0]), "5": float(c[1]), "15": float(c[2]),
            "corriendo": int(corriendo), "entidades": int(total),
        }
    except (IndexError, ValueError):
        return {}


def leer_uptime():
    raw = _leer(f"{PROC}/uptime")
    if not raw:
        return 0.0
    try:
        return float(raw.split()[0])
    except (IndexError, ValueError):
        return 0.0
