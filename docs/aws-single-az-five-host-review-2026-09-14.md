# Revisión crítica del runbook AWS de cinco hosts en una zona

Alcance: `docs/aws-single-az-five-host.md`. Fecha: 2026-09-14, sobre `main` en
`55bdb7a`. Continúa la revisión de
[los ejemplos de inventario](examples-inventory-review-2026-09-14.md).

Aviso de estado del árbol: durante la revisión el árbol de trabajo cambió (se añadían
los comandos de ciclo de vida `stop`/`start`/`restart`/`remove` sin commitear), lo que
desplazó algunas líneas de `cli.py` y de `tests/test_deployment_v3.py`. Las
referencias de línea de este documento están recalculadas contra el árbol de trabajo
al cerrar la revisión. Ninguno de los ficheros que sostienen los hallazgos de abajo
—`executor.py`, `readiness.py`, `verify.py`, `inventory.py`, `planner.py`,
`network.py`— figura entre los modificados, así que el contenido citado no cambió.

La interfaz operativa actual también incluye `deployments`, que lista los IDs
gestionados, y `services --deployment ID`, que consulta el estado real en cada
host. Las operaciones exactas usan `--target service:ID`: `stop`, `start`,
`restart`, `recreate` y `remove`. Esta revisión no ejecutó esas mutaciones contra
AWS; `remove` conserva volúmenes, datos bind-mounted y redes.

Método: lectura del documento contra `inventory.py`, `inventory_resolver.py`,
`planner.py`, `render.py`, `executor.py`, `readiness.py`, `verify.py` y `runner.py`;
y una prueba de extremo a extremo del consejo central del documento. Para esa prueba
construí en un directorio temporal el inventario exactamente como lo describe la §6
(copia de `examples/operator-inventory/production-five-host.json` con dos redes
lógicas `lan`/`vpn` compartiendo el CIDR `10.40.10.0/24`, la misma IP privada en
ambas y las cuatro claves de `routing`), y ejecuté `validate`, `plan` y `render`
sobre él. No se modificó ningún fichero del repositorio.

Marcas: **CONFIRMADO** con evidencia reproducible (fichero y línea, o salida de
comando) o **INFERIDO** cuando es una lectura mía sin prueba directa.

---

## 1. Resumen

El documento es sólido donde más importa. Su consejo central —declarar `lan` y `vpn`
como dos dominios lógicos con el mismo CIDR y la misma IP privada en cada host— es
correcto y **CONFIRMADO por ejecución**: el inventario resultante valida
(`[OK] dark-aws-five-host: 5 machine(s), 25 service(s)`), planifica y renderiza sin
`routes` ni NAT, tal como afirma la §6.

Los problemas son de precisión en tres sitios: la tabla de Security Groups omite un
puerto que sí hay que abrir, la lista de aceptación de la §10 atribuye a `verify`
comprobaciones que no hace, y la §4 pide en cada host una herramienta que no se usa
allí. Ninguno invalida el runbook; los tres se arreglan con ediciones cortas.

No he podido verificar contra AWS real (no tengo cuenta ni acceso), así que todas las
afirmaciones sobre comportamiento de AWS —referencias entre Security Groups,
semántica de zonas y volúmenes EBS, ALB— quedan fuera de esta revisión. Lo que sí he
verificado es todo lo relativo al deployer y al inventario.

---

## 2. Lo que sostiene la revisión

**CONFIRMADO** con evidencia:

- **§6, la configuración de dos redes con el mismo CIDR funciona.** Es la afirmación
  más arriesgada del documento y la más útil. Verificado ejecutando el inventario
  construido según la §6. El validador comprueba la unicidad de direcciones *por red*
  (`inventory.py:268,285-289`), así que la misma IP en `lan` y en `vpn` es legal; y
  como cada host declara ambas redes, ninguna conexión entre hosts necesita `routes`
  (`inventory.py:717-720`).
- **§9, el orden de instalación.** El plan real genera
  `readiness:validators`, `readiness:rpc`, `readiness:contracts`, `readiness:storage`,
  `readiness:data`, `readiness:applications` y `verify:deployment`, en ese orden, con
  las fases `validators, rpc, observers, contracts, storage, data, applications` de
  `planner.py:14-22`. La secuencia que describe el documento (validadores, consenso,
  RPC, contratos, storage, bases de datos, APIs/workers, dashboard, verificación
  final) coincide. En esta sede no aparece la fase `observers` porque no hay
  observadores.
- **§8, el contenido del preflight.** La enumeración «SSH, Docker, Compose,
  arquitectura, disco, direcciones privadas y rutas API/P2P» es exactamente lo que
  hacen `executor.run_preflight` (`executor.py:100-122`) y
  `executor.run_network_preflight` (`executor.py:135-188`). Y la advertencia de que un
  preflight correcto demuestra alcanzabilidad IP y no permiso de Security Group es
  precisa: la comprobación de rutas es `ip route get` (`executor.py:154,181`), que no
  ejercita ningún puerto.
- **§8, el SSH.** El ejecutor remoto usa `BatchMode=yes` y
  `StrictHostKeyChecking=yes` (`executor.py:50`). La consecuencia práctica —que el
  `known_hosts` tiene que estar preparado *antes*, porque no habrá pregunta
  interactiva— es la que ya anticipa la §5 del propio documento.
- **§2, `management_address` frente a `addresses`.** La separación existe en el
  modelo (`model.py:22-37`) y el ejecutor la usa: SSH va contra
  `management_address` (`executor.py:53`) y las APIs/P2P contra
  `addresses[network]` (`network.py:80,86`).
- **§4, `curl` y `git` aparte, los requisitos de sistema.** El deployer crea el árbol
  `/srv/dark`, `/srv/dark/data` y `/srv/dark/secrets` con `mkdir -p` sobre el host
  remoto (`runner.py:189`) y después los datos, secretos y directorios de runtime por
  servicio (`runner.py:355-395`). Lo único que el preflight exige de antemano es el
  directorio padre; el matiz de permisos que eso implica está en el hallazgo 3.5.
- **§3, el ALB y el TLS.** El proxy del ejemplo usa `tls.mode = external`, y en ese
  modo nginx escucha en el puerto 80 del contenedor sin terminar TLS
  (`network.py:37-40`); por eso la indicación de «poner un ALB con TLS delante de
  `apps:80`» encaja con el inventario sin cambios. **CONFIRMADO** en código; que un
  ALB concreto se configure así es AWS, fuera de esta revisión.
- **§11, las opciones del comando.** `install --resume` y `--refresh-bundle` existen y
  tienen el significado que el documento les da (`deploy.py install --help`).

---

## 3. Hallazgos

### 3.1 La tabla de Security Groups de la §3 no abre el puerto P2P del nodo RPC

**CONFIRMADO**. En esta topología, el nodo RPC vive en el host `apps` y su puerto P2P
es el último del rango Besu. El `firewall-suggestion.json` renderizado para el
inventario de la §6 pone, en la máquina `apps`:

```
{'bind': 'private', 'network': 'vpn', 'port': 30307, 'protocols': ['tcp','udp'], 'purpose': 'besu-p2p', 'service': 'rpc01'}
```

La §3 asigna «Besu P2P 30303-30307 TCP/UDP desde apps y blockchain» al grupo
`dark-blockchain`, y la fila de `dark-apps` solo menciona «RPC 8545 desde blockchain;
HTTP 80 desde ALB o red administradora». Siguiendo la tabla al pie de la letra, el
Security Group de `apps` nunca abre 30307, de modo que `rpc01` no acepta conexiones
P2P entrantes de validadores ni de storage. El resto del rango (30303-30306) sí está
bien asignado, porque esos validadores sí están en `blockchain-a`/`blockchain-b`.

El propio documento se protege con «después de renderizar, comparar estas reglas con
`shared/firewall-suggestion.json`», pero una tabla que se presenta como «una
separación práctica» es justo lo que se copia a una plantilla de Terraform o a la
consola, y el puerto que falta es el de un servicio que el operador no asocia a
`apps`.

### 3.2 La §10 atribuye a `verify` comprobaciones que no hace

**CONFIRMADO** leyendo `verify.py` completo. Contrastando la lista de aceptación con
lo que el código comprueba:

| Criterio de la §10 | Qué comprueba `verify` de verdad |
| --- | --- |
| identidad de la cadena | `eth_chainId` contra el `chain_id` del inventario (`verify.py:86-91`) |
| avance de la cadena | nada, en esta sede. La altura solo se compara entre RPC primario y observadores (`readiness.py:79,88-89`), y aquí no hay observadores |
| quorum de todos los validadores declarados | `net_peerCount` del RPC primario ≥ (nodos Besu − 1) (`verify.py:92`). Prueba conectividad, no que el conjunto QBFT esté completo: `qbft_getValidatorsByBlockNumber` solo se consulta para observadores (`readiness.py:83`) |
| RPC primario funcional | sí (`verify.py:86-92`) |
| sincronización y exclusión del conjunto validador de cada observer | sí, y con detalle (`readiness.observer_status`) — pero en esta sede no hay observadores |
| membresía Kubo y Cluster | solo que la salida no esté vacía (`verify.py:99-115`); la convergencia completa se comprueba en readiness, no en verify |
| Store API y objetivo de réplica declarado | que `/health?refresh=true` responda. Ese endpoint devuelve 503 solo si el backend no es escribible: Kubo local sano, al menos un peer de Cluster visible y sin identidades IPFS duplicadas, con `min_cluster_peers` fijo en 1 (`components/dark-store-api/app/backends/ipfs_cluster.py:514-531`). **No** comprueba `target_replicas: 2` |
| contratos disponibles | no los sondea. `contracts-deploy` figura en `one_shots` (`verify.py:60,78`), es decir, está exento de la exigencia de estar `running` |
| workers saludables | sí, comprobando que el PID 1 del contenedor es el worker correcto (`verify.py:120-128`) |
| dashboard, explorer y proxy por su ruta | sí (`verify.py:129-137`), aceptando 4xx del backend y rechazando ≥500 |

**INFERIDO**: que `verify` deba comprobar altura y conjunto validador es una decisión
de diseño. Lo que sostengo como hecho es el desajuste: la §10 convierte esas
comprobaciones en condición de aceptación («Aceptar la sede solo cuando `verify`
demuestre...») y `verify` no las demuestra, así que un operador que se guíe solo por
su salida dará por bueno un avance de cadena y una durabilidad de dos copias que nadie
ha medido.

La durabilidad de dos copias sí se puede probar, y el repositorio ya tiene la
herramienta: el ciclo de depósito termina en `PUBLISHED`, que es el estado que exige
el objetivo (`OPERATIONS-MANUAL.md:215`). Lo que falta es que este documento lo
enlace; es el único de los runbooks que no menciona ningún notebook.

### 3.3 La §4 pide `git` en cada host, pero git solo se usa en el controlador

**CONFIRMADO**. La adquisición de componentes ocurre contra `project_root`, es decir,
el checkout del controlador (`cli.py:250-259`, `acquire.py`), y el traslado a cada
host copia `components`, `blockchain` y `deployment_v3` **excluyendo `.git`**
(`runner.py:190-191`). No hay ninguna invocación de `git` en `runner.py` ni en las
rutas de ejecución remotas. La única función que usa git es `sources.source_evidence`,
que inspecciona el checkout del controlador.

Instalar git en las cinco instancias es inocuo, pero si el runbook se automatiza es un
paso que sobra y, peor, da la impresión de que cada host necesita un checkout propio,
que es justo lo que el diseño evita.

### 3.4 La §4 exige `curl` en los hosts y el preflight no lo comprueba

**CONFIRMADO**. `curl` sí se necesita, y en concreto en el host `apps`: las sondas de
readiness y verificación se ejecutan en la máquina destino vía SSH
(`readiness.py:40-47`), y la sonda del RPC primario se lanza contra la máquina que lo
aloja (`readiness.py:121-133`; `verify.py:86`). Las sondas de ruta del proxy también
(`verify.py:42-51`, sobre `sh -lc`).

`run_preflight` comprueba `docker`, `docker compose`, `uname -m`, espacio en disco,
directorios padre y presencia de las direcciones declaradas
(`executor.py:103-122`); no comprueba `curl` ni `sh`. La consecuencia es que un host
sin `curl` pasa el preflight y falla más tarde, en `readiness:rpc`, con un error de
«command not found» en vez de en el paso que el documento presenta como la
comprobación de requisitos. La §8 no promete comprobar `curl`, así que el documento es
coherente consigo mismo; el desajuste está entre la §4 y el preflight.

Dos arreglos posibles: añadir `curl` y `sh` a `run_preflight` (barato y en línea con
lo que ya comprueba), o decir en la §4 que ese requisito se verifica a mano, porque el
preflight no lo cubre.

### 3.5 Detalle de precisión sobre `/srv/dark` y los permisos

**CONFIRMADO**. El preflight exige que exista **el directorio padre** de las tres
rutas declaradas y que sea escribible (`executor.py:115-120`, con el comentario del
propio código en `executor.py:112-113` sobre medir el padre en vez del destino,
porque el destino lo crea `apply`). Con `workspace_root = /srv/dark`, lo que debe
existir y ser escribible es `/srv`, no `/srv/dark`.

La §4 dice que cada host necesita `/srv/dark` para workspace. Un operador que lo cree
a mano como `root` con permisos 0755 y luego despliegue con otro usuario tendrá un
preflight en verde y un `apply` que falla al crear los subdirectorios. La formulación
segura para el runbook es que `/srv` exista y sea escribible por el usuario de
despliegue, y que el deployer crea el resto.

Además, la §4 concentra el respaldo en el volumen de `/srv/dark/data`, mientras que
los secretos quedan en `/srv/dark/secrets` y el artefacto de cadena en
`secrets_root` (`runner.py:322,355,364-370`), fuera de ese volumen. No es un defecto
de diseño, porque el material privado vive en el controlador y se redistribuye en cada
`apply` (`runner.distribute_secrets`, `cli.py:146-170`), pero la §11.4 («restaurar un
volumen EBS desde snapshot en una instancia de reemplazo») puede leerse como que
restaurar el volumen de datos reconstruye el host. El runbook gana si lo dice
explícitamente.

### 3.6 Inconsistencia menor con el manual sobre la versión de Python

**CONFIRMADO**: la §5 usa `python3 -m venv venv`; `OPERATIONS-MANUAL.md:35` usa
`python3.12 -m venv venv`. **CONFIRMADO** además que el código y los tests funcionan
con 3.10 (aquí se ejecutaron con 3.10.12), y que `requirements.txt` fija
`jsonschema==4.23.0`, que es lo que hace falta para `Draft202012Validator`. Merece la
pena que los dos documentos digan lo mismo; si se quiere dar un mínimo en vez de una
versión concreta, habría que determinarlo (yo solo he comprobado que 3.10 basta).

---

## 4. Recomendaciones

1. **Añadir 30307 al Security Group `dark-apps`** (o reformular la fila Besu P2P como
   «30303-30307 en los tres hosts que ejecutan Besu: apps, blockchain-a y
   blockchain-b»), que es lo que dice `firewall-suggestion.json`.
2. **Ajustar la §10 a lo que `verify` demuestra**, y añadir dos comprobaciones
   explícitas de aceptación: avance de la cadena (por ejemplo, dos lecturas de
   `eth_blockNumber` separadas en el tiempo) y `target_replicas` mediante el ciclo de
   depósito hasta `PUBLISHED`. Enlazar
   `notebooks/dark_platform_deposit_lifecycle.ipynb`.
3. **Quitar `git` de la lista de la §4** o rebajarlo a «solo el controlador».
4. **Cubrir `curl`**: o añadirlo a `run_preflight`, o decir en la §4 que no lo
   comprueba el preflight y hay que verificarlo a mano.
5. **Reformular el requisito de rutas** como «`/srv` existe y es escribible por el
   usuario de despliegue; el deployer crea `/srv/dark`, `/srv/dark/data` y
   `/srv/dark/secrets`».
6. **Decir en la §11 que los secretos se reaportan desde el controlador** y no están
   en el volumen de datos, para que nadie planifique una restauración incompleta.
7. **Alinear la versión de Python** con `OPERATIONS-MANUAL.md`, o declarar el mínimo
   soportado.
