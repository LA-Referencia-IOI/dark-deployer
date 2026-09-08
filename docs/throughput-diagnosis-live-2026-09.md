# Diagnóstico en vivo del throughput de publicación

Fecha de observación: 2026-09-08. Este documento describe el comportamiento
observado durante una carga masiva y no cambia la implementación.

## Conclusión ejecutiva

El cuello de botella principal está en la visibilidad inicial de los pins en
IPFS Cluster. La entrada de metadata produce CIDs más rápido de lo que Cluster
puede pasar de `queued`/`pinning` a un pin observable. El problema se amplifica
porque el reconciliador vuelve a consultar repetidamente los mismos CIDs sin
un vencimiento efectivo: los registros quedan con `next_action_at = NULL` aun
cuando están esperando una operación normal de Cluster.

No hay evidencia de fallos permanentes, CID mismatch ni pérdida de datos. RPC y
validadores siguen disponibles; Chain puede procesar registros que ya tienen
ambos primeros pins, pero recibe pocos nuevos mientras Availability está
bloqueada por la cola de Cluster.

## Evidencia del monitor

Las muestras de `dark-infrastructure-monitor.jsonl` muestran:

- todos los endpoints de Admin, Minter, Resolver y Store normalmente en `OK`;
- los dos Clusters saludables y viendo dos peers;
- ciclos de metadata de 100 registros en aproximadamente 30,8 segundos;
- ciclos de replication de 100 registros entre 3 y 4 segundos;
- ciclos de Chain de 20 registros entre 5 y 12 segundos;
- Store API entre aproximadamente 8% y 26% de CPU durante la carga;
- ambos Clusters entre aproximadamente 20% y 45% de CPU, con mucho I/O acumulado;
- RPC con picos de CPU superiores al 100% y validadores con picos variables;
- cero fallos permanentes en el diagnóstico full.

En una muestra representativa, la cola era:

```text
metadata:     ready_now=430, waiting=0
availability: ready_now=0, waiting=1801
replication:  ready_now=1267, waiting=0
waiting reasons: cluster_queued=809, cluster_pinning=69,
                 initial_visibility=923
```

La suma de estados de espera no representa fallos: son solicitudes aceptadas
por Cluster que todavía no son observables como pins confirmados.

## Flujo y concurrencia actuales

### Metadata worker

- Página configurada: 100 ARKs.
- Concurrencia: 4 hilos.
- Cada ARK adquiere un advisory lock PostgreSQL.
- Puede ejecutar hasta dos operaciones de almacenamiento por ARK (L2 y luego
  L1), de forma secuencial dentro del hilo.
- Cada operación llama a `POST /v1/store`, que solicita `replication-min=1` y
  `replication-max=1`.
- Al completar ambos CIDs, el registro pasa a `AVAILABILITY/READY`.

El worker es correcto desde el punto de vista de exclusión, pero cuatro hilos
de ingreso pueden generar una presión sostenida de hasta cientos de llamadas
de almacenamiento por minuto durante una importación grande.

### Replication worker

- Página configurada: 100 ARKs.
- Concurrencia de reconciliación: 2 hilos.
- Extrae CIDs únicos y usa `POST /v1/status/batch`, con máximo 200 CIDs por
  llamada al Store API.
- El Store API limita la observación interna a 10 consultas concurrentes y
  cada observación consulta `/pins/{cid}` en Cluster.
- Si existe backlog crítico, prioriza Availability y no promueve durabilidad.

La deduplicación por página es correcta, pero una página de 100 ARKs puede
implicar hasta 200 CIDs y hasta 200 consultas de Cluster dentro del Store API.

### Chain worker

- Página observada: 20 ARKs.
- Solo selecciona registros que ya tienen los dos primeros pins confirmados.
- En la evidencia no es el cuello inicial: tenía miles de ARKs potencialmente
  listos, pero Availability no estaba liberando nuevos registros.

## Competencia entre workers

Los workers no compiten por el mismo ARK gracias al advisory lock, pero sí
compiten por recursos compartidos:

1. Metadata produce nuevas solicitudes `/add`.
2. Replication consulta `/pins/{cid}` para solicitudes que aún están en cola.
3. Store API ejecuta ambas clases de tráfico sobre los mismos endpoints Cluster.
4. Cluster procesa simultáneamente nuevas asignaciones y observaciones.
5. PostgreSQL recibe escrituras de metadata, estados de replicación,
   heartbeats y consultas de cola.

Por ello, aunque la exclusión por ARK es correcta, el sistema no tiene un
presupuesto global que limite la presión sobre Store API/Cluster.

## Hallazgo de planificación más importante

En `get_metadata_pending_reconciliation`, los registros se seleccionan cuando
`next_action_at` es nulo o vencido. Sin embargo, los estados normales
`cluster_queued`, `cluster_pinning` e `initial_visibility` se guardan con
`next_action_at = NULL`. Además, el parámetro histórico `recheck_seconds` se
descarta en el repositorio.

Resultado:

```text
ciclo N:     consultar CID todavía queued
guardar:     waiting + next_action_at=NULL
ciclo N+1:   volver a consultar el mismo CID
ciclo N+2:   volver a consultar el mismo CID
```

Esto convierte una espera normal de Cluster en polling continuo. No produce un
error funcional, pero consume Store API, conexiones HTTP, CPU e I/O de Cluster
sin aumentar la probabilidad de que el pin haya cambiado entre consultas.

## Qué no parece ser el cuello

- No hay fallos permanentes ni errores de CID.
- Los dos peers Cluster están conectados y saludables.
- El Store API acepta los `add` y devuelve CIDs correctamente.
- La blockchain está viva y avanzando.
- Los advisory locks no muestran contención anormal en la evidencia disponible.
- La memoria de los workers es baja; el problema es principalmente I/O,
  latencia y presión de solicitudes.

## Alternativas de mejora

### A. Backoff real para estados normales de Cluster (prioridad alta)

Asignar un `next_action_at` explícito según el estado observado:

- `queued`: backoff inicial mayor;
- `pinning`: intervalo intermedio;
- `initial_visibility`: intervalo progresivo;
- `error`/`unpinned`: ruta de reparación controlada.

El backoff debe ser por ARK o por CID, con límite superior y jitter. La
selección no debe volver a incluir un registro antes de su vencimiento.

### B. Dedupe y coordinación por CID

Varios ARKs pueden compartir o consultar el mismo CID. Un cache de observación
de corta duración en el proceso del reconciliador, o una tabla de observación
por CID, puede evitar repetir `/pins/{cid}` dentro de ventanas muy cortas.

### C. Presupuesto compartido de Store API

Separar límites para:

- `add` crítico de metadata;
- status de primer pin;
- status de durabilidad.

La durabilidad debe ceder capacidad cuando existe backlog de Availability. La
observación de pins iniciales debe tener prioridad sobre la durabilidad.

### D. Reducir el trabajo SQL repetido

Replication vuelve a cargar metadata individualmente para cada ARK después de
seleccionar la página. Conviene devolver ARK y ambos CIDs en una sola consulta
acotada, evitando el patrón N+1. Deben mantenerse los índices de etapa/estado y
`next_action_at`, pero verificarse con `EXPLAIN ANALYZE` sobre una carga real.

### E. Control de ingreso

Cuando la cola de Availability supere un umbral, el productor externo puede
recibir una señal de backpressure o reducir el batch. Esto no cambia la
semántica de publicación y evita que Cluster acumule una cola que el sistema no
puede drenar.

### F. Ajustar concurrencia mediante medición

Subir hilos de metadata o replication no es automáticamente mejor. Primero se
debe medir latencia p50/p95 de `/v1/store` y `/v1/status/batch`, profundidad de
cola Cluster y tasa de transición `AVAILABILITY → CHAIN`. Solo aumentar
concurrencia si Cluster mantiene latencia estable y no aparecen 503/timeouts.

## Orden recomendado para una futura implementación

1. Implementar backoff efectivo para `queued`, `pinning` e
   `initial_visibility`.
2. Evitar la consulta N+1 de metadata en la selección de páginas.
3. Medir durante una carga controlada la tasa de `/add`, `/pins`, latencia y
   profundidad de cola.
4. Añadir límites separados para tráfico crítico y mantenimiento.
5. Evaluar backpressure de ingreso y ajustar concurrencia solo con datos.

La prioridad es impedir el polling prematuro. Aumentar workers o tamaños de
página antes de corregirlo probablemente empeoraría la cola de Cluster.
