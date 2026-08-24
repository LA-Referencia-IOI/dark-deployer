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

Todo shoulder de DARK 2 tiene exactamente tres dígitos y el formato `2MM`:

```text
2MM
│└─ código de dos dígitos asignado al minter
└── versión de DARK
```

El valor predeterminado es `200`. Por ejemplo, un minter con código `01` usa
`MINTER_SHOULDER=201` y emite identificadores como:

```text
ark:41046/2010000000<dígito-de-control>
```

El prefijo inicial `2` separa de forma inequívoca los nombres emitidos por
DARK 2 del espacio `00*` reservado para DARK 1.

## Asignación y operación

- El código `MM` debe ser único entre los minters que puedan emitir bajo un
  mismo NAAN. Se debe mantener un registro de `NAAN`, shoulder, instancia,
  autoridad responsable, fecha de asignación y estado.
- `200` es la asignación predeterminada. Antes de operar más de un minter para
  un NAAN, cada instancia debe recibir un código diferente.
- Un shoulder que ya haya emitido ARKs no se reasigna a otra instancia ni se
  cambia sin una decisión operativa explícita. Cambiarlo inicia otro contador
  para el namespace `NAAN:shoulder`.
- El minter valida el formato al iniciarse: solo acepta tres dígitos que
  comiencen por `2`. Valores como `001`, `00x`, `x01` o `20` son rechazados.

La secuencia NOID sigue siendo independiente por namespace `NAAN:shoulder`.
Por lo tanto, el formato evita colisiones con DARK 1, mientras que el registro
de asignaciones evita colisiones entre minters de DARK 2.
