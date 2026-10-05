"""Cifrado en reposo de las claves de suscripciones: AES-GCM, clave maestra del entorno, rotación y AAD."""

from __future__ import annotations

import base64
import os

import pytest
from railspec.server.config import Configuracion
from railspec.server.proveedores.cifrado import (
    VARIABLE,
    VARIABLE_ANTERIOR,
    Cifrador,
    ClaveMaestraAusente,
    ErrorCifrado,
)

SECRETO = "sk-ant-api03-ESTA-CLAVE-NO-DEBE-APARECER"


def _maestra() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def _cifrador(actual: str, *anteriores: str) -> Cifrador:
    cifrador = Cifrador.desde_entorno({VARIABLE: actual, VARIABLE_ANTERIOR: ",".join(anteriores)})
    assert cifrador is not None
    return cifrador


def test_ida_y_vuelta_y_el_valor_guardado_no_contiene_la_clave():
    c = _cifrador(_maestra())
    guardado = c.cifrar(SECRETO, "acme", "foundry-eu")
    assert SECRETO not in guardado
    assert SECRETO.encode() not in base64.urlsafe_b64decode(guardado.split(".")[2])
    assert c.descifrar(guardado, "acme", "foundry-eu") == SECRETO
    # Cada cifrado lleva su propio nonce.
    assert c.cifrar(SECRETO, "acme", "foundry-eu") != guardado


def test_un_valor_cifrado_no_sirve_en_otra_suscripcion_ni_otra_organizacion():
    c = _cifrador(_maestra())
    guardado = c.cifrar(SECRETO, "acme", "foundry-eu")
    for org, id_ in (("acme", "otra"), ("otra-org", "foundry-eu")):
        with pytest.raises(ErrorCifrado) as exc:
            c.descifrar(guardado, org, id_)
        assert exc.value.codigo == "cifrado-ilegible" and SECRETO not in exc.value.detalle


def test_un_valor_alterado_o_con_formato_ajeno_se_rechaza_sin_filtrar():
    c = _cifrador(_maestra())
    guardado = c.cifrar(SECRETO, "acme", "x")
    version, kid, cuerpo = guardado.split(".")
    crudo = bytearray(base64.urlsafe_b64decode(cuerpo))
    crudo[-1] ^= 1
    alterado = ".".join((version, kid, base64.urlsafe_b64encode(bytes(crudo)).decode()))
    with pytest.raises(ErrorCifrado, match="no se pudo descifrar"):
        c.descifrar(alterado, "acme", "x")
    for malo in ("", "texto-en-claro", SECRETO, "v2.00000000.AAAA"):
        with pytest.raises(ErrorCifrado) as exc:
            c.descifrar(malo, "acme", "x")
        assert exc.value.codigo == "cifrado-ilegible" and SECRETO not in exc.value.detalle


def test_rotacion_la_anterior_sigue_descifrando_lo_guardado_y_lo_nuevo_usa_la_actual():
    vieja, nueva = _maestra(), _maestra()
    guardado_viejo = _cifrador(vieja).cifrar(SECRETO, "acme", "x")
    rotado = _cifrador(nueva, vieja)
    assert rotado.descifrar(guardado_viejo, "acme", "x") == SECRETO
    guardado_nuevo = rotado.cifrar(SECRETO, "acme", "x")
    assert guardado_nuevo.split(".")[1] != guardado_viejo.split(".")[1]
    # Sin la clave anterior, lo viejo dice qué falta (por su id, que no revela la clave).
    with pytest.raises(ErrorCifrado) as exc:
        _cifrador(nueva).descifrar(guardado_viejo, "acme", "x")
    assert (
        exc.value.codigo == "clave-maestra-desconocida" and guardado_viejo.split(".")[1] in exc.value.detalle
    )
    assert VARIABLE_ANTERIOR in exc.value.detalle and vieja not in exc.value.detalle


def test_sin_clave_maestra_no_hay_cifrador_y_la_falta_se_explica():
    assert Cifrador.desde_entorno({}) is None
    assert Cifrador.desde_entorno({VARIABLE: "  "}) is None
    with pytest.raises(ClaveMaestraAusente) as exc:
        Cifrador([])
    assert exc.value.codigo == "clave-maestra-ausente" and VARIABLE in exc.value.detalle


@pytest.mark.parametrize(
    "entorno",
    [
        {VARIABLE: "no es base64!!"},
        {VARIABLE: base64.b64encode(b"corta").decode()},
        {VARIABLE: base64.b64encode(os.urandom(48)).decode()},
        {VARIABLE_ANTERIOR: _maestra()},  # anterior sin actual
    ],
)
def test_una_clave_maestra_mal_formada_es_un_error_claro_y_no_filtra_el_valor(entorno):
    with pytest.raises(ErrorCifrado) as exc:
        Cifrador.desde_entorno(entorno)
    assert exc.value.codigo == "clave-maestra-invalida"
    assert all(valor not in exc.value.detalle for valor in entorno.values())


def test_la_misma_clave_actual_y_anterior_se_rechaza():
    k = _maestra()
    with pytest.raises(ErrorCifrado, match="repite"):
        Cifrador.desde_entorno({VARIABLE: k, VARIABLE_ANTERIOR: k})


def test_la_configuracion_lee_la_clave_maestra_sin_mostrarla():
    k = _maestra()
    config = Configuracion.desde_entorno({VARIABLE: k})
    assert config.cifrador is not None and k not in repr(config)
    assert Configuracion.desde_entorno({}).cifrador is None
    with pytest.raises(ValueError, match="32 bytes"):
        Configuracion.desde_entorno({VARIABLE: base64.b64encode(b"corta").decode()})
