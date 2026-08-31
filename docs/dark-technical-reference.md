# dARK: referencia técnica de arquitectura, APIs y operación

> Estado: síntesis de la arquitectura y operación implementadas en dark-deployer.
> Está dirigida a un equipo técnico que llega al proyecto por primera vez.

## 1. Propósito

dARK gestiona identificadores ARK para recursos digitales. Un ARK enlaza una URL
de resolución con metadatos inmutables fuera de la cadena, identificados por un
CID IPFS. La blockchain conserva identidad, autorización, URL y CID; IPFS
conserva los bytes.

Un ARK tiene la forma ark:/NAAN/name o ark:NAAN/name. NAAN identifica a la
entidad que asigna nombres y name al recurso dentro de ella.

| Capa | Responsabilidad |
| --- | --- |
| Blockchain | Autoridades, permisos por NAAN y ARKs publicados. |
| Apps | APIs, minter, workers, PostgreSQL y Store API. |
| Storage | Un clúster IPFS Cluster global que fija y replica contenido. |
| Deployer | Inventario, validación, instalación y operación de los hosts. |

## 2. Arquitectura

~~~mermaid
flowchart TB
    Client[Cliente institucional / notebook]
    Admin[Admin API :8000]
    Minter[Minter API + Worker :8001]
    Resolver[Resolver API :8002]
    Store[Store API :8003]
    DB[(PostgreSQL)]
    Chain[Besu + Authority + dARK]
    Client --> Admin
    Client --> Minter
    Client --> Resolver
    Minter <--> DB
    Minter --> Store
    Minter --> Chain
    Admin --> Chain
    Resolver --> Chain
    Resolver --> Store
    subgraph IPFS[Un clúster IPFS Cluster global CRDT]
      A1[Site A storage-1]
      A2[Site A storage-2]
      B1[Site B storage-1]
      B2[Site B storage-2]
      A1 --- A2
      A1 --- B1
      A2 --- B2
      B1 --- B2
    end
    Store --> IPFS
~~~

Store API de una sede sólo usa sus dos nodos IPFS locales. No se comunica con
Store APIs remotas; la propagación entre sedes ocurre dentro del clúster IPFS
por la VPN privada.

## 3. Sedes y layout físico

### Primera sede de producción

La forma productiva actual tiene cuatro servidores en una VPN/LAN privada:

| Host | Rol | Servicios |
| --- | --- | --- |
| site-a-blockchain-1 | blockchain | Besu, contratos y explorador. |
| site-a-apps-1 | apps | Admin, Minter, Worker, Resolver, Store API y dashboard. |
| site-a-storage-1 | storage-node | Un Kubo y un peer IPFS Cluster. |
| site-a-storage-2 | storage-node | Un Kubo y un peer IPFS Cluster. |

El inventario de ejemplo usa 10.20.30.10, .20, .31 y .32; son valores de
documentación, no direcciones para copiar a otro entorno.

~~~text
                     VPN / LAN privada

 Clientes ──> Apps ─────────────> Blockchain
                │
                ├───────────────> storage-1: Kubo + Cluster
                └───────────────> storage-2: Kubo + Cluster
~~~

Este diseño tolera la caída de un servidor IPFS. Blockchain, Apps y la sede
completa siguen siendo puntos únicos de fallo en la primera versión.

### Expansión multisede

Hay un único clúster CRDT global, no un clúster por sede:

~~~text
Un clúster global
├── Site A: dos storage nodes; Apps A opcional
├── Site B: dos storage nodes; Apps B opcional
└── Site N: dos storage nodes; Apps N opcional
~~~

Una sede es una etiqueta operativa y un dominio de fallo, no un subclúster.
Cada peer tiene una identidad persistente y única. Los peers locales se conectan
directamente; los peers de sedes distintas lo hacen por VPN.

Con dos sedes los datos pueden sobrevivir a la pérdida de una sede, pero los
payloads se conservan hasta que exista una réplica remota. Con tres o más sedes,
el almacenamiento puede seguir replicando tras perder una sede completa. Varios
Minters usando la misma cuenta blockchain necesitan una solución adicional de
signer o coordinación de nonces.

## 4. Componentes

| Componente | Puerto | Función | Estado persistente |
| --- | ---: | --- | --- |
| dark-core-admin-api | 8000 | Gestión de autoridades, wallets y NAANs. | Blockchain. |
| dark-core-minter-api | 8001 | Reserva/actualiza ARKs y expone API. | PostgreSQL. |
| Metadata Worker | interno | Persiste L1/L2, reconcilia y repara. | PostgreSQL. |
| Chain Worker | interno | Publica CIDs y URL en contratos. | PostgreSQL + blockchain. |
| dark-core-resolver-api | 8002 | Resuelve ARKs y entrega metadata. | Sin base local. |
| dark-store-api | 8003 | Fachada HTTP frente a IPFS/Cluster. | Sin base propia. |
| Kubo | 5001 admin; 4001 swarm | Bloques IPFS, lectura e importación. | Volúmenes IPFS. |
| IPFS Cluster | 9094/9095/9096 | Pinset CRDT, asignación y réplica. | Volúmenes Cluster. |
| dark-core-lib | biblioteca | SDK blockchain y abstracción de metadata. | Ninguno. |
| Dashboard | 8081 | Administración y visibilidad operativa. | Servicios asociados. |

Minter y Resolver usan MetadataService de dark-core-lib; no dependen
directamente de Kubo ni de IPFS Cluster.

## 5. Blockchain y autorización

| Contrato | Responsabilidad |
| --- | --- |
| Authority.sol | Registra UUID, wallet, estado activo y NAANs autorizados. |
| dARK.sol | Conserva naan, name, URL, CID, owner y timestamps del ARK. |
| IAuthority.sol | Interfaz por la que dARK.sol delega permisos. |

Un ARK se indexa por keccak256(naan + "/" + name). Para crearlo, la wallet debe
pertenecer a una autoridad activa autorizada para el NAAN. Para actualizarlo,
debe ser además la wallet propietaria original.

La primera versión productiva usa un signer de plataforma provisionado fuera de
Git. Se instala desde un archivo de secreto en Blockchain y Apps; el bundle y
el handoff contienen únicamente su dirección pública.

## 6. Ciclo de vida de un ARK

~~~mermaid
stateDiagram-v2
    [*] --> reserved: POST /api/v1/arks
    reserved --> draft: PUT metadata
    published --> update: PUT metadata
    draft --> published: L2 + L1 persistidos y transacción confirmada
    update --> published: actualización confirmada
    reserved --> tombstone: DELETE
    draft --> tombstone: DELETE
    published --> tombstone: DELETE
    update --> tombstone: DELETE
~~~

| Estado | Código | Significado |
| --- | --- | --- |
| reserved | R | Identificador NOID reservado, sin metadata. |
| draft | D | Metadata recibida; espera IPFS y creación on-chain. |
| update | U | Cambios de un ARK publicado, pendientes on-chain. |
| published | P | URL y CID L1 publicados en blockchain. |
| tombstone | T | Baja lógica irreversible. |

El orden de persistencia es L2 → L1:

1. El Worker guarda el original como Level 2 y confirma un peer PINNED.
2. Construye Level 1, que contiene el CID L2, y confirma un peer PINNED.
3. Escribe ambos CIDs en PostgreSQL y habilita al Chain Worker.
4. El Chain Worker publica L1 en dARK.sol mediante create_ark o update_ark.
5. El Metadata Worker reconcilia réplicas y sólo entonces purga los payloads.

El CID de blockchain siempre apunta a L1; Resolver sigue el vínculo L1 → L2
para devolver el registro original.

Los identificadores DARK 2 nuevos usan shoulders de tres dígitos 2MM, por
ejemplo 200 o 201. El espacio 00* se reserva para DARK 1; el sufijo MM debe ser
único por Minter dentro de un mismo NAAN.

## 7. Metadata, IPFS y réplicas

| Nivel | Contenido | Referencia |
| --- | --- | --- |
| L1 | JSON dARK normalizado y vínculo a L2. | CID publicado en blockchain. |
| L2 | Registro original XML, JSON o texto y su Content-Type. | CID dentro de L1. |

Un CID identifica contenido, no disponibilidad. IPFS Cluster coordina pins para
obtener durabilidad.

| Topología | Cluster min/max | Éxito de escritura | Meta de purga para L1 y L2 |
| --- | --- | --- | --- |
| Developer simple (un peer) | 1 / 1 | 1 PINNED | 1 copia local. |
| Una sede (dos peers) | 1 / 2 | 1 PINNED | 2 copias locales. |
| Dos o más sedes | 3 / 2 × sedes | 1 PINNED | 2 locales + 1 remota. |

Sólo PINNED cuenta como copia durable. PINNING, PIN_ERROR, una asignación o un
peer visible no cuentan como réplica.

No se bloquea minting hasta completar toda la replicación. PostgreSQL retiene
L1/L2 como fuente de reparación hasta que ambos CIDs cumplen el objetivo.
ark_metadata sólo conserva el último conteo L1/L2, fecha de comprobación y
último error; no hay una tabla de distribución por sede.

| Estado derivado | Criterio |
| --- | --- |
| pending | Payload retenido, sin error y con conteos positivos. |
| degraded | Payload retenido y algún CID sin copias. |
| error | Payload retenido y error de consulta/reparación. |
| complete | Payloads purgados tras cumplir ambas metas. |

El Metadata Worker reconcilia sin trabajo nuevo y también durante carga. Procesa
hasta 50 ARKs por lote y usa METADATA_WORKER_CONCURRENCY (por defecto 4). Un CID
con cero copias se reconstruye desde PostgreSQL; si produce otro CID, se rechaza.

Store API usa round-robin separado para Kubo, Cluster REST y Cluster Proxy
locales. Timeout, conexión rechazada, 429 y 5xx provocan un cooldown de 30
segundos. Así, con dos nodos el patrón es storage-1 → storage-2 → storage-1;
si uno falla, usa el otro y minting continúa si se puede confirmar un pin.

## 8. APIs

Las APIs de aplicaciones están bajo /api/v1; Store API usa /v1.

### Admin API — http(s)://<apps>:8000/api/v1

| Método | Ruta | Uso |
| --- | --- | --- |
| POST | /admin/authority | Registrar autoridad y NAANs. |
| GET | /admin/authority/{uuid} | Consultar autoridad. |
| POST | /admin/authority/{uuid}/authorize-naan | Autorizar NAAN. |
| POST | /admin/authority/{uuid}/revoke-naan | Revocar NAAN. |
| POST | /admin/authority/{uuid}/deactivate | Desactivar autoridad. |
| POST | /admin/authority/{uuid}/fund | Financiar wallet. |
| GET | /admin/status | Estado de cadena y saldo admin. |
| GET | /arks/count, /arks/recent | Estadísticas de ARKs. |

### Minter API — http(s)://<apps>:8001/api/v1

| Método | Ruta | Uso |
| --- | --- | --- |
| POST | /arks | Reservar ARK. |
| POST | /arks/batch | Reservar un lote. |
| GET | /arks/{ark} | Consultar estado local/on-chain. |
| PUT | /arks/{ark} | Enviar URL y metadata; crea draft/update. |
| DELETE | /arks/{ark} | Tombstone. |
| GET | /authority/{uuid} y subrutas | Consulta de autoridad y NAAN. |
| GET | /worker/status | Workers, cola y reconciliación. |
| GET | /worker/replication | CIDs retenidos y últimos conteos. |
| GET/POST | /worker/errors, /worker/errors/rescue | Errores y rescate operacional. |

### Resolver API — http(s)://<apps>:8002/api/v1

| Solicitud | Resultado |
| --- | --- |
| GET /arks/{ark} | Redirección al target. |
| HEAD /arks/{ark} | Redirección sin cuerpo. |
| GET /arks/{ark}?info | Datos on-chain y L1 normalizado. |
| GET /arks/{ark}?metadata | Bytes L2 y Content-Type original. |

### Store API — http://<apps>:8003

| Método | Ruta | Uso |
| --- | --- | --- |
| POST | /v1/store | Guarda bytes y devuelve CID/tamaño/réplicas. |
| GET | /v1/retrieve/{cid} | Recupera bytes. |
| GET | /v1/status/{cid} | Réplicas total/local/remota, sedes y meta de purga. |
| GET | /health/live | Proceso vivo. |
| GET | /health/read | Al menos un Kubo local operativo. |
| GET | /health/write | Ruta local de escritura y peer Cluster conocido. |
| GET | /health | Alias de compatibilidad de write readiness. |

~~~json
{
  "cid": "bafy...",
  "replication": {
    "total_replicas": 3,
    "local_replicas": 2,
    "remote_replicas": 1,
    "sites": {"site-a": 2, "site-b": 1},
    "purge_target_met": true,
    "checked_at": "2026-08-21T12:00:00Z"
  }
}
~~~

## 9. Seguridad y red

| Servicio | Exposición esperada |
| --- | --- |
| Admin | VPN; nunca Internet público. |
| Minter | Sólo clientes autorizados en la VPN. |
| Resolver | Puede exponerse mediante proxy. |
| Store API | Interno y operadores aprobados. |
| Kubo 5001 / Cluster 9094-9095 | Apps, peers y operadores VPN. |
| Kubo 4001 / Cluster 9096 | Exclusivamente entre peers VPN. |
| PostgreSQL, MySQL y Redis | Loopback/red Docker; nunca VPN. |

Kubo usa una clave privada de swarm común y Cluster un secreto distinto,
compartido por todos los peers. Ambos se distribuyen fuera de Git con permisos
0600. La posesión del secreto Cluster permite participar en el clúster: VPN y
gestión de secretos son necesarias.

Cuando MTLS_ENABLED=false, VPN y firewall forman el límite de confianza. El
diseño de claves Ed25519 firmadas por clientes de Minter es una propuesta; no se
debe asumir habilitado.

## 10. Despliegue e inventario

| Perfil | Uso |
| --- | --- |
| developer | Stack local; uno o dos peers en el mismo host. |
| sandbox | Infraestructura desacoplada de prueba; dos peers por sede. |
| production | Bundles de hosts; dos peers por sede. |

| Rol | Instala |
| --- | --- |
| blockchain | Besu, contratos y explorador. |
| apps | Librería, APIs, Workers, Store y Dashboard. |
| storage-node | Un Kubo y un peer Cluster. |
| all | Los tres roles; sólo escenarios restringidos/no productivos. |

El inventario de producción reúne CIDR VPN, clientes Minter permitidos, rutas
de secretos, sitios, hosts, DNS, direcciones VPN, roles y URLs anunciadas.

~~~bash
python3.12 install.py deployment validate --inventory deployment-inventory.json
python3.12 install.py deployment render \
  --inventory deployment-inventory.json \
  --output dist/dark-site-a-1
python3.12 install.py host validate --config dist/dark-site-a-1/hosts/<host>/host.json
python3.12 install.py host plan --config dist/dark-site-a-1/hosts/<host>/host.json
python3.12 install.py host apply --config dist/dark-site-a-1/hosts/<host>/host.json
~~~

El bundle contiene topología, endpoints, política de firewall, hashes, archivos
por host y checklist; nunca valores de secretos.

### Selección de versiones

La instalación actual selecciona deployer y componentes por ramas en .env:

~~~ini
DEPLOYER_BRANCH=codex/production-deployment-readiness
PRODUCTION_MINTER_REPOSITORY_BRANCH=codex/production-deployment-readiness
PRODUCTION_IPFS_REPOSITORY_BRANCH=codex/global-ipfs-cluster
~~~

Producción exige que el checkout del deployer coincida con DEPLOYER_BRANCH. El
instalador hace fetch, cambio de rama y merge --ff-only. Si la rama no existe en
un remoto accesible, avisa y usa main; no hace fallback ante errores de conexión
o autenticación.

No existe components.lock.json ni checkout por commits detached. Por tanto, una
rama que avance puede cambiar el código de una instalación futura. Para rollouts
controlados deben registrarse los hashes exactos realmente desplegados.

## 11. Operación y validación

1. Provisionar hosts, Docker, Python 3.10+ (preferible 3.12), DNS, NTP, VPN y
   firewall.
2. Crear/validar inventario; transferir secretos sólo a los hosts necesarios.
3. Aplicar Blockchain y copiar su .env.integration pública a Apps.
4. Aplicar storage-1 y después storage-2; ejecutar auditoría.
5. Aplicar Apps y validar autoridad, minting, resolución e IPFS E2E.
6. Detener cada storage node por separado; minting/resolución deben continuar.
   Recuperarlo y reconciliar.

~~~bash
# Auditoría sin modificar pins
python3 install.py storage audit

# Reaplica política a pins existentes; nunca elimina pins
python3 install.py storage reconcile

# Validación y plan sin mutar el host
python3 install.py validate
python3 install.py plan

# Reanudar desde la primera etapa pendiente
python3 install.py resume --from store-api
~~~

Ejecutar storage reconcile tras recuperar peers/sedes, añadir una sede o cambiar
factores de réplica. No automatiza unpin ni eliminación de volúmenes.

## 12. Fallos, alertas y límites

| Evento | Lecturas | Minting | Acción |
| --- | --- | --- | --- |
| Cae un storage local | Continúan por el peer local. | Continúa tras confirmar un pin. | Recuperar y reconciliar. |
| Cae una sede remota | Continúan localmente. | Puede continuar; purga espera réplica. | Recuperar VPN/sede. |
| Sede aislada de VPN | Lecturas locales. | Continúa localmente; no logra meta remota. | Conservar payloads. |
| Cae Store API | Apps de esa sede no sirve storage. | Indisponible desde esa sede. | Usar otra Apps si existe. |
| Cae Apps/Blockchain únicos | Indisponible. | Indisponible. | Recuperar host; no hay HA V1. |

Alertar por peers/sedes bajo objetivo, PIN_ERROR, pins bajo réplica máxima,
disco Kubo bajo 20 %, peer esperado ausente, tiempo de primer pin, réplica
completa y backlog/errores del reconciliador.

Replicación no es backup contra un unpin autorizado o pérdida global. Se
necesitan copias de secretos/configuración, inventario de pins y pruebas de
recuperación; exportaciones CAR offline son una opción.

## 13. Operaciones especiales y documentación de detalle

La importación CSV directa crea ARKs históricos en blockchain sin API HTTP,
PostgreSQL ni Store API. Usa URL y CID vacío. Primero se auditan NAANs y luego
se ejecuta desde el host/contenedor autorizado de Minter:

~~~bash
python -m app.cli.check_naans /input/registros.csv --authority-id <UUID>
python -m app.cli.import_arks /input/registros.csv --authority-id <UUID> --execute
~~~

| Documento | Uso |
| --- | --- |
| [Referencia de API](../DARK_2.0_API_REFERENCE.md) | Esquemas, códigos HTTP y ejemplos completos. |
| [Arquitectura IPFS](ipfs-architecture.md) | CRDT, puertos, bootstrap, fallos y expansión. |
| [Implementación de replicación](ipfs-replication-implementation.md) | Retención PostgreSQL, reconciliación y purga. |
| [Bundles de producción](production-deployment-bundles.md) | Inventario, secretos y aceptación. |
| [Instalación de cuatro servidores](production-single-site-four-server-installation.md) | Worksheet de red y firewall. |
| [Operación del deployer](deployer-operations.md) | Rebuild, resume y comportamiento Git. |
| [Importación directa](minter-direct-ark-import.md) | Migraciones CSV. |
| [Política de shoulders](minter-shoulder-policy.md) | Asignación de nombres DARK 2. |

Los documentos de despliegue remoto por SSH y de claves firmadas Ed25519 son
propuestas de evolución, no capacidades que una instalación actual deba asumir.
Si hay discrepancias históricas sobre persistencia de metadata, prevalece
[Implementación de replicación](ipfs-replication-implementation.md), que
describe el comportamiento vigente de Store API, Minter, Resolver y deployer.

