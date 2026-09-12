# Manual operativo dARK

Guía práctica para instalar, verificar y probar dARK desde cero. Este manual
complementa al `README.md`: el README explica el proyecto y este archivo indica
el orden operativo.

## 1. Preparación

```bash
cd /Users/lmatas/source/dark-deployer
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt
docker version
docker compose version
```

No guardar claves privadas en Git ni dentro de `deployment-topology.json`.
Preparar en el controlador los archivos fuente de secretos indicados por la
topología. Cada entrada declara su archivo `source`, sus consumidores, su
destino por host y el modo `0600`; no guardar valores en Git.

## 2. Topología

Usar un inventario JSON v3 completo, por ejemplo uno de
`examples/deployment-v3/`, o un inventario compacto de
`examples/operator-inventory/`. La topología es la fuente de verdad para
hosts, redes, roles, blockchain, storage, ramas, política de puertos, secretos
y rutas LAN/VPN por dependencia. El formato compacto describe decisiones del
operador y se expande automáticamente al contrato v3 antes de validar o
ejecutar.

Los ejemplos compactos mantenidos son:

- `local-simple.json`: un host Docker y un peer de storage; una copia.
- `local-ha.json`: cinco grupos lógicos en un host Docker y dos peers; prueba
  replicación, pero no pérdida de un host.
- `production-five-host.json`: cinco máquinas SSH con LAN/VPN de documentación;
  requiere sustituir los valores `REPLACE`.

El formato compacto usa `format: "dark-operator-inventory"`,
`format_version: 1` y `catalog: "dark-standard-1"`. Sus secciones
principales son `machines`, `placement`, `blockchain`, `storage`,
`networks`, `routing`, `access`, `secrets` y `overrides`. La
colocación se declara una sola vez por grupo; servicios, dependencias,
consumidores de secretos, grupos v3 y exposiciones privadas se derivan del
catálogo y de las rutas.

El catálogo actual soporta cuatro validadores, un RPC, el conjunto completo de
Minter, dashboard, Store y uno o dos peers de storage llamados `storage-a` y
`storage-b`. Para topologías fuera de esa receta se debe usar el inventario v3
completo.

Para la instalación normal se puede ejecutar directamente:

```bash
venv/bin/python deploy.py install \
  --inventory examples/deployment-v3/local-ha.json
```

El comando coordina validación, secretos runtime, artefacto blockchain,
preflight, render, aplicación y verificación. En una cadena local nueva pedirá
la dirección pública de la master wallet; en la configuración habitual,
`--master-wallet-file` basta: el instalador deriva la dirección pública y usa la
misma clave como signer de contratos. `--master-wallet-address` y
`--contract-signer-file` permiten sobrescribir esos valores cuando se usan
credenciales distintas. Una cadena existente debe
tener su artefacto privado provisionado previamente.

Durante `install` se adquieren automáticamente los componentes definidos en
el inventario (clone si faltan; `fetch`, cambio de rama y `merge --ff-only` si
ya existen). Para trabajar con checkouts ya preparados se puede usar
`--skip-acquire`.

`merge --ff-only` significa que el instalador solo avanza una rama local cuando
la rama remota está estrictamente por delante. No crea commits de merge ni
sobrescribe cambios locales. Si la rama local y la remota divergen, la
adquisición se detiene para que el operador revise, guarde o integre los
cambios explícitamente.

### Trabajo diario con el formato compacto

Crear una topología compacta desde una plantilla:

```bash
venv/bin/python deploy.py inventory-create \
  --template operator-local-ha \
  --output deployment-topology.json
```

Antes de instalar, revisar la expansión completa y su evidencia:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory deployment-topology.json \
  --output resolved-topology.json

venv/bin/python deploy.py inventory-explain \
  --inventory deployment-topology.json \
  --path /services/store-api

venv/bin/python deploy.py plan \
  --inventory deployment-topology.json --json
```

`inventory-resolve`, `inventory-explain` e `inventory-diff` son comandos
offline: no usan Docker, SSH, Git ni leen contenidos de secretos.
`inventory-resolve` no sobrescribe un archivo de salida existente. Para
comparar dos decisiones:

```bash
venv/bin/python deploy.py inventory-diff \
  --before previous.json \
  --after deployment-topology.json
```

Validar y ejecutar usa directamente el compacto:

```bash
venv/bin/python deploy.py validate --inventory deployment-topology.json
venv/bin/python deploy.py render --inventory deployment-topology.json --output /tmp/dark-render
venv/bin/python deploy.py install --inventory deployment-topology.json
```

El bundle contiene el v3 resuelto en
`shared/deployment-topology.json` y la evidencia en
`shared/inventory-resolution.json`. Esta evidencia registra el digest de la
entrada, el digest del catálogo y la versión del resolver.

### Ajustes operativos del compacto

El shoulder del Minter se edita explícitamente en:

```json
{
  "overrides": {
    "settings": {
      "minter": {"shoulder": "200"}
    }
  }
}
```

Los tres ejemplos compactos contienen `"200"`. El valor debe respetar el
formato `2MM`, por ejemplo `200`, `245` o `299`. El catálogo aporta
settings y componentes por defecto; `overrides.settings` y
`overrides.components` permiten excepciones controladas.

Para añadir storage se declara un peer lógico y su grupo:

```json
{
  "storage": {
    "peers": {
      "storage-a": {"group": "storage-1"},
      "storage-b": {"group": "storage-2"}
    },
    "replication": {
      "publish_after_replicas": 1,
      "target_replicas": 2
    }
  }
}
```

El resolver crea el par Kubo/Cluster, actualiza Store y asigna los consumidores
de secretos. No hay que editar esas relaciones internas manualmente.

Para una dependencia entre máquinas, `routing` selecciona la red por clase de
tráfico:

```json
{
  "routing": {
    "blockchain_p2p": "vpn",
    "storage_p2p": "vpn",
    "storage_api": "vpn",
    "application_api": "lan"
  }
}
```

El resolver usa Docker DNS dentro de una máquina. Entre máquinas añade red,
TCP y exposición privada al proveedor. Nunca convierte automáticamente un
servicio en público. `access.local-direct` publica APIs en loopback;
`access.gateway` usa el edge proxy y necesita `bind` y puerto explícitos.

Validar antes de generar cualquier bundle:

```bash
venv/bin/python -m deployment_v3.cli validate \
  --inventory examples/deployment-v3/local-ha.json
venv/bin/python -m deployment_v3.cli plan \
  --inventory examples/deployment-v3/local-ha.json
venv/bin/python -m deployment_v3.cli render \
  --inventory examples/deployment-v3/local-ha.json \
  --output /tmp/dark-local-ha-render
```

`plan` no debe modificar Docker, repositorios ni el filesystem operativo.
Revisar especialmente roles `apps`, `validators-a`, `validators-b` y los dos
grupos de storage.

### Editor interactivo de inventario

Para crear o editar la topología sin modificar JSON a mano, instalar la
dependencia opcional de interfaz y abrir el editor:

```bash
venv/bin/python -m pip install -r requirements-tui.txt
venv/bin/python deploy.py inventory-create \
  --template local-ha \
  --output deployment-topology.json \
  --edit

# Para un inventario existente:
venv/bin/python deploy.py inventory-edit \
  --inventory deployment-topology.json
```

El editor organiza un inventario v3 por deployment, defaults, redes, máquinas,
grupos, servicios, infraestructura, blockchain, storage, componentes, settings
y secretos. Para un inventario compacto presenta deployment, máquinas,
placement, blockchain, storage, redes, routing, access, fuentes de secretos y
overrides. Cada sección se valida contra la expansión completa antes de
incorporarla al documento. `Ctrl+V` valida la sección, `Ctrl+S` guarda y
`Ctrl+Q` sale.

El guardado valida el inventario completo y crea una copia
`deployment-topology.json.bak-AAAAMMDD-HHMMSSZ` antes de reemplazarlo de forma
atómica. La interfaz no inicia Docker, no clona repositorios y nunca muestra ni
escribe contenidos de secretos; solo trabaja con las referencias declaradas.
Los comandos `validate`, `plan`, `render` e `install` siguen siendo la vía
correcta para comprobar o aplicar cambios.

## 3. Preflight e instalación local

Para developer local, ejecutar el preflight y luego aplicar el bundle generado:

```bash
venv/bin/python -m deployment_v3.cli preflight \
  --inventory examples/deployment-v3/local-ha.json
venv/bin/python -m deployment_v3.cli apply \
  --inventory examples/deployment-v3/local-ha.json
```

El orden funcional es validadores, quórum RPC, contratos, storage, datos,
aplicaciones y proxy. Cada barrera comprueba disponibilidad funcional antes de
abrir la fase siguiente.
Las rutas entre hosts declaran siempre la red y el protocolo; la política
central de infraestructura deriva los puertos P2P y el archivo de firewall
sugerido por máquina.
`deploy.py` es la única entrada soportada y genera los archivos de ejecución
desde el inventario; no se editan manualmente los `.env.integration`.

## 4. Despliegue remoto

En un inventario con hosts SSH, revisar primero el plan y el preflight de cada
máquina. Después transferir y aplicar el mismo bundle:

```bash
venv/bin/python -m deployment_v3.cli push --inventory deployment-topology.json
venv/bin/python -m deployment_v3.cli apply --inventory deployment-topology.json
```

El controlador distribuye por SSH exclusivamente los secretos y fragmentos de
artefacto Besu declarados para cada consumidor; los bundles públicos no los
contienen. Cada copia se comprueba por hash y permisos. Si una transferencia se
interrumpe, usar `resume` después de corregir la causa:

```bash
venv/bin/python -m deployment_v3.cli resume \
  --inventory deployment-topology.json
```

## 5. Blockchain

Inicializar o verificar los artefactos solo cuando corresponda a una cadena
nueva:

```bash
venv/bin/python deploy.py chain-init \
  --inventory deployment-topology.json \
  --output /secure/dark/secrets \
  --master-wallet-address 0x...
venv/bin/python deploy.py chain-static-nodes \
  --inventory deployment-topology.json \
  --artifact-root /secure/dark/secrets/blockchain/role-artifact \
  --public-keys /secure/dark/secrets/blockchain/public-keys.json
venv/bin/python deploy.py chain-export \
  --inventory deployment-topology.json \
  --artifact-root /secure/dark/secrets/blockchain/role-artifact \
  --role validators-a \
  --output /secure/dark/secrets/blockchain/validators-a
venv/bin/python deploy.py chain-verify \
  --inventory deployment-topology.json \
  --artifact-root /secure/dark/secrets/blockchain/role-artifact
```

chain-init es para una red nueva y requiere un --master-wallet-address.
chain-static-nodes necesita un JSON que mapee los cinco nodos a sus claves
públicas. chain-export separa rpc, validators-a o validators-b. No regenerar
genesis ni identidades en una cadena existente sin una decisión operativa
explícita.

## 6. Verificación posterior

```bash
venv/bin/python -m deployment_v3.cli status --inventory deployment-topology.json
venv/bin/python -m deployment_v3.cli verify --inventory deployment-topology.json
```

Comprobar además manualmente:

- RPC accesible y `chain_id=2025`.
- Cinco nodos Besu visibles y produciendo bloques.
- Dos peers IPFS/Cluster visibles desde Store API.
- `/health` y `/health/live` de Admin, Minter, Resolver y Store.
- Los tres workers del Minter vivos; no existe Recovery Worker.
- Proxy de `apps` accesible por HTTP, sirviendo dashboard y `/explorer/` sin
  publicar directamente esos contenedores.

Para actualizar o recrear un único servicio sin reiniciar el resto de la
máquina:

```bash
venv/bin/python deploy.py recreate \
  --inventory examples/deployment-v3/local-ha.json \
  --service dashboard \
  --build
```

`--build` es opcional. El comando valida el servicio y ejecuta Compose
únicamente para ese servicio; también funciona en hosts SSH.

## 7. Prueba funcional manual

Abrir `notebooks/dark_platform_deposit_lifecycle.ipynb` y ejecutar sus celdas
en orden. El flujo es:

1. Comprobar health y workers.
2. Verificar autoridad y NAAN.
3. Reservar un ARK con `POST /api/v1/arks/batch`.
4. Repetir la reserva con el mismo `client_item_id` para comprobar idempotencia.
5. Completar y registrar metadata L1/L2 con `PUT /api/v1/arks/{ark}`.
6. Esperar la transición a `PUBLISHED`.
7. Consultar ambos CIDs en Store API.
8. Resolver el ARK mediante Resolver API.

La plataforma depositante no debe esperar IPFS ni blockchain dentro de su
solicitud web; debe guardar el ARK y verificarlo mediante una tarea posterior.

## 8. Diagnóstico y limpieza

Usar `status` para una vista rápida y `verify` para diagnóstico completo. Revisar
logs de los servicios del grupo correspondiente antes de reiniciar.

Para un ensayo local nuevo, detener únicamente los proyectos generados por el
inventario y conservar cualquier contenedor externo explícitamente excluido
(por ejemplo `lareferencia-dev`). No borrar volúmenes de producción. Después de
una limpieza local, volver a ejecutar desde la sección 2.

## 9. Evidencia que debe conservarse

Guardar el JSON de `status/verify`, el hash del bundle aplicado, los hashes de
genesis/static-nodes y los resultados del notebook. Una rama móvil no es un
release inmutable: la trazabilidad se obtiene registrando los hashes realmente
desplegados.
