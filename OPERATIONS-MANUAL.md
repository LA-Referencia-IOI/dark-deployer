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
Preparar las rutas de secretos indicadas por la topología.

## 2. Topología

Usar un inventario JSON v2 (por ejemplo uno de `examples/deployment-v2/`) y
adaptarlo al entorno. La topología es la fuente de verdad para hosts, redes,
roles, blockchain, storage, ramas y referencias de secretos.

Para la instalación normal se puede ejecutar directamente:

```bash
venv/bin/python deploy.py install \
  --inventory examples/deployment-v2/local-ha.json
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

Validar antes de generar cualquier bundle:

```bash
venv/bin/python -m deployment_v2.cli validate \
  --inventory examples/deployment-v2/local-ha.json
venv/bin/python -m deployment_v2.cli plan \
  --inventory examples/deployment-v2/local-ha.json
venv/bin/python -m deployment_v2.cli render \
  --inventory examples/deployment-v2/local-ha.json
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

El editor organiza la topología por secciones: deployment, defaults, redes,
máquinas, grupos, blockchain, storage, componentes, settings, secretos y
exposure. Cada sección se valida con las mismas reglas semánticas que usa el
instalador antes de incorporarla al documento. `Ctrl+V` valida la sección,
`Ctrl+S` guarda y `Ctrl+Q` sale.

El guardado valida el inventario completo y crea una copia
`deployment-topology.json.bak-AAAAMMDD-HHMMSSZ` antes de reemplazarlo de forma
atómica. La interfaz no inicia Docker, no clona repositorios y nunca muestra ni
escribe contenidos de secretos; solo trabaja con las referencias declaradas.
Los comandos `validate`, `plan`, `render` e `install` siguen siendo la vía
correcta para comprobar o aplicar cambios.

## 3. Preflight e instalación local

Para developer local, ejecutar el preflight y luego aplicar el bundle generado:

```bash
venv/bin/python -m deployment_v2.cli preflight \
  --inventory examples/deployment-v2/local-ha.json
venv/bin/python -m deployment_v2.cli apply \
  --inventory examples/deployment-v2/local-ha.json
```

El orden esperado es validators-a, validators-b, apps/RPC, storage y APIs.
`deploy.py` es la única entrada soportada y genera los archivos de ejecución
desde el inventario; no se editan manualmente los `.env.integration`.

## 4. Despliegue remoto

En un inventario con hosts SSH, revisar primero el plan y el preflight de cada
máquina. Después transferir y aplicar el mismo bundle:

```bash
venv/bin/python -m deployment_v2.cli push --inventory deployment-topology.json
venv/bin/python -m deployment_v2.cli apply --inventory deployment-topology.json
```

Las claves y artefactos privados deben existir previamente en las rutas
declaradas. Si una transferencia se interrumpe, usar `resume` después de
corregir la causa:

```bash
venv/bin/python -m deployment_v2.cli resume \
  --inventory deployment-topology.json
```

## 5. Blockchain

Inicializar o verificar los artefactos solo cuando corresponda a una cadena
nueva:

```bash
venv/bin/python -m deployment_v2.cli chain-init --inventory deployment-topology.json
venv/bin/python -m deployment_v2.cli chain-static-nodes --inventory deployment-topology.json
venv/bin/python -m deployment_v2.cli chain-export --inventory deployment-topology.json
venv/bin/python -m deployment_v2.cli chain-verify --inventory deployment-topology.json
```

No regenerar genesis ni identidades en una cadena existente sin una decisión
operativa explícita.

## 6. Verificación posterior

```bash
venv/bin/python -m deployment_v2.cli status --inventory deployment-topology.json
venv/bin/python -m deployment_v2.cli verify --inventory deployment-topology.json
```

Comprobar además manualmente:

- RPC accesible y `chain_id=2025`.
- Cinco nodos Besu visibles y produciendo bloques.
- Dos peers IPFS/Cluster visibles desde Store API.
- `/health` y `/health/live` de Admin, Minter, Resolver y Store.
- Los tres workers del Minter vivos; no existe Recovery Worker.
- Dashboard accesible y usando las APIs internas.

Para actualizar o recrear un único servicio sin reiniciar el resto del grupo:

```bash
venv/bin/python deploy.py recreate \
  --inventory examples/deployment-v2/local-ha.json \
  --group apps \
  --service dashboard \
  --build
```

`--build` es opcional. El comando valida que el servicio pertenezca al grupo y
ejecuta Compose únicamente para ese servicio; también funciona con grupos en
hosts SSH.

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
