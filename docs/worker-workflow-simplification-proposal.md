# Propuesta de simplificación del workflow de publicación

> **Estado: histórico.** La propuesta fue implementada y posteriormente
> simplificada. La referencia actual es [`worker-cycle-evidence.md`](worker-cycle-evidence.md);
> este documento conserva únicamente el razonamiento original.

**Estado:** implementado pendiente de recreación y verificación en una base nueva

**Fecha:** 2026-09-07

## Objetivo

Simplificar el procesamiento interno del minter sin cambiar los estados públicos de los ARKs. Este documento propone un modelo que pueda ser entendido por un operador mirando una sola pantalla y que pueda ser implementado por otro agente sin conservar compatibilidad con el workflow interno actual.

Los estados públicos permanecen exactamente como están:

```text
reserved -> draft/update -> published -> tombstone
```

La propuesta cambia únicamente workers, etapas internas, reintentos, errores y reportes.

## Diagnóstico: por qué el modelo actual resulta difícil de entender

El problema no está solamente en el dashboard. Hay tres modelos diferentes superpuestos:

1. El runtime del proceso: vivo, ejecutando un ciclo, durmiendo o pausado.
2. El estado de los registros: listos, programados para otra comprobación, recuperables o fallidos.
3. El resultado del último ciclo: cuántos procesó, cuántos tuvieron éxito y cuánto duró.

La interfaz mezcla los tres. Por eso puede mostrar un replication worker como `idle`, doce registros `ready now` y un último ciclo de 43 elementos. Las tres afirmaciones pueden ser técnicamente ciertas en instantes diferentes:

- el último ciclo procesó 43;
- después quedaron doce registros cuya fecha ya venció;
- el proceso está dentro de su espera de 30 segundos antes de consultar otra vez.

Sin embargo, para un operador parece una contradicción. `idle` se interpreta como “no tiene trabajo”, cuando actualmente significa aproximadamente “el proceso está vivo, pero no estaba ejecutando I/O en el instante del heartbeat”. `ready now` tampoco significa que esté siendo procesado; significa que una consulta SQL lo considera elegible. El dashboard no informa cuándo despierta el worker ni cuál será su siguiente acción efectiva.

### Complejidad estructural acumulada

El workflow actual contiene:

- un estado público;
- una etapa interna;
- un estado interno;
- una fecha de próximo intento;
- códigos de error recuperables y permanentes;
- contadores y timestamps específicos de replicación;
- un recovery worker que vuelve a introducir registros en las colas normales;
- una regla global que decide si recovery puede actuar;
- heartbeats que describen el proceso, pero no el registro que está atendiendo;
- reportes que reconstruyen “colas” a partir de varias combinaciones de columnas.

Cada elemento fue agregado para resolver un problema real, pero juntos formaron dos schedulers: los workers normales y recovery. Recovery no realiza la operación que falló; solamente cambia el estado para que otro worker vuelva a intentarla. Esto añade una segunda cola, otra cadencia y otra condición de bloqueo sin aportar una capacidad técnica que el worker original no pueda tener.

### El caso IPFS agrava la confusión

IPFS Cluster es asíncrono. Después de recibir un CID, `pinning` es una situación normal. El modelo actual intenta traducir esta espera natural a estados genéricos como `PENDING`, `scheduled`, `delayed` o, históricamente, `RECOVERABLE`.

El resultado es que un operador no sabe si un registro:

- espera simplemente que llegue su próxima hora de observación;
- espera que Cluster complete un pin;
- perdió el contenido;
- tuvo un timeout consultando Store API;
- está esperando ser rescatado por recovery.

La diferencia debe estar en la etapa y en la razón de espera, no en varias colas paralelas.

## Principio de simplificación

Cada operación debe ser reintentada por el mismo worker responsable de realizarla. Un error temporal no debe sacar el registro de su etapa ni enviarlo a otro scheduler.

Esto permite eliminar el recovery worker automático. La recuperación administrativa continúa existiendo, pero se limita a autorizar que un fallo permanente vuelva a su etapa original.

## Arquitectura recomendada

Mantener tres workers especializados:

| Worker | Responsabilidad exclusiva |
| --- | --- |
| Metadata | crear y persistir L2 y L1; guardar ambos CIDs |
| Replication | observar disponibilidad inicial, observar réplica objetivo y purgar payloads cuando corresponda |
| Chain | publicar o actualizar el ARK en blockchain |

No crear un cuarto worker para reencolar trabajo. API, metadata, replication y chain continúan en procesos separados porque tienen dependencias y perfiles de carga distintos. La simplificación está en el workflow, no en forzar todos los procesos dentro de uno.

### Flujo propuesto

```text
Metadata recibido
    |
    v
METADATA / READY
    |
    | guarda L2 y L1
    v
AVAILABILITY / WAITING (próxima comprobación explícita)
    |
    | alcanza el mínimo publicable
    v
CHAIN / READY
    |
    | transacción confirmada
    v
REPLICATION / WAITING (si falta durabilidad)
    |
    | alcanza el objetivo de réplicas y purga payload
    v
COMPLETE
```

Si una operación tiene un fallo transitorio, permanece en la misma etapa y pasa a `WAITING` con una fecha concreta. Cuando vence esa fecha, vuelve a ser `READY` para el mismo worker. No interviene recovery.

Si el error es permanente, pasa a `FAILED`. Un administrador puede revisar el caso y devolverlo a `READY` en su misma etapa. No se recalcula automáticamente una etapa nueva salvo que la herramienta administrativa lo solicite explícitamente y muestre previamente el cambio.

## Modelo interno mínimo

### Etapas

```text
NONE
METADATA
AVAILABILITY
CHAIN
REPLICATION
COMPLETE
```

### Estados internos

```text
READY
WAITING
FAILED
DONE
CANCELLED
```

No se necesita `RECOVERABLE` como estado. “Recuperable” es una propiedad del código de error, no una cola ni una etapa.

- `READY`: puede ser tomado ahora por el worker de su etapa.
- `WAITING`: no debe ser tomado antes de `next_action_at`.
- `FAILED`: requiere una decisión administrativa.
- `DONE`: completó todo el workflow interno.
- `CANCELLED`: el estado público ya no permite continuar.

No es necesario persistir `RUNNING` por ARK. El advisory lock de PostgreSQL sigue siendo la fuente de verdad para la exclusión durante la operación. Si se necesita mostrar actividad actual, el worker puede informar en su heartbeat cuántos elementos tiene en su lote activo; no debe convertirse en otro estado durable que quede huérfano si el proceso muere.

### Campos necesarios por ARK

```text
processing_stage
processing_status
next_action_at
attempt_count
last_error_code
last_error_detail
last_error_at
```

Los CIDs, conteos de réplicas y timestamps de observación permanecen en la metadata del ARK porque describen el contenido, no el scheduler.

`next_action_at` reemplaza conceptualmente la ambigüedad de `processing_next_attempt_at`: no siempre se trata de un error o reintento. En availability y replication normalmente representa una comprobación planificada.

## Manejo de IPFS

El replication worker utiliza el estado real de Store API:

| Respuesta | Acción |
| --- | --- |
| `pinned` y réplicas suficientes para publicar | avanzar a `CHAIN / READY` |
| `pinned` pero por debajo del objetivo final | mantener la etapa pertinente en `WAITING` |
| `pinning` | `WAITING`, con razón `cluster_pinning` |
| `unpinned` dentro de la ventana de gracia | `WAITING`, con razón `initial_visibility` |
| `unpinned` después de la ventana de gracia | intentar una reparación acotada; si falla, aplicar backoff en la misma etapa |
| Store API no disponible | `WAITING`, código transitorio y backoff |
| CID diferente durante una reparación | `FAILED`, porque es un conflicto de integridad |

No se debe crear una cola “awaiting pin” separada en base de datos. Es una vista de `AVAILABILITY/WAITING` o `REPLICATION/WAITING` con una razón concreta.

## Reintentos sin Recovery Worker

Cada worker selecciona exclusivamente:

```sql
processing_stage = :su_etapa
AND (
    processing_status = READY
    OR (
        processing_status = WAITING
        AND next_action_at <= now()
    )
)
```

Al reclamar un registro vencido, el worker lo trata como listo para su ciclo. El advisory lock por ARK evita que otro worker o una actualización de API actúe simultáneamente.

Una política única decide el resultado de un fallo:

- código transitorio: `WAITING`, próxima fecha calculada mediante backoff;
- código permanente: `FAILED`, sin fecha automática;
- éxito parcial normal: `WAITING`, fecha fija de observación y sin presentarlo como error;
- éxito completo de etapa: siguiente etapa en `READY`.

El catálogo `processing_error_codes` puede conservar `retryable`, pero su función será elegir entre `WAITING` y `FAILED`. No será usado por otro worker para descubrir registros.

## Recuperación administrativa

La API administrativa se simplifica a una operación:

```text
FAILED -> READY en la misma etapa
```

Debe:

- exigir una lista explícita de ARKs y una razón;
- conservar el error anterior en auditoría o log;
- limpiar la fecha de espera;
- reiniciar el contador solo si esa es la decisión documentada;
- no publicar directamente ni consultar IPFS/blockchain;
- mostrar en dry-run la etapa a la que volverá cada ARK.

No debe existir recuperación automática de registros `FAILED`. Si un error fue clasificado incorrectamente como permanente, el administrador lo aprueba y el worker responsable vuelve a realizar la operación.

## Un reporte comprensible

La pantalla no debe intentar representar una cola informática abstracta. Debe responder cinco preguntas por worker:

1. ¿Está vivo?
2. ¿Qué está haciendo ahora?
3. ¿Cuánto trabajo puede tomar ahora?
4. ¿Cuánto trabajo espera y hasta cuándo?
5. ¿Qué fallos requieren intervención?

### Estado del proceso

Usar solamente:

```text
RUNNING
SLEEPING
PAUSED
DOWN
DISABLED
```

- `RUNNING`: está ejecutando un ciclo.
- `SLEEPING`: espera deliberadamente hasta una hora concreta.
- `PAUSED`: una dependencia impide trabajar; debe incluir causa.
- `DOWN`: heartbeat vencido o proceso detenido.
- `DISABLED`: deshabilitado por configuración.

No usar `idle`. Si está esperando su próximo poll, debe decir `SLEEPING until 16:42:30`. Si no tiene trabajo y duerme, el texto debe decir “Sin trabajo; próxima revisión en 18 s”. Si tiene doce registros listos pero aún duerme, debe decir “12 listos; despierta en 4 s”.

### Carga de trabajo

Mostrar únicamente:

| Etiqueta | Significado |
| --- | --- |
| **Listos** | pueden ser tomados en el siguiente ciclo |
| **Esperando** | tienen una acción futura planificada |
| **Próxima revisión** | fecha más cercana entre los que esperan |
| **Fallidos** | necesitan intervención administrativa |

En replication puede agregarse un desglose secundario, sin convertirlo en otra cola:

```text
Esperando: 58
  - pinning: 35
  - objetivo de réplicas: 23
Próxima revisión: en 17 segundos
```

### Actividad del último ciclo

Debe estar visualmente separada de la carga actual:

```text
Último ciclo: hace 8 s
Procesó 43: 43 avanzaron, 0 fallaron
Duración: 2,0 s
Próximo ciclo: en 22 s
```

Así desaparece la contradicción entre “lo último que hizo” y “qué tiene ahora”.

### Ejemplo para la situación observada

En lugar de:

```text
Replication: idle
Ready now: 12
Scheduled: 58
```

mostrar:

```text
Replication: SLEEPING
12 listos para la próxima comprobación
58 esperando; próxima revisión en 17 s
El worker despierta en 4 s
Último ciclo: procesó 43 hace 26 s
```

La diferencia entre “el worker despierta” y “la próxima revisión de un ARK vence” es importante. Son dos relojes distintos y deben tener nombres explícitos.

## API de status recomendada

El endpoint liviano sigue informando solo salud del proceso:

```json
{
  "workers": {
    "replication": {
      "enabled": true,
      "process_state": "sleeping",
      "alive": true,
      "wake_at": "2026-09-07T16:42:30Z",
      "wake_in_seconds": 4,
      "last_cycle_at": "2026-09-07T16:42:00Z",
      "last_cycle": {"processed": 43, "advanced": 43, "failed": 0}
    }
  }
}
```

El endpoint full añade agregados de carga:

```json
{
  "workload": {
    "replication": {
      "ready": 12,
      "waiting": 58,
      "next_action_at": "2026-09-07T16:42:47Z",
      "waiting_reasons": {
        "cluster_pinning": 35,
        "replica_target": 23
      },
      "failed": 0
    }
  }
}
```

No incluir aliases como `pending_total`, `ready_now`, `delayed_by_backoff` ni una cola ficticia de recovery. Elegir un contrato único y actualizar minter y dashboard juntos.

## Qué eliminar

- El proceso `recovery-worker` y su heartbeat.
- El estado interno `RECOVERABLE`.
- La regla global “todos los workers normales deben estar idle”.
- La cola `queues.recovery`.
- Los estados visuales `idle`, `backlogged`, `delayed` y `blocked` como mezcla de proceso y carga.
- Los conteos duplicados `pending`, `ready` y `delayed` que describen los mismos registros desde perspectivas distintas.
- La inferencia de error a partir de texto; todo fallo real debe tener código estructurado.

## Qué conservar

- API y workers en procesos separados.
- Advisory lock PostgreSQL por ARK.
- Locks globales para evitar dos instancias del mismo worker cuando corresponda.
- Heartbeats livianos.
- Probes externos solamente en diagnóstico full.
- Etapas separadas para metadata, availability, chain y replication.
- Códigos de error estructurados y clasificación transitoria/permanente.
- Endpoint administrativo con mTLS.
- Estados públicos existentes.

## Estrategia de implementación para otro agente

### Fase 1 — fijar el modelo

1. Reemplazar los estados internos por `READY`, `WAITING`, `FAILED`, `DONE`, `CANCELLED` en el esquema inicial.
2. Renombrar conceptualmente `processing_next_attempt_at` a `next_action_at` en la nueva base desde cero.
3. Añadir una razón de espera compacta y enumerable, por ejemplo `pinning`, `replica_target`, `storage_backoff`, `rpc_backoff` y `chain_confirmation`.
4. No implementar migración de datos históricos si el despliegue objetivo parte con una base nueva.

### Fase 2 — hacer autónomo cada worker

1. Metadata transforma sus errores transitorios en `WAITING/METADATA`.
2. Replication maneja tanto availability como durabilidad y transforma toda espera normal en `WAITING`.
3. Chain conserva en `WAITING/CHAIN` timeouts o resultados ambiguos y reconcilia antes de reenviar.
4. Cada worker selecciona `READY` y `WAITING` vencidos de sus propias etapas.
5. Eliminar las llamadas y dependencias del recovery worker.

### Fase 3 — retirar recovery

1. Eliminar comando, PID, advisory lock global, servicio Compose y variables `RECOVERY_WORKER_*`.
2. Cambiar el endpoint administrativo para operar solamente sobre `FAILED` y devolverlos a `READY` en la misma etapa.
3. Eliminar `RECOVERABLE` del catálogo, modelos, queries, tests y documentación.
4. Verificar que ningún error transitorio llegue a `FAILED` salvo agotamiento de una política que explícitamente lo convierta en permanente.

### Fase 4 — reemplazar los contratos de status

1. Separar `process_state` de `workload`.
2. Hacer que el worker heartbeat registre `wake_at`, inicio/fin de ciclo y tamaño del lote activo.
3. Calcular `ready`, `waiting`, `next_action_at`, `waiting_reasons` y `failed` mediante agregados SQL acotados.
4. Eliminar las claves antiguas en la misma versión; no mantener dos contratos internos.
5. Actualizar dashboard y minter en el mismo cambio para evitar estados híbridos.

### Fase 5 — rehacer la pantalla

1. Mostrar primero estado del proceso y cuenta regresiva hasta despertar.
2. Mostrar después carga actual y fecha del próximo ARK programado.
3. Mostrar el último ciclo en una sección separada.
4. Usar verde para proceso sano, azul para espera normal, amarillo para dependencia pausada y rojo solo para caída o fallos permanentes.
5. Eliminar el panel de recovery. La página Errors lista únicamente `FAILED`.

### Fase 6 — validar el workflow completo

Probar al menos:

- almacenamiento inmediato seguido de varios estados `pinning`;
- Store API temporalmente caída;
- cero réplicas dentro y fuera de la ventana de gracia;
- publicación chain exitosa, timeout ambiguo y revert permanente;
- reinicio de cualquier worker durante I/O;
- dos workers intentando reclamar el mismo ARK;
- recuperación administrativa de un `FAILED`;
- lote masivo mientras los workers alternan `RUNNING` y `SLEEPING`;
- coherencia exacta entre las cifras de API, SQL y dashboard.

## Criterios de aceptación

La implementación se considera comprensible cuando, para cualquier worker, una persona puede responder mirando el dashboard:

- si el proceso está vivo;
- si está trabajando o cuándo despertará;
- cuántos registros puede tomar al despertar;
- cuántos esperan una condición normal y cuál es la próxima fecha;
- cuántos requieren intervención real;
- qué ocurrió en el último ciclo, sin confundirlo con el estado actual.

Operativamente:

- ningún ARK transitorio necesita que otro worker lo reencole;
- ningún `pinning` aparece como error;
- no existe starvation producido por una cola recovery;
- un fallo temporal permanece en la etapa que sabe resolverlo;
- solamente `FAILED` requiere acción administrativa;
- el número de workers se reduce de cuatro a tres sin combinar dependencias incompatibles.

## Recomendación final

La mejor simplificación no es renombrar otra vez `pending`, `ready` y `delayed`. Es eliminar el segundo scheduler representado por recovery y hacer que cada worker sea dueño completo de sus esperas y reintentos.

El modelo recomendado es:

```text
etapa responsable + READY/WAITING/FAILED + próxima acción concreta
```

Con ese modelo, el proceso puede estar `SLEEPING` aunque existan registros `READY`, pero la interfaz mostrará cuánto falta para que despierte. Un registro puede estar `WAITING`, pero siempre mostrará hasta cuándo y por qué. Esa separación entre estado del proceso y estado del trabajo elimina la contradicción principal del sistema actual.
