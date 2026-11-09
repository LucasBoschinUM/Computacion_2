# Plataforma de tareas distribuidas

Trabajo Práctico Nº 2 — Computación II — Universidad de Mendoza — 2026
Autor: Lucas Boschin

Un servicio al que se le encargan trabajos por HTTP, los ejecuta de forma
concurrente y permite consultar su estado y resultado.

## 1. Descripción y uso

### Cómo levantarlo

```bash
cd TP2_tareas
docker compose up --build
```

La API queda en `http://localhost:8000` y la documentación interactiva en
`http://localhost:8000/docs`.

### Tipos de tarea

| Tarea | Naturaleza | Parámetros | Cómo se ejecuta |
|-------|------------|------------|-----------------|
| `descargar` | I/O-bound | `urls`, `concurrencia` (por defecto 20) | `httpx` asíncrono dentro del event loop |
| `hashear` | CPU-bound | `texto`, `iteraciones` | SHA-256 iterado en un `ProcessPoolExecutor` |
| `esperar` | I/O simulado | `segundos` | `asyncio.sleep` |

Las tres aceptan `timeout` en segundos. Corre desde que un worker toma la
tarea; el tiempo en la cola no cuenta.

### Ejemplos con curl

```bash
# Encolar. Responde 202 al instante con el id y el estado inicial
curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo":"esperar","segundos":20}'

curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo":"hashear","texto":"hola","iteraciones":5000000}'

curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo":"descargar","urls":["https://example.com","https://www.python.org"]}'

# Con timeout: a los 2 segundos pasa a "fallida" con el motivo
curl -X POST localhost:8000/tareas -H 'Content-Type: application/json' \
     -d '{"tipo":"esperar","segundos":30,"timeout":2}'

# Consultar
curl localhost:8000/tareas
curl 'localhost:8000/tareas?estado=completada'
curl localhost:8000/tareas/1
curl localhost:8000/estadisticas

# Cancelar, esté en la cola o ejecutándose
curl -X DELETE localhost:8000/tareas/1
```

### Códigos de respuesta

| Código | Cuándo |
|--------|--------|
| 202 | Tarea aceptada. Se procesa después |
| 200 | Consulta o cancelación correcta |
| 404 | No existe esa tarea |
| 409 | Se pidió cancelar una tarea que ya terminó |
| 422 | El pedido no es válido. No llega a la cola |
| 503 | La cola está llena o el servidor se está apagando |

### Configuración

Se define con variables de entorno en `docker-compose.yml`.

| Variable | Por defecto | Qué controla |
|----------|-------------|--------------|
| `WORKERS` | 8 | Corrutinas que consumen de la cola |
| `PROCESOS` | mínimo entre 4 y los núcleos | Procesos para `hashear` |
| `TAMANO_COLA` | 1000 | Tareas que pueden esperar antes de responder 503 |
| `TIMEOUT_DEFECTO` | 60 | Timeout de las tareas que no piden otro |
| `LIMITE_APAGADO` | 15 | Segundos que se espera a las tareas al apagar |

### Tests y mediciones

```bash
docker compose up --build -d
docker compose exec api python -m pytest -q -p no:cacheprovider
docker compose exec api python -m mediciones.medir_io
docker compose exec api python -m mediciones.medir_bloqueo
docker compose exec api python -m mediciones.medir_techo
```

## 2. Arquitectura

```
                 ┌──────────── proceso principal, un solo hilo: el event loop ────────────┐
                 │                                                                         │
 Cliente ─POST─> │  FastAPI ──put_nowait──> asyncio.Queue ──get──> worker 0 ... worker N-1 │
         <─202── │     │                     (con maxsize)              │                  │
                 │     │                                       crea una Task hija          │
 Cliente ─GET──> │     └──── lee ───> dict de tareas <── anota ───┤      │                  │
                 │                                                       ├─ esperar        │
                 │                                                       ├─ descargar ─────┼──> internet
                 │                                                       └─ hashear        │
                 └──────────────────────────────────────────────────────────┼──────────────┘
                                                                  run_in_executor, de a tramos
                                                                            v
                                                          ProcessPoolExecutor (otros procesos)
```

### Recorrido de una tarea

1. El `POST /tareas` entra por FastAPI y Pydantic lo valida.
2. `Plataforma.encolar` crea la tarea en estado `pendiente` y la mete en la cola
   con `put_nowait`. El endpoint responde 202. No esperó a nada.
3. Un worker libre la saca con `await cola.get()` y la marca `ejecutando`.
4. El worker lanza la tarea en una `asyncio.Task` hija, con su timeout, y espera
   a que termine con `asyncio.wait`.
5. Según cómo terminó la hija, el worker anota `completada`, `fallida` o
   `cancelada`, y vuelve al paso 3.

```
pendiente ──> ejecutando ──> completada
    │             ├────────> fallida     (error o timeout)
    │             └────────> cancelada   (DELETE o apagado)
    └──────────────────────> cancelada   (DELETE o apagado, sin haber arrancado)
```

### Organización del código

| Archivo | Responsabilidad |
|---------|-----------------|
| `app/main.py` | Endpoints, arranque y señales |
| `app/plataforma.py` | Cola, workers, cancelación, estadísticas y apagado |
| `app/trabajos.py` | Los tres tipos de tarea |
| `app/calculo.py` | Lo que corre en los procesos del pool |
| `app/modelos.py` | Validación de pedidos y modelo de tarea |
| `app/persistencia.py` | Volcado a JSON y lectura del volcado previo |
| `app/config.py` | Variables de entorno |

## 3. Decisiones de diseño

### ¿Por qué `asyncio.Queue` y no `queue.Queue`?

`queue.Queue.get()` bloquea el hilo que la llama. Acá hay un solo hilo, el del
event loop, así que un worker esperando trabajo congelaría toda la API.
`asyncio.Queue.get()` es una corrutina: suspende solamente a ese worker y le
devuelve el control al loop.

La cola tiene `maxsize` y el endpoint usa `put_nowait`. Si se llena, el cliente
recibe un 503 enseguida. Con `await cola.put()` el endpoint se quedaría colgado
esperando lugar, y el enunciado pide que el `POST` responda de inmediato.

### ¿Cómo evito que una tarea CPU-bound congele la API?

`hashear` corre en un `ProcessPoolExecutor` y el loop espera el resultado con
`await loop.run_in_executor(...)`. Mientras un proceso calcula, el loop sigue
atendiendo pedidos. Elegí procesos y no `asyncio.to_thread` por el GIL: con
hilos, varias tareas de hash se turnarían para ejecutar bytecode y no habría
paralelismo real. La medición 3C muestra que los procesos sí escalan.

El pool se crea una sola vez, al arrancar. Uso el método `spawn` de forma
explícita: el hijo nace limpio, sin heredar el socket del servidor, los
handlers de señales ni el estado del event loop, y el comportamiento es igual
en cualquier sistema. Por eso el arranque en `main.py` está protegido con
`if __name__ == '__main__'`: cada hijo vuelve a importar el módulo.

### ¿Cómo implementé la cancelación, y qué pasa con los recursos a medio usar?

Cada tarea corre en una `asyncio.Task` hija propia. `cancel()` corta una Task
entera, así que si la tarea corriera directamente dentro del worker, un
`DELETE` mataría al worker. Con una hija se corta la tarea y el worker sigue.

El worker espera a la hija con `asyncio.wait`, que no relanza lo que le pase a
ella. Después mira con `hija.cancelled()` y `hija.exception()` cómo terminó.
Toda la decisión del estado final queda en un solo lugar, y funciona incluso si
la cancelación llega antes de que la hija haya empezado a correr.

Según el estado de la tarea:

- **Pendiente:** se marca `cancelada`. Sigue físicamente en la cola, pero el
  worker que la saque ve que ya no está pendiente y la saltea.
- **Ejecutando:** `hija.cancel()` inyecta `CancelledError` en su próximo `await`.

Qué pasa con los recursos en cada tipo de tarea:

- **`descargar`:** el `TaskGroup` cancela todas las descargas y no sale del
  bloque hasta que terminaron todas, así que no quedan corrutinas huérfanas. El
  `async with cliente.stream(...)` cierra la conexión y el `async with semaforo`
  devuelve su lugar. Lo descargado no se guarda: solo se cuentan los bytes.
- **`hashear`:** un proceso que ya está calculando no se puede interrumpir
  (clase 22). Si cancelara un cálculo de una sola pieza, el proceso seguiría
  ocupando un núcleo hasta el final. Por eso el hash se manda de a tramos de
  50.000 iteraciones. Entre tramo y tramo hay un `await`, que es donde entra la
  cancelación, y lo máximo que se desperdicia es un tramo.
- **`esperar`:** `asyncio.sleep` se cancela directamente.

El campo `corrutinas_vivas` de `/estadisticas` permite comprobarlo: después de
cancelar vuelve al valor que tenía.

### ¿Qué pasa si un worker lanza una excepción no capturada?

Las excepciones de una tarea no llegan al worker: quedan guardadas en la Task
hija y el worker las lee con `hija.exception()`, y la tarea pasa a `fallida` con
el motivo. Además el bucle del worker tiene un `except Exception` que registra
el error y sigue. Sin esa protección, un bug nuestro mataría esa corrutina en
silencio y el pool tendría un worker menos para siempre.

`task_done()` está en un `finally`, porque el apagado usa `cola.join()`. Si
alguna vez no se llamara, `join()` no volvería nunca.

### ¿Por qué no hace falta un Lock?

Todo el estado lo toca un solo hilo, y una corrutina solo le cede el control a
otra en un `await`. Los bloques que leen y modifican el estado no tienen ningún
`await` en el medio, así que son atómicos respecto de las demás corrutinas. Los
procesos del pool no comparten memoria: reciben una copia y devuelven una copia.

### ¿Por qué `httpx` para las descargas?

Es asíncrono, es el que vimos en clase, y permite leer la respuesta como stream
y testear con un transporte simulado sin usar la red. `aiohttp` puede rendir
más con volúmenes muy altos, pero acá lo que limita es la red y no el cliente.
`requests` no sirve porque es sincrónico y bloquearía el event loop.

### Timeouts

`asyncio.timeout` cancela lo que está adentro cuando vence y convierte esa
cancelación en `TimeoutError`. La tarea pasa a `fallida` con el motivo.

### Apagado ordenado

`loop.add_signal_handler` recibe `SIGTERM` y `SIGINT`. El callback corre entre
tareas, en un momento seguro. La secuencia es:

1. Dejar de aceptar: los `POST` reciben 503 y los `GET` siguen funcionando.
2. Marcar como `cancelada` lo que estaba en la cola sin arrancar.
3. Esperar con `cola.join()`, hasta `LIMITE_APAGADO` segundos, a las que están en curso.
4. Cancelar las que no llegaron.
5. Mandar un centinela por worker para que cada uno salga solo.
6. Cerrar el pool de procesos y el cliente HTTP.
7. Volcar el estado a `volcados/estado_<timestamp>.json`.

El volcado se escribe en un archivo temporal y se renombra con `os.replace`,
que es atómico, para que nunca quede un JSON por la mitad. Las operaciones que
bloquean, como escribir el archivo o cerrar el pool, van en `asyncio.to_thread`.

En Docker, `stop_grace_period` es de 30 segundos. Con los 10 por defecto, Docker
mandaría `SIGKILL` antes de terminar y se perdería el volcado. El `CMD` usa la
forma con corchetes para que Python sea el PID 1 y reciba la señal.

## 4. Mediciones

Corridas en: 12th Gen Intel(R) Core(TM) i7-12700H, 14Gi de RAM, Ubuntu 26.04.1 LTS, dentro del contenedor, con la
notebook enchufada.

### Medición 1: 20 descargas

Promedio de 3 corridas. En el servidor local la suma de las demoras es 14.9 s y la más lenta tarda 1.0 s.

| Escenario | URLs OK | Secuencial (s) | Concurrente (s) | Speedup |
|---|---|---|---|---|
| Servidor local con demoras | 20/20 | 15.00 | 1.02 | 14.7x |
| Internet (20 sitios) | 19/20 | 9.02 | 2.05 | 4.4x |

**Interpretación.** Con el servidor local, la versión secuencial tardó 15,00 s,
que es la suma de las demoras, y la concurrente 1,02 s, casi lo que tarda la más
lenta. El speedup fue de 14,7x. No llega a 20 porque el máximo teórico es la suma
dividida por la más lenta: 14,9 s / 1,0 s ≈ 15x. Las descargas pasan casi todo el
tiempo esperando la red, y mientras una espera el loop atiende a las demás.
Contra internet el speedup fue de 4,4x, con 19 de las 20 URLs correctas. Es menor
porque los sitios tardan muy distinto entre sí y el tiempo concurrente lo marca
el más lento.

### Medición 2: el event loop bloqueado

Una tarea `hashear` de 10,000,000 iteraciones mientras se sondea `GET /tareas`.

| Modo | Duración del cálculo (s) | GET en reposo (ms) | GET respondidos durante el cálculo | Mediana (ms) | Peor GET (ms) |
|---|---|---|---|---|---|
| Sin executor (en el loop) | 3.04 | 1.0 | 2 | 2990.1 | 2990.1 |
| Con ProcessPoolExecutor | 3.60 | 0.9 | 70 | 1.1 | 5.8 |

**Interpretación.** Sin executor, el peor `GET /tareas` tardó 2990 ms,
prácticamente lo mismo que el cálculo, y durante todo ese lapso se respondieron
solo 2 pedidos: el loop estaba ocupado y nadie más podía correr. Con
`ProcessPoolExecutor` el peor `GET` bajó a 5,8 ms y se respondieron 70 pedidos.
El cálculo tardó algo más (3,60 s contra 3,04 s), en parte por el costo de mandar
cada tramo a otro proceso. El executor no acelera el cálculo: lo saca del hilo
del loop.

### Medición 3: techo del pool

#### A. 8 workers fijos, tareas `esperar(0.5 s)`

| Tareas | Tiempo total (s) | Teórico: ⌈tareas/workers⌉ × 0.5 (s) | Espera promedio en cola (s) |
|---|---|---|---|
| 4 | 0.57 | 0.50 | 0.00 |
| 8 | 0.59 | 0.50 | 0.00 |
| 16 | 1.04 | 1.00 | 0.25 |
| 32 | 2.06 | 2.00 | 0.76 |
| 64 | 4.09 | 4.00 | 1.76 |

#### B. Tantos workers como tareas, `esperar(1 s)`

| Tareas y workers | Tiempo en encolar (s) | POST/s | Tiempo total (s) | Duración real de esperar(1 s) | GET /estadisticas p95 (ms) | Peor GET (ms) | Memoria pico (MB) |
|---|---|---|---|---|---|---|---|
| 100 | 0.03 | 3401 | 1.05 | 1.004 | 26 | 26 | 55 |
| 500 | 0.11 | 4665 | 1.14 | 1.005 | 31 | 31 | 58 |
| 1000 | 0.19 | 5257 | 1.21 | 1.006 | 12 | 28 | 61 |
| 2000 | 0.37 | 5427 | 1.39 | 1.006 | 14 | 27 | 66 |
| 5000 | 0.99 | 5051 | 2.01 | 1.007 | 20 | 52 | 86 |
| 10000 | 2.14 | 4663 | 3.18 | 1.022 | 32 | 42 | 101 |
| 20000 | 5.20 | 3844 | 6.34 | 1.035 | 57 | 101 | 132 |

#### C. Tareas CPU-bound de ~1 s, con 20 procesos (20 núcleos lógicos)

| Tareas | Tiempo total (s) | Tareas por segundo | Speedup | GET /estadisticas p95 (ms) |
|---|---|---|---|---|
| 1 | 1.26 | 0.79 | 1.00 | 4 |
| 2 | 1.26 | 1.59 | 2.01 | 2 |
| 4 | 1.43 | 2.81 | 3.54 | 2 |
| 10 | 2.02 | 4.94 | 6.24 | 5 |
| 20 | 3.19 | 6.28 | 7.92 | 6 |
| 40 | 6.13 | 6.52 | 8.23 | 5 |

#### D. 10.000 POST de golpe (8 workers, cola de 1000)

| Aceptadas (202) | Rechazadas (503) | Otros códigos | Tiempo en recibirlas (s) | Tiempo total (s) | Memoria pico (MB) |
|---|---|---|---|---|---|
| 1184 | 8816 | 0 | 1.67 | 8.28 | 57 |

**Interpretación.**

- **A. El primer techo es la cantidad de workers.** Hasta 8 tareas, todas corren
  a la vez. Con más, el tiempo sube de a escalones porque las tareas esperan en
  la cola: con 64 tareas tardó 4,09 s, contra los 4 s teóricos.
- **B. Con más workers, el techo es el hilo del event loop.** Las corrutinas son
  baratas: `esperar(1 s)` siguió durando 1,035 s incluso con 20.000 tareas
  juntas, y la memoria llegó a 132 MB. Lo que se degrada es la recepción: el
  servidor llegó a un máximo de 5427 `POST/s` y con 20.000 tareas bajó a 3844,
  porque cada pedido gasta CPU en parsear HTTP, validar y armar el JSON, y todo
  eso corre en un solo hilo. Con 5000 tareas, encolarlas ya tarda casi lo mismo
  que la tarea (0,99 s), y con 10.000 lo supera (2,14 s).
- **C. Con CPU, el techo son los núcleos.** El speedup creció hasta 7,9x con 20
  tareas y casi no mejoró con 40 (8,2x). Tener más tareas simultáneas ya no ayuda,
  porque no hay procesadores libres. No llega a 20 porque los 20 núcleos lógicos no
  equivalen a 20 procesadores completos: varios comparten un núcleo físico y, con
  todos trabajando, baja la frecuencia. Con el pico de carga la API siguió
  respondiendo: el `GET` p95 quedó en 6 ms o menos.
- **D. 10.000 tareas de golpe.** Se aceptaron 1184 y se rechazaron 8816 con 503,
  sin ningún otro código de error. Lo primero que se agota es la cola, a propósito:
  es el mecanismo de control. El servidor no se cayó.

## 5. Conceptos del curso aplicados

- **Docker y Compose (clases 1 y 2).** `Dockerfile` en capas, puertos y volúmenes.
- **Señales (clase 6).** `SIGTERM` y `SIGINT` para el apagado. Los procesos hijos
  ignoran `SIGINT` en `preparar_proceso`, porque Ctrl+C le llega a todo el grupo
  de procesos. `add_signal_handler` usa por debajo el patrón self-pipe.
- **fork, spawn y el guard (clase 8).** El pool usa `spawn` y por eso `main.py`
  necesita `if __name__ == '__main__'`.
- **Procesos para CPU (clase 9) y el GIL (clase 10).** Son el motivo de usar
  `ProcessPoolExecutor` en `hashear`.
- **Productor-consumidor (clase 11).** El pool consume de una `asyncio.Queue`
  compartida. Es el mismo patrón, pero con corrutinas en vez de threads: no hace
  falta lock porque el loop no interrumpe entre `await`s.
- **`select` y `epoll` (clase 17).** El loop de asyncio duerme en `epoll` hasta
  que algún socket tiene datos. Por eso miles de `esperar` casi no gastan CPU.
- **De `yield` a `await` (clase 18).** Una corrutina es una función que se
  suspende. Por eso `time.sleep` adentro de una es un bug y `asyncio.sleep` no.
- **FastAPI (clase 19).** Validación con Pydantic, códigos de estado y `lifespan`
  para crear y liberar recursos. Todos los endpoints son `async def`.
- **Asyncio en red (clase 20).** Cliente HTTP con streams, `Semaphore` para acotar
  descargas, `asyncio.timeout`, `CancelledError` que no se traga y apagado con
  `add_signal_handler`.
- **Asyncio avanzado (clase 21).** `maxsize` como backpressure, centinela para
  apagar workers, `task_done` en `finally`, `TaskGroup` y executors.
- **`concurrent.futures` (clase 22).** `cancel()` solo funciona si la tarea no
  arrancó. Ese límite es el motivo de partir el hash en tramos. Las funciones del
  pool son de nivel módulo porque viajan con pickle.

## 6. Limitaciones conocidas

- El estado vive en memoria. Al arrancar se informa el volcado previo, pero las
  tareas no se reanudan.
- El diccionario de tareas crece para siempre: no se borran las terminadas.
- Hay un solo proceso atendiendo HTTP, así que la recepción está limitada por un
  núcleo. Con `--workers` de uvicorn cada proceso tendría su propia cola.
- `/estadisticas` y `GET /tareas` recorren todas las tareas. Son milisegundos
  dentro del loop, pero crecen con la cantidad de tareas.
- Una tarea pendiente cancelada ocupa su lugar en la cola hasta que un worker la saca.
- Al cancelar `hashear`, el último tramo enviado termina igual y se descarta.
- Si un proceso del pool muere, por ejemplo por falta de memoria, el pool queda
  roto y los `hashear` siguientes fallan hasta reiniciar el servicio.
- `descargar` acepta cualquier URL, incluidas direcciones de la red interna, y no
  limita el tamaño. En un servicio público sería un riesgo.
- No hay autenticación ni límite de pedidos por cliente.
- El contenedor corre como root para que los volúmenes funcionen sin pasos extra.

## 7. Lo que aprendí

Lo que más me quedó es la diferencia entre esperar y calcular. Con 20 descargas
pasé de 15,00 s a 1,02 s sin agregar hilos ni procesos, porque casi todo ese tiempo
era espera. En cambio, un hash de unos 3 segundos dentro del event loop hizo que un
`GET /tareas` tardara 2990 ms en vez de 1 ms. Verlo medido hace concreta la regla de
no bloquear el loop: hay un solo hilo, y mientras calcula no atiende a nadie.

También aprendí que cancelar tiene más detalle del que parece. `cancel()` no mata
nada: inyecta una excepción en el próximo `await`. Por eso cada tarea corre en una
Task hija, para no llevarse puesto al worker, y por eso el hash va de a tramos, ya
que un proceso que arrancó no se puede interrumpir. Cancelar un cálculo de 2000
millones de iteraciones y ver la CPU bajar de 99% a 0,29% me mostró que la
cancelación era real y no solo un cambio de estado.

Por último, medir me mostró cosas que no se deducen del código. Con 20 núcleos
lógicos el speedup llegó a 8x y no a 20. Y al mandar 10.000 tareas de golpe, el
sistema no se cayó porque la cola rechazó 8816 con 503. Ahí entendí que poner un
límite también es parte del diseño.
