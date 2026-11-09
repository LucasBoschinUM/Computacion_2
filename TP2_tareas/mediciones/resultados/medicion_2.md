### Medición 2: el event loop bloqueado

Una tarea `hashear` de 10,000,000 iteraciones mientras se sondea `GET /tareas`.

| Modo | Duración del cálculo (s) | GET en reposo (ms) | GET respondidos durante el cálculo | Mediana (ms) | Peor GET (ms) |
|---|---|---|---|---|---|
| Sin executor (en el loop) | 3.04 | 1.0 | 2 | 2990.1 | 2990.1 |
| Con ProcessPoolExecutor | 3.60 | 0.9 | 70 | 1.1 | 5.8 |
