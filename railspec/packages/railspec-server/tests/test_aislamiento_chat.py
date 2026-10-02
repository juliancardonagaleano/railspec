"""Aislamiento por organización y workspace del almacén del chat (``AlmacenChat``).

Mismo método que ``test_aislamiento_almacenes``: un ``db`` espía registra el filtro de cada consulta y la
prueba falla si alguna no lleva org y workspace, también las lecturas y los reemplazos por ``_id`` (un id
que existe en otro workspace no se lee ni se pisa). Cada método público tiene su receta, y un método nuevo
sin receta rompe ``test_cada_metodo_publico_tiene_receta`` hasta que se clasifique.

Dos excepciones, documentadas en ``chat/almacen.py``: la puerta de entrada por id (``conversacion``), que se
acota por la persona porque las rutas no llevan workspace, y el consumo de fuga por persona y día, que es de
la organización.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from apoyo_motor import ORG, WS, WS_ALCANCE
from pymongo.errors import DuplicateKeyError
from railspec.contracts.insumo import Insumo
from railspec.server.chat.almacen import AlmacenChat
from railspec.server.chat.normalizacion import HuellasContexto
from test_aislamiento_almacenes import EspiaDb, revisar
from test_chat_servicio import (
    JULIAN as CABECERA,
)
from test_chat_servicio import (
    PROSA,
    cliente,
    montar,
    nueva,
    paso_leer,
    paso_responder,
    preguntar,
    ref_archivo,
    respuesta_de,
)

AUTOR = 83125327
OTRA_PERSONA = 7
OTRO_WS = "otro-workspace"
OTRA_ORG = "otra-org"
AHORA = datetime(2026, 10, 2, 12, tzinfo=UTC)
OTRO_ALCANCE = WS_ALCANCE.model_copy(update={"workspace": OTRO_WS})


# --- una conversación real ------------------------------------------------------------------------


def _poblar() -> SimpleNamespace:
    """Una conversación hecha por la API: pregunta que lee código, marca, insumo y lecturas."""

    almacen, _, app = montar(paso_leer(), paso_responder(PROSA, refs=[ref_archivo()]))

    async def caso() -> tuple[uuid.UUID, Insumo]:
        async with cliente(app) as c:
            conv = await nueva(c)
            _, eventos = await preguntar(c, conv)
            ruta = f"/v1/chat/conversaciones/{conv.id}"
            r = await c.patch(
                f"{ruta}/mensajes/{respuesta_de(eventos).id}",
                json={"conservar_en_insumo": True},
                headers=CABECERA,
            )
            assert r.status_code == 200, r.text
            r = await c.post(f"{ruta}/insumo", json={"objetivo": "Entender el total"}, headers=CABECERA)
            assert r.status_code == 201, r.text
            insumo = Insumo.model_validate(r.json()["insumo"])
            r = await c.get(ruta, headers=CABECERA)
            assert r.status_code == 200 and len(r.json()["mensajes"]) == 2, r.text
            r = await c.get("/v1/chat/conversaciones", params={"org": ORG, "workspace": WS}, headers=CABECERA)
            assert [x["id"] for x in r.json()["conversaciones"]] == [str(conv.id)], r.text
            return conv.id, insumo

    id_, insumo = asyncio.run(caso())
    chat = AlmacenChat(almacen.db, crear_indices=False)
    conv, meta = chat.conversacion(id_, AUTOR)
    mensajes = chat.mensajes(conv.alcance, id_)
    huellas = chat.huellas(conv.alcance, id_)
    assert [m.rol.value for m in mensajes] == ["usuario", "asistente"]
    assert not huellas.vacio and meta["commits"]
    assert chat.obtener_insumo(conv.alcance, insumo.id) is not None
    return SimpleNamespace(
        db=almacen.db,
        chat=chat,
        conv=conv,
        meta=meta,
        usuario=mensajes[0],
        asistente=mensajes[1],
        huellas=huellas,
        insumo=insumo,
        alcance=conv.alcance,
    )


def _espiado(c: SimpleNamespace) -> list[tuple[str, str, Any]]:
    registro: list[tuple[str, str, Any]] = []
    c.chat = AlmacenChat(EspiaDb(c.db, registro), crear_indices=False)
    return registro


# --- la regla de cada consulta --------------------------------------------------------------------


def _revisar(registro: list[tuple[str, str, Any]]) -> list[str]:
    """Las consultas del registro que incumplen la regla de su colección.

    ``chat_fuga_usuario`` es de la organización (su ``_id`` lleva la org); las demás exigen org y
    workspace. La única lectura por id sin workspace, ``conversacion``, tiene su propia comprobación
    en la receta: aquí aparecería como incumplimiento.
    """

    return revisar([e for e in registro if e[0] != "chat_fuga_usuario"]) + revisar(
        [e for e in registro if e[0] == "chat_fuga_usuario"], regla="org"
    )


def _recetas() -> dict[str, tuple[Any, str]]:
    """``Clase.método`` -> (receta, regla): ``ws`` por defecto; ``org`` y ``autor`` son a propósito."""

    def con_id_nuevo(m):  # los mensajes e insumos se insertan: id nuevo para no chocar con el sembrado
        return m.model_copy(update={"id": uuid.uuid4()})

    return {
        "AlmacenChat.crear_indices": (lambda c: c.chat.crear_indices(), "ws"),
        "AlmacenChat.guardar_conversacion": (
            lambda c: c.chat.guardar_conversacion(
                c.conv, n_tokens=c.meta["n_tokens"], commits=c.meta["commits"], autor_id=AUTOR
            ),
            "ws",
        ),
        "AlmacenChat.conversacion": (lambda c: c.chat.conversacion(c.conv.id, AUTOR), "autor"),
        "AlmacenChat.conversaciones_de": (
            lambda c: c.chat.conversaciones_de(c.alcance, AUTOR, datetime.now(UTC), 10),
            "ws",
        ),
        "AlmacenChat.actualizar_commits": (
            lambda c: c.chat.actualizar_commits(c.alcance, c.conv.id, {"otro-repo": "abc123"}),
            "ws",
        ),
        "AlmacenChat.agregar_mensaje": (
            lambda c: c.chat.agregar_mensaje(con_id_nuevo(c.usuario), c.conv.expira_en),
            "ws",
        ),
        "AlmacenChat.reemplazar_mensaje": (lambda c: c.chat.reemplazar_mensaje(c.asistente), "ws"),
        "AlmacenChat.mensajes": (lambda c: c.chat.mensajes(c.alcance, c.conv.id), "ws"),
        "AlmacenChat.agregar_huellas": (
            lambda c: c.chat.agregar_huellas(c.alcance, c.conv.id, c.huellas, c.conv.expira_en),
            "ws",
        ),
        "AlmacenChat.huellas": (lambda c: c.chat.huellas(c.alcance, c.conv.id), "ws"),
        "AlmacenChat.fuga_usuario": (lambda c: c.chat.fuga_usuario(ORG, AUTOR, AHORA), "org"),
        "AlmacenChat.sumar_fuga_usuario": (lambda c: c.chat.sumar_fuga_usuario(ORG, AUTOR, AHORA, 5), "org"),
        "AlmacenChat.guardar_insumo": (lambda c: c.chat.guardar_insumo(con_id_nuevo(c.insumo)), "ws"),
        "AlmacenChat.obtener_insumo": (lambda c: c.chat.obtener_insumo(c.alcance, c.insumo.id), "ws"),
    }


#: Solo insertan un documento nuevo (su alcance va en el documento) o crean índices.
SIN_CONSULTA = {
    "AlmacenChat.crear_indices",
    "AlmacenChat.agregar_huellas",
    "AlmacenChat.guardar_insumo",
}


def _publicos() -> set[str]:
    return {
        f"AlmacenChat.{metodo}"
        for metodo, _ in inspect.getmembers(AlmacenChat, inspect.isfunction)
        if not metodo.startswith("_")
    }


def test_cada_metodo_publico_tiene_receta():
    recetas = set(_recetas())
    assert _publicos() - recetas == set(), "métodos públicos sin receta: añade la receta y su regla"
    assert recetas - _publicos() == set(), "recetas de métodos que ya no existen"


@pytest.mark.parametrize("metodo", sorted(_recetas()))
def test_metodo_publico_consulta_con_alcance(metodo):
    c = _poblar()
    registro = _espiado(c)
    receta, regla = _recetas()[metodo]
    receta(c)
    if regla == "autor":
        # La puerta de entrada por id: sin workspace, pero solo de la persona.
        assert [(m, f) for _, m, f in registro] == [("find_one", {"_id": str(c.conv.id), "_autor": AUTOR})]
    else:
        assert _revisar(registro) == []
    if metodo not in SIN_CONSULTA:
        assert registro, "la receta no llegó a consultar: no prueba nada"


def test_el_espia_marca_las_consultas_del_chat_sin_alcance():
    c = _poblar()
    registro = _espiado(c)
    c.chat.db.chat_mensajes.find({"_conversacion": str(c.conv.id)})
    c.chat.db.chat_huellas.find({"_conversacion": str(c.conv.id), "alcance.org": ORG})  # la org sola no basta
    c.chat.db.chat_conversaciones.replace_one({"_id": str(c.conv.id)}, {"_id": str(c.conv.id)})
    c.chat.db.chat_fuga_usuario.find_one({"_id": "otra/1/2026-10-02"})  # ni siquiera la de la org
    assert [m.split("(")[0] for m in _revisar(registro)] == [
        "chat_mensajes.find",
        "chat_huellas.find",
        "chat_conversaciones.replace_one",
        "chat_fuga_usuario.find_one",
    ]


# --- el recorrido completo del servicio -----------------------------------------------------------


def test_el_recorrido_del_servicio_nunca_consulta_sin_alcance(monkeypatch):
    """Crear, preguntar (con lectura de código), marcar, exportar, abrir y listar, con el cableado real."""

    registro: list[tuple[str, str, Any]] = []
    original = AlmacenChat.__init__

    def con_espia(self, db, **kw):
        original(self, EspiaDb(db, registro), **kw)

    monkeypatch.setattr(AlmacenChat, "__init__", con_espia)
    _poblar()
    colecciones = {col for col, _, _ in registro}
    assert {
        "chat_conversaciones",
        "chat_mensajes",
        "chat_huellas",
        "chat_fuga_usuario",
        "insumos",
    } <= colecciones
    assert len(registro) > 20
    # La única lectura de conversaciones por id es la de la persona (``conversacion``): se comprueba aparte.
    por_id = [e for e in registro if e[:2] == ("chat_conversaciones", "find_one")]
    assert por_id and all(set(filtro) == {"_id", "_autor"} for _, _, filtro in por_id)
    assert _revisar([e for e in registro if e not in por_id]) == []


# --- el mismo id en otro workspace ----------------------------------------------------------------


def test_una_conversacion_con_el_id_de_otro_workspace_no_se_pisa():
    c = _poblar()
    antes = c.db.chat_conversaciones.find_one({"_id": str(c.conv.id)})
    ajena = c.conv.model_copy(update={"alcance": OTRO_ALCANCE})
    with pytest.raises(DuplicateKeyError):
        c.chat.guardar_conversacion(ajena, n_tokens=1, commits={}, autor_id=OTRA_PERSONA)
    assert c.db.chat_conversaciones.find_one({"_id": str(c.conv.id)}) == antes
    # En su propio workspace sí se reemplaza (es lo que hace cada pregunta al cerrar).
    c.chat.guardar_conversacion(
        c.conv.model_copy(update={"bloqueos": 1}),
        n_tokens=c.meta["n_tokens"],
        commits=c.meta["commits"],
        autor_id=AUTOR,
    )
    assert c.chat.conversacion(c.conv.id, AUTOR)[0].bloqueos == 1


def test_la_conversacion_de_otra_persona_no_sale_de_mongo():
    c = _poblar()
    assert c.chat.conversacion(c.conv.id, OTRA_PERSONA) is None
    assert c.chat.conversacion(uuid.uuid4(), AUTOR) is None
    assert c.chat.conversacion(c.conv.id, AUTOR)[0] == c.conv


def test_mensajes_y_huellas_de_otro_workspace_no_se_leen_ni_se_mezclan():
    c = _poblar()
    assert c.chat.mensajes(OTRO_ALCANCE, c.conv.id) == []
    assert c.chat.mensajes(c.alcance.model_copy(update={"org": OTRA_ORG}), c.conv.id) == []
    assert c.chat.huellas(OTRO_ALCANCE, c.conv.id) == HuellasContexto()
    assert c.chat.huellas(c.alcance.model_copy(update={"org": OTRA_ORG}), c.conv.id) == HuellasContexto()
    assert c.chat.mensajes(c.alcance, c.conv.id) == [c.usuario, c.asistente]
    assert c.chat.huellas(c.alcance, c.conv.id) == c.huellas

    # Escribir con el id de la conversación ajena no cuenta ni aparece en la propia.
    intruso = c.usuario.model_copy(update={"id": uuid.uuid4(), "alcance": OTRO_ALCANCE})
    c.chat.agregar_mensaje(intruso, c.conv.expira_en)
    assert c.db.chat_mensajes.find_one({"_id": str(intruso.id)})["_orden"] == 0  # no continúa la cuenta ajena
    assert c.chat.mensajes(c.alcance, c.conv.id) == [c.usuario, c.asistente]
    assert c.chat.mensajes(OTRO_ALCANCE, c.conv.id) == [intruso]
    c.chat.agregar_huellas(OTRO_ALCANCE, c.conv.id, HuellasContexto({"x"}, {"y"}, {"z"}), c.conv.expira_en)
    assert c.chat.huellas(c.alcance, c.conv.id) == c.huellas


def test_un_mensaje_con_el_id_de_otro_workspace_no_se_pisa_ni_se_reemplaza():
    c = _poblar()
    antes = c.db.chat_mensajes.find_one({"_id": str(c.asistente.id)})
    ajeno = c.asistente.model_copy(update={"alcance": OTRO_ALCANCE, "conservar_en_insumo": False})
    with pytest.raises(DuplicateKeyError):
        c.chat.agregar_mensaje(ajeno, c.conv.expira_en)
    c.chat.reemplazar_mensaje(ajeno)  # no coincide con nada: no hace nada
    assert c.db.chat_mensajes.find_one({"_id": str(c.asistente.id)}) == antes
    assert antes["conservar_en_insumo"] is True


def test_los_commits_de_otro_workspace_no_se_tocan():
    c = _poblar()
    repo = next(iter(c.meta["commits"]))
    c.chat.actualizar_commits(OTRO_ALCANCE, c.conv.id, {"repo-nuevo": "abc123"})
    c.chat.actualizar_commits(
        c.alcance.model_copy(update={"org": OTRA_ORG}), c.conv.id, {"repo-nuevo": "abc123"}
    )
    assert c.chat.conversacion(c.conv.id, AUTOR)[1]["commits"] == c.meta["commits"]
    # En el suyo se fija la primera vez y nunca cambia.
    c.chat.actualizar_commits(c.alcance, c.conv.id, {"repo-nuevo": "abc123", repo: "otro-commit"})
    assert c.chat.conversacion(c.conv.id, AUTOR)[1]["commits"] == {
        **c.meta["commits"],
        "repo-nuevo": "abc123",
    }


def test_un_insumo_con_el_id_de_otro_workspace_no_se_lee_ni_se_pisa():
    c = _poblar()
    assert c.chat.obtener_insumo(OTRO_ALCANCE, c.insumo.id) is None
    assert c.chat.obtener_insumo(c.alcance.model_copy(update={"org": OTRA_ORG}), c.insumo.id) is None
    antes = c.db.insumos.find_one({"_id": str(c.insumo.id)})
    with pytest.raises(DuplicateKeyError):
        c.chat.guardar_insumo(c.insumo.model_copy(update={"alcance": OTRO_ALCANCE}))
    assert c.db.insumos.find_one({"_id": str(c.insumo.id)}) == antes
    assert c.chat.obtener_insumo(c.alcance, c.insumo.id) == c.insumo


def test_la_fuga_por_persona_y_dia_es_de_la_organizacion():
    c = _poblar()
    dia = datetime.now(UTC) + timedelta(days=30)  # sin consumo previo y sin caducar (el TTL cuenta)
    assert c.chat.sumar_fuga_usuario(ORG, AUTOR, dia, 5) == 5
    assert c.chat.sumar_fuga_usuario(ORG, AUTOR, dia, 3) == 8
    assert c.chat.fuga_usuario(OTRA_ORG, AUTOR, dia) == 0  # otra organización no comparte tope
    assert c.chat.fuga_usuario(ORG, OTRA_PERSONA, dia) == 0
    assert c.chat.fuga_usuario(ORG, AUTOR, dia + timedelta(days=1)) == 0
