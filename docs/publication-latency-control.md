# Control de latencia de publicación

## Objetivo

El camino crítico es `Metadata -> CIDs aceptados -> primer pin real L1/L2 -> Chain`.
La segunda réplica y la purga son mantenimiento y nunca compiten con ese camino.

## Programación de Availability

`next_action_at` es la única autorización para volver a consultar un ARK en
Availability. Un ARK recién ingresado usa `READY` y se observa de inmediato;
un ARK en `WAITING` siempre tiene una fecha concreta. Las esperas normales no
son errores ni incrementan los intentos:

| Fase | Primera revisión | Segunda | Revisiones posteriores |
| --- | ---: | ---: | ---: |
| Primer pin, antes de Chain | 15 s | 1 min | 5 min |
| Durabilidad, después de Chain | 5 min | 15 min | 1 h |

La cadencia se guarda por ARK en `next_action_at`. Los estados `queued`,
`pinning` e `initial_visibility` no son fallos: Cluster ya posee la asignación
y el minter únicamente vuelve a auditarla cuando corresponde. Un cambio de
estado o el aumento de copias confirmadas reinicia la secuencia de esa fase.

## Capacidad y prioridad

- Replication toma hasta 100 ARKs, deduplica sus CIDs y observa grupos de hasta
  200 CIDs.
- Availability tiene prioridad absoluta. La durabilidad sólo se promueve y
  observa cuando no hay Metadata ni Availability pendientes.
- Store API reserva capacidad independiente: cuatro `add`, seis observaciones
  de estado y una promoción de durabilidad simultáneas.
- Una respuesta incompleta de `status/batch` se representa por CID. Sólo los
  ARKs afectados se reprograman; los demás pueden avanzar.
- Cuando Availability supera 2.000 ARKs, Metadata reduce su concurrencia de 4
  a 2. Vuelve a 4 al bajar de ese umbral.

La durabilidad está deliberadamente limitada a 10 ARKs/20 CIDs por ronda. Si
Cluster ya tiene la asignación objetivo, el ciclo no vuelve a solicitar el pin:
solo agenda la siguiente auditoría de cinco, quince o sesenta minutos.
`queued`, `pinning` e `initial_visibility` son espera normal, no fallos.

Al activar esta política en una base que ya contenía esperas de la estrategia
anterior, los ARKs de durabilidad vencidos se reprograman una sola vez para la
auditoría horaria, con una dispersión corta por identificador. No se modifican
sus CIDs, asignaciones de Cluster ni estados públicos.

## Chain: página y ventanas RPC

Chain reclama hasta 100 ARKs por ciclo, pero para cada autoridad los divide en
ventanas secuenciales de 50 (`CHAIN_WORKER_RPC_BATCH_SIZE`). Una página completa
genera dos envíos de 50; la segunda ventana sólo comienza después de preparar
la primera, manteniendo el orden de nonces y evitando competencia entre
transacciones de la misma cuenta.

## Operación

El estado simple de workers sólo lee heartbeats. El diagnóstico `detail=full`
agrega una consulta SQL acotada para `ready_now`, `waiting`, edad de la espera,
próxima comprobación, motivos y nivel de presión (`normal`, `busy`,
`saturated`). No realiza observaciones IPFS salvo en el bloque explícito de
infraestructura.

La base nueva crea el índice parcial `ix_ark_processing_active_due` sobre
`(processing_stage, next_action_at, id)` para los estados internos READY y
WAITING. Antes de operar con carga de producción debe ejecutarse `EXPLAIN
ANALYZE` de los selectores de Metadata, Availability, Chain y el diagnóstico
full contra una base de al menos 10.000 ARKs.
