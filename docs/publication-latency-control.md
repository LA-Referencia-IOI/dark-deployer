# Control de latencia de publicación

## Objetivo

El camino crítico es `Metadata -> CIDs aceptados -> primer pin real L1/L2 -> Chain`.
La segunda réplica y la purga son mantenimiento y nunca compiten con ese camino.

## Programación de Availability

`next_action_at` es la única autorización para volver a consultar un ARK en
Availability. Un ARK recién ingresado usa `READY` y se observa de inmediato;
un ARK en `WAITING` siempre tiene una fecha concreta. Las esperas normales no
son errores ni incrementan los intentos:

| Estado de Cluster | Inicio | Tope |
| --- | ---: | ---: |
| `pinning` | 2 s | 10 s |
| `initial_visibility` | 3 s | 20 s |
| `queued` | 5 s | 30 s |

El incremento es exponencial, con jitter determinista de ±10% por ARK. Se
reinicia cuando cambia el estado observado o aumenta el número de copias
confirmadas. Así se evita que páginas enteras vuelvan a consultar de inmediato
los mismos CIDs.

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
