# Diagnóstico: replicación IPFS bloqueada en el swarm privado local-ha

Fecha: 2026-09-14
Estado: abierto; causa inmediata identificada, causa de implementación aún no confirmada.

## Resumen ejecutivo

En el escenario `local-ha`, IPFS Cluster asigna correctamente una segunda
réplica, pero el segundo Kubo no consigue recuperar los bloques desde el
primero. El pin queda en `pinning` y termina con `pin_error` y `context
canceled`.

No es un problema de la cola de replication, de Store API ni de la asignación
de Cluster. La recuperación falla antes: Kubo B encuentra a Kubo A como
proveedor del CID, pero Bitswap no abre un stream hacia ese peer, no envía un
WANT y no transfiere bytes. Por tanto, Cluster no puede materializar el pin que
ya ha asignado.

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

### 3. Bitswap no inicia la transferencia

Con el proveedor ya visible desde B:

```bash
docker exec <kubo-b> sh -c 'timeout 15 ipfs block stat <cid>'
docker exec <kubo-a> ipfs stats bitswap --human
docker exec <kubo-b> ipfs stats bitswap --human
```

El resultado fue `Error: context canceled`. Los dos `stats bitswap` siguieron
con cero bloques y cero bytes enviados/recibidos; B no mantenía partners
Bitswap. Al habilitar logs de depuración, A tampoco registró un WANT entrante.

Esto sitúa el fallo entre el descubrimiento de proveedor y la apertura del
stream Bitswap. No es una espera normal de pinning: no hay transferencia en
progreso.

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
| Falta de peering persistente | No resuelve el fallo: se probó temporalmente y B siguió sin partners Bitswap. |
| Reconectar bootstrap tras readiness | No resuelve el fallo y se retiró del entrypoint. |

## Experimentos que no deben quedar como solución

Durante el diagnóstico se probaron temporalmente:

- fijar `Bitswap.Libp2pEnabled=true` y `Bitswap.ServerEnabled=true`;
- desactivar `Internal.Bitswap.BroadcastControl.Enable`;
- declarar el otro nodo en `Peering.Peers`;
- desconectar y reconectar explícitamente el bootstrap tras arrancar.

Los cambios se revirtieron de los volúmenes de Kubo. No deben añadirse al
inventario, al renderer ni a los entrypoints: no hicieron fiable la
transferencia y algunos alteran innecesariamente el ciclo de vida del daemon.

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
