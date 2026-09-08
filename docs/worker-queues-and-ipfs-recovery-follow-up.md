# Colas de workers, pinning asíncrono y recuperación

> **Histórico, reemplazado.** Este diagnóstico describe el modelo retirado de
> `PENDING`/`RECOVERABLE` y recovery automático. La referencia vigente es la
> [implementación de simplificación](worker-workflow-simplification-implementation.md),
> con la [verificación pendiente](worker-workflow-simplification-verification.md).

**Estado:** documento de trabajo

**Fecha:** 2026-09-07

## Propósito

Este documento reúne los hallazgos del procesamiento masivo de ARKs en el entorno developer HA y define el trabajo pendiente. No cambia los estados públicos de un ARK (`reserved`, `draft`, `update`, `published`, `tombstone`). Se concentra en los estados internos, la observabilidad y la recuperación segura.

## Situación observada

La infraestructura estaba sana: RPC disponible, Store API disponible y el Cluster IPFS con dos peers activos. Durante el lote masivo, el metadata worker persistió CIDs correctamente, pero muchos ARKs pasaron a aparecer como `recoverable` en la etapa `availability`.

En una observación posterior, la interfaz informó a la vez:

| Worker | Situación real observada | Problema de interpretación actual |
| --- | --- | --- |
| Metadata | sin trabajo listo | `pending` se entiende como trabajo activo aunque puede incluir trabajo programado |
| Replication | 31 registros programados para una comprobación futura | aparece como `delayed` y contribuye a bloquear recovery aunque no hay trabajo ejecutable ahora |
| Chain | sin trabajo | no es la causa del bloqueo |
| Recovery | miles de candidatos vencidos, pero sin procesar | se muestra `backlogged`, sin explicar con precisión qué lo bloquea |

La cifra de recovery no significa que miles de ARKs estén siendo procesados ni que todos tengan un error técnico. Describe candidatos internos que requieren una nueva clasificación o una nueva oportunidad de ejecución.

## Cómo funciona actualmente

Cada ARK tiene un estado público y una etapa interna. Las etapas principales son `METADATA`, `AVAILABILITY`, `REPLICATION` y `CHAIN`. Los tres workers normales seleccionan registros `READY` o `WAITING` cuyo próximo intento venció. No existe recovery worker automático en la implementación vigente.

El problema aparece después de guardar L1 y L2. IPFS Cluster puede aceptar el `add` y devolver el CID antes de que el pin se vea como materializado en todos los peers. Entre ambas cosas existe un estado normal de transición: `pinning`.

La implementación anterior reducía la respuesta de Store API a un número de réplicas. Si la primera consulta devolvía cero, el reconciliador podía tratar ese resultado transitorio como fallo recuperable e intentar volver a guardar el payload. Esto mezcla espera normal de infraestructura asíncrona con un error técnico real.

Ya se ajustó la parte inicial: cuando la comprobación de disponibilidad aún no alcanza la política de publicación, el registro permanece `PENDING` y se programa con `processing_next_attempt_at`; no debe pasar automáticamente a `RECOVERABLE`. También se añadió una ventana de gracia para que `pinning` y un `unpinned` recién aceptado no provoquen una reparación inmediata, y recovery ya ignora trabajo normal programado a futuro. Aún quedan los puntos siguientes.

## Causas técnicas que permanecen abiertas

### 1. Se pierde la semántica `pinning`

Store API conoce estados como `pinned`, `pinning`, `unpinning`, `unpinned` y `error`. El minter recibe, en la práctica, solo un total de réplicas. Por tanto no puede distinguir:

- contenido aceptado por Cluster y aún en propagación;
- contenido que perdió el pin;
- un peer con error;
- contenido aún no observable por una consulta eventual.

Sin esa distinción, una comprobación temprana de cero réplicas puede disparar una reparación innecesaria.

### 2. Reparación demasiado temprana

El reconciliador puede volver a enviar L1 o L2 a Store API cuando observa cero réplicas. Esa operación es válida ante una pérdida real, pero no mientras Cluster informa o razonablemente puede estar en `pinning`. En un lote grande amplifica tráfico, logs y carga sobre el primer peer sin mejorar la durabilidad.

### 3. Recovery queda bloqueado por trabajo que aún no es ejecutable

La condición descrita aquí pertenece al diseño retirado; el flujo vigente no
usa recovery automático ni rechecks diferidos por etapa.

El problema no es el advisory lock por ARK: ese lock sigue siendo la exclusión correcta para impedir que dos workers trabajen el mismo ARK. El problema es la regla global de prioridad, que confunde trabajo listo con trabajo calendarizado.

### 4. Datos históricos ambiguos

Existen registros `RECOVERABLE` históricos en availability con CIDs presentes pero sin `error_code` ni detalle. No representan un fallo comprobable. No deben mantenerse indefinidamente en una cola de errores ni reintentarse a ciegas como si fueran pérdidas de contenido.

## Vocabulario propuesto para las colas

No se debe usar `pending` como etiqueta visual genérica. En el modelo interno `PENDING` es útil, pero en la interfaz debe expresarse según su capacidad de ejecución.

| Nombre de reporte | Definición exacta | Qué significa para un operador |
| --- | --- | --- |
| **Listos ahora** (`ready_now`) | `PENDING` sin fecha futura de reintento | el worker puede tomarlos en este ciclo |
| **Programados** (`scheduled`) | `PENDING` con `processing_next_attempt_at > now` | espera intencional; no es un fallo ni trabajo bloqueante |
| **En ejecución** (`active`) | registro tomado por un worker, si se puede observar con fiabilidad | está consumiendo una ranura de trabajo |
| **Esperando pin** (`awaiting_pin`) | availability/replication con Cluster en `pinning` o dentro de la ventana de gracia | estado normal de IPFS asíncrono |
| **Recuperables elegibles** (`eligible_recovery`) | `RECOVERABLE` vencido con evidencia suficiente para reencolar | recovery podría promoverlos si tiene presupuesto y no desplaza trabajo normal listo |
| **Recovery bloqueado** (`blocked_recovery`) | candidatos elegibles que no se procesan por una regla de prioridad concreta | requiere mostrar el motivo exacto, no solo el total |
| **Fallos permanentes** (`permanent_failed`) | `FAILED` con código no reintentable | requiere revisión administrativa externa |

Los valores deben informarse por etapa. Un número aislado de 850 no es operativo si no indica si son ARKs listos para metadata, esperando un pin, programados para replication o candidatos de recovery.

## Contrato de status propuesto

El status liviano conserva solamente heartbeats y actividad. No debe intentar calcular todas las colas. El diagnóstico `detail=full` debe usar agregados SQL acotados y una sola sonda por dependencia externa.

Ejemplo orientativo del bloque de colas de un diagnóstico completo:

```json
{
  "queues": {
    "metadata": {"ready_now": 63, "scheduled": 0, "active": 0},
    "availability": {"ready_now": 0, "awaiting_pin": 815, "scheduled": 35},
    "replication": {"ready_now": 0, "scheduled": 31},
    "chain": {"ready_now": 0, "scheduled": 0},
    "recovery": {
      "eligible_now": 6030,
      "scheduled": 5237,
      "blocked": 6030,
      "blocked_reason": "normal_scheduled_only",
      "normal_ready_work": 0
    },
    "failed": {"permanent": 0, "unknown_code": 0}
  }
}
```

`normal_ready_work` debe contar solo trabajo normal ejecutable ahora: `PENDING` sin reintento futuro. Nunca debe contar todo `PENDING` para decidir si recovery puede usar capacidad ociosa.

Cada bloque debería incluir, cuando sea útil, `oldest_ready_at`, `oldest_scheduled_at` y una distribución acotada por etapa/código. No debe devolver mapas ilimitados por autoridad en el resumen.

## Política propuesta de scheduling

1. Los workers normales conservan prioridad para `ready_now` de su etapa.
2. Un ARK en `awaiting_pin` se reconsulta después de una ventana configurable; no se clasifica como error mientras Cluster indique `pinning` o la ventana no venza.
3. Recovery puede ejecutar una página pequeña cuando todos los workers normales están vivos y no existe trabajo normal `ready_now`.
4. La presencia exclusiva de trabajo `scheduled` normal no bloquea recovery.
5. Si un worker normal empieza a tener trabajo listo, recovery termina su página actual y cede el siguiente ciclo.
6. Todo ARK se recarga y adquiere su advisory lock individual antes de cambiar de etapa; la decisión global nunca reemplaza esa exclusión.

Valores iniciales que deben validarse bajo carga, no tratarse como política definitiva:

```env
La configuración anterior de rechecks fue retirada. Consulte la configuración
vigente en `docs/deployer-operations.md`.
RECOVERY_WORKER_PAGE_SIZE=10
RECOVERY_WORKER_SLEEP_SECONDS=30
```

## Tareas de implementación

### Prioridad 0 — diferenciar espera normal de error IPFS

- Ampliar el contrato interno de Store API para que minter reciba estado de pin además del total de réplicas.
- Definir explícitamente qué estados de Cluster son transitorios y durante cuánto tiempo se concede la ventana de gracia.
- No volver a ejecutar `store` ante `pinning` ni ante cero réplicas dentro de esa ventana.
- Permitir reparación solo cuando exista evidencia de `error`/`unpinned`, o cuando venza una política de observación que lo justifique.
- Hacer que toda transición a `RECOVERABLE` escriba siempre `error_code`, `error_detail`, fecha y próximo intento. La ausencia de código debe aparecer como dato histórico inválido, no como un error silencioso.

### Prioridad 1 — desbloquear recovery sin quitar prioridad a los workers normales

- Cambiar la condición de inactividad global para contar solo trabajo normal `ready_now` y actividad de workers, no todos los registros `PENDING`.
- Añadir al runtime de recovery un motivo estructurado de bloqueo: `normal_ready_work`, `worker_unhealthy`, `rate_limited` o `none`.
- Procesar una página reducida de recovery y volver a evaluar prioridades antes de la siguiente.
- Mantener el lock global de proceso y el advisory lock por ARK.
- Probar que 31 rechecks futuros de replication no impiden rescatar un candidato vencido.

### Prioridad 2 — saneamiento explícito de los registros históricos

- Crear un comando administrativo de diagnóstico con `--dry-run` que clasifique los `RECOVERABLE` sin código por etapa, CIDs y payload retenido.
- Reencolar de forma explícita los casos `AVAILABILITY` que tengan ambos CIDs y evidencia de que solo esperaban pinning.
- Mantener aparte los casos sin CID o sin payload: deben acabar como `FAILED` con `RECOVERY_NOT_POSSIBLE` si no existe evidencia local para reconstruirlos.
- Registrar el resultado y las cantidades antes de ejecutar cambios. No realizar una conversión masiva automática sin ese informe.

El comando disponible para esta revisión es:

```bash
docker compose exec minter-api \
  python -m app.cli.reclassify_recoverable --dry-run
```

En el entorno revisado el informe identificó `11134` ARKs de availability que
pueden volver a la cola normal por tener ambos CIDs. El comando no los cambió.
La aplicación de esa reclasificación debe ejecutarse como una operación
administrativa separada, después de revisar el volumen y la capacidad de
replication.

### Prioridad 3 — hacer comprensible la API y el dashboard

- Reemplazar en el diagnóstico de workers las filas visuales `Pending`, `Ready` y `Delayed` por las categorías de este documento.
- Separar la etapa `availability` de `replication` cuando se muestren sus conteos.
- Mostrar en recovery: candidatos elegibles, candidatos programados, cantidad bloqueada y razón concreta del bloqueo.
- Diferenciar visualmente espera normal de pin (neutro/azul), backlog ejecutable (amarillo), error recuperable con código (naranja) y fallo permanente (rojo).
- No convertir un backlog en estado global degradado solo por volumen. Debe degradar cuando haya trabajo listo sin avance, heartbeat vencido, dependencia caída o bloqueo con causa anormal.
- Mantener la página de errores para errores estructurados. Los ARKs esperando pin no deben aparecer allí.

### Prioridad 4 — pruebas y documentación operativa

- Cubrir `pinned`, `pinning`, `unpinned`, `error` y respuesta temporalmente vacía de Cluster.
- Probar que la primera aceptación de Store API devuelve CID y deja el ARK programado, no recuperable.
- Probar prioridad: trabajo normal listo bloquea recovery; trabajo normal solo programado no lo bloquea.
- Probar que ninguna vista calcula colas con scans no acotados.
- Añadir un procedimiento de diagnóstico: health de Store/Cluster, estado de pin por CID, `detail=full`, resumen de errores y comando de clasificación en seco.

## Criterios de aceptación

- Un lote grande con Cluster sano puede acumular ARKs `awaiting_pin` sin poblar la lista de errores.
- Al terminar la ventana de pinning, los ARKs continúan a availability, replication o chain sin una reescritura innecesaria del payload.
- Si Store/Cluster realmente falla, cada ARK recuperable tiene código y detalle suficientes para diagnosticarlo.
- Recovery avanza con capacidad ociosa aunque existan rechecks futuros de workers normales.
- El dashboard permite contestar, sin interpretar columnas ambiguas: qué está listo, qué está programado, qué espera IPFS, qué está bloqueado y por qué.
- Los estados públicos y la semántica de publicación no cambian por esta mejora interna.

## No objetivos

- No se modifica la política funcional de réplica mínima ni la definición pública de `published` en este documento.
- No se autoriza una recuperación administrativa automática de fallos permanentes.
- No se elimina la exclusión PostgreSQL por ARK: la simplificación de colas no debe permitir procesamiento concurrente del mismo registro.
