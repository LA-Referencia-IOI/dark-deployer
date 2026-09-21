# local-ha-monitoring

Este inventario es una copia de `local-ha.json` para probar la PR de
monitorización sin modificar el inventario local estable.

Mantiene la misma topología local HA, pero sobrescribe mediante
`overrides.components` los branches experimentales de:

- `dashboard-web`;
- `dark-explorador`;
- `dark-monitoring`.

El catálogo conserva `main` como valor por defecto para todos ellos.

## Resolver y validar

```bash
venv/bin/python deploy.py inventory-resolve \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --output /tmp/local-ha-monitoring-resolved.json

venv/bin/python deploy.py validate \
  --inventory examples/operator-inventory/local-ha-monitoring.json

venv/bin/python deploy.py plan \
  --inventory examples/operator-inventory/local-ha-monitoring.json
```

Comprobar los branches resueltos:

```bash
rg -n 'dashboard-web|dark-explorador|dark-monitoring|monitoriing' \
  /tmp/local-ha-monitoring-resolved.json
```

## Adquirir y ejecutar

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --verbose
```

La adquisición debe encontrar la rama `monitoriing` en los tres repositorios.
El stack de monitorización se instala separadamente desde el snapshot aplicado,
según las instrucciones de `dark-monitoring`.

Para volver a probar los checkouts ya existentes sin actualizarlos:

```bash
venv/bin/python deploy.py install \
  --inventory examples/operator-inventory/local-ha-monitoring.json \
  --skip-acquire \
  --verbose
```

En ese caso los tres checkouts deben estar ya posicionados en `monitoriing`.
