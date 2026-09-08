# Análisis de throughput de blockchain, Core API y Chain Worker

Documento de referencia para evaluar el rendimiento de publicación de ARKs en
la instalación developer actual. No propone cambios de código; su objetivo es
dar a otro agente una base verificable para decidir si conviene aumentar la
ventana de publicación.

## Resumen ejecutivo

La red local utiliza Hyperledger Besu con consenso QBFT y cinco nodos: cuatro
validadores dedicados y un nodo `rpc01` no validador que expone el RPC. Para
developer, el genesis configura `blockperiodseconds: 6`, por lo que
la red intenta producir un bloque aproximadamente cada seis segundos cuando hay quorum y
transacciones disponibles. Ese valor es el ritmo de producción de bloques,
no una garantía de que cada transacción confirme en dos segundos.

El Chain Worker no publica una transacción por ARK de forma aislada. Reclama
una página, agrupa las operaciones por autoridad y llama a
`dark-core-lib.publish_ark_operations()` con una ventana de nonces secuenciales.
El tamaño máximo por ciclo y el tamaño de esa ventana son actualmente 20.
Además, el worker consulta la capacidad de la cadena y puede reducir o volver
aumentar gradualmente el tamaño efectivo.

## Blockchain local

La configuración QBFT se genera en `blockchain/setup.sh` y
`config/genesis-template.json`. Los parámetros relevantes son:

- consenso: QBFT;
- `blockperiodseconds`: 6;
- `requesttimeoutseconds`: 10 (the effective generated configuration; the
  previous value of 4 in this document was stale);
- `epochlength`: 30000;
- tres validadores más `rpc01` en el conjunto de validadores;
- RPC HTTP en el puerto 8545, con APIs `ETH`, `NET`, `QBFT`, `TXPOOL`, `DEBUG`,
  `ADMIN` y `WEB3`.

El período de bloque solo describe la producción de bloques. El throughput
real depende además de gas por bloque, tiempo de ejecución del contrato,
latencia RPC, confirmación de receipts, nonces por cuenta y capacidad del
txpool.

### Evidencia de inestabilidad durante la carga

Las muestras y el log de `dark-validator02` muestran que el ritmo nominal de
dos segundos no se mantuvo de forma estable. Se observaron bloques separados
por aproximadamente 7--8 segundos en promedio, con intervalos individuales de
10--20 segundos. El log registra cambios de ronda QBFT a `Round=2` y
`Round=3`, aunque el quorum informado seguía siendo 3. Esto significa que la
red no perdió completamente el consenso, pero algunos rounds no se cerraron a
tiempo y tuvieron que reiniciarse.

El validador 02 también fue reiniciado durante la carga. Al volver a arrancar
registró `Connecting to 3 static nodes`, pero su configuración mostraba
`Sync min peers: 5`. En una topología local de cuatro nodos, cada nodo solo
puede tener tres peers remotos estáticos; exigir cinco puede retrasar o
complicar la recuperación después de un reinicio. Debe revisarse para que el
mínimo sea compatible con la topología real.

La carga de CPU fue desigual: el validador 02 alcanzó picos cercanos al 92%,
mientras `dark-rpc01` llegó aproximadamente al 45%. También apareció un
`BlockedThreadChecker` de Vert.x con un event loop bloqueado durante unos 2,8
segundos. Los bloques no estaban limitados por gas: incluso los bloques con
transacciones usaban una fracción muy pequeña del gas disponible. La evidencia
apunta a presión de CPU, sincronización y cambios de ronda, no a falta de gas.

Durante el mismo período, IPFS Cluster utilizó más de 50% de CPU por peer y
compartió recursos con Besu en el entorno developer. Esto puede retrasar al
proposer o a los validadores aunque el proceso Besu no registre un error fatal.

El Chain Worker actuó como protección: redujo o pausó el pipeline cuando detectó
RPC no disponible, bloque detenido o una página fallida. Por eso aumentar la
ventana de transacciones antes de estabilizar QBFT puede aumentar el txpool y
la latencia sin mejorar el throughput.

## Flujo de publicación

1. Metadata y disponibilidad dejan el ARK listo para Chain después de que se
   cumplen las condiciones de publicación.
2. El Chain Worker reclama una página, cuyo límite nominal es
   `CHAIN_WORKER_PAGE_SIZE` (por defecto 20).
3. `dark-core-lib` obtiene el nonce pendiente de la cuenta de la autoridad.
4. Para cada operación construye y firma una transacción individual con nonce
   secuencial, `gasLimit` por defecto 550000 y el `gasPrice` leído del RPC.
5. Envía las transacciones de la ventana y espera un receipt para cada una.
6. Un fallo de envío, una incertidumbre de receipt o una brecha de nonce
   detiene la ventana y marca las operaciones posteriores como no enviadas o
   ambiguas para evitar publicar fuera de orden.
7. Los resultados confirmados avanzan el estado interno; los reverts y errores
   se clasifican según el catálogo de errores.

El camino normal no ejecuta una consulta `exists()` ni un `get()` después de
cada escritura confirmada. La confirmación se basa en los receipts.

## Cómo se determina el tamaño de página

El valor configurado (`CHAIN_WORKER_PAGE_SIZE=20`) es simultáneamente:

- el límite de ARKs reclamados por ciclo;
- el tamaño máximo de la ventana de transacciones en Core Lib;
- el `max_page_size` usado para consultar capacidad.

Con el ajuste adaptativo activo, el worker mantiene un tamaño efectivo separado
del máximo configurado. El comportamiento es:

- comienza con el tamaño efectivo configurado;
- si la capacidad está saludable durante el número configurado de ciclos sanos,
  crece gradualmente hasta el recomendado;
- si la capacidad se degrada, reduce inmediatamente el tamaño efectivo;
- si el RPC está caído, el bloque está detenido o el txpool está congestionado,
  pausa el worker en lugar de enviar más transacciones.

La configuración relevante está en `dark-core-minter-api/.env.example`:

```text
CHAIN_WORKER_PAGE_SIZE=20
CHAIN_WORKER_ADAPTIVE_PAGE_ENABLED=true
CHAIN_WORKER_MIN_PAGE_SIZE=1
CHAIN_WORKER_HEALTHY_CYCLES_BEFORE_GROWING=3
CHAIN_WORKER_BLOCK_STALL_SECONDS=120
CHAIN_WORKER_CONGESTION_RETRY_SECONDS=30
```

## Señal de capacidad de Core Lib

`ChainService.get_capacity(max_page_size, include_txpool=True)` devuelve una
recomendación semántica. Primero comprueba el RPC y el número de bloque;
después, si está disponible, consulta `txpool_besuStatistics` o
`txpool_status`.

Los umbrales se derivan del máximo solicitado:

- high watermark: `max(max_page_size * 2, 40)`;
- pause watermark: `max(max_page_size * 5, 100)`;
- bloque sin avance durante 30 s: estado `slow` y página de un cuarto;
- bloque sin avance durante 120 s: estado `stalled` y página 0;
- txpool sobre high watermark: estado `congested` y página de la mitad;
- txpool sobre pause watermark: estado `congested` y página 0;
- sin congestión: recomienda el máximo solicitado.

Por tanto, con `max_page_size=20`, la recomendación saludable es 20, la
congestión moderada reduce a 10 y la congestión crítica pausa el worker. Esta
lógica no mide directamente gas usado, gas disponible por bloque ni tiempo
individual de receipt.

## Reevaluación posterior a los cambios (2026-09-08)

Las optimizaciones de Availability y Replication ya están activas: las
observaciones de Cluster se agrupan por CID, se respeta `next_action_at` y la
durabilidad cede prioridad al primer pin. En las muestras actuales Store API
devuelve resultados batch y no hay fallos permanentes; el cuello ya no parece
ser el polling de IPFS.

El Chain Worker permanece sin errores, pero sus ciclos de 20 ARKs tardan entre
25 y 45 segundos y quedan unos 2.900 ARKs listos para publicar. El txpool no
está saturado (`localCount=20`, `remoteCount=0`), mientras que los validadores
02 y 03 se reiniciaron durante la carga y sus logs contienen `Killed`. También
se observaron picos de CPU de aproximadamente 90--136% y producción de bloques
irregular, con rondas QBFT prolongadas.

La capacidad máxima sigue fijada en 20: el tamaño de página, la ventana de
Core Lib y el `max_page_size` de capacidad comparten ese límite. Con una red
estable, es el siguiente límite a evaluar; con validadores inestables, subirlo
solo aumentaría transacciones pendientes y latencia de receipts.

Orden recomendado: estabilizar memoria/CPU/I/O y reinicios de Besu, verificar
quorum y cadencia QBFT cercana a seis segundos, y solo después probar ventanas
20→40→60 midiendo receipts, nonces, txpool y bloques. No se recomienda relajar
la confirmación de Chain ni modificar Store/Replication mientras la evidencia
apunte a la red blockchain.

## Observación de la carga actual

En la última consulta del entorno limpio:

- Chain estaba `working`;
- el último ciclo procesó y avanzó 20 ARKs en aproximadamente 3,37 s;
- había 1.201 ARKs listos para Chain;
- no había errores ni congestión reportada;
- RPC y los dos peers de storage estaban saludables;
- el bloque avanzaba normalmente.

Ese resultado equivale aproximadamente a 5,9 ARKs/s en ese ciclo. No permite
concluir todavía que la cadena esté al límite: el tiempo incluye firma,
envío, espera secuencial de receipts y persistencia del worker, no solo la
producción QBFT de bloques.

## Preguntas que debe resolver el siguiente análisis

Antes de aumentar la página, medir en varios ciclos:

1. tiempo de construir y firmar transacciones;
2. tiempo de envío al RPC;
3. tiempo de espera de receipts;
4. número de transacciones pendientes y queued en Besu;
5. gas usado por transacción y gas límite del bloque;
6. cantidad de operaciones por autoridad dentro de cada página;
7. número de bloques atravesados por cada página;
8. si el tiempo de 6 s del bloque se mantiene bajo carga;
9. si aparecen reverts, receipts ambiguos o errores de nonce al aumentar la
   ventana;
10. si el cuello de botella está en Besu/RPC, en Core Lib o en PostgreSQL.

El diagnóstico full del minter debe registrar la recomendación de capacidad,
el tamaño efectivo, el estado del txpool y el tiempo de ciclo. La vista lite
no debe ejecutar probes RPC costosos.

## Diagnóstico sin sobrecargar la API

El minter expone ahora cuatro niveles de consulta:

- `detail=simple`: solo heartbeats y estado de proceso;
- `detail=workload`: agregados SQL de colas y errores;
- `detail=infrastructure`: probes acotados de RPC y Store API;
- `detail=full`: combina workload e infraestructura.

El dashboard debe usar `simple` en la portada y reservar `full` para una vista
de diagnóstico con caché. Las consultas full deben tener un timeout acotado y
no bloquear la respuesta simple si RPC o Store API están lentos.

Para los agregados SQL se añadieron índices parciales sobre ARKs activos y
fallos permanentes. Estos índices reducen el conjunto que debe recorrer
PostgreSQL, pero deben validarse con `EXPLAIN (ANALYZE, BUFFERS)` y estadísticas
actualizadas; cada índice adicional también aumenta el coste de las
actualizaciones frecuentes de los workers.

## Alternativas para evaluar

### Aumentar el máximo de forma gradual

Probar 30, 40 y 50 manteniendo el ajuste adaptativo, comparando throughput,
latencia y errores. Es la alternativa de menor riesgo porque conserva la
protección del txpool y la política de nonces.

### Separar página de base de datos y ventana de Core Lib

Permitir reclamar más ARKs, pero enviar ventanas pequeñas por autoridad. Esto
mejora el aprovechamiento de la consulta SQL sin aumentar necesariamente la
presión sobre el RPC.

### Agrupar por autoridad antes de publicar

Medir si las páginas actuales mezclan autoridades y producen ventanas pequeñas
o esperas innecesarias. La cuenta y el nonce son por autoridad, por lo que la
distribución de la página afecta directamente al paralelismo posible.

### Revisar el límite de gas

Confirmar que `550000` sea suficiente y comparar gas usado real. Subirlo no
acelera automáticamente la red; si el bloque queda limitado por gas, puede
reducir el número de ARKs por bloque.

## Criterio de decisión

Solo aumentar el tamaño por encima de 20 si, durante una prueba sostenida, el
txpool permanece bajo, los bloques siguen avanzando cerca de 6 s, no aparecen
errores de nonce/revert, y el tiempo medio por ARK mejora. Si el tiempo de
receipt domina el ciclo, aumentar la página puede aumentar la latencia sin
mejorar el throughput; en ese caso debe estudiarse primero el pipeline de
confirmación o la separación entre reclamación y publicación.

Con la evidencia del validador 02, el orden recomendado es: estabilizar los
reinicios y el quorum QBFT; corregir `sync-min-peers` para que sea compatible
con los tres peers reales; reducir la competencia de CPU entre Besu e IPFS en
developer; verificar que todos los validadores mantengan alturas cercanas y
round 1; y solo después probar ventanas de 30, 40 y 50. Para developer puede
ser razonable usar un `blockperiodseconds` mayor si los cuatro Besu comparten
máquina; el valor developer actual es seis segundos para reducir cambios de
ronda. En producción separada se puede evaluar un período menor únicamente
después de demostrar estabilidad.

## Incidente Vert.x/QBFT observado el 2026-09-08

Se observó este patrón en `rpc01` y `validator03`:

```text
BlockedThreadChecker: vert.x-eventloop-thread-1 blocked for 3704 ms
BlockedThreadChecker: vert.x-eventloop-thread-1 blocked for 8404 ms
RoundTimer: Moved to round 5 which will expire in 320 seconds
RoundChangeManager: BFT round summary (quorum = 3)
ClosedChannelException en el event loop de Netty
```

Esto no es un healthcheck fallido. El healthcheck de Besu únicamente abre el
socket TCP de RPC y puede seguir informando `healthy` aunque el nodo esté
atrasado en consenso. Tampoco hay evidencia de que el healthcheck haya
provocado el reinicio o el cambio de ronda.

La interpretación más probable es una degradación del procesamiento del nodo:

1. Un validador o el RPC deja de procesar eventos durante varios segundos.
2. Los mensajes QBFT llegan tarde o se pierde una conexión entre peers.
3. La propuesta no se completa dentro del plazo de la ronda.
4. Los nodos avanzan a rondas 4 y 5, cuyos timeouts llegan a cientos de
   segundos.
5. La cadena continúa viva, pero produce bloques muy lentamente o queda
   temporalmente sin progreso.

El `ClosedChannelException` aparece en el cierre de un canal Netty y debe
considerarse una consecuencia de la pausa o de una reconexión, no la causa
raíz. El dato importante es la pausa del event loop de 8,4 segundos frente a
un límite de 2 segundos, combinada con rondas QBFT altas.

La evidencia es compatible con presión de CPU compartida con IPFS Cluster,
pausas de JVM/GC, contención de disco, pérdida de red entre validadores o
reinicio/sincronización de un nodo. Con quorum 3, disponer efectivamente de
tres participantes deja la red funcionando al límite: cualquier retraso de
uno de ellos provoca cambios de ronda.

### Verificaciones obligatorias

Antes de aumentar el throughput del Chain Worker se debe:

1. comparar la altura de bloque de los cuatro nodos;
2. confirmar cuántos peers QBFT ve cada nodo;
3. correlacionar reinicios y pérdidas de conexión con `13:12–13:13`;
4. revisar CPU, memoria, GC y disco del host en ese intervalo;
5. mantener la competencia de IPFS Cluster acotada mientras se estabiliza la
   blockchain;
6. confirmar que todos los validadores vuelven a `round 1` y mantienen alturas
   cercanas.

Mientras existan rondas 4–5 con expiraciones de 320 segundos, el Chain Worker
no podrá avanzar de forma confiable aunque el RPC responda y los contenedores
aparezcan como saludables.
