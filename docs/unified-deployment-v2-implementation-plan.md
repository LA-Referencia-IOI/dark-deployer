# Plan de implementación del despliegue unificado v2

Estado: especificación para implementar; no describe capacidades ya disponibles.
Fecha: 2026-09-09. Base: [análisis del instalador](installer-local-remote-unification-analysis.md).

## Estado de implementación

La primera fase está implementada: `deploy.py` y `deployment_v2/` validan el
inventario v2, resuelven grupos/máquinas, asignan subredes Docker de forma
determinista, derivan endpoints y generan un plan y bundle público. También
existe `preflight` de solo lectura con executor local/SSH. Los ejemplos local
simple, HA local y cinco hosts validan en las pruebas automatizadas. Todavía
no ejecuta una instalación validada de extremo a extremo; las secciones 5 a 12
siguen siendo trabajo pendiente y son la fuente de verdad para continuar.

La segunda fase está iniciada: el bundle contiene `compose.yaml` independiente
por grupo, entornos públicos, configuración de endpoints Store API y los
entrypoints/variables derivados para Kubo y Cluster. `apply` crea una red
bridge por máquina, prepara rutas, transfiere fuentes públicas/bundle por SSH y
ejecuta Compose, guardando un journal. En local crea un workspace de staging
bajo `.generated/deployment-v2/<id>/local/`, por lo que no escribe las rutas de
producción declaradas en el inventario. `chain-bootstrap` genera contexto QBFT
público y `chain-static-nodes` deriva enodes desde las claves públicas
generadas fuera del repositorio. Los secretos se definen por referencia
relativa a `secrets_root`, nunca se incorporan al bundle.

Antes de aplicar cada grupo, el runner ejecuta el preflight de Docker/rutas y
comprueba que sean legibles los secretos declarados para el tipo de grupo. Una
ejecución local usa también `data_root` y `secrets_root` de staging; por ello el
operador debe provisionar explícitamente los archivos de prueba bajo esa ruta
antes de invocar `apply`. Esta validación evita que un Compose parcialmente
configurado llegue a arrancar.

Solo pasó pruebas estructurales y `docker compose config`; no usarlo aún para
reemplazar la instalación actual. Faltan la generación e importación completa
de identidades/genesis Besu, jobs de contratos/migraciones, health checks,
reanudación y una prueba Docker real.

## 1. Resultado obligatorio y límites

Construir un único motor que lea `deployment-topology.json`, resuelva dónde
vive cada grupo de servicios, genere configuración y ejecute Docker Compose
en el destino correspondiente. Local, LAN y VPN usan el mismo plan y el mismo
runner. El transporte cambia; la definición de servicios no.

Implementar primero una entrada independiente `deploy.py` y paquete `deployment_v2/`.
No invocar `install.py` desde el nuevo runner. Mantener el sistema actual durante
la validación; retirarlo del camino operativo cuando se cumpla la sección 12.
No admitir inventarios v1 ni convertirlos silenciosamente. No resetear datos
existentes para demostrar instalación limpia: usar otro deployment ID y rutas.

Mantener cuatro validadores QBFT y un RPC no validador, tres workers del minter,
publicación tras primer pin y durabilidad posterior. Este trabajo no cambia
algoritmos del minter, contratos ni política de replicación.

No implementar un scheduler, daemon, base de datos de control, Kubernetes ni
red overlay multi-host. No instalar ni configurar una VPN. LAN/VPN son redes
existentes que el inventario describe y el preflight verifica.

## 2. Evidencia que motiva el cambio

| Fuente actual | Evidencia concreta | Consecuencia para v2 |
| --- | --- | --- |
| `install.py:central_compose()` | Elige `apps-production.yml` leyendo `TYPE`; ejecuta strings shell. | La selección debe depender del plan de grupos y transporte; usar argumentos. |
| `install.py:_prepare_developer_storage_assets()` | Genera storage simple/HA sin usar hosts del inventario productivo. | Un mismo resolver de nodos para todos los ejemplos. |
| `dark_deployer/deployment.py:deployment_delivery_plan()` | Produce destinos SSH desde bundles. | Añadir local y agrupar destinos físicos; conservar transporte separado del runner. |
| `dark_deployer/deployment.py:verify_deployment_bundle()` | Valida archivos sin contactar hosts. | Separar integridad de bundle y verificación operativa. |
| `compose/host.yml` | Declara un include general, pero existen entradas diferenciadas por perfil. | No asumir que ese archivo ya unifica la ejecución. |
| `blockchain/compose.yml` | IPs `172.31.0.11`, `.12`, `.21`, etc., nombres globales y configuración compartida. | Generar direcciones y configuración por instancia. |
| `compose/components/storage/ipfs.yml` | Dos redes, aliases y montajes relativos a scripts en componentes. | Renderizar una pareja por nodo, con rutas del destino y bootstrap derivado. |
| `compose/components/services/minter.yml` | Lee `.env.integration` dentro del checkout, puerto PostgreSQL fijo y redes adicionales. | Generar entornos dentro del bundle y no publicar DB por defecto. |
| `.env.example` | Ramas, comandos y endpoints repetidos por prefijo, además de tuning. | Declarar propietarios de cada valor y eliminar precedencias implícitas. |

Estas observaciones proceden de lectura de código. No equivalen a una prueba
actual de instalación remota. El agente debe verificar los nombres de funciones
al comenzar por si el checkout ha avanzado.

## 3. Modelo: máquinas, grupos y nodos

Una **máquina** representa un sistema operativo y su daemon Docker. Un
**grupo** representa una unidad Compose. Un **nodo** representa una instancia
Besu o una pareja Kubo/Cluster. No usar host para ambos conceptos.

Grupos iniciales: `apps`, `validators-a`, `validators-b`, `storage-a`, `storage-b`.
Apps contiene RPC, APIs, workers, PostgreSQL y dashboard con sus dependencias.
Validators-a contiene dos validadores y explorer; validators-b dos validadores.
Cada storage contiene un Kubo y un Cluster.

Local HA: los cinco grupos referencian una máquina. Producción: cada grupo
referencia su máquina. Local simple: mismo blockchain, apps y un grupo storage,
objetivo de réplica uno. No crear ramas de código por nombre de entorno.

Un proyecto Compose por grupo; una red bridge por máquina para este despliegue.
Cuando grupos comparten máquina comparten esa red y aliases únicos. Cuando
viven en máquinas distintas se comunican por puertos publicados en LAN/VPN.
Esto simula colocación local, no aislamiento físico ni tolerancia a fallos de
cinco servidores. No agregar redes por cada componente para reproducir historia.

## 4. Inventario v2: contrato a implementar

Crear JSON Schema con `additionalProperties: false`, referencias verificadas y
errores con ruta JSON. Usar Python 3.12, `dataclasses`, `ipaddress`, `jsonschema`
y PyYAML para serialización. No implementar un lenguaje de interpolación.

Los bloques raíz obligatorios son `version`, `deployment`, `defaults`,
`machines`, `groups`, `networks`, `blockchain`, `storage`, `components`,
`settings`, `secrets` y `exposure`. `version` debe ser 2.

### 4.1 Identidad, defaults y referencias

- `deployment.id`: slug estable; se usa en nombres de proyectos, redes y rutas.
- `deployment.label`: texto informativo; nunca controla comportamiento.
- `defaults.ssh`: usuario, puerto, ruta de clave en el controlador y known_hosts.
- `defaults.paths`: workspace, data y secrets **del destino**. Las relativas
  se resuelven respecto del directorio del inventario solo para máquina local.
  SSH exige absolutas. Cada máquina puede sobrescribir campos individuales.
- `defaults.docker`: contexto permitido y pool CIDR para bridges, tamaño de
  subred por máquina; no aceptar un DOCKER_HOST heredado como override oculto.
- `components.default_branch`: `main`; cada repositorio declara URL y solo
  declara branch si necesita otra. No introducir refs de commit para seleccionar
  versiones; registrar hashes resueltos como evidencia de lo aplicado.

Las referencias son IDs, por ejemplo `machine: linux-a`, `network: private`,
`ssh_profile: default`. No repetir IPs en storage, enodes o URLs.
Precedencia: override explícito de entidad → defaults del inventario → default
versionado del schema. `.env` y variables del shell no sobrescriben infraestructura.

### 4.2 Máquinas y selección del transporte

`machines` es un objeto indexado por ID. Campos: `execution` (`local`, `ssh`,
`auto`), `management` (address y override SSH), `addresses` (network ID → IP),
`paths`, `docker` (contexto local y CIDR bridge opcionales).

Recomendar `local` para Docker Desktop, `ssh` para servidores y `auto` cuando
el mismo inventario se ejecuta desde uno de los servidores. `auto` resuelve
direcciones de gestión y compara con interfaces del controlador: si coincide
sin ambigüedad, local; si no, SSH. DNS con resultados locales y remotos mezclados
produce error. `localhost` no sirve como destino remoto. Nunca decidir por el
nombre developer/production, ni pasar a local porque SSH falló.

Preflight confirma el daemon elegido y su identidad. Docker CLI disponible no
demuestra que el daemon sea local: rechazar contextos TCP/SSH en ejecución local;
aceptar socket Docker Desktop/local explícito. En destino SSH ejecutar contra
socket local. Registrar daemon ID; dos máquinas declaradas que apuntan al mismo
daemon deben rechazarse y consolidarse en una sola máquina.

### 4.3 Redes y direcciones

`networks.private` declara `kind: lan|vpn`, CIDR y, opcionalmente, nombre de
interfaz esperado. Gestión SSH puede usar otra dirección, incluso pública.
El tráfico entre servicios usa `machines[*].addresses.private`, no management.

`defaults.docker.subnet_pool` y `subnet_prefix` determinan las subredes Docker;
override `machines[*].docker.subnet` permite una subred explícita. Asignar por
orden de IDs de máquina y persistir asignación en el plan. Una incorporación que
cambie una subred ya aplicada debe bloquearse hasta que el operador explicite
esa subred en el inventario. No renumerar redes con contenedores existentes.

Validar IP dentro de CIDR, duplicados por dominio de red, subredes bridge frente
a LAN/VPN/rutas conocidas y colisiones con redes Docker existentes. No crear la
red externa ni asignar IP al sistema operativo. En Linux verificar interfaces;
en Desktop el bridge vive dentro de la VM y no es una IP del host macOS.

Resolver cada conexión con una función única:

1. Mismo daemon/red: alias único de servicio + puerto interno.
2. Daemon distinto: IP private del proveedor + puerto publicado.
3. Probe del controlador: endpoint publicado alcanzable, o comando de probe
   ejecutado en destino si no existe ruta directa.

Besu local compartido necesita enodes alcanzables: reservar IPs bridge por ID
de nodo en el plan y anunciar esas IPs. Entre máquinas, anunciar IP private y
puerto P2P por nodo. No repetir estas IPs en el inventario; se derivan.
Kubo/Cluster local puede anunciar aliases DNS exclusivos; remoto anuncia IP
private. Derivar bootstrap de la misma tabla de endpoints. Nunca anunciar
`0.0.0.0`, loopback ni una IP bridge de otro daemon.

Puertos internos en catálogo de servicio; publicar únicamente comunicaciones
inter-host y accesos solicitados en `exposure`. Asignar P2P Besu desde base
configurada + índice de nodo ordenado, con override por nodo si se requiere.
Detectar colisiones por máquina, bind IP y protocolo, incluyendo wildcard.
Dos nodos storage en una máquina usan aliases distintos; publicar APIs solo
si se necesitan probes externos y darles puertos únicos derivados.

### 4.4 Grupos, blockchain, storage y tuning

- `groups[id]`: `kind: apps|validators|storage`, `machine` y `members` (IDs de
  nodos); `explorer: true` solo para un grupo validators.
- `blockchain`: chain ID, imagen Besu, parámetros QBFT actuales, nodos
  `{id, validator}` y referencia al artefacto seguro de cadena. Grupo y máquina
  se obtienen de `members`. RPC debe pertenecer a apps.
- `storage`: cluster name, lista de node IDs, política
  `{publish_after_replicas: 1, target_replicas: 2}` y referencias a secretos.
  Endpoints derivan del placement. No duplicar addresses ni peer IDs libp2p.
- `settings`: tuning de minter, Store, IPFS y dashboard. Migrar aquí los valores
  operativos elegidos de `.env.example` sin alterar sus valores durante el corte.
  Schema tipado; generar solo las variables que consume cada servicio.
- `secrets`: ID → ruta relativa al secrets root del destino, consumidores y
  formato esperado. La clave SSH reside en el controlador y pertenece a SSH,
  no a esta lista de secretos de aplicaciones.
- `exposure`: accesos dashboard/APIs/explorer, bind address por referencia de
  máquina/red y clientes autorizados. No publicar DB ni Redis por defecto.

Ejemplo de placement HA local (fragmento, no inventario ejecutable):

```json
{
  "machines": {"local": {"execution": "local"}},
  "groups": {
    "apps": {"kind": "apps", "machine": "local", "members": ["rpc01"]},
    "validators-a": {"kind": "validators", "machine": "local", "members": ["validator01", "validator02"], "explorer": true},
    "validators-b": {"kind": "validators", "machine": "local", "members": ["validator03", "validator04"]},
    "storage-a": {"kind": "storage", "machine": "local", "members": ["storage-a"]},
    "storage-b": {"kind": "storage", "machine": "local", "members": ["storage-b"]}
  }
}
```

Producir tres ejemplos completos validados: local simple, local HA y cinco
máquinas LAN/VPN. Para distribuir el ejemplo HA cambian machines, referencias
machine de grupos, redes externas y rutas; no definiciones de contenedores.

## 5. Código y archivos nuevos

| Archivo nuevo | Responsabilidad y contrato |
| --- | --- |
| `deploy.py` | CLI fina; llama funciones, sin lógica por perfil. |
| `deployment_v2/schema.json`, `inventory.py` | Validación, defaults y referencias; devuelve Inventory normalizado. |
| `deployment_v2/model.py` | Inventory, Machine, Group, Endpoint, Step, Plan, Result. |
| `deployment_v2/network.py` | IPAM, matriz de conexiones, puertos, bind/advertise; función pura. |
| `deployment_v2/planner.py` | Plan con pasos y dependencias explícitas, sin efectos. |
| `deployment_v2/render.py` | YAML Compose, entornos, configs y manifiesto público por grupo/máquina. |
| `deployment_v2/services.py` | Catálogo de servicios, dependencias, build contexts, variables y probes. |
| `deployment_v2/executor.py` | LocalExecutor/SshExecutor: run(argv), transfer(bundle), probe(); mismo Result. |
| `deployment_v2/runner.py` | Ejecuta un paso en el destino; no lee inventario ni entorno legado. |
| `deployment_v2/artifacts.py` | Genesis/identidades/contratos, validación y handoff público. |
| `deployment_v2/state.py` | Journal JSONL y estado atómico por ejecución; lock por despliegue/daemon. |
| `deployment_v2/verify.py` | Verificación real y reanudación basada en evidencia. |
| `tests/deployment_v2/` | Fixtures, unitarias, integración local y SSH. |

No crear un módulo por entorno. Reutilizar funciones puras de files/process
solo tras revisar manejo de secretos; portar generación de configuración útil
de `install.py`. No importar el monolito para reutilizar funciones con globals.

El renderer genera un Compose concreto por grupo, sin includes que apunten al
checkout del controlador. Los fragmentos actuales sirven de fuente de portado:
preservar commands, healthchecks, builds, permisos y volúmenes. Eliminar
`container_name` global; usar nombres Compose y aliases únicos por servicio.
Red externa al proyecto, creada una vez por máquina por el runner y etiquetada
con deployment ID. Cada proyecto la referencia con `external: true`.

Conservar bind mounts Besu/IPFS y semántica de almacenamiento actual de bases
de datos; no convertir volúmenes DB como parte de este trabajo. Todas las rutas
de bind se resuelven en destino. No montar archivos generados del controlador.

## 6. CLI y flujo de entrega

Comandos obligatorios:

```text
deploy.py validate --inventory FILE
deploy.py plan --inventory FILE [--json]
deploy.py preflight --inventory FILE
deploy.py render --inventory FILE --output DIR
deploy.py apply --inventory FILE
deploy.py resume --run RUN_ID
deploy.py verify --inventory FILE
deploy.py status --inventory FILE
deploy.py secrets init --inventory FILE --output SECURE_DIR
deploy.py chain init --inventory FILE --output SECURE_DIR
deploy.py chain export --inventory FILE --machine ID --output SECURE_DIR
```

Validate/plan solo leen archivos. Render escribe únicamente output explícito.
Apply ejecuta preflight, prepara fuentes, genera bundle y llama al runner local
o SSH. No requerir checkout preinstalado en servidores: el controlador entrega
runner y fuentes públicas necesarias usando rsync con lista de inclusión.
Excluir `.git`, `.env`, venv, node_modules, datos, secretos y caches. Resolver
ramas en checkout de staging separado; nunca resetear los componentes del usuario.
Si rama falta, fallar explícitamente con su nombre; no fallback silencioso.

Construir imágenes en destino para respetar arquitectura. Incluir dark-core-lib
en contexto compartido de las APIs; no usar un contexto vacío o rutas del
controlador. Construcción dapp debe soportar arquitectura o emulación comprobada
explícitamente por preflight. No instalar dependencias de todas las aplicaciones
en un venv del host; usar jobs Docker para migración/contratos/setup Laravel.

SSH usa known_hosts, clave del controlador y comando fijo runner; parámetros
via JSON, sin comandos arbitrarios del inventario. La entrega pública no copia
claves privadas. Preflight exige secretos de destino provisionados. Secretos init
y chain export producen paquetes privados explícitos fuera del bundle; documentar
su instalación por máquina. Local usa exactamente esas rutas y controles.

`.env.example` final solo contiene `DEPLOYMENT_TOPOLOGY_FILE`. Tuning y secretos
referenciados quedan en inventario. Entornos de servicios se generan; credenciales
se materializan en destino con modo restringido y nunca entran en manifiesto
público. Conservar compatibilidad con la forma de consumir credenciales de cada
aplicación; soportar archivo montado cuando exista y entorno privado cuando no.

## 7. Orden de aplicación y handoffs

Steps tienen ID estable, máquina, grupo, dependencias, timeout y probe de éxito.
Ejecutar secuencialmente inicialmente; no crear paralelismo distribuido innecesario.

1. Preflight todas las máquinas: Docker/Compose, Python 3.12 runner, arquitectura,
   rutas/permisos, IPs, puertos, secretos y conectividad SSH.
2. Preparar fuentes y builds; validar Compose generado con `config` sin volcar
   secretos. Crear directorios y red de cada máquina.
3. Verificar artefactos comunes y claves asignadas; arrancar los cuatro
   validadores. No esperar quórum después de arrancar solo los dos primeros.
4. Arrancar RPC; verificar cadena/genesis, conjunto de validadores y progreso
   de bloques dentro de ventana ligada al block period.
5. Desplegar contratos una sola vez desde apps; verificar balance de master
   wallet antes de enviar y código on-chain después. Persistir direcciones/ABI,
   chain ID y genesis hash como resultado público, nunca como entrada manual.
6. Arrancar todos los storage antes de esperar pertenencia completa del Cluster.
   Verificar Kubo↔Cluster local y descubrimiento mutuo de peers.
7. Arrancar DBs/Redis; migrar minter con Alembic y dashboard una vez. Generar
   entornos con direcciones de contratos y endpoints resueltos. Arrancar Store,
   Admin, Resolver, Minter y sus tres workers, dashboard.
8. Arrancar explorer con endpoint RPC resuelto desde su grupo. Verificar flujo
   entre grupos y emitir reporte final.

El handoff público de contratos vive en estado del deployment, se distribuye a
consumidores y se valida contra cadena; nunca mezclar `.env.integration` global
de distintos hosts. Si el job termina con respuesta incierta, comprobar transacción
o contrato antes de repetir. La reanudación no debe desplegar otros contratos.

## 8. Preflight, seguridad de red y recuperación

No afirmar que CIDR correcto prueba conectividad. Probar cada arista requerida
desde el consumidor, por ejemplo explorer→RPC y Store→Cluster; comprobar puertos
después del arranque. Liveness barato para Docker, readiness de dependencias en
verify, avance de bloques para blockchain.

Derivar firewall-policy de las mismas aristas, con protocolo/puerto y origen.
No exponer TCP Docker. Firewall del sistema se aplica mediante procedimiento
explícito, no cambiando reglas automáticamente durante apply. Reportar políticas
no verificadas; declarar servicio operativo no equivale a certificar restricciones
de firewall. Restringir bindings privados y no abrir RPC a Internet.

Repetir apply conserva genesis, claves, datos y contratos. Journal guarda step,
hashes públicos, duración y resultado sin secretos. Una etapa marcada exitosa
se revalida antes de saltarla. Lock por máquina protege operaciones simultáneas
de dos controladores. Timeout no implica que un job remoto terminó: reconectar y
consultar su run ID; no repetir una migración o despliegue mientras siga vivo.

Rollback limitado a configuración/imágenes compatibles; no prometer rollback
automático de blockchain, contratos o schema SQL. Cambios de genesis/subred con
datos existentes producen bloqueo explicado. No incluir reset automático.

## 9. Pasos de implementación con entregables

1. Inventariar variables y servicios actuales en una tabla versionada
   `docs/deployment-v2-variable-map.md`: variable, consumidor, nuevo campo,
   default, secreto/derivado, destino. Cubrir todas las variables de `.env.example`,
   interpolaciones Compose y generaciones `configure_*` del instalador.
2. Implementar schema/model y los tres inventarios completos. Validar referencias,
   cardinalidad cuatro validadores, RPC apps, storage≥1 y objetivo≤peers.
3. Implementar resolver de máquinas/redes y plan puro. Congelar snapshots de
   endpoints para los tres ejemplos y un caso mixto local+SSH.
4. Portar catálogo/renderer. `compose config` debe funcionar en staging sin
   depender del checkout original. Comparar todos los servicios con los actuales.
5. Implementar runner local, journal y bootstrap de artefactos. Probar repetición
   local antes de agregar transporte remoto.
6. Implementar SSH y transferencia del mismo bundle. Probar en Linux limpio
   preparado con Docker/Compose/Python/rsync, sin checkout del deployer.
7. Implementar jobs contratos, migraciones y dashboard, handoffs y verify.
8. Ejecutar la matriz de aceptación. Corregir fallos antes del corte.
9. Hacer el corte operativo y actualizar documentación según sección 11.

Cada paso debe entregar tests y evidencia antes del siguiente. No marcar fases
completas por mocks cuando requieren Docker o red real.

## 10. Pruebas concretas

| Prueba | Evidencia exigida |
| --- | --- |
| Esquema y referencias | Rechaza versión 1, campos desconocidos, referencias ausentes e IPs inválidas con ruta JSON. |
| Red local | Cinco grupos, una máquina, una bridge, aliases únicos, puertos sin colisiones. |
| Red LAN/VPN | Cinco máquinas, bridges locales y conexiones por IP privada; ninguna IP Docker ajena anunciada. |
| Auto | Local inequívoco y remoto resueltos; DNS ambiguo y daemon duplicado rechazados. |
| Plan | No cambia filesystem, repositorios ni Docker. |
| Portabilidad bundle | Render/build correcto fuera del checkout original; incluye SDK y scripts requeridos. |
| Funcional local simple/HA | Bloques avanzan; tres workers vivos; reservar, registrar, publicar y confirmar objetivo de durabilidad. |
| Funcional SSH | Misma prueba desde hosts Linux y reporte de rutas privadas efectivamente usadas. |
| Idempotencia | Segundo apply conserva direcciones contratos, claves, genesis y datos; no crea contenedores duplicados. |
| Fallos | SSH interrumpido, secreto ausente, puerto ocupado y job incierto permiten diagnóstico/reanudación sin duplicación. |
| Secretos | Búsqueda en bundles/logs públicos no encuentra credenciales; paquetes privados solo contienen claves de su máquina. |
| Regresión de configuración | Todos los valores antiguos tienen destino documentado o retirada explícita. |

Prueba SSH con cinco VMs Linux para equivalencia física; si solo hay una VM
disponible, probar runner SSH allí y dejar cinco-host pendiente expresamente.
No llamar validación multiservidor a cinco proyectos en un único daemon.

## 11. Corte y retirada

Durante desarrollo usar `examples/deployment-v2/*.json`. Tras aceptación,
`deployment-topology.example.json` pasa a v2; inventario activo del usuario se
recrea explícitamente, nunca se sobrescribe automáticamente.

Retirar entradas operativas `compose/*-production.yml`, `apps.yml`,
`blockchain-a.yml`, `blockchain-b.yml`, `storage.yml`, `host.yml` cuando el
catálogo generado cubra todas sus funciones. Portar definición Besu de
`blockchain/compose.yml`; conservar scripts/config útiles hasta reemplazarlos.
Eliminar llamadas a installers de componentes que levanten sus propios Compose.

Reemplazar `install.py` por wrapper pequeño que remita a `deploy.py` o retirarlo
con error que indique el comando nuevo. No mantener una traducción del formato
viejo. Retirar módulos antiguos solo después de buscar sus consumidores en
monitor, scripts de operación y tests. Adaptar monitor a discovery por labels
deployment/group y endpoints del plan aplicado, no nombres fijos.

Actualizar README, docs de operaciones/producción, arquitectura, redes, secretos,
recuperación y ejemplos `.env`. Documentar paso a paso instalación local y SSH
con los mismos comandos. El análisis previo queda como evidencia anterior.

## 12. Definición de terminado

El agente entregará código, ejemplos v2 completos, mapa de variables, tests y
reporte con comandos ejecutados y resultados. Debe demostrar instalación local
y SSH, IPAM parametrizable, endpoints derivados, contratos idempotentes y
reanudación. El instalador no debe leer prefijos developer/production ni pedir
URLs que se puedan calcular del inventario. Diferencias de transporte deben
estar concentradas en executor, nunca en definición de servicios.

Decisiones iniciales fijadas para evitar ambigüedad: SSH ejecuta Docker en el
servidor, fuentes públicas se entregan y se construyen allí, secretos se
provisionan aparte, una bridge por máquina, ramas como selección de código y
hashes como evidencia. Si evidencia técnica obliga a cambiar alguna, documentar
el motivo y su efecto antes de sustituirla; no introducir otro sistema paralelo.
