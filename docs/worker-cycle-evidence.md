# Evidencia del ciclo Metadata → Replication → Chain

Documento de trabajo basado en el código actualmente desplegado en `main`.
Su objetivo es permitir que otro agente evalúe si el flujo es demasiado lento
o complejo y proponga cambios con evidencia. La sección final conserva las
alternativas históricas; la optimización agrupada de la opción A está
implementada y se registra al final.

## Resumen ejecutivo

El flujo no es una cola única. Cada ARK tiene dos estados diferentes:

1. **Estado público:** `R` reservado, `D` draft, `U` update, `P` published o
   `T` tombstone.
2. **Etapa/estado interno:** etapa (`METADATA`, `AVAILABILITY`, `CHAIN`,
   `REPLICATION`, `COMPLETE`) y estado (`READY`, `WAITING`, `FAILED`, `DONE`,
   `CANCELLED`).

Un ARK solo llega a blockchain cuando se cumplen simultáneamente estas
condiciones:

- el estado público es `DRAFT` o `UPDATE`;
- existen los dos CIDs locales, Level 1 y Level 2;
- la etapa interna es `CHAIN`;
- el estado interno es `READY` o `WAITING` ya vencido;
- el chain worker puede adquirir el lock advisory individual;
- la capacidad RPC/chain permite ejecutar la publicación.

La condición de disponibilidad no es “la API Store respondió al `add`”. El
Store API devuelve un CID tan pronto Cluster acepta el contenido localmente,
pero replication vuelve a consultar el pin global y exige que **ambos CIDs**
tengan al menos `publish_after_replicas` pines en estado `pinned`.

En la configuración vigente, las cadencias nominales son:

| Worker | Página | Concurrencia | Espera general | Revisión del registro |
| --- | ---: | ---: | ---: | ---: |
| Metadata | 100 | 4 | 2 s | backoff de error, normalmente 1 min para un retry catalogado |
| Replication | 100 | 2 | 2 s para detectar entradas | primer pin 15 s→1 min→5 min; durabilidad 5 min→15 min→1 h |
| Chain | 100, adaptativa | controlada por capacidad | 5 s | ventanas RPC de 50; backoff RPC/errores |

La espera de replication es intencionalmente más larga después de que Cluster
acepta una asignación. Un registro se revisa a los 15 segundos, un minuto y
cinco minutos durante availability; después de Chain se revisa a los cinco
minutos, quince minutos y cada hora. El worker puede mostrar `RUNNING` o `SLEEPING` sin que
eso signifique error: el estado del proceso y el estado de la cola son campos
independientes.

## Entrada del flujo: API y persistencia inicial

La operación `PUT /api/v1/arks/{ark}` está implementada en
`components/services/dark-core-minter-api/app/api/arks.py`.

La API realiza estas operaciones dentro de la transacción de la solicitud:

1. Valida ARK, autoridad y estado público.
2. Rechaza sobreescrituras si el ARK ya está en `DRAFT` o `UPDATE` pendiente.
3. Valida el objeto Level 1.
4. Construye la referencia `original_metadata` con CID todavía nulo.
5. Guarda en PostgreSQL `level1_json`, `original_content`, `original_schema` y
   `original_media_type`.
6. Limpia los CIDs anteriores y los contadores de réplica cuando se trata de
   una actualización.
7. Cambia el estado interno a `METADATA/READY` y deja `next_action_at` nulo.
8. Hace `commit` y responde sin escribir todavía en IPFS ni blockchain.

La API, por tanto, no espera a que Kubo o Cluster confirme nada. La respuesta
con `level1_cid` y `level2_cid` puede contener `null` en este punto; los CIDs
son responsabilidad del primer worker.

La transición pública es independiente:

- `RESERVED → DRAFT` para creación.
- `PUBLISHED → UPDATE` para una actualización.

El código se encuentra en `ARKRepository.create_or_update_metadata`,
`update_to_draft` y `update_to_update`.

## Metadata worker

### Selección

`MetadataPersistenceWorker.run_publish_cycle()` pide al repositorio hasta
`metadata_worker_page_size` ARKs que cumplan:

- estado público `DRAFT` o `UPDATE`;
- etapa `METADATA`;
- falta al menos uno de los CIDs;
- estado interno `READY`, o `WAITING` con `next_action_at <= now`.

El repositorio ordena por autoridad y `created_at`. La selección no mantiene
un mensaje de cola externo: vuelve a consultar PostgreSQL en cada ciclo.

### Procesamiento de un ARK

Cada elemento se procesa con un advisory lock PostgreSQL por ARK.

1. Recarga y valida el registro bajo lock.
2. Si falta Level 2, hace `Store API POST /v1/store` con el contenido original.
3. Si falta Level 1, hace otro `POST /v1/store` con el JSON que referencia el
   CID Level 2.
4. Reabre la transacción, recarga el registro y compara que siga siendo el
   mismo ARK/metadata.
5. Persiste ambos CIDs y limpia contadores/errores anteriores.
6. Cambia la etapa a `AVAILABILITY/READY`.
7. Hace `commit`.

El lock no se mantiene durante la llamada HTTP a Store API: se hace `commit`
antes de I/O y se vuelve a adquirir lock al persistir. Esto reduce el tiempo de
bloqueo, pero implica que la segunda lectura/comparación es la protección
contra una actualización concurrente.

### Fallos

- Un fallo de infraestructura se almacena como `WAITING` con error de
  almacenamiento y se reintenta después del retraso configurado.
- Un resultado inválido o metadata insuficiente se marca como `FAILED` según
  el catálogo de errores.
- El worker no consulta todavía el estado global de réplicas; solo confirma que
  Store API aceptó y devolvió los dos CIDs.

## Store API y significado del CID

El backend Cluster de Store API está en
`components/services/dark-store-api/app/backends/ipfs_cluster.py`.

`POST /v1/store`:

1. Selecciona un endpoint Cluster local del pool.
2. Envía `POST /add?pin=true&local=true&stream-channels=false`.
3. Acepta variantes de respuesta `Hash`, `hash`, `cid`, `Cid` o `CID`.
4. Devuelve el CID inmediatamente después de un HTTP exitoso con CID válido.
5. Registra que el pin es asíncrono; no espera que los peers remotos hayan
   terminado.

Por ello, **tener un CID no equivale a tener una réplica confirmada**. El CID
prueba que Cluster aceptó la operación de add; la visibilidad y distribución
se comprueban después.

El endpoint adicional `POST /v1/status/batch` recibe una lista acotada de CIDs,
consulta cada pin con concurrencia máxima 10 y devuelve, por CID:

- `pinned`: número de peers cuyo estado es `pinned`;
- `pinning`: peers asignados que todavía están fijando;
- `queued`: peers asignados en cola;
- `error`: peers con error;
- `unpinned`: ningún pin encontrado;
- `unknown`: respuesta sin estados interpretables.

El contador `total_replicas` solo cuenta peers `pinned`. `assigned_replicas`
cuenta también queued/pinning/error, por lo que no debe usarse para publicar.

## Replication worker

### Selección

`ReplicationReconciliationWorker.run_publish_cycle()` pide hasta 50 registros
que cumplan:

- estado público `DRAFT`, `UPDATE` o `PUBLISHED`;
- etapa `AVAILABILITY` o `REPLICATION`;
- Level 1 y Level 2 tienen CIDs;
- estado `READY`, o `WAITING` con acción vencida.

El parámetro `recheck_seconds` pasado al repositorio no filtra directamente la
consulta: el tiempo real de espera está en `ARKRecord.next_action_at`, que se
calcula y persiste al terminar cada observación. `replication_checked_at` se
usa para ordenar por la observación más antigua, no para impedir por sí solo
una selección.

### Procesamiento y reparación

Para cada ARK:

1. Adquiere advisory lock.
2. Comprueba estado público, etapa y si el registro está listo.
3. Lee ambos CIDs desde `ARKMetadata`.
4. Libera la transacción antes de consultar Store API.
5. Ejecuta un `status/batch` para los dos CIDs.
6. Si un CID no tiene pines y existe evidencia de una ausencia real, intenta
   volver a subir el payload local.
7. Reacquire lock y comprueba que ambos CIDs sigan siendo los mismos.
8. Persiste contadores, `replication_checked_at` y número de observación.

La reparación es posible únicamente si todavía existe `level1_json` o
`original_content` local. Si el payload ya fue purgado o no tiene media type,
la reconciliación falla y se difiere; no inventa un CID ni acepta una respuesta
con CID diferente.

### Transiciones

#### Etapa AVAILABILITY

Se calcula:

```text
replicas = min(level1_pinned, level2_pinned)
```

Si `replicas >= publish_after_replicas`:

```text
AVAILABILITY/READY → CHAIN/READY
```

Se limpia `next_action_at` y la razón de espera. Este es el único momento en
que replication habilita al chain worker.

Si no se cumple:

```text
AVAILABILITY/READY → AVAILABILITY/WAITING
```

La razón es:

- `CLUSTER_QUEUED` si algún CID sigue queued;
- `CLUSTER_PINNING` si está pinning;
- `INITIAL_VISIBILITY` en otros casos sin réplica confirmada.

La siguiente observación la decide el scheduler global; no hay una secuencia
de backoff configurable por ARK.

#### Etapa REPLICATION

Después de que el ARK ya fue publicado en cadena, `update_to_published()` lo
deja en:

```text
PUBLISHED/REPLICATION/READY
```

Si ambos CIDs llegan a `target_replicas`:

```text
REPLICATION → COMPLETE/DONE
```

Además se limpian `level1_json` y `original_content`, y se guarda
`payload_purged_at`. Si todavía no alcanza el objetivo:

```text
REPLICATION/READY → REPLICATION/WAITING
```

La razón es `REPLICA_TARGET`. El objetivo de durabilidad posterior a la
publicación no bloquea la primera publicación de un ARK, pero mantiene el
registro en replication hasta alcanzar el objetivo y poder purgar el payload
local.

### Qué significa un ciclo `waiting`

`waiting` no es un fallo permanente. Es el resultado contabilizado cuando el
worker observó el ARK, no pudo avanzar la etapa, persistió el motivo y
programó `next_action_at`.

Un ciclo puede terminar con:

```text
checked/observed > 0
advanced = 0
waiting = checked
failed = 0
```

Esto significa “se verificaron registros y todos deben volver a consultarse
después”, no “el worker está detenido”. El motivo concreto se encuentra en
`processing_wait_reason` y la hora exacta en `next_action_at`.

## Chain worker

### Selección

`ChainPublisherWorker.run_publish_cycle()` selecciona hasta 20 ARKs que cumplan:

- estado público `DRAFT` o `UPDATE`;
- etapa `CHAIN`;
- ambos CIDs presentes;
- estado listo o espera vencida.

No selecciona `AVAILABILITY`, `REPLICATION`, `WAITING` no vencidos ni
`PUBLISHED` normal. Chain tampoco consulta Store API; confía en que replication
ya persistió la observación que permitió la transición a `CHAIN`.

### Capacidad y cadencia

Antes de seleccionar registros, chain consulta la capacidad de la red por
core-lib. Si RPC no está disponible, marca `PAUSED_RPC_UNAVAILABLE`; si la
capacidad está estancada o congestionada, usa estados de pausa y espera de 30
segundos. Cuando está saludable adapta el tamaño de página dentro de los
límites configurados y solo aumenta después de varios ciclos saludables.

Si no hay ARKs en `CHAIN/READY`, el ciclo termina normalmente y duerme 5
segundos. La publicación es agrupada por autoridad y puede usar un pipeline de
operaciones. Cada resultado se reconcilia bajo lock y se compara el CID Level 1
antes de marcar el ARK `PUBLISHED`.

### Resultado de publicación

Una confirmación válida ejecuta:

```text
DRAFT/CHAIN → PUBLISHED/REPLICATION/READY
UPDATE/CHAIN → PUBLISHED/REPLICATION/READY
```

Un error RPC operativo queda `WAITING` con retry. Autoridad inexistente,
autorización fallida, revert o conflicto de cadena pueden ser `FAILED` según el
catálogo de códigos. Un resultado ambiguo se reconcilia primero leyendo la
cadena para evitar duplicar una transacción que sí fue aceptada.

## Locks y concurrencia

Hay dos niveles distintos:

1. Cada proceso adquiere un advisory lock global por nombre de worker, evitando
   dos instancias de metadata, replication o chain con el mismo runtime.
2. Cada ARK se procesa bajo un advisory lock individual. Metadata y replication
   vuelven a cargar el registro bajo lock después de I/O; chain vuelve a
   comprobar etapa, estado y CID antes de actualizar.

El lock individual se libera al salir del contexto de la operación, incluso si
el worker termina antes de su espera. No es un lease persistente. El dato
persistente que queda en la tabla es `next_action_at`, junto con estado, etapa,
razón, contadores y error.

## Cadencia efectiva de un ARK

Para un ARK nuevo, sin fallos y con un Cluster que tarda en hacer visible el
pin, la secuencia es:

```text
t0      API guarda metadata y deja METADATA/READY
t0+     metadata almacena Level 2 y Level 1 en Store API
t0+     metadata deja AVAILABILITY/READY
t0+2s   siguiente ciclo metadata; ya no selecciona ese ARK
t1      replication observa ambos CIDs
t1+10s  si no hay dos pines confirmados, vuelve a observar (backoff)
t1+...  cuando min(L1 pinned, L2 pinned) >= publish_after: CHAIN/READY
t2      chain lo selecciona en su próximo ciclo, normalmente <= 5s
t2+     publica y deja PUBLISHED/REPLICATION/READY
t2+5s   replication retoma la verificación de objetivo de réplicas
t2+300s o más  vuelve a observar para target_replicas
t3      al alcanzar el target: COMPLETE/DONE y purga payload local
```

El tiempo de `t0` a `t1` depende del fin del ciclo metadata y de la espera de
Store API. El tiempo de `t1` a `CHAIN` depende de la propagación Cluster y de
los backoffs de availability. El tiempo de `PUBLISHED` a `DONE` puede ser
deliberadamente mucho mayor porque usa el intervalo de objetivo de réplicas,
no el de publicación.

## Evidencia runtime observada

En el entorno recreado el 8 de septiembre de 2026:

- metadata, replication, chain y API minter estaban `Up` durante unas 8 horas;
- los tres workers tenían `restart=0`;
- Store API estaba `healthy`;
- los dos peers Cluster estaban `healthy`;
- chain estaba conectado a RPC y ejecutaba ciclos;
- el endpoint de status del minter devolvía heartbeats recientes y
  `last_error: null`.

Una muestra real del status fue:

```json
{
  "metadata": {
    "process_state": "SLEEPING",
    "last_cycle": {"observed": 0, "advanced": 0, "waiting": 0, "failed": 0}
  },
  "replication": {
    "process_state": "RUNNING",
    "last_cycle": {"observed": 50, "advanced": 0, "waiting": 50, "failed": 0}
  },
  "chain": {
    "process_state": "SLEEPING",
    "last_cycle": {"observed": 0, "advanced": 0, "waiting": 0, "failed": 0}
  }
}
```

La evidencia significa que replication sí estaba trabajando: observó una
página completa y Store API devolvió respuestas `200` para sus consultas de
estado. Los 50 registros no cumplieron la condición de avance en esa
observación. Chain no recibió trabajo porque ninguno de esos registros había
sido cambiado a `CHAIN/READY`.

Esta evidencia no permite por sí sola distinguir si el retraso estaba causado
por `queued`, `pinning`, `unpinned`, un objetivo de réplica superior o
`next_action_at` futuro. Para evaluar un ARK concreto hay que consultar en
PostgreSQL y Store API, como mínimo:

```text
ark_records.processing_stage
ark_records.processing_status
ark_records.processing_wait_reason
ark_records.next_action_at
ark_metadata.level1_cid
ark_metadata.original_cid
ark_metadata.level1_replica_count
ark_metadata.level2_replica_count
ark_metadata.replication_checked_at
ark_metadata.replication_observation_count
```

## Puntos de complejidad a evaluar

1. **Dos colas conceptuales en replication.** Availability y replication usan
   el mismo worker pero intervalos muy distintos.
2. **Estados duplicados.** El dashboard debe combinar etapa, estado, motivo,
   `next_action_at`, heartbeat y último ciclo para explicar una sola espera.
3. **Backoff persistente por ARK.** Un lote de 50 puede tener 50 fechas de
   próxima acción distintas; una página completa no significa que todos estén
   listos otra vez.
4. **Consulta global por dos CIDs.** Cada ARK implica un status batch que en
   realidad consulta dos CIDs, y el Store API puede consultar varios endpoints
   Cluster hasta encontrar uno saludable.
5. **Reparación dentro de observación.** Un status sin pines puede disparar una
   nueva subida y después una segunda observación, aumentando latencia y carga.
6. **Frontera de publicación.** Chain no valida Store API por sí mismo; toda la
   confianza se deposita en la transición persistida por replication.
7. **Diferencia entre vivo y productivo.** Heartbeat reciente prueba que el
   proceso corre; no prueba que haya avanzado ARKs.

## Datos que debe reunir una evaluación posterior

Para una muestra de ARKs, registrar una fila por observación con:

- timestamps de entrada/salida de metadata, replication y chain;
- etapa/estado/motivo antes y después;
- `next_action_at` antes y después;
- ambos CIDs y conteos `pinned`, `queued`, `pinning`, `error`;
- endpoint Cluster consultado y latencia;
- si hubo reparación, CID retornado y duración;
- momento de transición a `CHAIN`;
- momento de envío, confirmación y transición a `PUBLISHED`;
- momento de alcanzar target y purgar payload.

Sin esta correlación no se puede afirmar que “IPFS está lento”: solo se puede
afirmar que el worker observó un estado que no habilitaba chain.

## Archivos de referencia

- API y entrada del flujo: `components/services/dark-core-minter-api/app/api/arks.py`.
- Selección y transiciones DB: `components/services/dark-core-minter-api/app/repositories/ark_repository.py`.
- Implementación de los tres workers: `components/services/dark-core-minter-api/app/workers/publisher.py`.
- Ciclo, heartbeat, locks y sleeps: `components/services/dark-core-minter-api/app/main_worker.py`.
- Códigos internos: `components/services/dark-core-minter-api/app/models/processing.py`.
- Modelo persistente: `components/services/dark-core-minter-api/app/database/models.py`.
- Store API y status batch: `components/services/dark-store-api/app/api/store.py`.
- Conteo de estados Cluster: `components/services/dark-store-api/app/backends/ipfs_cluster.py`.
- Cliente de metadata/Store API: `components/libraries/dark-core-lib/dark_core_lib/metadata/storage/store_api.py`.

## Alternativas para aumentar throughput — propuesta documental

Esta sección se elaboró exclusivamente a partir del documento anterior, sin
volver a inspeccionar código ni contenedores. Describe cambios posibles para
que otro agente los evalúe. Los valores sugeridos son puntos de partida para
un experimento, no resultados medidos ni promesas de rendimiento.

### Qué se intenta mejorar

Hay que medir dos resultados distintos: ARKs publicados por segundo y tiempo
desde que se entrega metadata hasta `PUBLISHED`. Reducir una espera mejora la
latencia de detección, pero no necesariamente la capacidad de IPFS para fijar
contenido. Aumentar las comprobaciones puede incluso competir con los adds y
los pins por los mismos recursos.

La muestra `observed=50, advanced=0, waiting=50` no prueba por sí sola que
Cluster esté lento. Tampoco distingue availability de durabilidad posterior a
publicación. La primera decisión del evaluador debe ser separar esas dos
poblaciones y medir cuándo aparece el primer pin y cuándo se observa.

Hay límites en la evidencia anterior que deben resolverse antes de implementar:

- Un `commit` no demuestra que se libere un advisory lock de sesión. La
  afirmación previa sobre liberación del lock durante HTTP requiere verificar
  su tipo y duración. No diseñar concurrencia a partir de esa afirmación.
- La cronología `t2+5s` para retomar replication no está justificada por su
  intervalo nominal de 30 s. Tampoco 5 s es un máximo de acceso a chain bajo
  carga: hay páginas, I/O y pausas adicionales.
- Las secuencias de backoff necesitan confirmar la fórmula y si el contador se
  reinicia al cambiar de etapa. El documento no permite garantizar que la
  primera espera de durabilidad comience siempre en 300 s.
- La regla de reparación abreviada no permite decidir que todo CID sin pines
  debe reingresarse. Hay que distinguir contenido queued/pinning, ausencia
  real y fallo de consulta.

### Opción A: optimizar los tres workers existentes (recomendada primero)

Conservar el flujo de aceptación, comprobación de disponibilidad, publicación
y durabilidad. Cambiar cómo se selecciona, consulta y espera el trabajo.

**1. Consultar CIDs por página, no por ARK.** Para una página acotada de ARKs,
reunir hasta 100 CIDs, eliminar duplicados y consultar en lotes acotados. El
documento describe 50 peticiones HTTP con dos CIDs; una petición por página
reduciría ese número a una, si el límite del endpoint lo permite. Se reduce el
overhead entre minter y Store API, no necesariamente las 100 consultas internas
de Store API a Cluster.

La concurrencia hacia Cluster debe tener un límite global por proceso Store
API; un límite independiente en cada petición batch podría multiplicar la
carga. Como arranque experimental, mantener diez observaciones simultáneas en
total y un solo lote del reconciliador en vuelo. No subir simultáneamente
página, concurrencia de metadata y concurrencia de observación.

Un resultado lento no debería retener toda una página indefinidamente: usar
sub-lotes pequeños si aparece ese problema, timeout acotado y errores por CID.
Si falla la consulta de un CID, conservar resultados válidos de los demás.
No interpretar timeout como cero réplicas. Aplicar cada resultado bajo la
protección individual del ARK y comprobando que etapa y CIDs sigan vigentes.

**2. Priorizar availability y relegar durabilidad a mantenimiento ocioso.** La
implementación vigente no reparte una página entre cuotas: consume primero el
trabajo que bloquea el primer pin y solo promueve durabilidad cuando el sistema
no tiene backlog de metadata ni de availability.

**3. Dormir según trabajo vencido y próximo vencimiento.** Después de una
página, si quedan ARKs vencidos, continuar. Si no, esperar hasta el menor de
un intervalo de sondeo y la próxima acción programada. El intervalo de dos
segundos es un sondeo global, no un temporizador por CID.

Las filas bloqueadas o inválidas no deben generar un bucle activo. Si una
página no puede procesarse, aplicar una pausa corta y registrar la causa.
Las esperas siguen siendo interrumpibles y mantienen heartbeat. La nueva
selección debe ser SQL acotado, no recorrer todos los ARKs para decidir si dormir.

**4. Distinguir espera normal de fallo de infraestructura.** Probar un intervalo
simple de 10–15 s para availability queued/pinning mientras no haya saturación;
reservar el backoff creciente para errores de conexión o sobrecarga. Para
durabilidad conservar un intervalo lento, por ejemplo 300 s. Reiniciar la
cadencia al cambiar de etapa para no heredar intentos de availability.

Un intervalo corto fijo con muchos pendientes tiene coste: aproximadamente
`2 × ARKs pendientes / intervalo` observaciones de CID por segundo, antes de
deduplicación y fallos. Con 10.000 ARKs y 10 s serían unas 2.000 observaciones/s.
Por eso esta opción exige presupuesto de consultas y medición de saturación;
si se supera, espaciar observaciones y reportar el retraso real. No prometer
una revisión cada 10 s si la capacidad disponible no puede cumplirla.

**5. Apartar la reparación lenta del camino de observación.** Conservarla en
el mismo proceso, con una cuota pequeña de trabajo por ciclo y cooldown. No
volver a subir CIDs queued/pinning como respuesta a una espera normal. Cuando
una reparación sea necesaria, procesar primero observaciones que pueden
habilitar chain, conservar payload y exigir que el CID reparado coincida.
Un fallo de conectividad no debe convertirse en una reparación de contenido.

**6. Controlar la entrada si Cluster está saturado.** Si suben continuamente
los CIDs queued y la edad de availability, aumentar metadata de cuatro a ocho
hilos podría empeorar la situación. Evaluar entonces una reducción temporal
de adds, usando señales agregadas ya observadas por replication, con umbrales
de entrada/salida distintos para evitar oscilaciones. Mantener los ARKs
aceptados en PostgreSQL, sin transformarlos en errores por control de carga.
Es una opción condicionada a saturación medida, no un subsistema obligatorio.

El beneficio esperado de A es reducir viajes HTTP y demora de detección,
alimentar antes chain y preservar recursos para pinning. Si el primer pin
tarda realmente minutos, A por sí sola no elimina ese tiempo físico.

### Opción B: confirmación acotada de disponibilidad durante almacenamiento

Evaluar que Store API pueda devolver, junto al CID, evidencia verificable de
pins ya confirmados. Metadata utilizaría esa evidencia para dejar directamente
`CHAIN/READY` solo cuando ambos niveles alcancen el umbral de publicación.
Cuando la evidencia no esté disponible dentro de un presupuesto corto,
conservar el camino `AVAILABILITY` y devolver el CID normalmente.

Esta vía podría ahorrar el primer recorrido del reconciliador cuando el pin
local ya existe. No basta con el éxito de `/add` ni con un contador assigned.
Con umbral mayor que uno, un único pin local sigue siendo insuficiente.

El coste es que una espera añadida a cada add ocupa capacidad de metadata y
puede reducir su throughput: si las escrituras suman `t` segundos por ARK y
se añaden `w` segundos de espera, cuatro hilos pasan de una capacidad teórica
`4/t` a `4/(t+w)` ARKs/s. Evaluar primero si la respuesta puede aportar evidencia
sin espera adicional. No convertir Store API en otro reconciliador continuo.

### Opción C: publicar al recibir los CIDs

Metadata dejaría directamente `CHAIN/READY` después de persistir L1 y L2;
replication comprobaría disponibilidad y durabilidad después de publicar.
Es el cambio que elimina completamente la espera IPFS del acceso a chain.

También cambia la garantía de `PUBLISHED`: habría ARKs publicados cuyos CIDs
todavía no pueden recuperarse desde IPFS. Si Cluster pierde contenido antes
de fijarlo, la reparación dependería del payload local. Aumentaría los ARKs
publicados por segundo sin necesariamente aumentar los ARKs resolubles por
segundo. Conservar payload hasta alcanzar durabilidad y hacer visible la
disponibilidad por separado sería imprescindible.

No es la primera recomendación porque contradice la condición documentada de
publicación con pins reales. Solo tendría sentido tras aceptar explícitamente
esa nueva semántica; no debe introducirse como una optimización transparente.

### Alternativas que conviene dejar para una segunda evaluación

Separar availability y durabilidad en procesos distintos puede proporcionar
capacidad independiente, pero añade operación y no corrige la capacidad del
Cluster. Evaluarlo solo si las cuotas de un único reconciliador no bastan.
Las notificaciones PostgreSQL podrían despertar chain o replication tras una
transición; deben ser una señal auxiliar con polling de respaldo, pues la
tabla sigue siendo la fuente durable. No hace falta añadir un broker para
probar primero el sondeo acotado de la opción A.

### Cómo decidir con una prueba comparativa

El siguiente agente debería fijar infraestructura, payloads, volumen y tasa
de entrada; medir una ejecución base y cambiar un factor cada vez. Usar una
carga breve y otra sostenida que permita observar acumulación de pendientes.

| Medida | Qué permite distinguir |
| --- | --- |
| ARKs publicados y resolubles por segundo | Rendimiento útil frente a mero cambio de estado público. |
| p50/p95 de aceptación → CIDs → primer pin → CHAIN → PUBLISHED | Tiempo de escritura, de pinning, de detección y de cadena. |
| Edad máxima y tamaño de availability/durabilidad | Acumulación y posible inanición de una población. |
| HTTP batches/s y consultas Cluster/s | Ahorro real frente a traslado de carga. |
| CPU, memoria, I/O y latencia de adds/status | Saturación provocada por mayor concurrencia. |
| Reparaciones y bytes de payload retenido | Coste de reparación y retraso de purga. |

El instante real del primer pin no está demostrado por la muestra actual.
Medirlo con una muestra acotada o instrumentación disponible: aumentar
masivamente las consultas para medirlo alteraría el experimento.

Aceptar una variante solo si mejora throughput sostenido o latencia de
publicación sin crecimiento indefinido de durabilidad, aumento sostenido de
errores o degradación de resolución. Mantener umbrales de publicación/purga,
validación de CIDs y reintento de resultados ambiguos de cadena. No escalar
chain mientras permanece sin trabajo por falta de disponibilidad.

### Recomendación para el agente evaluador

Empezar por A: batches entre ARKs, cuota prioritaria de availability y sueño
según próximos vencimientos. Medir antes de cambiar el recheck y la concurrencia.
Después evaluar B si el primer pin es rápido pero su observación sigue llegando
tarde. Si el pin real es lento, investigar el cuello de botella de Cluster y
regular adds; acortar esperas del minter no crea réplicas más rápido.

La opción C queda como una decisión de producto sobre la garantía de publicación.
Todas estas propuestas son documentales y requieren validación posterior del
código, de las protecciones de concurrencia y de la capacidad real del entorno.

## Implementación de la opción A

Aplicada en septiembre de 2026 sin cambiar la garantía de publicación ni la
política de purga.

### Reconciliación agrupada

`ReplicationReconciliationWorker` ahora selecciona una página de hasta 100
registros y prioriza `AVAILABILITY`; solo usa capacidad restante para
`REPLICATION` cuando no hay más trabajo crítico.

Los ARKs se ordenan por `next_action_at`, antigüedad de observación, autoridad
y creación. La selección ya no reemplaza ese orden al aplicar el límite SQL.
Después de seleccionar la página, el worker:

1. Carga los dos CIDs de todos los ARKs.
2. Elimina duplicados.
3. Consulta los estados en lotes de hasta 200 CIDs.
4. Reutiliza el resultado para todos los ARKs que comparten CID.
5. Reacquire el lock individual de cada ARK antes de persistir y vuelve a
   comprobar que los CIDs no cambiaron.

Las reparaciones ya no ejecutan una consulta singular inmediata después del
`store()`. El resultado se confirma en la siguiente observación agrupada.
Una falla de un lote queda limitada a los ARKs afectados; otros lotes de la
misma página pueden continuar. `queued`, `pinning` e `initial_visibility`
siguen siendo esperas normales y no se convierten en errores de Recovery.

### Planificación y diagnóstico

Después de cada ciclo, el worker consulta si queda trabajo vencido. Si existe,
continúa inmediatamente. Si no existe, calcula el próximo
`next_action_at` y duerme hasta ese vencimiento, limitado por
`REPLICATION_MAX_IDLE_SLEEP_SECONDS` (5 segundos por defecto para detectar
nuevos ingresos sin ciclos vacíos prolongados).

El heartbeat conserva la información de proceso y el diagnóstico full expone:

- `ready_now`: ARKs listos para ser procesados ahora;
- `waiting`: ARKs programados para una revisión posterior;
- `next_action_at`: próxima revisión conocida;
- `waiting_reasons`: agrupación por causa;
- `by_stage`: desglose separado de availability y replication;
- configuración efectiva de página, batch, umbral de publicación y objetivo de
  durabilidad.

El estado liviano continúa limitado a heartbeats y no consulta Store API,
IPFS, RPC ni conteos de ARKs.

### Configuración registrada

Se añaden y propagan desde el instalador:

```env
REPLICATION_STATUS_BATCH_SIZE=200
REPLICATION_IDLE_SLEEP_SECONDS=2
```

El minter valida que el batch no supere el máximo de Store API y que la página
y el intervalo de sondeo sean positivos.

### Verificación realizada

- Suite del minter: **174 passed, 13 skipped**.
- Validación sintáctica de los módulos Python modificados: correcta.
- `git diff --check`: sin errores.
- Pendiente: prueba Docker integrada con Store API/Cluster reales y medición
  comparativa de throughput bajo carga sostenida.

## Propuesta histórica para reemplazar el scheduler de replication

Esta sección conserva el razonamiento original. La propuesta fue implementada
posteriormente y su estado vigente aparece al final de este documento. Reemplaza las esperas individuales por
`next_action_at` para estados normales de Cluster (`queued`, `pinning` e
`initial_visibility`) por una exploración continua, justa y acotada. No cambia
la garantía de publicación: Chain seguirá recibiendo un ARK solo cuando Level 1
y Level 2 tengan el mínimo de pines reales configurado.

### Objetivo

Reducir el tiempo entre el primer pin real y la promoción a Chain, eliminar la
complejidad de backoffs por ARK y dar prioridad absoluta a los ARKs que todavía
no tienen su primer pin confirmado.

```text
METADATA
  → AVAILABILITY: falta primer pin de L1 o L2
  → CHAIN: ambos CIDs cumplen publish_after_replicas
  → REPLICATION: publicado, faltan réplicas para target_replicas
  → COMPLETE: objetivo cumplido y payload local purgado
```

### Scheduler propuesto

El `ReplicationReconciliationWorker` ejecutará ciclos continuos de 100 ARKs.
Cada ciclo podrá consultar hasta 200 CIDs, que coincide con el límite de
`POST /v1/status/batch`.

1. Seleccionar hasta 100 ARKs de `AVAILABILITY` con ambos CIDs y sin primer
   pin confirmado.
2. Ordenar por `replication_checked_at ASC NULLS FIRST`, luego `created_at` e
   identificador. Es decir: primero los nunca observados, luego los que llevan
   más tiempo sin observación. **No** ordenar solo por creación, porque los
   ARKs más antiguos en `queued` monopolizarían el ciclo.
3. Consultar L1 y L2 una única vez por CID, agrupados en un batch.
4. Para cada ARK:
   - si ambos CIDs alcanzan `publish_after_replicas`, promover inmediatamente
     a `CHAIN/READY`;
   - si alguno está `queued`, `pinning` o sin pin, conservarlo en
     `AVAILABILITY`; registrar la observación, pero no crear un retry/error;
   - si hay un error real de Store API/Cluster, aplicar el backoff técnico de
     infraestructura correspondiente.
5. Si hay menos de 100 ARKs en availability, completar la página con ARKs de
   `REPLICATION`, aplicando el mismo orden por última observación.
6. Si availability está vacía, la página completa se usa para durability.
7. Si no existe trabajo en ninguna etapa, el proceso duerme solamente un
   intervalo global corto configurable antes de volver a consultar PostgreSQL.

Por tanto, no habrá timers ni `WAITING` por ARK como respuesta normal a
`queued` o `pinning`. La frecuencia de revisión surgirá de la duración de una
vuelta completa de la cola:

```text
intervalo efectivo de observación ≈ ARKs pendientes / throughput de ciclos
```

### Política de prioridad y purga

Availability tiene prioridad absoluta porque desbloquea la publicación. Sin
embargo, durability no debe quedar permanentemente sin ejecución ante un flujo
continuo de nuevos depósitos. La regla implementable es:

- mientras haya 100 o más ARKs en availability, usar toda la página para
  availability;
- cuando haya menos de 100, usar los espacios restantes para replication;
- cuando availability esté vacía, usar toda la capacidad para replication.

Un primer pin permite publicar, pero **no** permite purgar
`level1_json` ni `original_content`: ambos payloads se conservan para reparar
un CID hasta que L1 y L2 alcancen `target_replicas`. Solo entonces:

```text
REPLICATION → COMPLETE/DONE → purge payload local
```

### Estados y diagnóstico simplificados

El scheduler debe presentar cola por objetivo, no como una mezcla de `READY`,
`WAITING` y tiempos de reintento:

| Métrica | Significado |
| --- | --- |
| `awaiting_first_pin` | ARKs en availability sin ambos pines confirmados. |
| `first_pin_ready` | ARKs promovidos a Chain desde el último ciclo. |
| `awaiting_durability` | ARKs publicados que no alcanzan el objetivo de réplicas. |
| `durability_complete` | ARKs purgados desde el último ciclo. |
| `cluster_states` | Conteo observado de `queued`, `pinning`, `pinned`, `error` y `unpinned`. |
| `scan_round_seconds` | Tiempo estimado para volver a observar una población equivalente. |

`queued` y `pinning` deben mostrarse como actividad normal de Cluster; no como
errores ni como recuperación administrativa. Los únicos errores recuperables
son fallos de transporte/Store API o resultados inválidos, y los permanentes
siguen dependiendo del catálogo de códigos estructurados.

### Cambios técnicos para otro agente

- Cambiar la selección del repositorio para que use dos consultas: availability
  primero y replication solo para completar la página.
- Eliminar el cálculo de backoff de observación y la transición normal a
  `WAITING`; conservar `next_action_at` únicamente para errores técnicos.
- Mantener adquisición de advisory lock por ARK, lectura de CIDs, batch global
  de estados y comparación de CIDs antes de guardar.
- Cambiar el tamaño por defecto de página de replication a 100 y mantener el
  máximo de batch en 200 CIDs.
- Sustituir la configuración histórica de cuotas y backoff por una
  configuración mínima:

  ```env
  REPLICATION_WORKER_PAGE_SIZE=100
  REPLICATION_STATUS_BATCH_SIZE=200
  REPLICATION_IDLE_SLEEP_SECONDS=2
  ```

- Actualizar el status full y el dashboard para usar las métricas de objetivo
  anteriores y no mostrar `ready/waiting/delayed` como modelo de cola.

### Pruebas de aceptación

1. Con 100 ARKs de availability, se ejecuta una sola consulta batch de hasta
   200 CIDs y no se consulta durability.
2. Con 60 ARKs de availability y 40 de durability, la página procesa ambos
   grupos en ese orden.
3. Un ARK `queued` es observado de nuevo después de que la cola completa haya
   avanzado, no monopoliza los ciclos y no se marca como error.
4. Al recibir primer pin en ambos CIDs, el ARK pasa a `CHAIN/READY` en el mismo
   ciclo y Chain puede tomarlo en su siguiente ciclo.
5. Un ARK publicado conserva payload local hasta `target_replicas`; solo se
   purga al completar durability.
6. Fallos de Store API usan retry técnico; `queued`/`pinning` no generan retry,
   FAILED ni acciones de Recovery.
7. El dashboard distingue inequívocamente primer pin, durabilidad y errores.

## Estado vigente de implementación

La implementación vigente usa un scheduler ligero y una agenda persistida por
ARK. No se usan cuotas separadas de availability/durabilidad, pero sí se limita
el mantenimiento a 10 ARKs/20 CIDs por ronda.

- `REPLICATION_WORKER_PAGE_SIZE` queda en 100 por defecto.
- Availability se selecciona siempre primero; Replication completa los
  lugares libres de la página únicamente cuando availability tiene menos de
  100 registros.
- La selección usa `next_action_at IS NULL OR <= now`; los estados normales
  `queued` y `pinning` vuelven a entrar solo cuando vence su agenda individual.
- La prioridad se ordena por `next_action_at`,
  `replication_checked_at ASC NULLS FIRST`, creación e identificador.
- `queued`, `pinning` e `initial_visibility` se programan a 15 s, 1 min y
  después 5 min. `REPLICA_TARGET` se programa a 5 min, 15 min y después cada
  hora. Solo los fallos técnicos conservan además un retry de error.
- Se conserva el batch de hasta 200 CIDs, el lock individual y la comprobación
  de CIDs antes de persistir.
- El payload local continúa retenido hasta alcanzar `target_replicas`.
- La configuración de cuotas y backoff normal fue sustituida por:

  ```env
  REPLICATION_WORKER_PAGE_SIZE=100
  REPLICATION_STATUS_BATCH_SIZE=200
  REPLICATION_IDLE_SLEEP_SECONDS=2
  ```

La prueba Docker con Cluster real sigue siendo necesaria para medir el tiempo
real de `queued/pinning → pinned`. El límite de ejecución está en el pin
tracker de cada peer: la configuración actual parte de
`CLUSTER_PINTRACKER_CONCURRENTPINS=20`. El valor debe compararse con el
predeterminado 10 bajo carga sostenida y conservarse solo si mejora el tiempo
hasta el primer pin sin saturar Kubo, disco o Cluster.

## Implementación vigente: primer pin frente a durabilidad

La implementación actual no usa un reparto configurable entre availability y
durabilidad. `REPLICATION_WORKER_PAGE_SIZE` es 100 por defecto y
la selección llena primero la página con ARKs de `AVAILABILITY`; solo utiliza
los lugares restantes para `REPLICATION`. `REPLICATION_STATUS_BATCH_SIZE` es
200 y los CIDs repetidos dentro de una página se consultan una sola vez.

Store API devuelve el CID tan pronto Cluster acepta el `add`; no espera a que
el primer pin aparezca como `pinned`. Replication confirma después el primer
pin real de L1 y L2. Solo entonces libera `CHAIN`. Tras la publicación, la
misma etapa de replication espera `target_replicas` antes de purgar los payloads
locales.

`queued`, `pinning` e `initial_visibility` son espera normal y ahora tienen una
agenda por ARK en `next_action_at`: 15 segundos, 1 minuto y después 5 minutos.
Tras Chain, `REPLICA_TARGET` se audita a los 5 minutos, 15 minutos y después
cada hora. `REPLICATION_IDLE_SLEEP_SECONDS` solo sirve para detectar nuevas
entradas cuando no hay un ARK vencido; no sustituye la agenda persistida.

## Modelo vigente de dos fases

El ingreso solicita a Cluster una asignación inicial `replication-min=1` y
`replication-max=1`. Después de observar un pin real en L1 y L2, el ARK queda
listo para Chain. Una vez publicado, el ARK permanece en `REPLICATION`.

El worker solo llama a la operación de promoción de Store API cuando no existe
trabajo pendiente en `METADATA` ni ningún ARK con primer pin pendiente en
`AVAILABILITY`. Esa operación eleva el pin existente a
`replication-max=target_replicas`; no vuelve a subir el payload. La purga se
produce únicamente cuando ambos CIDs alcanzan ese objetivo.
