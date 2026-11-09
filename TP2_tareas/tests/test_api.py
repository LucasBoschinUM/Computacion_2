"""Tests de los endpoints, sin levantar un servidor de verdad."""
import asyncio
import time

import httpx
import pytest

from app.main import app, ciclo_de_vida


@pytest.fixture
async def api(tmp_path, monkeypatch):
    monkeypatch.setenv('DIR_VOLCADOS', str(tmp_path))
    monkeypatch.setenv('PROCESOS', '1')
    monkeypatch.setenv('TAMANO_COLA', '3')
    monkeypatch.setenv('WORKERS', '1')
    async with ciclo_de_vida(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte,
                                     base_url='http://test') as cliente:
            yield cliente


async def hasta(cliente, tarea_id, estado):
    async with asyncio.timeout(5):
        while True:
            cuerpo = (await cliente.get(f'/tareas/{tarea_id}')).json()
            if cuerpo['estado'] == estado:
                return cuerpo
            await asyncio.sleep(0.01)


async def test_post_responde_al_instante(api):
    inicio = time.perf_counter()
    r = await api.post('/tareas', json={'tipo': 'esperar', 'segundos': 30})
    assert time.perf_counter() - inicio < 0.2
    assert r.status_code == 202
    assert r.json() == {'id': 1, 'estado': 'pendiente'}
    await api.delete('/tareas/1')


async def test_ciclo_completo_y_filtro(api):
    r = await api.post('/tareas', json={'tipo': 'hashear', 'texto': 'hola',
                                        'iteraciones': 1})
    cuerpo = await hasta(api, r.json()['id'], 'completada')
    assert cuerpo['resultado']['hash'] == ('b221d9dbb083a7f33428d7c2a3c3198a'
                                           'e925614d70210e28716ccaa7cd4ddb79')
    listado = (await api.get('/tareas', params={'estado': 'completada'})).json()
    assert len(listado) == 1
    assert 'resultado' not in listado[0] and listado[0]['duracion_s'] >= 0
    assert (await api.get('/tareas', params={'estado': 'pendiente'})).json() == []
    assert (await api.get('/tareas', params={'estado': 'inventado'})).status_code == 422


async def test_delete_en_ejecucion(api):
    r = await api.post('/tareas', json={'tipo': 'esperar', 'segundos': 30})
    tarea_id = r.json()['id']
    await hasta(api, tarea_id, 'ejecutando')
    r = await api.delete(f'/tareas/{tarea_id}')
    assert r.status_code == 200
    assert r.json()['estado'] == 'cancelada'
    assert (await api.delete(f'/tareas/{tarea_id}')).status_code == 409


async def test_errores_http(api):
    assert (await api.get('/tareas/999')).status_code == 404
    assert (await api.delete('/tareas/999')).status_code == 404
    assert (await api.post('/tareas', json={'tipo': 'nada'})).status_code == 422
    assert (await api.post('/tareas', json={'tipo': 'hashear', 'texto': 'x',
                                            'iteraciones': -1})).status_code == 422
    assert (await api.post('/tareas', json={'tipo': 'descargar',
                                            'urls': ['no-es-url']})).status_code == 422


async def test_cola_llena_da_503(api):
    respuestas = [await api.post('/tareas', json={'tipo': 'esperar',
                                                  'segundos': 5})
                  for _ in range(6)]
    codigos = [r.status_code for r in respuestas]
    assert 503 in codigos
    assert codigos.count(202) >= 3
    for r in respuestas:
        if r.status_code == 202:
            await api.delete(f'/tareas/{r.json()["id"]}')


async def test_estadisticas(api):
    r = await api.post('/tareas', json={'tipo': 'esperar', 'segundos': 0})
    await hasta(api, r.json()['id'], 'completada')
    e = (await api.get('/estadisticas')).json()
    assert e['total'] == 1
    assert e['por_estado']['completada'] == 1
    assert e['workers'] == {'total': 1, 'ocupados': 0, 'libres': 1}
    assert e['por_tipo']['esperar']['cantidad'] == 1
