"""Prueba de humo manual contra los proveedores reales: ``python -m railspec.server.humo``.

Lee las mismas variables de entorno que el servidor (ver
``railspec/docs/proveedores.md``) y, sin Mongo ni unidades:

1. lee el catálogo de cada proveedor configurado y lo imprime;
2. valida el perfil ``estandar`` contra él, con las claves de cada nivel pedido
   (el nivel no restringe proveedores);
3. hace una llamada estructurada mínima por nivel con el modelo que elija la
   selección (``--sin-llamada`` la omite) e imprime uso, región y despliegue;
4. si hay ``RAILSPEC_PCE_URL``, consulta la gobernanza una vez.

Sale con código 1 si algo falla. Gasta unas decenas de tokens por nivel.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import BaseModel
from railspec.contracts.comun import (
    AlcanceUnidad,
    AlcanceWorkspace,
    GateFase,
    GobernanzaConsultada,
    NivelCodigo,
    Perfil,
    Riesgo,
)

from .app import _contexto, _proveedores
from .config import Configuracion
from .estado import almacen_en_memoria
from .motor.gate import requisitos_del_gate
from .motor.perfiles import perfil_por_defecto, requisito, tope_gate
from .proveedores import ErrorProveedor, PerfilInsatisfacible, PeticionModelo


class Eco(BaseModel):
    ok: bool
    eco: str


async def humo(org: str, niveles: list[NivelCodigo], llamar: bool) -> int:
    config = Configuracion.desde_entorno()
    almacen = almacen_en_memoria()
    proveedores = _proveedores(config, almacen)
    fallos = 0
    print(f"proveedores configurados: {', '.join(p.value for p in proveedores.configurados) or 'ninguno'}")
    await proveedores.refrescar(org)
    if proveedores.catalogo is not None:
        for p in sorted(proveedores.catalogo.proveedores):
            error = proveedores.catalogo.error(p)
            print(f"\ncatálogo {p.value}{f' (error: {error})' if error else ''}:")
            for e in proveedores.catalogo.entradas(p):
                caps = e.capacidades
                print(
                    f"  {e.modelo:<24} despliegue={e.despliegue or '-':<20} region={e.region or '-':<12} "
                    f"structured={caps.structured_outputs} efforts={[x.value for x in caps.efforts]}"
                )
            fallos += 1 if error and not proveedores.catalogo.leido(p) else 0
    else:
        print("sin catálogo: la región auditada sale de RAILSPEC_FOUNDRY_REGION")

    ws = AlcanceWorkspace(org=org, workspace="humo")
    perfil = perfil_por_defecto(ws, Perfil.estandar)
    for nivel in niveles:
        print(f"\nnivel {nivel.value}:")
        pares = requisitos_del_gate(perfil, nivel, tope_gate(perfil, Riesgo.alto).adversarial)
        motivos = proveedores.validar(pares, org=org)
        if motivos:
            fallos += 1
            for m in motivos:
                print(f"  perfil estandar NO satisfacible: {m}")
            continue
        print("  perfil estandar satisfacible")
        if not llamar:
            continue
        req = requisito(perfil, "critico-estructural", GateFase.tasks, nivel)
        try:
            eleccion = proveedores.elegir("critico-estructural", req, org=org)
            r = await eleccion.proveedor.completar(
                PeticionModelo(
                    rol="humo",
                    modelo=eleccion.modelo,
                    sistema="Responde solo con el JSON pedido.",
                    contenido='Devuelve ok=true y eco="railspec".',
                    esquema=Eco,
                    effort=req.effort,
                    max_tokens=2048,
                    despliegue=eleccion.despliegue,
                    region=eleccion.region,
                )
            )
        except (ErrorProveedor, PerfilInsatisfacible) as exc:
            fallos += 1
            print(f"  llamada FALLÓ: {exc}")
            continue
        u = r.uso
        print(
            f"  llamada ok: {r.proveedor.value}/{r.modelo} despliegue={eleccion.despliegue or '-'} "
            f"region={r.region} valor={r.valor.model_dump()} tokens={u.tokens_entrada}+{u.tokens_salida} "
            f"cache={u.tokens_cache_lectura} costo~{u.costo_usd} USD {u.duracion_ms} ms"
        )

    if config.pce_url:
        gob = _contexto(config, almacen)
        r = await gob.consultar(
            AlcanceUnidad(org=org, workspace="humo", unidad="0001-humo"), GateFase.spec, "seguridad de datos"
        )
        print(f"\ngobernanza (PCE): {r.consultada.value}, {len(r.items)} ítems {r.detalle}")
        for i in r.items[:5]:
            print(f"  [{i.tipo}] {i.id}: {i.titulo}")
        fallos += 0 if r.consultada == GobernanzaConsultada.si else 1
    else:
        print("\nsin RAILSPEC_PCE_URL: gobernanza no probada")
    print(f"\n{'OK' if not fallos else f'{fallos} fallo(s)'}")
    return 1 if fallos else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m railspec.server.humo", description=__doc__.split("\n")[0])
    p.add_argument(
        "--org", default="humo", help="organización con la que se persiste el catálogo (en memoria)"
    )
    p.add_argument(
        "--nivel",
        action="append",
        choices=[n.value for n in NivelCodigo],
        help="nivel a validar y probar; repetible (por defecto restringido y abierto)",
    )
    p.add_argument(
        "--sin-llamada", action="store_true", help="no llama al modelo: solo catálogo y validación"
    )
    a = p.parse_args(argv)
    niveles = [NivelCodigo(n) for n in (a.nivel or ["restringido", "abierto"])]
    return asyncio.run(humo(a.org, niveles, not a.sin_llamada))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
