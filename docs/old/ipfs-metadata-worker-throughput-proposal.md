# Propuesta para mejorar el rendimiento del Metadata Worker e IPFS

> **Estado: histórico.** Las decisiones aplicadas se documentan en
> [`publication-latency-control.md`](publication-latency-control.md) y
> [`ipfs-architecture.md`](ipfs-architecture.md). No use esta propuesta como
> configuración vigente.

## Estado de este documento

Este es un análisis histórico de rendimiento. Su diseño de referencia fue
reemplazado: Store API devuelve el CID cuando Cluster acepta el `add`, sin
esperar un pin; el Minter retiene los payloads, el Replication Reconciliation
Worker observa/repara réplicas y Chain publica cuando L1 y L2 alcanzan
`publish_after_replicas`. La purga requiere `target_replicas` para ambos
CIDs. Las secciones que hablan de confirmar un pin, conteos por sede o
`purge_target_met` describen alternativas descartadas, no el comportamiento
vigente.

## 1. Problema observado

Durante una carga de aproximadamente 30.000 ARKs, el dashboard mostró:

- 29.571 registros pendientes en el Metadata Worker;
- ciclos de 100 registros;
- 172,552 segundos por ciclo;
- 100 registros exitosos y cero errores;
- Chain Worker prácticamente vacío;
- blockchain sin congestión (`txpool pending: 0`);
- almacenamiento disponible como `ipfs_cluster · 2/1 peers`.

Ese ciclo representa aproximadamente:

- 1,73 segundos por ARK;
- 0,58 ARKs por segundo;
- 2.086 ARKs por hora;
- unas 14,2 horas para consumir 29.571 pendientes, si no ingresan nuevos
  registros.

El sistema no estaba fallando: procesaba correctamente, pero con un rendimiento
menor al requerido para cargas grandes.

## 2. Qué significa `2/1 peers`

El dashboard presenta:

```text
peers disponibles / peers mínimos para escritura
```

Por lo tanto, `2/1 peers` era una métrica del diseño anterior. En el diseño
vigente Store API informa disponibilidad de endpoints locales, mientras la
política de publicación y purga pertenece al Minter. Store API devuelve al ser
aceptado el `add`; no espera un quorum de réplica antes de responder.

## 3. Diagnóstico del flujo actual

Para cada ARK nuevo, el Metadata Worker normalmente realiza:

1. almacenamiento del documento Level 2;
2. espera de la respuesta de Store API;
3. almacenamiento del documento Level 1, que referencia el CID de Level 2;
4. espera de la respuesta de Store API;
5. persistencia de ambos CIDs en PostgreSQL;
6. habilitación del registro para el Chain Worker.

El orden Level 2 → Level 1 debe conservarse dentro de cada ARK. Sin embargo,
los ARKs de una página también se procesan secuencialmente. Una página de 100
ARKs puede producir aproximadamente 200 operaciones de almacenamiento sin
paralelismo entre ARKs.

Además, los clientes HTTP actuales crean conexiones con mucha frecuencia, lo
que agrega establecimiento de conexión y negociación a un camino que se repite
miles de veces.

Los principales candidatos a cuello de botella son:

- procesamiento secuencial entre ARKs;
- dos escrituras de metadata por ARK;
- creación repetida de clientes/conexiones HTTP;
- tiempo de `add` y pin en IPFS Cluster;
- observación de réplicas y reparación después de la admisión de Store API.

Antes de modificar la semántica de durabilidad, se debe medir cuánto tiempo
corresponde a cada etapa.

## 4. Objetivos

- Aumentar el throughput del Metadata Worker para cargas grandes.
- Mantener disponible el minting cuando uno de los dos servidores IPFS de una
  sede no está disponible.
- No publicar un CID que Store API no haya aceptado y cuyas réplicas no hayan
  alcanzado el umbral de publicación en el reconciliador.
- Mantener una fuente recuperable mientras la réplica objetivo todavía no está
  completa.
- No agregar Redis, Kafka ni nuevos servidores para resolver este problema.
- Conservar el orden Level 2 → Level 1 dentro de cada ARK.
- Hacer que el cambio sea gradual, configurable y reversible.

## 5. Modelo de confirmación y persistencia

La topología sigue siendo un único IPFS Cluster global. El problema a resolver
no requiere crear clusters independientes ni agregar infraestructura. Requiere
separar cuatro momentos que hoy suceden casi juntos:

```text
T0  CID calculado o aceptado por Cluster
T1  contenido PINNED en al menos un peer
T2  contenido PINNED en los dos peers de la sede local
T3  contenido PINNED en al menos dos sedes
T4  contenido PINNED en todos los peers objetivo
```

Para cada momento se debe decidir independientemente:

- cuándo Store API responde con éxito;
- cuándo el ARK queda habilitado para el Chain Worker;
- cuándo la metadata original puede eliminarse de PostgreSQL;
- cuál es el objetivo final que debe mantener IPFS Cluster.

Una respuesta rápida de Store API no obliga a eliminar el payload ni a publicar
el CID inmediatamente en blockchain.

## 6. Componentes comunes a todas las opciones

### 6.1 Concurrencia limitada entre ARKs

El orden Level 2 → Level 1 debe conservarse dentro de cada ARK, pero varios ARKs
pueden procesarse en paralelo:

```text
ARK A: Level 2 → Level 1 ┐
ARK B: Level 2 → Level 1 ├─ máximo configurable, inicialmente 4
ARK C: Level 2 → Level 1 ┤
ARK D: Level 2 → Level 1 ┘
```

Configuración propuesta:

```dotenv
METADATA_WORKER_CONCURRENCY=4
```

La primera prueba debe comparar concurrencia 1, 2 y 4. Solamente se debería
subir a 8 después de medir CPU, memoria, conexiones PostgreSQL, disco, red,
Store API e IPFS. `METADATA_WORKER_PAGE_SIZE` continúa siendo el tamaño de la
página seleccionada, no el nivel de concurrencia.

### 6.2 Reutilización de conexiones

Se deben reutilizar clientes HTTP de larga vida en:

- Minter → Store API;
- Store API → Kubo/IPFS Cluster.

Los clientes deben cerrarse durante el shutdown y sus pools deben tener límites
compatibles con la concurrencia configurada.

### 6.3 Mediciones por etapa

Todas las opciones necesitan medir separadamente:

- preparación de Level 2;
- `POST /v1/store` de Level 2;
- preparación de Level 1;
- `POST /v1/store` de Level 1;
- `add` en Cluster Proxy;
- tiempo hasta el primer pin;
- tiempo hasta el quorum de publicación;
- tiempo hasta la réplica objetivo;
- consultas de estado de replicación;
- commit PostgreSQL;
- duración y throughput total del ciclo.

Las métricas deben incluir p50, p95 y p99. El dashboard debe estimar el tiempo
de drenaje usando throughput observado, no el intervalo de sleep del worker.

## 7. Opciones de diseño

| Opción | Store responde | Minting | Purga del payload | Complejidad |
| --- | --- | --- | --- | --- |
| A. Optimizar flujo actual | quorum actual | inmediata | quorum actual | baja |
| B. Un pin + reconciliación oportunista | T1 | T1 | objetivo de persistencia | media-baja |
| C. Publicación en dos fases | T1 | T2 o T3 | objetivo de persistencia | media |
| D. Réplica objetivo sincrónica | T2, T3 o T4 | inmediata | al responder | baja-media |
| E. Spool durable en Store API | spool local | quorum posterior | quorum posterior | media-alta |
| F. Batch/CAR | por lote | por lote o CID | por lote | alta |
| G. Workers horizontales | sin cambio | sin cambio | según política | alta |

### 7.1 Opción A — Optimizar el flujo actual

Mantener la semántica actual y agregar solamente pooling, concurrencia y
métricas.

Ventajas:

- cambio funcional mínimo;
- rollback sencillo;
- permite comprobar si la espera de IPFS es realmente el límite dominante.

Desventajas:

- el payload continúa eliminándose al alcanzar el quorum mínimo;
- no queda una fuente local durante la ventana hasta la réplica objetivo.

Esta opción debe ser la primera línea base optimizada, pero no resuelve por sí
sola la ventana de durabilidad.

### 7.2 Opción B — Un pin confirmado y reconciliación oportunista

Esta es la opción simple solicitada y la principal candidata para una primera
implementación completa.

El diseño vigente mantiene la política en la topología, pero Store API no la
interpreta: el Minter aplica `publish_after_replicas` y
`target_replicas` al total de pins observado.

El flujo sería:

```text
1. guardar Level 2 y esperar un PINNED
2. guardar Level 1 y esperar un PINNED
3. conservar Level 1 y Level 2 en PostgreSQL
4. marcar replication_pending
5. permitir que el Chain Worker continúe
6. cuando el Metadata Worker tenga capacidad ociosa, verificar ambos CIDs
7. eliminar el payload solamente cuando ambos alcancen el objetivo
```

IPFS Cluster continúa distribuyendo los pins sin bloquear el camino principal.
El Metadata Worker reutiliza sus períodos ociosos para comprobar y, cuando sea
necesario, reparar la persistencia.

Un reconciliador estrictamente limitado a momentos sin nuevos ARKs puede sufrir
starvation durante una carga continua. Por eso se recomienda:

- prioridad para nuevas escrituras;
- reconciliación inmediata cuando no existan ARKs listos;
- un pequeño presupuesto obligatorio de mantenimiento cuando una replicación
  supere una antigüedad máxima, aunque continúe entrando trabajo nuevo.

La implementación utiliza solamente:

```dotenv
METADATA_WORKER_CONCURRENCY=4
REPLICATION_WORKER_CONCURRENCY=2
REPLICATION_PUBLISH_AFTER_REPLICAS=1
REPLICATION_TARGET_REPLICAS=2
```

Los valores efectivos de publicación y purga se derivan de la topología y se
propagan al Minter. No se exige una distribución por sede; los conteos se
guardan en `ark_metadata` y el workflow interno en `ark_records`.
La política comprueba Level 1 y Level 2 de manera independiente; no se pueden
sumar copias de ambos CIDs ni asumir que están fijados en los mismos peers.

Si un CID conserva al menos una copia pero todavía no alcanza el objetivo, el
reconciliador solamente vuelve a consultar o solicita la reparación del pin. Si
un CID llega a cero copias, debe volver a ejecutar el `add` desde el payload
retenido y verificar que el CID resultante sea exactamente el esperado.

Ventajas:

- confirma al menos una copia antes de avanzar;
- mantiene el minting disponible con un peer local caído;
- no necesita Redis, Kafka ni otro servicio;
- aprovecha la capacidad ociosa del worker;
- conserva una fuente de recuperación hasta alcanzar la política de purga.

Límites:

- el CID puede publicarse durante una ventana con una sola copia IPFS;
- PostgreSQL puede crecer durante una falla prolongada;
- necesita estados, métricas, backoff y alertas de reconciliación.

### 7.3 Opción C — Persistencia y publicación en dos fases

Store API responde después de T1 y el Metadata Worker marca el registro como
almacenado, pero el Chain Worker solamente publica cuando se alcanza T2 o T3:

```text
metadata_pending
  → replication_pending
  → replication_ready
  → chain_pending
  → published
```

Esta alternativa desacopla la ingestión de la latencia geográfica sin publicar
en blockchain un CID que todavía no cumple la durabilidad acordada.

Ventajas:

- absorbe cargas rápidamente;
- permite exigir dos copias locales o dos sedes antes del minting;
- conserva el payload para reparar cualquier pérdida previa a la publicación.

Desventajas:

- agrega un estado explícito al pipeline;
- el backlog puede trasladarse de metadata a replicación;
- requiere definir con precisión el quorum que habilita al Chain Worker.

### 7.4 Opción D — Esperar sincrónicamente la réplica objetivo

Cada `POST /v1/store` espera T2, T3 o T4 antes de responder. Es la opción más
simple de razonar, pero coloca toda la latencia de replicación local o WAN en el
Metadata Worker.

En una sede, esperar dos peers detiene el minting si cualquiera falla. En varias
sedes, esperar tres peers en dos sedes ofrece fuerte durabilidad previa al
minting, pero reduce disponibilidad durante particiones y aumenta la latencia.

### 7.5 Opción E — Spool durable en Store API

Store API calcula el CID, guarda el payload en un spool durable y responde. Un
proceso interno completa después el `add`, pin y replicación.

Esta opción desacopla completamente la ingestión, pero Store API tendría que
implementar cola durable, recuperación tras reinicio, idempotencia, límites de
disco, estados y limpieza. PostgreSQL en Minter ya puede cumplir esta función,
por lo que no se recomienda inicialmente.

Responder sin confirmar un pin es el diseño vigente: PostgreSQL del Minter es
el spool durable que retiene L1/L2 hasta su observación y posterior purga.

### 7.6 Opción F — API batch o archivos CAR/DAG

Agrupar documentos reduce llamadas HTTP o cantidad de pins, pero complica
resultados parciales, reintentos, recuperación individual, auditoría y unpin.
Debe considerarse solamente si pooling y concurrencia no alcanzan y las métricas
demuestran que el número de pins es el cuello de botella.

### 7.7 Opción G — Varios procesos Metadata Worker

El advisory lock global actual impide escalar el worker directamente. Sería
necesario usar claims por registro, `FOR UPDATE SKIP LOCKED` o leases, asegurar
idempotencia y recuperar tareas abandonadas. Es una opción futura, no la más
simple para el problema actual.

## 8. Observabilidad de copias locales y remotas

Store API recibe la topología y deriva sus endpoints locales por
un grupo de acceso de Store API, pero la topología no es una dependencia funcional
ni se expone en la API.

```json
{
  "cid": "bafy...",
  "status": "pinned",
  "replication": {"total_replicas": 4, "checked_at": "2026-08-21T12:00:00Z"}
}
```

La distribución por sede no forma parte de la respuesta ni de la política
funcional; el reconciliador decide sobre el total observado y los umbrales del
Minter.

El recuento se obtiene de los peers con estado `PINNED`, no de allocations,
peers conectados ni pins en progreso. Un peer `PINNING`, `PIN_ERROR` o
inalcanzable no cuenta como copia durable.

### 8.1 Estado persistido por ARK

No se agregó una tabla de replicación. `ark_metadata` conserva solamente:

```text
level1_replica_count
level2_replica_count
replication_checked_at
replication_last_error
```

Los estados de workflow se conservan compactamente en `ark_records`. El payload
solamente se elimina cuando L1 y L2 cumplen la política de purga.

### 8.2 API operativa

Esta entrega no modifica el dashboard. `/api/v1/worker/status` agrega:

- ARKs con replicación pendiente, completa, degradada y en error;
- antigüedad del pendiente más antiguo;
- payloads retenidos;
- última ejecución con revisados, reparados, purgados y fallidos.

`/api/v1/worker/replication` lista CIDs, los dos conteos, fecha y último error.
La respuesta de Store API solo aporta el total observado; no se consulta
distribución por sede.

Alertas mínimas:

- cualquier CID con cero copias y payload recuperable;
- replicación pendiente por encima de la antigüedad máxima;
- payload no recuperable que no cumple el quorum;
- crecimiento de PostgreSQL por encima del límite operativo;
- una sede completa sin copias o sin peers visibles.

## 9. Política implementada por entorno

| Entorno | Store responde | Chain publica | Purga PostgreSQL | Objetivo Cluster |
| --- | --- | --- | --- | --- |
| Developer simple | 1 peer | 1 peer | 1 peer | 1 peer |
| Developer HA / una sede | 1 local | 1 local | 2 locales | 2 peers |
| Multisede disponible | 1 local | 1 local | 2 locales + 1 remoto, 2 sedes | todos |

La política prioriza continuidad y acepta una ventana de una sola copia,
mitigada por la retención en PostgreSQL. No hay variantes configurables.

## 10. Comportamiento del reconciliador

La reconciliación es responsabilidad exclusiva del Replication Reconciliation Worker:

1. procesar la página normal de metadata pendiente;
2. si no hay trabajo listo, seleccionar los payloads retenidos con la
   comprobación más antigua;
3. consultar L1 y L2 con concurrencia limitada;
4. actualizar solamente los conteos totales y la fecha;
5. si ambos alcanzaron la política, purgar el payload;
6. si existe al menos una copia, mantener el payload;
7. si no existe ninguna copia, reingresar el contenido conservado y comprobar
   que el CID obtenido coincide;
8. si hay carga continua, ejecutar un lote máximo de 50 cada cinco minutos.

Las operaciones deben ser idempotentes. Un reinicio entre la comprobación y la
purga no debe perder el payload: la purga se realiza en una transacción que
vuelve a verificar que el estado persistido cumple la política.

## 11. Plan de evaluación gradual

### Fase 0 — Línea base e instrumentación

1. Ejecutar una carga representativa con el código actual.
2. Registrar throughput, latencias por etapa y recursos.
3. Repetir con un nodo IPFS detenido.
4. Medir separadamente aceptación de Store API, primera réplica observada y réplica completa.

### Fase 1 — Pooling

1. Reutilizar clientes HTTP sin cambiar concurrencia.
2. Repetir la carga y comparar conexiones y latencias.

### Fase 2 — Concurrencia

1. Probar concurrencia 2 y luego 4.
2. Evaluar 8 solamente si los recursos tienen margen.
3. Mantener Level 2 → Level 1 dentro de cada ARK.

### Fase 3 — Admisión, retención y observabilidad

1. Admitir el CID sin bloquear la ruta crítica y habilitar Chain solo tras observación asíncrona.
2. Conservar L1 y L2 en PostgreSQL.
3. Exponer el total de réplicas observado y las colas internas del Minter.
4. Mostrar `replication_pending` y su antigüedad.

### Fase 4 — Reconciliación oportunista

1. Ejecutarla primero durante períodos ociosos.
2. Verificar que el límite de starvation funciona bajo carga continua.
3. Simular caída y recuperación de cada peer y de una sede.
4. Purgar solamente cuando L1 y L2 cumplan la política.
5. Probar recuperación desde PostgreSQL cuando un CID llega a cero copias.

### Fase 5 — Política previa al minting

Comparar con la misma carga:

- publicación con un peer local;
- publicación con dos peers locales;
- publicación con dos peers en dos sedes;
- publicación con tres peers en dos sedes.

## 12. Criterios de aceptación

- Cero pérdida de metadata durante pruebas de falla controlada.
- Cero ARKs publicados sin alcanzar el quorum de publicación configurado.
- Level 1 siempre referencia correctamente el CID de Level 2.
- Los reintentos y reinicios no producen resultados inconsistentes.
- Un peer caído no detiene el minting cuando el mínimo es uno.
- El reconciliador no queda postergado indefinidamente bajo carga continua.
- Al recuperar peers o sedes, los CIDs pendientes alcanzan el objetivo.
- Los recuentos locales, remotos y por sede reflejan estados `PINNED` reales.
- El payload se purga solamente cuando L1 y L2 cumplen la política definida.
- Un CID con cero copias puede reconstruirse desde el payload retenido.
- El throughput mejora de manera medible frente a la línea base.
- CPU, memoria, disco, red y pool PostgreSQL permanecen dentro de límites
  operativos acordados.
- El cambio puede desactivarse mediante configuración y rollback de imagen.

## 13. Riesgos y límites

- Publicar con un solo pin crea una ventana con una sola copia IPFS. La copia en
  PostgreSQL permite recuperación, pero no disponibilidad IPFS inmediata si el
  único peer desaparece.
- Esperar una copia remota antes del minting elimina la ventana de una sola sede,
  pero introduce dependencia de la VPN y de otra sede.
- Una caída larga puede hacer crecer PostgreSQL; deben definirse capacidad,
  alertas y una política que nunca purgue payload no protegido.
- Los conteos son una fotografía: pueden cambiar después de la consulta. La
  decisión de purga debe registrar cuándo y con qué política se tomó.
- IPFS Cluster sigue siendo responsable de mantener allocations. El
  reconciliador de Minter conserva y reingresa contenido; no debe implementar
  un segundo sistema de asignación de peers.

## 14. Preguntas abiertas

1. ¿El Chain Worker debe publicar con un peer local, dos locales o dos sedes?
2. ¿La purga multisede requiere todos los peers o una política mínima estable?
3. ¿Cuánto payload y durante cuánto tiempo puede conservar PostgreSQL?
4. ¿Cinco minutos sigue siendo el presupuesto correcto bajo carga continua?
5. ¿El reconciliador puede solicitar explícitamente un nuevo pin o solamente
   reingresar contenido cuando detecte cero copias?
6. ¿Cuál es el throughput mínimo requerido para producción?
7. ¿Qué límites de CPU, IOPS y red tienen los servidores IPFS actuales?
8. ¿Se necesita preservar orden global o solamente Level 2 → Level 1 por ARK?
9. ¿Qué período histórico de snapshots de replicación necesita el dashboard?

## 15. Recomendación

Mantener el único Cluster global y no agregar infraestructura nueva.

La evolución recomendada es:

1. instrumentación y clientes HTTP persistentes;
2. concurrencia configurable, comenzando con 2 y luego 4;
3. admitir el CID y conservar el payload hasta la política de purga;
4. reconciliar prioritariamente cuando el worker esté ocioso, con un límite de
   starvation para garantizar progreso;
5. exponer el total de copias observadas para cada CID;
6. purgar solamente cuando Level 1 y Level 2 alcancen el objetivo acordado.

Antes de implementar la fase que habilita el Chain Worker se debe elegir
explícitamente entre disponibilidad —publicar con un pin— y durabilidad
geográfica —esperar al menos dos sedes—. Esta decisión puede ser configurable
por entorno sin cambiar la arquitectura del cluster.
