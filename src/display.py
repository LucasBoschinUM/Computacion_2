"""
display.py — TUI con curses.

Se eligio curses (stdlib) sobre rich por dos razones concretas:
  1. Cero dependencias externas, con lo cual el Dockerfile no necesita pip.
  2. curses ya trae el manejo de teclado no bloqueante (halfdelay/getch) y
     el decodificado de teclas especiales (KEY_UP, KEY_DOWN, KEY_RESIZE)
     que el TP pide en los keybindings. Con rich habria que sumar
     prompt_toolkit o leer termios a mano.

Este proceso es SOLO LECTOR del snapshot. Lo unico que escribe son los
Value('d') de intervalos (teclas +/-), que son escrituras atomicas de un
double con lock propio: no hay riesgo de ver un valor a medio escribir.
"""

import curses
import os
import sys
import time

VISTAS = [
    ("resumen", "Resumen", "1r"),
    ("memoria", "Memoria", "2m"),
    ("fds", "FDs", "3f"),
    ("threads", "Threads", "4t"),
    ("senales", "Senales", "5s"),
    ("scheduling", "Sched", "6p"),
    ("sistema", "Sistema", "7g"),
]

ORDENES = ["cpu", "rss", "pid"]

AYUDA = [
    "AYUDA — Monitor de procesos y threads (Computacion II)",
    "",
    "  1-7 / r m f t s p g   cambiar de vista",
    "  flechas / j k         navegar la lista de procesos",
    "  PgUp / PgDn           saltar de a una pagina",
    "  Enter                 fijar (pin) el proceso seleccionado",
    "  /                     filtrar por nombre de comando",
    "  u                     filtrar por usuario",
    "  c                     alternar orden: CPU% -> RSS -> PID",
    "  + / -                 ajustar el intervalo de la vista activa",
    "  h / ?                 esta ayuda",
    "  q                     salir limpiamente",
    "",
    "Senales que acepta el monitor (kill -SIG <pid del main>):",
    "  SIGINT / SIGTERM      apagado ordenado",
    "  SIGHUP                recarga config.json",
    "  SIGUSR1               vuelca el snapshot a dump_<ts>.json",
    "  SIGUSR2               alterna modo verbose",
    "  SIGWINCH              repinta la pantalla",
    "",
    "  (cualquier tecla para volver)",
]


def humano(kb_val):
    """Formatea KB a una unidad legible."""
    if kb_val is None:
        return "-"
    if kb_val < 1024:
        return f"{kb_val}K"
    if kb_val < 1024 * 1024:
        return f"{kb_val / 1024:.1f}M"
    return f"{kb_val / 1048576:.2f}G"


def recortar(texto, ancho):
    texto = texto.replace("\n", " ")
    return texto if len(texto) <= ancho else texto[:max(0, ancho - 1)] + "~"


class Interfaz:
    def __init__(self, pantalla, snapshot, intervalos, minimos, apagando,
                 tuberia, config, verbose):
        self.p = pantalla
        self.snapshot = snapshot
        self.intervalos = intervalos
        self.minimos = minimos
        self.apagando = apagando
        self.tuberia = tuberia
        self.verbose = verbose

        self.vista = 0
        self.seleccion = 0
        self.scroll = 0
        self.pin = None
        self.filtro_cmd = config.get("filtro_default", "")
        self.filtro_usuario = ""
        self.orden = config.get("orden_default", "cpu")
        self.aviso = ""
        self.aviso_hasta = 0.0
        self.mostrando_ayuda = False
        self.lista = []

    # ------------------------------------------------------------------
    # Datos
    # ------------------------------------------------------------------

    def seccion(self, nombre):
        s = self.snapshot.get(nombre)
        return s if s else {"datos": {}, "ts": 0.0, "ms": 0.0, "ciclos": 0}

    def construir_lista(self):
        """Arma la lista visible aplicando filtros y orden.

        Se lee la seccion 'resumen' UNA sola vez y se copia a memoria local.
        Cada acceso a un Manager.dict es un round-trip IPC al proceso del
        Manager; hacerlo dentro del loop de render seria carisimo.
        """
        datos = dict(self.seccion("resumen")["datos"])
        procs = [p for p in datos.values() if isinstance(p, dict)]

        if self.filtro_cmd:
            f = self.filtro_cmd.lower()
            procs = [p for p in procs
                     if f in p.get("comm", "").lower()
                     or f in p.get("cmdline", "").lower()]
        if self.filtro_usuario:
            f = self.filtro_usuario.lower()
            procs = [p for p in procs if f in p.get("usuario", "").lower()]

        if self.orden == "cpu":
            procs.sort(key=lambda p: (p.get("cpu", 0.0), p.get("rss_kb", 0)),
                       reverse=True)
        elif self.orden == "rss":
            procs.sort(key=lambda p: p.get("rss_kb", 0), reverse=True)
        else:
            procs.sort(key=lambda p: p.get("pid", 0))

        self.lista = procs

    def pid_activo(self):
        """PID sobre el que se muestra el detalle.

        Si hay pin, gana el pin: es justamente para que el detalle no salte
        cuando el reordenamiento por CPU mueve las filas debajo del cursor.
        """
        if self.pin is not None:
            return self.pin
        if 0 <= self.seleccion < len(self.lista):
            return self.lista[self.seleccion]["pid"]
        return None

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def escribir(self, y, x, texto, attr=0):
        alto, ancho = self.p.getmaxyx()
        if y < 0 or y >= alto or x >= ancho:
            return
        try:
            self.p.addnstr(y, x, texto, ancho - x - 1, attr)
        except curses.error:
            pass

    def fila(self, y, texto, attr=0):
        """Pinta una fila COMPLETA, de la columna 0 al ancho de la terminal.

        Se pinta siempre el ancho entero (y no solo el largo del texto)
        para que un frame corto nunca deje colgando la cola del frame
        anterior. Es mas barato que un clear() global y no produce
        parpadeo.
        """
        alto, ancho = self.p.getmaxyx()
        if y < 0 or y >= alto:
            return
        try:
            self.p.addnstr(y, 0, texto.ljust(ancho)[:ancho - 1], ancho - 1, attr)
        except curses.error:
            pass

    def dibujar(self):
        self.p.erase()
        alto, ancho = self.p.getmaxyx()

        if self.mostrando_ayuda:
            for i in range(alto - 1):
                linea = AYUDA[i] if i < len(AYUDA) else ""
                self.fila(1 + i, "  " + linea,
                          curses.A_BOLD if i == 0 else 0)
            self.p.refresh()
            return

        self.construir_lista()
        self.dibujar_encabezado(ancho)
        alto_lista = max(3, (alto - 6) // 2)
        fin_lista = self.dibujar_lista(3, alto_lista, ancho)
        self.dibujar_detalle(fin_lista + 1, alto - fin_lista - 2, ancho)
        self.dibujar_pie(alto - 1, ancho)
        self.p.refresh()

    def dibujar_encabezado(self, ancho):
        clave, titulo, _ = VISTAS[self.vista]
        sec = self.seccion(clave)
        edad = time.time() - sec["ts"] if sec["ts"] else -1
        iv = self.intervalos[clave].value

        izq = f" MONITOR /proc  ·  vista: {titulo}"
        der = (f"int {iv:.1f}s  ·  edad {edad:.1f}s  ·  "
               f"{sec['ms']:.0f}ms  ·  ciclo {sec['ciclos']}"
               f"{'  · VERBOSE' if self.verbose.value else ''} ")
        self.fila(0, izq, curses.A_REVERSE | curses.A_BOLD)
        self.escribir(0, max(0, ancho - len(der) - 1), der,
                      curses.A_REVERSE | curses.A_BOLD)

        self.fila(1, "")
        x = 1
        for i, (_, nombre, teclas) in enumerate(VISTAS):
            etiqueta = f" {i + 1}:{nombre} "
            self.escribir(1, x, etiqueta,
                          curses.A_REVERSE if i == self.vista else 0)
            x += len(etiqueta) + 1

        estado = (f"orden:{self.orden}  procesos:{len(self.lista)}"
                  f"{'  filtro:/' + self.filtro_cmd if self.filtro_cmd else ''}"
                  f"{'  user:' + self.filtro_usuario if self.filtro_usuario else ''}"
                  f"{'  PIN:' + str(self.pin) if self.pin else ''}")
        self.fila(2, " " + estado, curses.A_DIM)

    def dibujar_lista(self, y0, alto, ancho):
        cab = (f"{'PID':>7} {'PPID':>7} {'USUARIO':<10} {'S':<1} "
               f"{'CPU%':>6} {'RSS':>8} {'THR':>4}  COMANDO")
        self.fila(y0, " " + cab, curses.A_REVERSE)

        filas = alto - 1
        if self.seleccion < self.scroll:
            self.scroll = self.seleccion
        elif self.seleccion >= self.scroll + filas:
            self.scroll = self.seleccion - filas + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self.lista) - filas)))

        for i in range(filas):
            idx = self.scroll + i
            if idx >= len(self.lista):
                self.fila(y0 + 1 + i, "")
                continue
            p = self.lista[idx]
            marca = "*" if self.pin == p["pid"] else " "
            linea = (f"{p['pid']:>7} {p['ppid']:>7} "
                     f"{recortar(p['usuario'], 10):<10} {p['estado']:<1} "
                     f"{p['cpu']:>6.1f} {humano(p['rss_kb']):>8} "
                     f"{p['threads']:>4}{marca} {p['cmdline']}")
            attr = curses.A_REVERSE if idx == self.seleccion else 0
            if p["estado"] == "Z":
                attr |= curses.A_BOLD
            self.fila(y0 + 1 + i, " " + recortar(linea, ancho - 3), attr)
        return y0 + alto

    def dibujar_detalle(self, y0, alto, ancho):
        clave, titulo, _ = VISTAS[self.vista]
        pid = self.pid_activo()
        cab = f" DETALLE · {titulo}" + (f" · PID {pid}" if pid else "")
        self.fila(y0, " " + cab, curses.A_REVERSE)

        sec = self.seccion(clave)
        datos = sec["datos"]

        if "_error" in datos:
            self.escribir(y0 + 1, 3, f"error en el analizador: {datos['_error']}")
            return

        if clave == "sistema":
            lineas = self._detalle_sistema(datos)
        elif pid is None:
            lineas = ["(sin proceso seleccionado)"]
        else:
            d = datos.get(str(pid))
            if not d:
                lineas = ["(sin datos todavia para este PID en esta vista;",
                          " el analizador refresca cada "
                          f"{self.intervalos[clave].value:.1f}s)"]
            else:
                lineas = getattr(self, f"_detalle_{clave}")(d)

        lineas = lineas[:max(0, alto - 1)]
        lineas += [""] * max(0, alto - 1 - len(lineas))
        for i, linea in enumerate(lineas):
            self.fila(y0 + 1 + i, "   " + recortar(linea, ancho - 5))

    # -- un formateador por vista --------------------------------------

    def _detalle_resumen(self, d):
        return [
            f"comando   : {d['cmdline']}",
            f"comm      : {d['comm']}    estado: {d['estado']} ({d['estado_txt']})",
            f"pid/ppid  : {d['pid']} / {d['ppid']}",
            f"usuario   : {d['usuario']} (uid {d['uid']}, gid {d['gid']})",
            f"cpu       : {d['cpu']:.1f} %   (jiffies acumulados: {d['cpu_jiffies']})",
            f"memoria   : RSS {humano(d['rss_kb'])}   VSZ {humano(d['vsz_kb'])}",
            f"threads   : {d['threads']}",
        ]

    def _detalle_memoria(self, d):
        seg = d.get("segmentos") or {}
        return [
            f"VmSize {humano(d['vm_size']):>9}   VmRSS  {humano(d['vm_rss']):>9}"
            f"   VmHWM {humano(d['vm_hwm']):>9}   VmSwap {humano(d['vm_swap']):>9}",
            f"VmData {humano(d['vm_data']):>9}   VmStk  {humano(d['vm_stk']):>9}"
            f"   VmExe {humano(d['vm_exe']):>9}   VmLib  {humano(d['vm_lib']):>9}",
            "",
            f"page faults: minor {d['minflt']}  major {d['majflt']}"
            f"   (hijos: min {d['cminflt']} / maj {d['cmajflt']})",
            "",
            "segmentos de /proc/<pid>/maps (agrupados por permisos):",
            f"  text  {humano(seg.get('text', 0)):>9}"
            f"   data  {humano(seg.get('data', 0)):>9}"
            f"   heap  {humano(seg.get('heap', 0)):>9}",
            f"  stack {humano(seg.get('stack', 0)):>9}"
            f"   shared{humano(seg.get('shared', 0)):>9}"
            f"   ro    {humano(seg.get('ro', 0)):>9}",
            f"  regiones mapeadas: {seg.get('regiones', 0)}",
        ]

    def _detalle_fds(self, d):
        if not d["accesible"]:
            return ["sin permisos para leer /proc/<pid>/fd "
                    "(proceso de otro usuario, hace falta root)"]
        lineas = [f"FDs abiertos: {d['total']}  "
                  f"(mostrando {d['mostrados']}; SIGUSR2 amplia el limite)",
                  "por tipo: " + "  ".join(f"{k}={v}"
                                           for k, v in sorted(d["por_tipo"].items())),
                  ""]
        for fd in d["lista"]:
            lineas.append(f"  {fd['fd']:>4}  {fd['tipo']:<7} -> {fd['destino']}")
        return lineas

    def _detalle_threads(self, d):
        lineas = [f"{d['cantidad']} thread(s) en /proc/{d['pid']}/task/",
                  f"  {'TID':>7} {'NOMBRE':<16} {'S':<1} {'CPU%':>6} "
                  f"{'utime':>8} {'stime':>8} {'CPU':>4} {'ctx-vol':>9} "
                  f"{'ctx-inv':>9}"]
        for h in d["hilos"]:
            marca = "*" if h["principal"] else " "
            lineas.append(
                f"  {h['tid']:>7}{marca}{recortar(h['nombre'], 15):<15} "
                f"{h['estado']:<1} {h['cpu']:>6.1f} {h['utime']:>8} "
                f"{h['stime']:>8} {h['cpu_actual']:>4} {h['ctx_vol']:>9} "
                f"{h['ctx_invol']:>9}")
        lineas.append("")
        lineas.append("  (* = thread lider del grupo; su TID es igual al PID)")
        return lineas

    def _detalle_senales(self, d):
        etiquetas = {
            "SigPnd": "pendientes (thread)",
            "ShdPnd": "pendientes (grupo) ",
            "SigBlk": "bloqueadas         ",
            "SigIgn": "ignoradas          ",
            "SigCgt": "con handler propio ",
        }
        lineas = []
        for campo, etiqueta in etiquetas.items():
            info = d[campo]
            nombres = ", ".join(info["nombres"]) or "(ninguna)"
            lineas.append(f"{campo}  {etiqueta}  0x{info['hex']}")
            lineas.append(f"          {nombres}")
        lineas.append("")
        lineas.append("SIGKILL(9) y SIGSTOP(19) nunca pueden bloquearse,")
        lineas.append("ignorarse ni capturarse: el kernel lo impide.")
        return lineas

    def _detalle_scheduling(self, d):
        return [
            f"politica      : SCHED_{d['politica']}"
            f"    rt_priority: {d['rt_priority']}",
            f"nice          : {d['nice']:<6} priority: {d['priority']}",
            f"afinidad CPU  : {d['afinidad']}   (corriendo ahora en CPU "
            f"{d['cpu_actual']})",
            f"tiempo CPU    : user {d['utime_s']}s   system {d['stime_s']}s",
            "",
            f"context switches voluntarios   : {d['ctx_vol']}"
            "   (cedio la CPU, tipico de I/O-bound)",
            f"context switches involuntarios : {d['ctx_invol']}"
            "   (lo desalojo el scheduler, tipico de CPU-bound)",
            "",
            f"session id (SID): {d['sid']}    process group (PGID): {d['pgid']}",
        ]

    def _detalle_sistema(self, d):
        if not d:
            return ["(esperando primer ciclo del analizador de sistema)"]
        cpu = d.get("cpu_pct", {})
        load = d.get("load", {})
        usada = d["mem_total"] - d.get("mem_disponible", d["mem_libre"])
        swap_usada = d["swap_total"] - d["swap_libre"]
        up = int(d.get("uptime_s", 0))

        lineas = [
            f"CPU ({d['nucleos']} nucleos)  user {cpu.get('user', 0)}%  "
            f"sys {cpu.get('system', 0)}%  idle {cpu.get('idle', 0)}%  "
            f"iowait {cpu.get('iowait', 0)}%  irq {cpu.get('irq', 0)}%  "
            f"steal {cpu.get('steal', 0)}%",
            f"load average   {load.get('1', 0)}  {load.get('5', 0)}  "
            f"{load.get('15', 0)}   "
            f"(corriendo {load.get('corriendo', 0)}/{load.get('entidades', 0)})",
            f"memoria        usada {humano(usada)} / {humano(d['mem_total'])}"
            f"   libre {humano(d['mem_libre'])}"
            f"   buffers {humano(d['buffers'])}   cache {humano(d['cached'])}",
            f"swap           usada {humano(swap_usada)} / "
            f"{humano(d['swap_total'])}",
            f"uptime         {up // 3600}h {(up % 3600) // 60}m {up % 60}s"
            f"    boot_time {d['boot_time']}",
            f"procesos       {d['procesos_total']}   threads "
            f"{d['threads_totales']}   zombies {d['zombies']}",
            "por estado     " + "  ".join(
                f"{k}={v}" for k, v in sorted(d.get("por_estado", {}).items())),
            "",
            "top 3 CPU:  " + "   ".join(
                f"{p[0]}:{p[1]} {p[2]}%" for p in d.get("top_cpu", [])),
            "top 3 RSS:  " + "   ".join(
                f"{p[0]}:{p[1]} {humano(p[3])}" for p in d.get("top_rss", [])),
        ]
        return lineas

    def dibujar_pie(self, y, ancho):
        if self.aviso and time.time() < self.aviso_hasta:
            self.fila(y, " " + recortar(self.aviso, ancho - 3),
                      curses.A_BOLD | curses.A_REVERSE)
            return
        pie = (" 1-7 vista | flechas navegar | Enter pin | / cmd | u user | "
               "c orden | +/- intervalo | h ayuda | q salir")
        self.fila(y, " " + recortar(pie, ancho - 3), curses.A_DIM)

    def notificar(self, texto, segundos=3.0):
        self.aviso = texto
        self.aviso_hasta = time.time() + segundos

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    def pedir_texto(self, prompt, actual=""):
        """Mini editor de una linea en el pie, sin salir del modo curses."""
        alto, ancho = self.p.getmaxyx()
        buf = list(actual)
        curses.curs_set(1)
        try:
            while True:
                texto = prompt + "".join(buf)
                self.fila(alto - 1, " " + texto, curses.A_REVERSE)
                self.p.refresh()
                c = self.p.getch()
                if c in (10, 13, curses.KEY_ENTER):
                    return "".join(buf)
                if c == 27:                       # ESC: cancelar
                    return actual
                if c in (curses.KEY_BACKSPACE, 127, 8):
                    if buf:
                        buf.pop()
                elif 32 <= c < 127:
                    buf.append(chr(c))
        finally:
            curses.curs_set(0)

    def ajustar_intervalo(self, delta):
        clave, titulo, _ = VISTAS[self.vista]
        v = self.intervalos[clave]
        minimo = self.minimos.get(clave, 0.5)
        # Escritura sobre memoria compartida: el Value trae su propio lock,
        # con lo cual el analizador nunca lee un double a medio escribir.
        nuevo = max(minimo, min(60.0, round(v.value + delta, 2)))
        v.value = nuevo
        tope = " (minimo)" if nuevo <= minimo else ""
        self.notificar(f"intervalo de {titulo}: {nuevo:.1f}s{tope}")

    def _flecha_manual(self):
        """Traduce a mano una secuencia de escape que ncurses no decodifico.

        Red de seguridad: algunas terminales (o algunos wrappers de Docker)
        mandan la secuencia partida y getch() devuelve el ESC suelto. Se leen
        los bytes que siguen con un timeout corto y se traducen.
        """
        self.p.timeout(60)
        try:
            b1 = self.p.getch()
            if b1 not in (ord("["), ord("O")):
                return -1
            b2 = self.p.getch()
            if b2 in (ord("5"), ord("6")):
                self.p.getch()          # descarta el '~' final
        finally:
            self.p.timeout(200)
        return {
            ord("A"): curses.KEY_UP,
            ord("B"): curses.KEY_DOWN,
            ord("5"): curses.KEY_PPAGE,
            ord("6"): curses.KEY_NPAGE,
        }.get(b2, -1)

    def procesar_tecla(self, c):
        if c == -1:
            return
        if c == 27:                      # ESC suelto: puede ser una flecha
            c = self._flecha_manual()
            if c == -1:
                return
        if self.mostrando_ayuda:
            self.mostrando_ayuda = False
            return

        if c in (ord("q"), ord("Q")):
            self.apagando.set()
        elif c in (ord("h"), ord("?")):
            self.mostrando_ayuda = True
        elif c in (curses.KEY_UP, ord("k")):
            self.seleccion = max(0, self.seleccion - 1)
        elif c in (curses.KEY_DOWN, ord("j")):
            self.seleccion = min(max(0, len(self.lista) - 1), self.seleccion + 1)
        elif c == curses.KEY_PPAGE:
            self.seleccion = max(0, self.seleccion - 10)
        elif c == curses.KEY_NPAGE:
            self.seleccion = min(max(0, len(self.lista) - 1), self.seleccion + 10)
        elif c in (10, 13, curses.KEY_ENTER):
            actual = self.lista[self.seleccion]["pid"] if self.lista else None
            if self.pin == actual:
                self.pin = None
                self.notificar("pin liberado")
            else:
                self.pin = actual
                self.notificar(f"pin fijado en PID {actual}")
        elif c == ord("/"):
            self.filtro_cmd = self.pedir_texto("filtrar comando: ",
                                               self.filtro_cmd)
            self.seleccion = 0
        elif c == ord("u"):
            self.filtro_usuario = self.pedir_texto("filtrar usuario: ",
                                                   self.filtro_usuario)
            self.seleccion = 0
        elif c == ord("c"):
            self.orden = ORDENES[(ORDENES.index(self.orden) + 1) % len(ORDENES)]
            self.notificar(f"orden: {self.orden}")
        elif c in (ord("+"), ord("=")):
            self.ajustar_intervalo(+0.5)
        elif c in (ord("-"), ord("_")):
            self.ajustar_intervalo(-0.5)
        elif c == curses.KEY_RESIZE:
            self.p.clear()
        else:
            for i, (_, _, teclas) in enumerate(VISTAS):
                if c in (ord(str(i + 1)), ord(teclas[1])):
                    self.vista = i
                    return

    # ------------------------------------------------------------------

    def loop(self):
        curses.curs_set(0)
        # keypad(True) hace que ncurses traduzca las secuencias de escape
        # de las teclas especiales (ESC [ A) a constantes (KEY_UP).
        self.p.keypad(True)
        # OJO: no usar nodelay(True) aca. En modo no bloqueante ncurses no
        # espera los bytes que siguen al ESC, asi que devuelve el ESC suelto
        # y las flechas nunca se decodifican. timeout() solo ya da la espera
        # acotada que necesitamos.
        self.p.timeout(200)      # getch devuelve -1 tras 200 ms
        # Se desactivan las optimizaciones de insertar/borrar lineas: en
        # una pantalla que se reordena entera cada frame no ayudan y hacen
        # que algunos emuladores dejen restos de la pintada anterior.
        self.p.idlok(False)
        self.p.scrollok(False)
        while not self.apagando.is_set():
            # Mensajes que manda el proceso principal por el Pipe cuando
            # atiende una senal (SIGHUP, SIGUSR1, SIGUSR2...).
            while self.tuberia.poll():
                tipo, carga = self.tuberia.recv()
                if tipo == "aviso":
                    self.notificar(carga, 4.0)
                elif tipo == "repintar":
                    self.p.clear()
                elif tipo == "config":
                    self.minimos = carga
            self.dibujar()
            self.procesar_tecla(self.p.getch())


def correr(snapshot, intervalos, minimos, apagando, tuberia, config,
           verbose, fd_stdin):
    """Entry point del proceso de display.

    multiprocessing cierra stdin en los hijos y lo reemplaza por
    /dev/null, asi que curses no podria leer el teclado. El padre duplica
    el fd de stdin antes del fork y aca lo volvemos a poner en el fd 0.
    """
    try:
        os.dup2(fd_stdin, 0)
        sys.stdin = os.fdopen(0, "r")
    except OSError:
        pass

    import senales as sen
    sen.ignorar_en_hijo()

    try:
        curses.wrapper(lambda p: Interfaz(p, snapshot, intervalos, minimos,
                                          apagando, tuberia, config,
                                          verbose).loop())
    except Exception as e:  # noqa: BLE001
        # Si la terminal no soporta curses (por ejemplo sin tty), se avisa
        # y se ordena el apagado en vez de dejar procesos huerfanos.
        print(f"[display] no se pudo iniciar la TUI: {e}", file=sys.stderr)
    finally:
        apagando.set()
