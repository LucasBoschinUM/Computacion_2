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
