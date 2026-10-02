"""``GET /v1/chat/conversaciones``: las conversaciones de la persona en un workspace.

Cada persona solo ve las suyas (una ajena no se revela), recientes primero y con un tope
de 50; el filtro de workspace y de autor va en la consulta de Mongo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apoyo_motor import ORG, WS
from railspec.contracts.chat import Conversacion
from test_chat_servicio import ANA, JULIAN, cliente, correr, montar, nueva

RUTA = "/v1/chat/conversaciones"


async def listar(c, cabecera=JULIAN, **consulta):
    consulta = {"org": ORG, "workspace": WS, **consulta}
    return await c.get(RUTA, params=consulta, headers=cabecera)


def ids(r) -> list[str]:
    assert r.status_code == 200, r.text
    return [x["id"] for x in r.json()["conversaciones"]]


def test_lista_las_propias_recientes_primero_y_con_el_modelo_conversacion():
    _, _, app = montar()

    async def caso():
        async with cliente(app) as c:
            creadas = [await nueva(c) for _ in range(3)]
            r = await listar(c)
            assert ids(r) == [str(x.id) for x in reversed(creadas)]
            leidas = [Conversacion.model_validate(x) for x in r.json()["conversaciones"]]
            assert leidas == sorted(leidas, key=lambda x: x.creada_en, reverse=True)
            assert leidas[0].autor.github_id == 83125327
            assert leidas[0].repositorios == creadas[0].repositorios
            # La lista no trae mensajes ni nada fuera del contrato.
            assert set(r.json()) == {"conversaciones"}

    correr(caso)


def test_la_conversacion_de_otra_persona_no_aparece():
    _, _, app = montar()

    async def caso():
        async with cliente(app) as c:
            mia = await nueva(c)
            de_ana = await nueva(c, ANA)
            assert ids(await listar(c)) == [str(mia.id)]
            assert ids(await listar(c, ANA)) == [str(de_ana.id)]

    correr(caso)


def test_otro_workspace_y_org_no_mezclan_conversaciones():
    _, _, app = montar()

    async def caso():
        async with cliente(app) as c:
            await nueva(c)
            assert ids(await listar(c, workspace="otro-ws")) == []
            assert ids(await listar(c, org="otra-org")) == []

    correr(caso)


def test_tope_de_50_y_limite_opcional():
    _, _, app = montar()

    async def caso():
        async with cliente(app) as c:
            creadas = [await nueva(c) for _ in range(51)]
            recientes = [str(x.id) for x in reversed(creadas)]
            todas = ids(await listar(c))
            assert todas == recientes[:50]
            assert ids(await listar(c, limite=500)) == recientes[:50]
            assert ids(await listar(c, limite=2)) == recientes[:2]
            assert ids(await listar(c, limite=0)) == recientes[:1]

    correr(caso)


def test_no_lista_las_vencidas():
    almacen, _, app = montar()

    async def caso():
        async with cliente(app) as c:
            vigente, vencida = await nueva(c), await nueva(c)
            almacen.db.chat_conversaciones.update_one(
                {"_id": str(vencida.id)}, {"$set": {"_expira": datetime.now(UTC) - timedelta(minutes=1)}}
            )
            assert ids(await listar(c)) == [str(vigente.id)]

    correr(caso)


def test_sin_credencial_o_con_entrada_invalida():
    _, _, app = montar()

    async def caso():
        async with cliente(app) as c:
            assert (await c.get(RUTA, params={"org": ORG, "workspace": WS})).status_code == 401
            for consulta in (
                {},
                {"org": ORG},
                {"org": "ACME", "workspace": WS},
                {"org": ORG, "workspace": WS, "limite": "x"},
            ):
                r = await c.get(RUTA, params=consulta, headers=JULIAN)
                assert r.status_code == 422, (consulta, r.text)
                assert r.json()["codigo"] == "entrada-invalida"

    correr(caso)
