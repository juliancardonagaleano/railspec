# sdd-kit

Kit de Spec-Driven Development (SDD) — motor genérico del protocolo,
agnóstico a cualquier repositorio destino concreto.

> Esqueleto inicial. Este README se completa a medida que el contenido del
> kit (instalador, scripts de andamiaje, prosa normativa, skills `sdd-*`) se
> extrae desde su repositorio de origen y se generaliza.

## Qué es

Un repositorio propio que aloja el instalador y el motor del protocolo SDD
— separado de cualquier implementación concreta que lo consuma — para que
pueda evolucionar por su cuenta e instalarse sobre más de un destino.

## Instalación en un destino

```
python3 installer/cli.py --target <ruta-destino> --install
```

Ver `docs/` para el detalle del contenido del kit y su manifiesto.

## Railspec

El repositorio también aloja [Railspec](railspec/README.md), un plugin que da rieles SDD a
los arneses con un motor remoto. Puede instalarse junto al kit en el mismo repositorio;
para traer unidades y, si quieres, quitar el kit:
[railspec/docs/migracion-kit.md](railspec/docs/migracion-kit.md).
