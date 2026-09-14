# Diagnóstico: replicación IPFS bloqueada en el swarm privado local-ha

Fecha: 2026-09-14
Estado: abierto; causa inmediata identificada, causa de implementación aún no confirmada.

## Resumen ejecutivo

En el escenario `local-ha`, IPFS Cluster asigna correctamente una segunda
réplica, pero el segundo Kubo no consigue recuperar los bloques desde el
primero. El pin queda en `pinning` y termina con `pin_error` y `context
canceled`.

No es un problema de la cola de replication, de Store API ni de la asignación
de Cluster. La recuperación falla en la transferencia: Kubo B encuentra a Kubo
A como proveedor del CID y lo añade a la sesión Bitswap, pero no se observa la
entrega de un WANT ni la transferencia de bytes. Por tanto, Cluster no puede
materializar el pin que ya ha asignado.

El escenario afectado tiene dos pares Kubo/IPFS Cluster en un único host Docker.
Sirve para probar replicación lógica; no pretende ofrecer tolerancia ante la
pérdida de host.

## Síntoma observado

Después de publicar un payload y solicitar el objetivo de dos réplicas, Store
API puede informar una secuencia como esta:

```text
total=1 assigned=2 pinning=1 queued=0
...
pin_error: context canceled
```

La lectura de Cluster muestra un peer `pinned` y el otro en `pinning` o
`pin_error`. El `assigned_replicas=2` es importante: demuestra que el allocator
de Cluster seleccionó ambos peers; no demuestra que el segundo haya podido
obtener el contenido.

## Versiones y configuración examinadas

| Elemento | Valor probado |
| --- | --- |
| Kubo inicial | `ipfs/kubo:v0.41.0` |
| Kubo probado durante el diagnóstico | `ipfs/kubo:v0.42.0` |
| IPFS Cluster | `ipfs/ipfs-cluster:v1.1.6` |
| Red | swarm privado, misma PSK para ambos Kubo |
| Routing | DHT LAN privado |
| Transporte entre peers | TCP/4001, red Docker local |

Kubo 0.42.0 no corrigió el comportamiento. La actualización queda pendiente de
decisión y no debe presentarse como arreglo de replicación.

Los dos Kubo anuncian soporte para `/ipfs/bitswap`, `/ipfs/bitswap/1.0.0`,
`/ipfs/bitswap/1.1.0` y `/ipfs/bitswap/1.2.0`. Según la
[referencia de configuración de Kubo](https://github.com/ipfs/kubo/blob/master/docs/config.md),
Bitswap sobre libp2p y su servidor están habilitados por defecto.

## Evidencia recogida

### 1. La conectividad entre peers funciona

Ambos procesos ven una conexión activa en `ipfs swarm peers --verbose`. El DHT
LAN de cada nodo contiene exactamente al otro peer. Las direcciones anunciadas
son los nombres DNS internos Docker con TCP/4001.

También se verificó la conectividad TCP en ambos sentidos. No hubo evidencia de
un problema de puerto, firewall, DNS de servicio, PSK distinta o ruta cruzada.

### 2. El proveedor del bloque es descubrible

Para eliminar la ambigüedad de un bloque no anunciado, se introdujo un bloque
raw en A y se publicó de forma explícita:

```bash
docker exec <kubo-a> ipfs routing provide <cid>
docker exec <kubo-b> ipfs routing findprovs <cid> -n 1
```

El segundo comando devolvió el peer ID de A. Por tanto, B conoce tanto el CID
como el peer que lo sirve.

### 3. Bitswap descubre el proveedor, pero no completa la transferencia

Con el proveedor ya visible desde B:

```bash
docker exec <kubo-b> sh -c 'timeout 15 ipfs block stat <cid>'
docker exec <kubo-a> ipfs stats bitswap --human
docker exec <kubo-b> ipfs stats bitswap --human
```

El resultado fue `Error: context canceled` al vencer el tiempo de espera de la
prueba. Ese texto, por sí solo, no es una causa del incidente: también lo
produce el propio comando `timeout` cuando la recuperación no ha terminado.

La traza de Bitswap aporta más información que el texto del error. En B se
observó esta secuencia:

```text
No peers - broadcasting
Found peer for CID
Added peer to session
availability -> true
```

Después de esa secuencia no se observó un stream Bitswap con transferencia, un
WANT procesado por A ni bytes en los contadores. La conexión TCP/DHT de swarm
sí permanece activa. La evidencia sitúa el problema en la transición entre el
peer que la sesión considera disponible y la cola de mensajes o stream de
Bitswap; todavía no identifica qué componente devuelve o provoca el bloqueo.

Los dos `stats bitswap` permanecieron con cero bloques y cero bytes
enviados/recibidos durante la prueba. Es una señal de que no hubo transferencia
en esa ventana, no una prueba aislada de que la sesión nunca hubiera intentado
conectar.

Esto sitúa el fallo después del descubrimiento de proveedor y antes de una
transferencia Bitswap observable. No se puede clasificar como una espera normal
de pinning mientras los contadores sigan en cero y no haya un stream activo.

## Hipótesis descartadas

| Hipótesis | Resultado |
| --- | --- |
| Cluster no asigna la réplica | Descartada: asigna dos peers. |
| Store API no pide la replicación | Descartada: Cluster recibe y conserva el pin. |
| Falta conectividad Docker/TCP | Descartada: conexión P2P y DHT LAN activos. |
| PSK o identidad incompatibles | Descartada: ambos peers forman el swarm privado. |
| CID sin proveedor anunciado | Descartada: B encuentra explícitamente a A mediante `findprovs`. |
| Bitswap deshabilitado | Descartada: ambos nodos anuncian los protocolos y usan los defaults habilitados. |
| Sólo afecta a Kubo 0.41 | Descartada como explicación suficiente: se reproduce con 0.42. |
| Falta de peering persistente | No resuelve el fallo: se probó temporalmente y B siguió sin completar la transferencia. |
| Reconectar bootstrap tras readiness | No resuelve el fallo de forma fiable y se retiró del entrypoint fuente. |

## Experimentos que no deben quedar como solución

Durante el diagnóstico se probaron temporalmente:

- fijar `Bitswap.Libp2pEnabled=true` y `Bitswap.ServerEnabled=true`;
- desactivar `Internal.Bitswap.BroadcastControl.Enable`;
- declarar el otro nodo en `Peering.Peers`;
- desconectar y reconectar explícitamente el bootstrap tras arrancar.

Los cambios se revirtieron de los volúmenes de Kubo. No deben añadirse al
inventario, al renderer ni a los entrypoints: no hicieron fiable la
transferencia y algunos alteran innecesariamente el ciclo de vida del daemon.

Durante la limpieza se detectó una diferencia entre el código fuente y el
entorno generado. El entrypoint fuente ya no contiene el wrapper de
reconexión, pero un bundle generado previamente puede conservar una copia en
`.generated/.../sources/components/dark-ipfs/scripts/ipfs-entrypoint.sh`.
Después de cambiar un entrypoint hay que regenerar o volver a publicar el
bundle antes de recrear los contenedores; revisar sólo el archivo fuente no
confirma qué script ejecuta Docker.

## Cómo reproducir y comprobar el problema

Usar el despliegue local ya instalado. Sustituir los nombres por los de los dos
contenedores Kubo del bundle activo.

```bash
# En A, crear y anunciar un bloque de prueba.
cid=$(docker exec <kubo-a> sh -c \
  'printf bitswap-diagnostic | ipfs block put --format=raw --mhtype=sha2-256')
docker exec <kubo-a> ipfs routing provide "$cid"

# B debe encontrar A como proveedor.
docker exec <kubo-b> ipfs routing findprovs "$cid" -n 1

# El fallo actual: no recupera el bloque aunque el proveedor sea visible.
docker exec <kubo-b> sh -c "timeout 15 ipfs block stat '$cid'"

# Evidencia de que Bitswap no transmitió.
docker exec <kubo-a> ipfs stats bitswap --human
docker exec <kubo-b> ipfs stats bitswap --human
```

Un arreglo correcto debe hacer que `block stat` devuelva tamaño y CID, y que
los contadores indiquen bytes enviados por A y recibidos por B. Después, una
solicitud de réplica de Cluster debe llegar a `pinned` en ambos peers sin
`pin_error`.

## Próximos pasos propuestos

1. Construir una reproducción mínima fuera de Cluster con dos repositorios
   Kubo, PSK, AutoConf desactivado, direcciones Docker y la misma secuencia de
   bootstrap del entrypoint. Debe ejecutarse como prueba automatizada o script
   autocontenido, no como cambio de producción.
2. Comparar esa reproducción con el entrypoint actual, especialmente el perfil
   `autoconf-off`, el routing DHT LAN, `AppendAnnounce` y el bootstrap
   resuelto mediante la API del peer.
3. Recoger trazas de libp2p y Bitswap durante una lectura de bloque, incluyendo
   apertura de stream y cualquier rechazo del resource manager. La traza debe
   explicar por qué un proveedor conocido no se convierte en partner Bitswap.
4. Si la reproducción mínima falla, abrir una incidencia a Kubo con el script,
   versiones, configuración saneada y la evidencia de proveedor descubrible
   sin transferencia. No incluir swarm keys, secretos ni datos de producción.
5. Si la reproducción mínima funciona, reducir las diferencias hasta obtener
   un cambio mínimo en el renderer o entrypoint, y cubrirlo con una prueba de
   pin remoto de Cluster.

## Criterios de cierre

El incidente se considera resuelto solamente cuando un escenario de dos peers
puede repetir varias veces esta secuencia sin intervención manual:

1. escribir contenido en A;
2. solicitar a Cluster dos réplicas;
3. observar dos pins `pinned`;
4. leer el CID desde B;
5. verificar bytes y bloques Bitswap en ambos sentidos;
6. completar el flujo de Store API sin `context canceled` ni reintentos
   indefinidos.

La mera presencia de dos peers en `/peers`, una asignación de Cluster o un
estado `pinning` transitorio no son criterios suficientes.

## Resultados de las pruebas ejecutadas el 2026-09-14

### Estado del entorno generado

El despliegue `dark-operator-local-ha` estaba activo con todos sus contenedores
`Up`. Los dos Kubo ejecutaban `ipfs/kubo:v0.42.0` y los dos peers de Cluster
ejecutaban `ipfs/ipfs-cluster:v1.1.6`.

Los Kubo estaban conectados en la red Docker
`dark-operator-local-ha-local`:

- Kubo A: `172.30.0.2`, peer ID
  `12D3KooWM8jaL5sF8e3fwiBgQQePBFxRav4D1JiH9zQ7PUpsJEJR`.
- Kubo B: `172.30.0.3`, peer ID
  `12D3KooWJNUTpZoS5u6eoF6s9pofU5PW7vL8JXCKK8pUi2yJqZhN`.

Ambos compartían la misma huella de swarm key, anunciaban los protocolos
Bitswap `/ipfs/bitswap`, `1.0.0`, `1.1.0` y `1.2.0`, y veían al otro peer por
DHT LAN. Cluster A y Cluster B también se veían entre sí.

Se observó además que Cluster B intentó acceder a su API local de Kubo antes
de que el puerto 5001 estuviera escuchando, produciendo temporalmente
`connection refused`. Este evento es una carrera de arranque independiente
que debe conservarse como observación operativa, pero no explica por sí solo
el fallo de Bitswap reproducido posteriormente.

### Reproducción en el entorno generado

Se creó en Kubo A un bloque raw de diagnóstico:

```text
bafkreibxbhej46mmvotmsw5bljpvobydrdwqmulvt7oq33q34gqvgt6hym
```

La secuencia fue:

1. `routing provide` desde A: correcto.
2. `routing findprovs` desde B: devolvió el peer ID de A.
3. `block stat` desde B con timeout de 15 segundos: terminó con
   `Error: context canceled`.
4. `swarm connect` explícito B → A: correcto.
5. Un segundo `block get` desde B: volvió a terminar con
   `Error: context canceled`.

Antes y después de la prueba, ambos nodos mostraron:

```text
blocks received: 0
blocks sent: 0
data received: 0 B
data sent: 0 B
partners [0]
```

### Reproducción mínima fuera de Cluster

Se levantó una red Docker efímera con dos Kubo `v0.42.0`, sin IPFS Cluster ni
Store API. Se reutilizó únicamente la swarm key del entorno y se ejecutó el
entrypoint fuente `components/dark-ipfs/scripts/ipfs-entrypoint.sh`, con
`autoconf-off`, DHT LAN, `AppendAnnounce`, `Swarm.AddrFilters=[]` y bootstrap
DNS hacia el peer A.

El resultado fue el mismo: B encontró el proveedor mediante `findprovs`, pero
la lectura del bloque expiró con `context canceled`. Con Bitswap en debug, la
traza mostró:

```text
No peers - broadcasting
Found peer for CID
change: availability ... -> true
Bitswap: Added peer to session
```

Después no apareció una cola de mensajes ni un stream Bitswap, A no registró
un WANT y los contadores de ambos nodos permanecieron en cero. Tampoco se
observó un rechazo del resource manager.

### Control sin swarm privado

Se repitió la reproducción con dos Kubo aislados sin swarm key, conservando
la configuración necesaria para permitir direcciones privadas Docker
(`Swarm.AddrFilters=[]`). La conexión se estableció, pero la transferencia
Bitswap volvió a fallar con `context canceled` y cero bytes.

Esto descarta la swarm key privada/PNET como causa suficiente. Un control
totalmente virgen, sin `Swarm.AddrFilters=[]`, no fue válido: el gater de Kubo
rechazó correctamente el dial hacia la dirección privada `172.x`.

Todos los contenedores y redes Docker efímeros usados en estas pruebas fueron
eliminados al finalizar. El despliegue `dark-operator-local-ha` no fue
reiniciado ni modificado.

### Conclusiones actualizadas

- El fallo se reproduce sin Cluster, Store API ni la cola de replicación.
- El descubrimiento DHT del proveedor funciona.
- La conexión libp2p base funciona.
- El fallo ocurre después de que la sesión Bitswap marque disponible al peer y
  antes de que se materialice la cola o el stream Bitswap.
- No hay evidencia de que `context canceled` sea la causa; es el resultado del
  timeout de la prueba.
- No hay evidencia de que PNET, la PSK o la versión `v0.42.0` sean la causa
  suficiente. El problema ya estaba observado anteriormente con Kubo
  `v0.41.0`, por lo que cambiar de versión no es la siguiente línea de trabajo.
- La configuración de Bitswap permanece por defecto (`Bitswap: {}`) y no está
  deshabilitada.
- El control sin PNET indica que la investigación debe centrarse en la
  interacción entre Kubo/libp2p, las direcciones privadas Docker y la
  configuración manual de red, aislando individualmente `AppendAnnounce`,
  `autoconf-off`, `Routing.Type=dht`, el perfil `server` y `AddrFilters`.
