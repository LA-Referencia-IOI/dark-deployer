# Desarrollo de componentes y pruebas de branches

Esta guía describe cómo desarrollar un componente dARK nuevo o probar una
rama experimental sin cambiar accidentalmente los branches usados por los
inventarios de producción.

## Regla principal

El catálogo define los valores por defecto, normalmente `main`. Una rama de
desarrollo debe declararse en el inventario que la necesita, no cambiarse en
el catálogo global.

Así, producción continúa usando `main`, mientras un inventario de desarrollo
puede sobrescribirlo mediante `overrides.components`.

## Inventario completo V3

En un inventario V3 completo, cada componente se declara en `components`:

```json
{
  "components": {
    "dashboard-web": {
      "repository_url": "git@github.com:LA-Referencia-IOI/dashboard-web.git",
      "branch": "monitoriing"
    },
    "dark-explorador": {
      "repository_url": "git@github.com:LA-Referencia-IOI/dark-explorer.git",
      "branch": "monitoriing"
    }
  }
}
```

El deployer registra además el commit exacto adquirido.

## Operator inventory

El formato compacto hereda los componentes del catálogo. Para sobrescribir
solo algunos branches se usa `overrides.components`:

```json
{
  "overrides": {
    "components": {
      "dashboard-web": { "branch": "monitoriing" },
      "dark-explorador": { "branch": "monitoriing" }
    }
  }
}
```

Los campos permitidos para un componente son `repository_url` y `branch`. Para
probar un componente nuevo, primero debe existir en el catálogo; el resolvedor
no permite agregar componentes desconocidos desde `overrides`.

## Flujo para una rama experimental

1. Crear la rama en el repositorio del componente.
2. Copiar un inventario mantenido para desarrollo.
3. Añadir únicamente las excepciones en `overrides.components`.
4. Resolver y revisar el resultado:

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --output /tmp/local-ha-monitoring-resolved.json
rg -n 'dashboard-web|dark-explorador|monitoriing' \
  /tmp/local-ha-monitoring-resolved.json
```

5. Validar y planificar:

```bash
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-ha-monitoring.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-ha-monitoring.json
```

6. Instalar y adquirir las fuentes:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --verbose
```

La adquisición verifica que el branch exista, clona en esa rama o actualiza
el checkout mediante fast-forward. Los cambios locales no se sobrescriben
silenciosamente.

## Probar sin actualizar checkouts

Para usar exactamente los checkouts ya presentes:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --skip-acquire \
  --verbose
```

Cada checkout debe estar ya en el branch declarado.

## Añadir un componente nuevo

Un componente nuevo requiere:

1. Repositorio y branch estable.
2. Identificador y ruta local en `deployment_v3/sources.py`.
3. Definición en el catálogo con `repository_url` y `branch`.
4. Tipo, renderizado, validaciones, conexiones, secretos y puertos necesarios.
5. Pruebas e inventario ejecutable.
6. Documentación de instalación y verificación.

El catálogo debe usar el branch estable por defecto; las ramas experimentales
se seleccionan desde inventarios de desarrollo.

## Convenciones y vuelta a `main`

Usa nombres explícitos como `local-ha-monitoring.json` y documenta los
componentes sobrescritos, commits esperados, rutas y cómo volver a `main`. No
modifiques inventarios de producción para probar una rama.

Eliminar el override devuelve el componente al branch del catálogo. Para un
inventario completo, declara explícitamente `"branch": "main"` y vuelve a
adquirir las fuentes.

## Validación mínima antes de una PR

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  venv/bin/python -m pytest -q tests/test_operator_inventory.py tests/test_deployment_v3.py
venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-ha-monitoring.json
venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-ha-monitoring.json --json
```

La PR debe demostrar que los inventarios de producción continúan resolviendo a
`main`.
