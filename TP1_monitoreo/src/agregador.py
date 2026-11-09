"""
agregador.py — Unico escritor del snapshot global.

Los 7 analizadores NO escriben el Manager.dict directamente. Empujan sus
resultados a una Queue unica y este proceso los aplica de a uno. Es el
patron single-writer: la serializacion de los mensajes en la Queue elimina
por construccion las race conditions de escritura, sin necesidad de un Lock
explicito y sin que los analizadores se bloqueen entre si.

Ademas, escribir una clave entera de golpe (snapshot["memoria"] = {...})
hace que el display nunca vea una seccion a medio actualizar: o esta la
version vieja completa, o la nueva completa.
"""

import queue
import time

import senales

SECCIONES = ("resumen", "memoria", "fds", "threads",
             "senales", "scheduling", "sistema")


def correr(cola_resultados, snapshot, apagando):
    senales.ignorar_en_hijo()

    for s in SECCIONES:
        snapshot[s] = {"datos": {}, "ts": 0.0, "ms": 0.0, "ciclos": 0}

    ciclos = {s: 0 for s in SECCIONES}

    while not apagando.is_set():
        try:
            msg = cola_resultados.get(timeout=0.3)
        except queue.Empty:
            continue
        except (EOFError, OSError):
            break

        seccion = msg["seccion"]
        datos = msg["datos"]
        ciclos[seccion] = ciclos.get(seccion, 0) + 1

        # El top-3 de la vista Sistema se deriva del snapshot, no se
        # recalcula leyendo /proc: es informacion que el analizador de
        # resumen ya pago. Se hace aca porque el agregador es el unico
        # que tiene una vista consistente de todas las secciones.
        if seccion == "sistema":
            datos = dict(datos)
            datos["top_cpu"], datos["top_rss"] = _top3(snapshot)

        snapshot[seccion] = {
            "datos": datos,
            "ts": msg["ts"],
            "ms": msg.get("ms", 0.0),
            "ciclos": ciclos[seccion],
        }


def _top3(snapshot):
    seccion = snapshot.get("resumen")
    if not seccion or not seccion["datos"]:
        return [], []
    procs = list(seccion["datos"].values())
    top_cpu = sorted(procs, key=lambda p: p.get("cpu", 0.0), reverse=True)[:3]
    top_rss = sorted(procs, key=lambda p: p.get("rss_kb", 0), reverse=True)[:3]
    fmt = lambda p: (p["pid"], p["comm"], round(p.get("cpu", 0.0), 1),
                     p.get("rss_kb", 0))
    return [fmt(p) for p in top_cpu], [fmt(p) for p in top_rss]
