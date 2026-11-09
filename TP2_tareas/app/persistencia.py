"""Volcado del estado a JSON y lectura del volcado anterior.

Son funciones bloqueantes (tocan el disco), así que quien las llama desde
una corrutina lo hace con asyncio.to_thread.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def volcar(tareas: list[dict], directorio: Path) -> Path:
    """Escribe estado_<timestamp>.json y devuelve la ruta."""
    directorio.mkdir(parents=True, exist_ok=True)
    marca = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    destino = directorio / f'estado_{marca}.json'
    temporal = destino.with_suffix('.tmp')
    contenido = {'volcado_en': marca, 'tareas': tareas}
    temporal.write_text(
        json.dumps(contenido, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    # os.replace es atómico: nunca queda un .json escrito por la mitad.
    os.replace(temporal, destino)
    return destino


def describir_volcado_previo(directorio: Path) -> str | None:
    """Arma el texto para el log, o None si no hay ningún volcado."""
    # El nombre lleva la fecha ordenable, así que el último es el más nuevo.
    volcados = sorted(directorio.glob('estado_*.json'))
    if not volcados:
        return None
    ultimo = volcados[-1]
    try:
        tareas = json.loads(ultimo.read_text(encoding='utf-8'))['tareas']
    except (OSError, ValueError, KeyError) as error:
        return f'{ultimo.name} (no se pudo leer: {error})'
    por_estado = Counter(t['estado'] for t in tareas)
    return f'{ultimo.name} ({len(tareas)} tareas: {dict(por_estado)})'
