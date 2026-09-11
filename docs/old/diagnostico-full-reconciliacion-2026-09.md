# Diagnóstico temporal: `detail=full`, retención y reconciliación

> **Diagnóstico fechado.** Los números y conclusiones corresponden a la
> ejecución indicada en este documento. No representan una métrica en vivo.

> **Histórico, reemplazado.** Este documento analiza el workflow previo con
> recovery automático; la referencia vigente es la
> [implementación de simplificación](worker-workflow-simplification-implementation.md).

> **Estado:** diagnóstico operativo temporal. Describe la instalación observada en septiembre de 2026 y no constituye todavía la especificación definitiva del sistema. Las correcciones de status, parser de CID, probes y reconstrucción del minter descritas al final están implementadas en la revisión de trabajo posterior a este diagnóstico.

## Resumen ejecutivo

La cola de replicación que muestra `GET /api/v1/worker/status?detail=full` está sobredimensionada. El diagnóstico llegó a informar `5.432` elementos pendientes, pero el `ReplicationReconciliationWorker` no tenía candidatos reales y ejecutaba ciclos correctos con `processed=0`.

El problema principal no es una reconciliación atascada ni una consulta SQL catastrófica. Se mezclan tres situaciones distintas:

1. El status `full` cuenta registros terminados o fallidos como si fueran trabajo de replicación.
2. Store API rechaza algunas respuestas HTTP 200 de Cluster REST porque no encuentra `Hash` ni `cid`.
3. IPFS Cluster estaba ejecutando una recuperación/sincronización del pinset, con respuestas lentas o incompletas en algunos probes.

## Evidencia de la base de datos

La distribución observada fue:

```text
stage 1 / failed: 5868
stage 3 / failed: 1107
stage 5 / done:   4325
```

Los códigos de error fueron:

```text
storage_invalid_response: 5868
chain_reverted:           1107
replication_unavailable:     2
sin error:                4323
```

Los 4.325 registros publicados tenían dos réplicas para L1 y L2 y `payload_purged_at` informado. Los 1.107 registros bloqueados por blockchain tenían una réplica de cada nivel.

No había registros procesables en las etapas `AVAILABILITY` o `REPLICATION` con estado `PENDING`. Por tanto, la selección real del reconciliador devolvía cero candidatos.

## Por qué `full` informa una cola falsa

En `components/dark-core-minter-api/app/api/worker.py`, el status de replicación define un registro como retenido cuando se cumple:

```python
level1_json IS NOT NULL OR original_content IS NOT NULL
```

Esa condición no distingue entre:

- un payload pendiente de procesar;
- un registro terminado cuyo JSON histórico todavía existe;
- un registro fallido en otra etapa;
- un registro que ya tiene `payload_purged_at`.

Además, el agregado no restringe la selección a las etapas y estados que utiliza el worker.

La fuente de verdad correcta para retención es:

```text
payload_purged_at IS NULL     -> payload retenido
payload_purged_at IS NOT NULL -> payload purgado
```

Y la cola efectiva debe limitarse a:

```text
processing_stage IN (AVAILABILITY, REPLICATION)
processing_status IN (PENDING, RECOVERABLE)
level1_cid IS NOT NULL
original_cid IS NOT NULL
payload_purged_at IS NULL
```

## Coste de las consultas

Las mediciones directas fueron aproximadamente:

```text
consulta real del reconciliador:      1,4 ms
agregado de registros activos:        7,6 ms
consulta inflada de replicación:      101 ms
```

El índice existente `ix_ark_processing_queue` ya se utiliza para encontrar trabajo del reconciliador. No hay evidencia suficiente para crear índices nuevos antes de corregir la semántica de las consultas.

Después de corregirlas deberá repetirse la medición con una base mayor. Solo si el volumen lo justifica convendría evaluar un índice parcial sobre metadata con CIDs completos y `payload_purged_at IS NULL`.

## Situación del replication worker

El worker tenía heartbeat vigente, proceso activo y ciclos regulares. Sus ciclos indicaban:

```text
processed = 0
next_action = sleep
```

Este resultado era coherente con la base: no existían filas en las etapas de disponibilidad o replicación que pudieran procesarse.

Por tanto, no hay evidencia de que el worker haya dejado páginas fuera. El problema está en que el status `full` utiliza una definición de cola distinta y mucho más amplia.

El status debería separar explícitamente:

- `candidate_pending`: trabajo real del reconciliador;
- `retained_unprocessed`: payload retenido que todavía requiere trabajo;
- `done`: réplica completada y payload purgado;
- `failed_chain`: bloqueado por blockchain;
- `storage_error`: error al obtener o interpretar un CID.

## Situación de IPFS Cluster

Los peers Cluster se veían entre sí. No se observó evidencia de que todos los CIDs hubieran quedado fuera del pinset.

Sí se observaron eventos de recuperación y sincronización CRDT:

```text
repinning
new pin added
repinned ... out of peer
```

También hubo respuestas lentas o incompletas del segundo endpoint `/peers` y errores `context canceled` en Kubo. Esto es compatible con una recuperación del Cluster bajo carga y explica que Store API considere el almacenamiento temporalmente no disponible.

## Error `missing Hash` en Store API

`components/dark-store-api/app/backends/ipfs_cluster.py` acepta actualmente estas formas de respuesta de `/add`:

```json
{"Hash": "..."}
```

o:

```json
{"cid": "..."}
```

El log observado fue equivalente a:

```text
HTTP 200
invalid IPFS add response: missing Hash/cid
```

Esto significa que el endpoint respondió, pero el cuerpo no tenía la estructura esperada. Las hipótesis a verificar son:

- diferencia de formato entre la versión de Cluster REST y el parser;
- respuesta asíncrona con campos anidados;
- respuesta parcial o mezcla de eventos;
- incompatibilidad del endpoint `/add` con `local=true`;
- respuesta alterada durante la inestabilidad del peer.

La siguiente instrumentación diagnóstica debe registrar únicamente `Content-Type`, tamaño, tipo JSON y nombres de campos; no debe registrar el payload completo.

## Timeouts y disponibilidad

El Store API prueba Kubo y luego recorre endpoints Cluster de forma secuencial. Cada probe puede esperar hasta cinco segundos. El minter tiene un timeout de almacenamiento de diez segundos.

Durante la recuperación de Cluster, esa combinación puede producir este flujo:

```text
Cluster tarda en responder
        ↓
Store API agota uno o más probes
        ↓
Store API responde error o respuesta incompleta
        ↓
Minter marca fallo de storage
        ↓
Replication worker pausa y reintenta
```

Debe mantenerse la separación entre:

- `/health/live`: proceso vivo, sin dependencias externas;
- `/health/read`: capacidad de lectura;
- `/health/write`: capacidad de aceptar contenido.

Para `write` conviene usar un deadline global, aceptar el primer endpoint válido y evitar que la disponibilidad dependa de esperar secuencialmente todos los peers.

## Acciones recomendadas

1. Corregir los agregados de status y `/worker/replication` para usar etapas, estados y `payload_purged_at`.
2. Separar registros terminados, fallos de chain y candidatos reales de reconciliación.
3. Inspeccionar la estructura real de la respuesta Cluster `/add` cuando se produce `missing Hash/cid`.
4. Ajustar los probes y los timeouts de Store API con un deadline global.
5. Repetir las mediciones después de esos cambios.
6. Evaluar índices adicionales solo con evidencia posterior de crecimiento o regresión SQL.

## Correcciones aplicadas

Se aplicaron las siguientes correcciones después de capturar este diagnóstico:

1. Los agregados de replicación y `/worker/replication` ahora usan `payload_purged_at`, etapas de disponibilidad/replicación y estados procesables. Los ARKs fallidos en chain y los ya purgados no aparecen como trabajo pendiente.
2. Los agregados que cruzan `ark_metadata` y `ark_records` usan un `JOIN` explícito, evitando productos cartesianos en el diagnóstico `full`.
3. Store API acepta CIDs devueltos como objetos CID JSON, por ejemplo `{"cid": {"/": "bafy..."}}`, además de las formas string previas. Si el formato sigue siendo desconocido, registra de forma segura el tipo de contenido, tamaño y nombres de campos.
4. Los probes de Kubo y Cluster tienen presupuesto total acotado, intentos cortos y ejecución concurrente entre ambas dimensiones. Un Cluster lento puede dar paso al siguiente endpoint sin consumir el timeout completo del minter.
5. `install.py rebuild minter` inicia la API y los tres workers vigentes:
   metadata, replication y chain.
6. La recuperación automática fue retirada. Los errores históricos no se
   reencolan mediante un worker separado; los casos permanentes requieren
   revisión administrativa explícita.
7. El `full` ya no consulta `txpool_*` a través de `web3`; usa conectividad y bloque para el diagnóstico. La capacidad completa con txpool permanece disponible para los workers.

## Conclusión

El replication worker no está atascado con miles de elementos: actualmente no tiene trabajo válido que procesar. La cifra alta del `full` es principalmente una sobreestimación del diagnóstico.

El problema operativo real combina una clasificación incorrecta de la cola, respuestas inesperadas de Cluster REST y una recuperación de IPFS Cluster que vuelve frágiles los probes de escritura. La prioridad debe ser corregir la observabilidad y validar el contrato de `/add`; agregar índices por sí solo no resolvería el comportamiento observado.
