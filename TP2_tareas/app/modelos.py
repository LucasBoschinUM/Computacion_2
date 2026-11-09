"""Los datos del sistema: qué puede pedir el cliente y cómo es una tarea."""
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, HttpUrl, computed_field


class Estado(str, Enum):
    PENDIENTE = 'pendiente'
    EJECUTANDO = 'ejecutando'
    COMPLETADA = 'completada'
    FALLIDA = 'fallida'
    CANCELADA = 'cancelada'


def ahora() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------
# Lo que manda el cliente en el POST. Pydantic lo valida antes de que
# entre al sistema: un pedido mal formado recibe 422 y nunca se encola.
# ------------------------------------------------------------------

class _PedidoBase(BaseModel):
    timeout: float | None = Field(
        default=None, gt=0, le=86400,
        description='Segundos máximos de ejecución. Empieza a correr cuando '
                    'un worker toma la tarea, no mientras espera en la cola.',
    )


class PedidoDescargar(_PedidoBase):
    tipo: Literal['descargar']
    urls: list[HttpUrl] = Field(min_length=1, max_length=200)
    concurrencia: int = Field(
        default=20, ge=1, le=200,
        description='Descargas simultáneas. Con 1 es secuencial.',
    )


class PedidoHashear(_PedidoBase):
    tipo: Literal['hashear']
    texto: str = Field(max_length=10_000)
    iteraciones: int = Field(gt=0, le=2_000_000_000)


class PedidoEsperar(_PedidoBase):
    tipo: Literal['esperar']
    segundos: float = Field(ge=0, le=86400)


# El campo "tipo" decide cuál de los tres modelos se usa para validar.
PedidoTarea = Annotated[
    PedidoDescargar | PedidoHashear | PedidoEsperar,
    Field(discriminator='tipo'),
]


# ------------------------------------------------------------------
# La tarea tal como la guarda y la devuelve el servidor.
# ------------------------------------------------------------------

class Tarea(BaseModel):
    id: int
    tipo: str
    parametros: dict[str, Any]
    timeout: float
    estado: Estado = Estado.PENDIENTE
    resultado: dict[str, Any] | None = None
    motivo: str | None = None      # por qué falló o se canceló
    worker: int | None = None      # qué worker la tomó
    creada_en: datetime = Field(default_factory=ahora)
    iniciada_en: datetime | None = None
    finalizada_en: datetime | None = None

    @computed_field
    @property
    def espera_s(self) -> float | None:
        """Tiempo que pasó en la cola."""
        if self.iniciada_en is None:
            return None
        return (self.iniciada_en - self.creada_en).total_seconds()

    @computed_field
    @property
    def duracion_s(self) -> float | None:
        """Tiempo que tardó en ejecutarse."""
        if self.iniciada_en is None or self.finalizada_en is None:
            return None
        return (self.finalizada_en - self.iniciada_en).total_seconds()
