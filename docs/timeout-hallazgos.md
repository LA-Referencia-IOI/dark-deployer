# Hallazgos de timeouts y disponibilidad de publicación

Documento operativo actualizado el 4 de septiembre de 2026. Distingue la evidencia observada en el entorno Docker local de los cambios aplicados y de las tareas aún pendientes antes de producción.

## Resumen actual

La causa principal de los timeouts observados fue eliminada: Store API ya no mantiene abierta la solicitud mientras espera que IPFS Cluster confirme el primer pin. Tras aceptar el contenido, Cluster devuelve el CID y Store API lo retorna inmediatamente al minter.

La política de replicación se simplificó. `storage-topology.json` (schema v3) es la fuente de verdad y solo define nodos, pools de acceso y:

```json
{
  "replication": {
    "publish_after_replicas": 1,
    "target_replicas": 2
  }
}
```

El estado de un CID se calcula contando los elementos de `peer_map` de Cluster que tienen estado `pinned`. No se usan mapas estáticos peer→sede, réplicas locales/remotas ni etiquetas de sede como condición funcional.

## Cambios aplicados

| Área | Cambio |
| --- | --- |
| Topología | `storage-topology.json` v3 valida nodos, IPs, grupos de acceso y los umbrales de publicación/objetivo. |
| Configuración | El instalador genera para Store API solo el documento de endpoints del grupo seleccionado y propaga los umbrales al minter. |
| Escritura | `POST /v1/store` usa Cluster REST `POST /add?local=true` y devuelve el CID sin esperar un pin. |
| Estado | Store API informa `total_replicas` y `checked_at`; el minter aplica la política. |
| Reconciliación | Un worker independiente verifica ambos CIDs, repara contenidos sin pins y purga payloads solo cuando ambos alcanzan el mínimo. |

La respuesta temprana significa únicamente que Cluster aceptó el contenido y devolvió un CID. No significa todavía que exista una copia con estado `pinned`.

## Flujo actual

```text
Plataforma / harvester
  -> POST /api/v1/arks/batch (minter)
  -> metadata worker
  -> POST /v1/store (Store API)
  -> Cluster REST /add?local=true
  -> CID devuelto inmediatamente
  -> metadata worker persiste L2 y L1
  -> replication worker verifica y repara los CIDs
  -> chain worker publica el ARK
```

El metadata worker procesa actualmente páginas de 100 registros con concurrencia 4. Cada ARK implica una escritura de Level 2 y otra de Level 1.

## Disponibilidad antes de publicar

La separación de escritura y replicación eliminó el timeout de la ruta crítica y ahora la publicación está bloqueada por disponibilidad real.

Después de persistir los CIDs, el metadata worker deja los contadores de réplicas en `NULL` (desconocidos). El ARK entra en la etapa `AVAILABILITY`; el replication worker consulta Cluster y escribe los conteos efectivos. Solo cuando L1 y L2 alcanzan `REPLICATION_PUBLISH_AFTER_REPLICAS` el ARK pasa a la etapa `CHAIN` y el chain worker puede publicarlo.

La secuencia normal es:

```text
Cluster acepta L2 y L1 -> minter guarda ambos CIDs con conteos NULL
                      -> replication worker confirma los pins reales
                      -> chain worker publica solo al alcanzar el umbral
```

La regla efectiva es:

```text
chain worker publica solo si L1.total_replicas >= publish_after_replicas
                         y L2.total_replicas >= publish_after_replicas
```

Esos valores son escritos por el reconciliador después de consultar Cluster, nunca asumidos al almacenar el CID. `target_replicas` no bloquea la publicación: solo condiciona la purga segura del payload retenido.

## Exclusión de workers

Cada intento adquiere un advisory lock de PostgreSQL derivado del ARK, en una sesión dedicada que se conserva durante el I/O externo y la escritura final. No se persiste un estado `RUNNING`, propietario ni vencimiento. Si el proceso o su conexión mueren, PostgreSQL libera el lock automáticamente y el registro permanece en `READY` o `WAITING` para el siguiente ciclo.

La API y los workers adquieren el mismo lock por ARK antes de leer y modificar el registro. La conexión física permanece reservada hasta liberar el lock, incluso entre transacciones. La API responde `409 Conflict` si el ARK está ocupado; tras adquirirlo comprueba que una actualización parte de `PUBLISHED` (la primera carga sigue siendo `RESERVED → DRAFT`). Tombstones y rescates manuales también participan de la exclusión. Los workers vuelven a comprobar etapa, estado y fecha del reintento al adquirirlo. Se elimina el contador de revisión: el contenido no puede cambiar durante el intento. Una conexión perdida invalida el intento y no puede reconectarse para escribir resultados sin lock.

## Evidencia histórica que explica el incidente

La configuración anterior contenía solo un peer en `IPFS_CLUSTER_PEER_SITES_JSON`, aunque el Cluster tenía dos peers activos. Un CID que Store API reportaba como `0/1` estaba realmente `pinned` en el segundo peer. El mapa estático no podía reconocer esa copia y producía un falso negativo.

Además, el minter usaba un timeout HTTP de 10 segundos mientras Store API podía esperar hasta 120 segundos para confirmar el primer pin. La combinación produjo errores de infraestructura, reintentos de metadata y ciclos como:

```text
processed=100 | succeeded=0 | failed=100 | duration≈251 s
```

Esas configuraciones y comportamientos ya no forman parte del diseño actual:

- `IPFS_CLUSTER_PEER_SITES_JSON` fue eliminado.
- `IPFS_CLUSTER_LOCAL_SITE_ID` dejó de ser necesario para el conteo.
- `IPFS_REPLICATION_CONFIRM_TIMEOUT_SECONDS` fue eliminado.
- Store API ya no usa polling síncrono de `/pins/{cid}` ni devuelve 503 por no haber confirmado el primer pin.

El timeout de 10 segundos del minter sigue siendo un parámetro a observar: ahora cubre la aceptación del `add`, la conexión y la respuesta de Cluster, no la replicación completa. Debe medirse bajo carga antes de modificarlo.

## Problema independiente: conectividad del harvester

Un harvester configurado con `http://localhost:8001` dentro de su contenedor no alcanza al minter: `localhost` apunta al propio harvester. Como el cliente Java añade `/api/v1`, la URL base tampoco debe incluir ese prefijo.

Usar una dirección alcanzable desde la red del contenedor, por ejemplo:

```text
http://minter-api:8001            # contenedor conectado a dark-net
http://host.docker.internal:8001  # alternativa de Docker Desktop
```

Este problema es independiente de Store API e IPFS Cluster.

## Asuntos pendientes

### Obligatorio antes de producción

- Probar la carrera entre metadata, replication y chain workers con Cluster lento, sin pins y recuperándose de una caída.
- Confirmar con PostgreSQL real que dos reconciliadores no operen a la vez sobre el mismo ARK y que el advisory lock se libere al caer un proceso.
- Probar liberación automática del advisory lock después de matar un worker durante I/O y la toma segura por el siguiente ciclo.
- Validar desde el contenedor del harvester DNS, TCP y HTTP hacia el minter.

### Operación y capacidad

- Medir p50, p95 y p99 de `POST /v1/store` durante ingestas pequeña, mediana y masiva.
- La configuración vigente usa páginas de 100 para Chain y Replication; la
  ventana RPC de Chain es 50 y el mantenimiento de Replication se limita a 10
  ARKs/20 CIDs cada cinco segundos para no competir con el primer pin.
- Registrar edad del elemento más antiguo, tamaño de cola, latencia de Store API y errores por capa.
- Revisar backoff para evitar que una caída de Cluster genere una tormenta de reintentos.
- Definir tasa máxima de ingesta por sitio y estrategia de pausa/reanudación.

### Pruebas de aceptación

- Almacenar CIDs cuando cada peer local es elegido y comprobar el conteo total de pins, sin usar pertenencia a una sede.
- Comprobar que `total_replicas=0` mantiene el ARK fuera de la cola de cadena.
- Comprobar que L1 y L2 habilitan la publicación solo al alcanzar `publish_after_replicas`.
- Comprobar que la purga solo ocurre cuando ambos CIDs cumplen `target_replicas`.
- Simular Cluster no disponible, respuesta lenta y recuperación, verificando heartbeat, backoff y ausencia de duplicación.
- Repetir una ingesta desde el harvester una vez corregida su URL base.

## Health y observabilidad

`/health` debe seguir siendo una comprobación barata de liveness y conectividad local. No prueba que un CID esté disponible ni que un ARK pueda publicarse.

El status operativo debe mostrar por separado:

- heartbeat y último ciclo de metadata, replication y chain;
- registros pendientes de persistencia, disponibilidad, publicación y reconciliación;
- último error por worker;
- cantidad total de réplicas de cada CID cuando se solicita detalle.

Debe alertarse cuando el health sea `ok` pero la tasa de persistencia o de publicación sea cero, o cuando la cola crezca sostenidamente.

## Conclusión

El incidente original combinó un conteo de réplicas dependiente de una topología incompleta y un timeout de cliente menor que la espera síncrona de replicación. Ambos factores fueron removidos del diseño.

La separación entre aceptación, disponibilidad, publicación y purga ya está implementada. La prioridad restante es validarla bajo fallos reales de PostgreSQL, Cluster, RPC y procesos, incluyendo la recuperación automática de advisory locks.

## Estado vigente del primer pin

`POST /v1/store` confirma únicamente que Cluster aceptó el contenido y devolvió
un CID. La respuesta no garantiza que exista todavía un peer `pinned`. La
confirmación del primer pin es responsabilidad exclusiva de
`ReplicationReconciliationWorker`, que observa ambos CIDs mediante el endpoint
batch de Store API.

La publicación se habilita cuando `min(L1.pinned, L2.pinned)` alcanza
`REPLICATION_PUBLISH_AFTER_REPLICAS` (por defecto 1). El payload local no se
purga en ese momento: permanece retenido hasta que ambos CIDs alcancen
`REPLICATION_TARGET_REPLICAS` (por defecto 2).

La configuración vigente usa páginas de 100 ARKs y lotes de hasta 200 CIDs.
No se usan cuotas separadas. Availability tiene prioridad y
durabilidad usa la capacidad restante de la página.
