"""Configuración del servicio, leída de variables de entorno.

docker-compose.yml las define; acá están los valores por defecto.
"""
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    workers: int              # corrutinas que consumen de la cola
    procesos: int             # procesos del pool para el trabajo CPU-bound
    tamano_cola: int          # tareas que pueden esperar; si se llena, 503
    timeout_defecto: float    # segundos, si la tarea no pide otro
    limite_apagado: float     # cuánto se espera a las tareas al apagar
    dir_volcados: Path        # dónde va estado_<timestamp>.json
    puerto: int
    log_acceso: bool          # loguear cada pedido HTTP
    hashear_en_el_loop: bool  # BUG A PROPÓSITO: solo para la medición 2


def cargar_config() -> Config:
    entorno = os.environ.get
    return Config(
        workers=int(entorno('WORKERS', 8)),
        procesos=int(entorno('PROCESOS', min(4, os.cpu_count() or 1))),
        tamano_cola=int(entorno('TAMANO_COLA', 1000)),
        timeout_defecto=float(entorno('TIMEOUT_DEFECTO', 60)),
        limite_apagado=float(entorno('LIMITE_APAGADO', 15)),
        dir_volcados=Path(entorno('DIR_VOLCADOS', 'volcados')),
        puerto=int(entorno('PUERTO', 8000)),
        log_acceso=entorno('LOG_ACCESO', '1') == '1',
        hashear_en_el_loop=entorno('HASHEAR_EN_EL_LOOP', '0') == '1',
    )
