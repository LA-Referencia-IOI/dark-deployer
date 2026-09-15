# Política de shoulders para el Minter

## Alcance

Esta política aplica a los ARKs reservados por `dark-core-minter-api` mediante
su generador NOID. No modifica ARKs existentes ni los identificadores entregados
explícitamente al comando de importación directa.

## Espacio histórico de DARK 1

DARK 1 emitió identificadores cuyos nombres comienzan con `00`, incluidos los
identificadores observados con el prefijo `0013`. Todo el espacio `00*` queda
reservado para DARK 1. Un minter de DARK 2 no debe emitir nombres que usen ese
prefijo.

## Formato de DARK 2

Todo shoulder de DARK 2 tiene exactamente tres caracteres ASCII en minúscula y
el formato `2xx`:

```text
2xx
│└─ código alfanumérico de dos caracteres asignado al minter
└── versión de DARK
```

El valor predeterminado es `200`. Por ejemplo, un minter con código `s0` usa
`MINTER_SHOULDER=2s0` y emite identificadores como:

```text
ark:41046/2s00000000<dígito-de-control>
```

El prefijo inicial `2` separa de forma inequívoca los nombres emitidos por
DARK 2 del espacio `00*` reservado para DARK 1.

## Configuración mediante el Deployer

El valor se declara en el `.env` raíz del Deployer, de acuerdo con el perfil
de instalación:

```dotenv
DEVELOPER_MINTER_SHOULDER=200
SANDBOX_MINTER_SHOULDER=200
PRODUCTION_MINTER_SHOULDER=201
```

Durante la instalación, el Deployer valida el formato `2xx` y escribe el valor
seleccionado como `MINTER_SHOULDER` en
`components/dark-core-minter-api/.env.integration`. No es necesario
editar manualmente el `.env.integration` del minter; este archivo se regenera
en cada instalación.

## Asignación y operación

- El código `xx` debe ser único entre los minters que puedan emitir bajo un
  mismo NAAN. Se debe mantener un registro de `NAAN`, shoulder, instancia,
  autoridad responsable, fecha de asignación y estado.
- `200` es la asignación predeterminada. Antes de operar más de un minter para
  un NAAN, cada instancia debe recibir un código diferente.
- Un shoulder que ya haya emitido ARKs no se reasigna a otra instancia ni se
  cambia sin una decisión operativa explícita. Cambiarlo inicia otro contador
  para el namespace `NAAN:shoulder`.
- El minter valida el formato al iniciarse: solo acepta `2` seguido de dos
  caracteres alfanuméricos ASCII en minúscula. Valores como `001`, `00x`,
  `x01`, `20` o `2S0` son rechazados.

La secuencia NOID sigue siendo independiente por namespace `NAAN:shoulder`.
Por lo tanto, el formato evita colisiones con DARK 1, mientras que el registro
de asignaciones evita colisiones entre minters de DARK 2.
