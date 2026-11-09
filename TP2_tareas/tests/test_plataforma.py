"""Tests del núcleo, con foco en cancelación y en no bloquear el loop."""
import asyncio
import hashlib
import json
import time

import httpx
import pytest

from app import trabajos
from app.config import Config
from app.modelos import Estado, PedidoEsperar, PedidoHashear
from app.plataforma import Apagandose, ColaLlena, Plataforma, YaTerminada


def configuracion(tmp_path, **cambios) -> Config:
    valores = dict(workers=2, procesos=2, tamano_cola=50, timeout_defecto=30,
                   limite_apagado=2, dir_volcados=tmp_path, puerto=0,
                   log_acceso=False, hashear_en_el_loop=False)
    valores.update(cambios)
    return Config(**valores)


@pytest.fixture
async def plataforma(tmp_path):
    p = Plataforma(configuracion(tmp_path))
    await p.iniciar()
    yield p
    await p.apagar()


def esperar_tarea(segundos, timeout=None):
    return PedidoEsperar(tipo='esperar', segundos=segundos, timeout=timeout)


def hashear_tarea(iteraciones, timeout=None):
    return PedidoHashear(tipo='hashear', texto='hola',
                         iteraciones=iteraciones, timeout=timeout)


async def hasta_estado(tarea, *estados, limite=20):
    async with asyncio.timeout(limite):
        while tarea.estado not in estados:
            await asyncio.sleep(0.005)


async def test_encolar_no_espera_a_la_tarea(plataforma):
    inicio = time.perf_counter()
    tarea = plataforma.encolar(esperar_tarea(5))
    assert time.perf_counter() - inicio < 0.05
    assert tarea.estado is Estado.PENDIENTE
    await plataforma.cancelar(tarea.id)


async def test_esperar_completa_y_registra_tiempos(plataforma):
    tarea = plataforma.encolar(esperar_tarea(0.05))
    await hasta_estado(tarea, Estado.COMPLETADA)
    assert tarea.resultado == {'dormi_s': 0.05}
    assert tarea.duracion_s >= 0.05
    assert tarea.espera_s >= 0


async def test_hashear_coincide_con_hashlib_cruzando_tramos(plataforma):
    n = trabajos.TAMANO_TRAMO + 7
    esperado = b'hola'
    for _ in range(n):
        esperado = hashlib.sha256(esperado).digest()
    tarea = plataforma.encolar(hashear_tarea(n))
    await hasta_estado(tarea, Estado.COMPLETADA)
    assert tarea.resultado['hash'] == esperado.hex()


async def test_hashear_no_bloquea_el_loop(plataforma):
    tarea = plataforma.encolar(hashear_tarea(1_000_000))
    peor = 0.0
    while tarea.estado in (Estado.PENDIENTE, Estado.EJECUTANDO):
        t0 = time.perf_counter()
        await asyncio.sleep(0.01)
        peor = max(peor, time.perf_counter() - t0 - 0.01)
    assert tarea.estado is Estado.COMPLETADA
    assert peor < 0.2


async def test_cancelar_tarea_pendiente_nunca_arranca(plataforma):
    ocupantes = [plataforma.encolar(esperar_tarea(0.3)) for _ in range(2)]
    await asyncio.sleep(0.02)
    tarea = plataforma.encolar(esperar_tarea(0.01))
    await plataforma.cancelar(tarea.id)
    assert tarea.estado is Estado.CANCELADA
    for o in ocupantes:
        await hasta_estado(o, Estado.COMPLETADA)
    await asyncio.sleep(0.05)
    assert tarea.iniciada_en is None


@pytest.mark.parametrize('pedido', [esperar_tarea(30), hashear_tarea(10**9)])
async def test_cancelar_en_ejecucion_sin_huerfanas_y_worker_sigue(
        plataforma, pedido):
    await asyncio.sleep(0)
    base = len(asyncio.all_tasks())
    tarea = plataforma.encolar(pedido)
    await hasta_estado(tarea, Estado.EJECUTANDO)
    await asyncio.sleep(0.05)
    inicio = time.perf_counter()
    await plataforma.cancelar(tarea.id)
    assert time.perf_counter() - inicio < 0.5
    assert tarea.estado is Estado.CANCELADA
    assert tarea.motivo == 'cancelada por el cliente'
    await asyncio.sleep(0.01)
    assert len(asyncio.all_tasks()) == base
    siguiente = plataforma.encolar(esperar_tarea(0))
    await hasta_estado(siguiente, Estado.COMPLETADA, limite=3)


async def test_cancelar_justo_al_arrancar_no_queda_ejecutando(plataforma):
    tarea = plataforma.encolar(esperar_tarea(30))
    # Dejo que el worker la tome pero que la hija todavía no haya corrido.
    while tarea.estado is Estado.PENDIENTE:
        await asyncio.sleep(0)
    await plataforma.cancelar(tarea.id)
    assert tarea.estado is Estado.CANCELADA


async def test_cancelar_terminada_da_error(plataforma):
    tarea = plataforma.encolar(esperar_tarea(0))
    await hasta_estado(tarea, Estado.COMPLETADA)
    with pytest.raises(YaTerminada):
        await plataforma.cancelar(tarea.id)


@pytest.mark.parametrize('pedido', [esperar_tarea(30, timeout=0.1),
                                    hashear_tarea(10**9, timeout=0.2)])
async def test_timeout_pasa_a_fallida_con_motivo(plataforma, pedido):
    tarea = plataforma.encolar(pedido)
    await hasta_estado(tarea, Estado.FALLIDA)
    assert 'timeout' in tarea.motivo


async def test_worker_sobrevive_a_una_excepcion(plataforma, monkeypatch):
    async def explota(segundos):
        raise ValueError('boom')
    monkeypatch.setattr(trabajos, 'esperar', explota)
    mala = plataforma.encolar(esperar_tarea(0))
    await hasta_estado(mala, Estado.FALLIDA)
    assert mala.motivo == 'ValueError: boom'
    monkeypatch.undo()
    buenas = [plataforma.encolar(esperar_tarea(0)) for _ in range(4)]
    for b in buenas:
        await hasta_estado(b, Estado.COMPLETADA, limite=3)


async def test_cola_llena_rechaza(tmp_path):
    p = Plataforma(configuracion(tmp_path, workers=1, tamano_cola=1))
    await p.iniciar()
    p.encolar(esperar_tarea(0.2))
    await asyncio.sleep(0.02)      # el worker la saca de la cola
    p.encolar(esperar_tarea(0.01)) # ocupa el único lugar
    with pytest.raises(ColaLlena):
        p.encolar(esperar_tarea(0.01))
    assert len(p.tareas) == 2      # la rechazada no quedó registrada
    await p.apagar()


# --- Descargas, con un transporte falso que simula demoras -------------

def cliente_falso(demora=0.2, respuesta=b'x' * 1000, error_en=()):
    async def manejador(pedido):
        if pedido.url.host in error_en:
            raise httpx.ConnectError('sin conexión')
        await asyncio.sleep(demora)
        return httpx.Response(200, content=respuesta)
    return httpx.AsyncClient(transport=httpx.MockTransport(manejador))


async def test_descargas_concurrentes_tardan_lo_que_la_mas_lenta():
    urls = [f'http://sitio{i}.test/' for i in range(10)]
    async with cliente_falso(0.2) as cliente:
        inicio = time.perf_counter()
        resultado = await trabajos.descargar(cliente, urls, 10)
    assert time.perf_counter() - inicio < 0.5
    assert resultado['suma_de_tiempos_s'] >= 1.9
    assert all(d['estado_http'] == 200 and d['tamano_bytes'] == 1000
               for d in resultado['descargas'])


async def test_concurrencia_uno_es_secuencial():
    urls = [f'http://sitio{i}.test/' for i in range(5)]
    async with cliente_falso(0.05) as cliente:
        inicio = time.perf_counter()
        await trabajos.descargar(cliente, urls, 1)
    assert time.perf_counter() - inicio >= 0.25


async def test_una_url_rota_no_tira_abajo_a_las_otras():
    urls = ['http://bien.test/', 'http://mal.test/']
    async with cliente_falso(0, error_en=('mal.test',)) as cliente:
        resultado = await trabajos.descargar(cliente, urls, 2)
    bien, mal = resultado['descargas']
    assert bien['estado_http'] == 200
    assert mal['estado_http'] is None and 'ConnectError' in mal['error']


async def test_todas_las_urls_rotas_es_error():
    async with cliente_falso(error_en=('mal.test',)) as cliente:
        with pytest.raises(RuntimeError):
            await trabajos.descargar(cliente, ['http://mal.test/'], 1)


async def test_cancelar_a_mitad_de_descarga_no_deja_huerfanas():
    urls = [f'http://sitio{i}.test/' for i in range(20)]
    async with cliente_falso(30) as cliente:
        base = len(asyncio.all_tasks())
        tarea = asyncio.create_task(trabajos.descargar(cliente, urls, 20))
        await asyncio.sleep(0.05)
        assert len(asyncio.all_tasks()) > base + 10
        tarea.cancel()
        await asyncio.wait({tarea})
        assert tarea.cancelled()
        assert len(asyncio.all_tasks()) == base


# --- Apagado ------------------------------------------------------------

async def test_apagado_espera_cancela_pendientes_y_vuelca(tmp_path):
    p = Plataforma(configuracion(tmp_path, workers=1, limite_apagado=3))
    await p.iniciar()
    corriendo = p.encolar(esperar_tarea(0.2))
    await asyncio.sleep(0.02)
    pendiente = p.encolar(esperar_tarea(1))
    await p.apagar()
    assert corriendo.estado is Estado.COMPLETADA
    assert pendiente.estado is Estado.CANCELADA
    with pytest.raises(Apagandose):
        p.encolar(esperar_tarea(0))
    volcados = list(tmp_path.glob('estado_*.json'))
    assert len(volcados) == 1
    datos = json.loads(volcados[0].read_text(encoding='utf-8'))
    assert {t['estado'] for t in datos['tareas']} == {'completada', 'cancelada'}


async def test_apagado_respeta_el_limite(tmp_path):
    p = Plataforma(configuracion(tmp_path, limite_apagado=0.1))
    await p.iniciar()
    larga = p.encolar(esperar_tarea(60))
    await asyncio.sleep(0.02)
    inicio = time.perf_counter()
    await p.apagar()
    assert time.perf_counter() - inicio < 2
    assert larga.estado is Estado.CANCELADA
    assert 'límite' in larga.motivo
    await p.apagar()              # idempotente
    assert len(list(tmp_path.glob('estado_*.json'))) == 1
