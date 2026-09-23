# Estado y validación de la PR #14

PR revisada: `Integra monitoramento e acessos de liveness`.

Este documento registra las decisiones ya aplicadas en la rama de trabajo
`monitoriing` y las validaciones pendientes. No solicita más cambios de diseño
en `dark-deployer` para esta revisión; los pendientes son pruebas funcionales y
una revisión del repositorio `dark-monitoring` frente al contrato final de
métricas.

## Decisiones confirmadas

### Catálogo y branches

El catálogo `dark-platform-baseline-v1.0` conserva `main` como branch por
defecto de `dashboard-web`, `dark-explorador` y `dark-monitoring`.

Así, los inventarios operativos y de producción no heredan ramas de trabajo.
La rama experimental `monitoriing` se selecciona por inventario mediante
`overrides.components`, no modificando el catálogo global.

### Inventario de laboratorio

Se añadió el laboratorio independiente:

```text
examples/operator-inventory/local-ha-monitoring.json
```

Su deployment ID es `dark-operator-local-ha-monitoring`. Solo sobrescribe los
branches de `dashboard-web`, `dark-explorador` y `dark-monitoring` hacia
`monitoriing`. `local-ha.json` continúa representando el laboratorio estable.

### `dark-monitoring` como componente del catálogo

`dark-monitoring` permanece intencionalmente en el catálogo y en
`deployment_v3/sources.py`, con ruta local:

```text
components/dark-monitoring
```

Por tanto, se adquiere y registra como evidencia de fuentes junto a los demás
componentes, aunque no se renderice como servicio del compose principal. Es una
herramienta disponible para que el operador instale el stack de monitorización
separadamente desde el snapshot aplicado.

El repositorio y sus branches `main` y `monitoriing` fueron comprobados.

### Contrato `metrics-export`

El comando toma un deployment gestionado ya aplicado, carga su topología
efectiva y usa el colector Docker/SSH de solo lectura para producir un archivo
Prometheus:

```bash
venv/bin/python deploy.py metrics-export \
  --deployment dark-operator-local-ha-monitoring \
  --output /tmp/dark.prom
```

La lectura remota no modifica máquinas. La única escritura es local y
deliberada: crea o reemplaza atómicamente el archivo `.prom` solicitado.

Además de disponibilidad, CPU, memoria, reinicios, red, bloques y PIDs, el
flag `--include-disk` ahora publica las filas de `docker system df`:

```text
dark_docker_disk_size_bytes
dark_docker_disk_reclaimable_bytes
dark_docker_disk_objects
```

Sus labels son `deployment`, `machine`, `site` y `type`. Los valores que Docker
no entregue se omiten; no se convierten en cero. La label `state` se eliminó de
las métricas de contenedor para evitar series efímeras cada vez que un
contenedor cambia de estado. `dark_container_running` conserva el estado
operativo mediante su valor `1` o `0`.

## Validaciones pendientes

### 1. Prueba integrada de exportación

Una vez aplicado `local-ha-monitoring`, ejecutar:

```bash
venv/bin/python deploy.py metrics-export \
  --deployment dark-operator-local-ha-monitoring \
  --include-disk \
  --output /tmp/dark.prom

rg -n '^dark_(machine_probe_success|container_running|docker_disk_)' /tmp/dark.prom
```

Criterios de aceptación:

- el comando informa una sonda por cada máquina del snapshot;
- el archivo termina con salto de línea y no se observa parcialmente escrito;
- contiene `dark_machine_probe_success`;
- contiene métricas `dark_docker_disk_*` cuando Docker entrega filas de disco;
- una máquina inaccesible produce `dark_machine_probe_success 0`, no métricas
  ficticias de capacidad.

### 2. Revisión y prueba de `dark-monitoring`

El repositorio `dark-monitoring` debe revisarse contra el contrato anterior.
No se presupone que requiera cambios, pero debe confirmarse lo siguiente:

1. El instalador acepta el snapshot aplicado como fuente de hosts y no mantiene
   una lista manual divergente.
2. Prometheus o node-exporter monta el directorio que recibe el archivo
   `dark.prom` y lo lee mediante el textfile collector.
3. Los dashboards y alertas toleran que las métricas nuevas no existan todavía
   antes de la primera exportación.
4. Si se quiere visualizar almacenamiento Docker, se añaden paneles para:

   ```promql
   dark_docker_disk_size_bytes
   dark_docker_disk_reclaimable_bytes
   dark_docker_disk_objects
   ```

5. No se debe confundir ese almacenamiento con la capacidad del EBS o del
   filesystem `/srv/dark/data`: para volumen, raíz y espacio libre se deben usar
   métricas de filesystem de node-exporter, no `docker system df`.
6. Las credenciales iniciales de Grafana y el archivo `.env` permanecen fuera
   de Git y el acceso anónimo, si se conserva, es estrictamente de lectura.

La revisión estática actual encontró una inconsistencia: `install.py` ejecuta
`metrics-export` sin `--include-disk`. Por eso, aunque `dark-deployer` ya sabe
exportar `dark_docker_disk_size_bytes`,
`dark_docker_disk_reclaimable_bytes` y `dark_docker_disk_objects`, la
instalación automática de monitoring no las publica en `generated/dark.prom`.
La corrección recomendada en `dark-monitoring` es:

1. pasar `--include-disk` al invocar `metrics-export` desde `install.py`;
2. añadir al dashboard de recursos paneles para esas métricas Docker;
3. probar que la instalación genera el archivo con dichas series cuando Docker
   entrega los datos;
4. mantener separadas esas métricas de Docker de las métricas de filesystem y
   EBS proporcionadas por `node-exporter`.

Esto es un ajuste del componente `dark-monitoring`, no un cambio adicional del
modelo ni del contrato de inventarios de `dark-deployer`.

La revisión debe hacerse tanto sobre `main` como sobre `monitoriing` si esa
rama contiene dashboards o instaladores modificados.

### 3. Enlaces y rutas web

Probar en navegador el inventario local de monitoring:

```text
http://localhost/admin/
http://localhost/explorer/
http://localhost:3000/dashboards
http://localhost:9090/targets
```

Criterios de aceptación:

- Dashboard carga CSS, JavaScript, login y navegación;
- Explorer carga assets y navega correctamente bajo `/explorer/`;
- los enlaces de liveness abren Grafana, Prometheus y Explorer cuando sus URLs
  están presentes;
- despliegues SSH siguen dejando Grafana y Prometheus vacíos hasta declarar un
  endpoint autenticado;
- la UI oculta o deshabilita enlaces vacíos.

`local-two-site` usa `docker-lab` y actualmente deja las URLs de Grafana y
Prometheus vacías. Solo se debe ampliar esa condición si se decide instalar
explícitamente el stack de monitoring en ese laboratorio.

### 4. Regresión de branches

Comprobar ambas resoluciones:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha.json \
  --output /tmp/local-ha-resolved.json

venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --output /tmp/local-ha-monitoring-resolved.json
```

El primer archivo debe resolver los componentes a `main`; el segundo solo debe
resolver `dashboard-web`, `dark-explorador` y `dark-monitoring` a
`monitoriing`.

Las pruebas automatizadas asociadas son:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  venv/bin/python -m pytest -q \
  tests/test_operator_inventory.py \
  tests/test_deployment_v3.py \
  tests/test_metrics_prometheus.py
```

## Cierre de revisión

La revisión quedará cerrada cuando las pruebas integradas de exportación, el
recorrido de navegador y la revisión de `dark-monitoring` confirmen que el
contrato de métricas y los dashboards funcionan juntos. Si esa revisión revela
que `dark-monitoring` no monta el textfile collector, no reconoce las nuevas
métricas de disco o necesita dashboards/alertas para ellas, esos cambios deben
hacerse en `dark-monitoring`, no en el modelo de deployment de dARK.
