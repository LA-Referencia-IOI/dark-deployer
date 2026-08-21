# Propuesta para mejorar el rendimiento del Metadata Worker e IPFS

## Estado de este documento

Esta es una propuesta de trabajo, no una implementación activa.

El cambio experimental que motivó este documento fue revertido para poder
evaluar alternativas con calma. El comportamiento actual del Minter, Store API
e IPFS Cluster permanece sin cambios.

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

Por lo tanto, `2/1 peers` significa que dos peers están visibles y que la
política de escritura exige confirmar uno. No significa que uno de los dos
peers esté caído.

Esto también significa que Store API no está esperando necesariamente la
replicación completa en ambos peers antes de responder. Espera el quorum mínimo
configurado, actualmente un peer.

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
- verificación de quorum realizada dentro de cada solicitud de Store API.

Antes de modificar la semántica de durabilidad, se debe medir cuánto tiempo
corresponde a cada etapa.

## 4. Objetivos

- Aumentar el throughput del Metadata Worker para cargas grandes.
- Mantener disponible el minting cuando uno de los dos servidores IPFS de una
  sede no está disponible.
- No publicar un CID que Store API no haya aceptado y fijado al menos en el
  quorum mínimo.
- Mantener una fuente recuperable mientras la réplica objetivo todavía no está
  completa.
- No agregar Redis, Kafka ni nuevos servidores para resolver este problema.
- Conservar el orden Level 2 → Level 1 dentro de cada ARK.
- Hacer que el cambio sea gradual, configurable y reversible.

## 5. Propuesta simple y confiable

La propuesta principal es conservar la confirmación sincrónica mínima y
paralelizar el trabajo entre ARKs:

```text
ARK A: Level 2 → Level 1 ┐
ARK B: Level 2 → Level 1 ├─ máximo configurable, inicialmente 4
ARK C: Level 2 → Level 1 ┤
ARK D: Level 2 → Level 1 ┘
```

### 5.1 Quorum de escritura

Mantener para la sede de dos nodos:

```dotenv
IPFS_CLUSTER_EXPECTED_PEERS=2
IPFS_CLUSTER_WRITE_MIN_PEERS=1
IPFS_CLUSTER_WRITE_MIN_SITES=1
```

Store API solamente responde con éxito cuando observa el quorum mínimo. IPFS
Cluster continúa intentando alcanzar la réplica objetivo en el segundo peer.

Esto permite seguir escribiendo cuando un servidor IPFS está temporalmente
caído.

### 5.2 Concurrencia limitada

Agregar una concurrencia configurable al Metadata Worker:

```dotenv
METADATA_WORKER_CONCURRENCY=4
```

La primera prueba debería usar cuatro ARKs concurrentes. Solamente se debería
subir a 8 o más después de medir CPU, memoria, conexiones PostgreSQL, disco,
red, Store API e IPFS.

`METADATA_WORKER_PAGE_SIZE` seguiría siendo el límite de selección de una
página, no el nivel de concurrencia.

### 5.3 Reutilización de conexiones

Reutilizar clientes HTTP de larga vida en:

- Minter → Store API;
- Store API → Kubo/IPFS Cluster.

Los clientes deben cerrarse durante el shutdown de cada servicio. El pool de
conexiones debe tener límites compatibles con la concurrencia configurada.

### 5.4 Conservación del payload durante la ventana de replicación

Después del quorum mínimo, el Chain Worker puede continuar usando los CIDs.
Sin embargo, Level 1 y Level 2 no deberían eliminarse inmediatamente de
PostgreSQL.

Un paso de reconciliación ejecutado por el mismo Metadata Worker comprobaría
periódicamente el estado de ambos CIDs. Solamente cuando los dos alcancen la
réplica objetivo se eliminaría el payload local.

Configuración propuesta:

```dotenv
METADATA_REPLICATION_TARGET_PEERS=2
METADATA_REPLICATION_CHECK_DELAY_SECONDS=30
```

No es necesario crear otro servicio. La reconciliación puede ser una segunda
fase del ciclo existente y procesar estados concurrentemente.

Para Developer con un único peer, el objetivo debería ser 1. Para Developer HA,
Sandbox y Production de una sede con dos peers, el objetivo debería ser 2.

## 6. Garantías y límites

Con esta propuesta:

- existe al menos una copia confirmada antes de avanzar;
- el minting puede continuar con un solo peer disponible;
- PostgreSQL conserva la fuente mientras falta la segunda réplica;
- la segunda réplica se verifica sin bloquear cada escritura individual;
- una caída temporal del segundo peer no bloquea toda la cola.

Existe una decisión importante: si el ARK se publica en blockchain después del
quorum de un peer, puede haber una ventana con una sola copia IPFS. Conservar el
payload permite recuperación, pero no garantiza disponibilidad IPFS si ese
único peer también desaparece antes de completar la segunda réplica.

Si se exige cero ventana de una sola copia, el Chain Worker deberá esperar dos
peers. Esa alternativa mejora durabilidad antes del minting, pero detiene nuevos
mintings cuando cualquiera de los dos peers está caído.

## 7. Alternativas consideradas

### Alternativa A — Solo concurrencia y pooling

Mantener exactamente la semántica actual y agregar únicamente:

- concurrencia limitada entre ARKs;
- reutilización de conexiones HTTP;
- métricas por etapa.

Ventajas:

- menor cambio funcional;
- sin cambios de estado ni reconciliación;
- rollback sencillo.

Desventajas:

- el payload continúa eliminándose después del quorum mínimo;
- no existe una fuente local durante la ventana hasta la segunda réplica.

Esta alternativa debería evaluarse primero porque puede resolver gran parte del
problema sin cambiar la política de persistencia.

### Alternativa B — Concurrencia, pooling y reconciliación

Es la propuesta principal descrita en la sección 5.

Ventajas:

- mejora de rendimiento;
- mantiene disponibilidad con un peer;
- conserva material de recuperación hasta llegar a dos réplicas.

Desventajas:

- agrega un estado implícito o explícito de replicación pendiente;
- requiere controlar el crecimiento temporal de PostgreSQL;
- necesita métricas y alertas de reconciliación.

### Alternativa C — Responder sin confirmar ningún pin

Store API devolvería el CID inmediatamente después de que el Cluster acepte el
`add`, sin comprobar un peer fijado.

No se recomienda para producción. Introduce una ventana en la que el CID existe
en la aplicación pero todavía no hay una copia durable observada.

### Alternativa D — Varios procesos Metadata Worker

Levantar múltiples réplicas del worker no funciona directamente con el diseño
actual porque existe un advisory lock por worker. Para escalar horizontalmente
sería necesario:

- reemplazar el lock global por claims por registro;
- usar `FOR UPDATE SKIP LOCKED` o leases;
- asegurar idempotencia de cada etapa;
- coordinar estadísticas y heartbeats entre instancias.

Es una alternativa válida a futuro, pero no es la más simple para el problema
actual.

### Alternativa E — API batch de Store API

Enviar múltiples documentos en una solicitud puede reducir overhead HTTP, pero
complica resultados parciales, reintentos e idempotencia. Debería considerarse
solamente si concurrencia y pooling no alcanzan el throughput requerido.

## 8. Instrumentación necesaria antes de decidir

Agregar mediciones separadas para:

- preparación de Level 2;
- `POST /v1/store` de Level 2;
- preparación de Level 1;
- `POST /v1/store` de Level 1;
- `add` en Cluster Proxy;
- tiempo hasta quorum mínimo;
- consultas de estado de replicación;
- commit PostgreSQL;
- duración y throughput total del ciclo.

Las métricas deberían incluir percentiles p50, p95 y p99, no solamente el
promedio del último ciclo.

El dashboard también debería calcular la estimación de drenaje usando el
throughput observado. Multiplicar páginas pendientes por el tiempo de sleep no
representa correctamente ciclos largos.

## 9. Plan de evaluación gradual

### Fase 0 — Línea base

1. Ejecutar una carga representativa con el código actual.
2. Registrar throughput, latencias por etapa y recursos.
3. Repetir con un nodo IPFS detenido.
4. Verificar ausencia de errores y disponibilidad de resolución.

### Fase 1 — Pooling

1. Reutilizar clientes HTTP sin cambiar concurrencia.
2. Repetir la misma carga.
3. Comparar latencias y conexiones.

### Fase 2 — Concurrencia

1. Activar concurrencia 2.
2. Probar concurrencia 4.
3. Evaluar 8 solamente si los recursos tienen margen.
4. Mantener Level 2 → Level 1 dentro de cada ARK.

### Fase 3 — Retención y reconciliación

1. Conservar payload después del quorum mínimo.
2. Verificar ambos CIDs en segundo plano.
3. Purgar solamente al alcanzar el objetivo.
4. Simular caída y recuperación de un peer.
5. Definir alertas y política de retención máxima.

## 10. Criterios de aceptación

- Cero pérdida de metadata durante pruebas de falla controlada.
- Cero ARKs publicados con CIDs inexistentes en el quorum mínimo.
- Level 1 siempre referencia correctamente el CID de Level 2.
- Los reintentos no producen resultados inconsistentes.
- Un peer caído no detiene el minting cuando el quorum mínimo es 1.
- Al recuperar el segundo peer, los CIDs pendientes alcanzan dos réplicas.
- El payload local solamente se purga después de la política definida.
- El throughput mejora de manera medible frente a la línea base.
- CPU, memoria, disco, red y pool PostgreSQL permanecen dentro de límites
  operativos acordados.
- El cambio puede desactivarse mediante configuración y rollback de imagen.

## 11. Preguntas abiertas

1. ¿El Chain Worker debe publicar con un peer confirmado o esperar dos?
2. ¿Cuánto tiempo puede conservarse el payload en PostgreSQL?
3. ¿Cuál es el crecimiento esperado de PostgreSQL durante una caída larga?
4. ¿El reconciliador debe solamente verificar o también volver a ejecutar el
   `add` cuando un CID no tiene ninguna copia?
5. ¿La réplica objetivo es dos peers locales o incluye sedes remotas?
6. ¿Cuál es el throughput mínimo requerido para producción?
7. ¿Qué límites de CPU, IOPS y red tienen los servidores IPFS actuales?
8. ¿Se necesita preservar el orden global de ARKs o solamente el orden Level 2
   → Level 1 por ARK?
9. ¿Cómo se mostrarán `replication_pending`, antigüedad y errores en el
   dashboard?

## 12. Recomendación para el próximo paso

No implementar todavía el flujo asíncrono completo.

El siguiente experimento debería limitarse a instrumentación, reutilización de
conexiones y concurrencia configurable, comenzando con 2 y luego 4. Con esos
resultados se podrá determinar si la reconciliación asíncrona es necesaria o si
el cuello de botella queda resuelto sin cambiar la semántica de persistencia.

La decisión sobre publicar con una o dos réplicas debe tomarse explícitamente
antes de implementar la fase de retención y reconciliación.
