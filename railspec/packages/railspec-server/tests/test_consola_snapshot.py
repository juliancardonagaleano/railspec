"""Consola: el snapshot no carga ``diff`` ni ``fragmentos`` (B12), ni sale en ninguna respuesta."""

from __future__ import annotations

import json

from apoyo_motor import JULIAN, ORG, WS, aprobar, avanzar, entrada_start, reporte
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.repositorio import Rol
from railspec.contracts.snapshot import Fragmento
from railspec.server.consola.almacen import AlmacenConsola
from test_consola import ANA_ID, CSRF, Montaje, _snapshot_con_delta, asignar, correr


def _snapshot_interno(orden):
    base = _snapshot_con_delta(orden)
    texto = "def firmar_pdf():\n    return 'MARCA-FRAGMENTO-77aa'"
    fragmento = Fragmento(ruta="src/pdf.py", sha256="b" * 64, texto=texto)
    datos = base.model_dump()
    datos.update(
        nivel_codigo=NivelCodigo.interno,
        diff="--- a/src/pdf.py\n+++ b/src/pdf.py\n+    return 'MARCA-DIFF-55bb'\n",
        fragmentos=[fragmento.model_dump()],
    )
    return type(base).model_validate(datos)


def test_snapshot_de_consola_sin_diff_ni_fragmentos_en_ninguna_respuesta():
    async def caso():
        m = Montaje(nivel=NivelCodigo.interno)
        salida = await m.motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        for _ in range(20):
            av = await avanzar(m.motor, alcance)
            if av.tipo == "cerrada":
                break
            if av.tipo == "orden":
                r = reporte(av.orden)
                if av.orden.tipo == "implementar":
                    r = r.model_copy(update={"snapshot": _snapshot_interno(av.orden)})
                await m.motor.report(r, JULIAN)
            elif av.tipo == "checkpoint":
                await aprobar(m.motor, alcance, av.checkpoint.id)
        # El motor sí lo guarda completo (el arnés lo necesita)...
        guardados = list(m.almacen.db.snapshots.find({}))
        assert len(guardados) == 1 and guardados[0]["diff"] and guardados[0]["fragmentos"]
        sid = str(guardados[0]["_id"])
        # ...pero la capa de datos de la consola ni lo carga.
        snap = AlmacenConsola(m.almacen.db).snapshot(alcance, sid)
        assert snap is not None and snap.diff is None and snap.fragmentos is None
        assert snap.archivos and snap.delta_indice is not None
        asignar(m.almacen, Rol.lector, ANA_ID)
        base = f"/consola/api/orgs/{ORG}/workspaces/{WS}"
        async with m.cliente("tk-ana") as c:
            textos = []
            for ruta in ("", "/linea-de-tiempo", "/trazabilidad"):
                r = await c.get(f"{base}/unidades/{alcance.unidad}{ruta}")
                assert r.status_code == 200, r.text
                textos.append(r.text)
            traza = json.loads(textos[2])
            assert traza["criterios"][0]["archivos"] and traza["criterios"][0]["simbolos"]
            textos.append((await c.get(f"{base}/resumen")).text)
            textos.append((await c.get(f"{base}/auditoria")).text)
            textos.append((await c.get("/consola/api/tools")).text)
            r = await c.post(
                "/consola/api/tools/unit.status",
                json={"unidad": alcance.model_dump(mode="json", exclude_none=True)},
                headers=CSRF,
            )
            textos.append(r.text)
            for texto in textos:
                for marca in ("MARCA-DIFF-55bb", "MARCA-FRAGMENTO-77aa"):
                    assert marca not in texto

    correr(caso())
