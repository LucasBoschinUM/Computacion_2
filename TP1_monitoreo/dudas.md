# Dudas que me quedaron

Cosas que no terminé de resolver o que resolví "porque anda" pero sin estar
seguro de que sea la forma correcta.

---

**1. ¿El campo 6 de `stat` es `session` o `pgrp`?**

La consigna dice "Sesión (SID) y grupo de procesos (PGID) — `/proc/<pid>/stat`
campos 6-7". En `proc(5)` el campo 5 es `pgrp` y el 6 es `session`; el 7 es
`tty_nr`. Usé la numeración del man page (pgid=5, sid=6) porque coincide con lo
que devuelve `ps -o sid,pgid`, pero puede que la consigna esté contando distinto
o que me esté perdiendo algo.

---

**2. ¿Cómo calcular bien el CPU% "del sistema" versus "de un core"?**

Uso `(Δjiffies / HZ) / Δt * 100`, que da el uso relativo a **un** core (igual que
`top` por defecto). Un proceso con 4 threads saturados marca 400%. La alternativa
sería dividir por la cantidad de cores para que el techo sea 100%. No sé cuál
espera la cátedra. Dejé el comportamiento tipo `top` porque es lo que la gente
espera al comparar con `htop`, pero es discutible.

---

**3. Precisión del delta cuando el intervalo es corto.**

Con el intervalo mínimo de 0.5 s y `HZ=100`, un delta de 1 jiffy son 10 ms sobre
500 ms → 2%. La resolución es horrible y el número parpadea. ¿Es esperable, o
habría que suavizar con una media móvil? No implementé suavizado porque me pareció
que ocultaría el problema en vez de mostrarlo, pero no estoy seguro de que sea la
decisión correcta desde el punto de vista de usabilidad.

---

**4. `Queue` acotada + `put_nowait()`: ¿descartar es aceptable?**

Las queues de PIDs tienen `maxsize=4` y el recolector usa `put_nowait()`,
descartando la lista si el analizador está atrasado. Me pareció mejor que bloquear
al recolector (que frenaría a los 7). Pero significa que un analizador lento puede
trabajar sobre una lista de PIDs de hace varios ciclos. ¿Hay una forma canónica de
hacer "solo me interesa el último valor" con `multiprocessing`? Una especie de
mailbox de un solo slot. Lo simulé drenando la queue con `get_nowait()` en un
`while`, pero se siente artesanal.

---

**5. Reiniciar componentes caídos.**

Detecto si muere el display (y ahí apago todo) pero no relanzo analizadores
muertos. Se me ocurrió hacer un supervisor que los reviva, pero no sé si vale la
pena o si es sobre-ingeniería para el alcance del TP. Además hay un problema
conceptual: si un analizador se muere por un bug determinístico, revivirlo lo
único que hace es un loop de crashes.

---

**6. ¿`Manager` o memoria compartida "de verdad" para el snapshot?**

Entiendo por qué `Manager.dict` es lo razonable acá (estructura variable). Pero
me quedó la duda de si se podría hacer con `mmap` + un formato binario propio y
doble buffer, y si eso sería *mucho* más rápido o solo un poco. Sospecho que la
serialización con pickle es el cuello de botella real, pero no lo medí.

---

**7. Threads del monitor visibles como ruido.**

`multiprocessing.Queue` levanta un thread alimentador por proceso, y el `Manager`
levanta varios. Entonces el monitor se muestra a sí mismo con más threads de los
que "escribí". No es un problema, pero me hizo dudar de si estoy contando bien
hasta que leí la implementación de `Queue`.

---

**8. Async-signal-safety en Python.**

Entiendo el concepto en C. En Python el handler ni siquiera corre en el momento de
la señal: el intérprete setea un flag y ejecuta el handler entre bytecodes. ¿Eso
hace que el problema de re-entrancia sea distinto, o el self-pipe sigue siendo
igual de necesario? Lo implementé con self-pipe porque es lo que se vio en clase y
porque igual necesitaba integrar las señales con un `select()`, pero no tengo del
todo claro cuánto del razonamiento de C aplica literalmente acá.
