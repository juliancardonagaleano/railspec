"""Corpus de regresión del gate de salida del chat.

Cada caso de ataque es un intento concreto de sacar código, secretos o datos
fuera del alcance; cada caso de prosa es una respuesta legítima que debe
pasar. Ningún cambio al gate entra sin que esta suite pase completa.
"""

from __future__ import annotations

import base64
import codecs
from datetime import UTC, datetime

import pytest
from railspec.contracts.chat import ReglaGate, ResultadoRegla, VeredictoGateSalida
from railspec.server.chat import forma
from railspec.server.chat.gate import Consumo, PoliticaGate, Visibilidad, evaluar
from railspec.server.chat.normalizacion import HuellasContexto

AHORA = datetime(2026, 10, 1, tzinfo=UTC)
COMMIT = "4063ae9" + "0" * 33
REPO = "certificados-api"

CODIGO = '''\
from decimal import Decimal

IMPUESTO_IVA = Decimal("0.19")


def calcular_total_pedido(pedido, descuentos=None):
    """Suma las líneas del pedido, aplica descuentos y luego el IVA."""
    subtotal = sum(linea.precio_unitario * linea.cantidad for linea in pedido.lineas)
    for descuento in descuentos or []:
        subtotal -= descuento.aplicar(subtotal)
    if subtotal < 0:
        raise ValueError("el subtotal no puede ser negativo")
    return (subtotal * (1 + IMPUESTO_IVA)).quantize(Decimal("0.01"))


class FirmadorPdf:
    def __init__(self, certificado, clave_privada):
        self._certificado = certificado
        self._clave = clave_privada

    def firmar(self, documento):
        firma = self._clave.sign(documento.hash_contenido(), algoritmo="sha256")
        documento.adjuntar_firma(firma, self._certificado)
        return documento
'''

#: Líneas del fragmento que un atacante querría sacar.
TROZO = "subtotal = sum(linea.precio_unitario * linea.cantidad for linea in pedido.lineas)"
TROZO_LARGO = (
    "subtotal = sum(linea.precio_unitario * linea.cantidad for linea in pedido.lineas) "
    "for descuento in descuentos or []: subtotal -= descuento.aplicar(subtotal)"
)

RESTRINGIDO = PoliticaGate(
    n_tokens=12, fragmentos_permitidos=False, tope_conversacion=1500, tope_usuario_dia=6000
)
ABIERTO = PoliticaGate(
    n_tokens=24, fragmentos_permitidos=True, tope_conversacion=4000, tope_usuario_dia=20000
)
VISIBLE = Visibilidad(org="acme", workspace="certificados", repositorios=frozenset({REPO}))


def huellas(politica=RESTRINGIDO, texto=CODIGO) -> HuellasContexto:
    h = HuellasContexto()
    h.agregar(texto, politica.parametros)
    return h


def respuesta(*textos: str, refs=None, preguntas=()) -> dict:
    return {
        "afirmaciones": [{"texto": t, "referencias": refs or []} for t in textos],
        "preguntas_abiertas": list(preguntas),
    }


SIN_CONSUMO = Consumo(0, 0)


def gate(crudo, *, politica=RESTRINGIDO, h=None, consumo=SIN_CONSUMO, cobrar=True):
    return evaluar(
        crudo,
        huellas=huellas(politica) if h is None else h,
        politica=politica,
        visibilidad=VISIBLE,
        consumo=consumo,
        ahora=AHORA,
        cobrar=cobrar,
    )


def fallidas(r) -> set[ReglaGate]:
    return set(r.veredicto.reglas_fallidas)


def ref_archivo(repo=REPO):
    return {"tipo": "archivo", "repositorio": repo, "commit": COMMIT, "ruta": "src/pedidos/total.py",
            "linea_inicio": 6, "linea_fin": 13}  # fmt: skip


# --- Prosa legítima: debe pasar -----------------------------------------------------------

PROSA = [
    "El total del pedido se calcula en calcular_total_pedido, en src/pedidos/total.py, que suma "
    "las líneas, descuenta y aplica el IVA definido en IMPUESTO_IVA.",
    "La firma la hace FirmadorPdf.firmar: toma el hash del contenido del documento, lo firma con la "
    "clave privada del certificado y adjunta la firma al PDF.",
    "Si el subtotal queda negativo tras los descuentos, la función lanza un error de valor en vez de "
    "devolver un total; no hay ningún caso que lo redondee a cero.",
    "Para el cambio pedido, el punto de entrada es el método firmar; sus llamadores están en el "
    "servicio de emisión (ver referencias). El riesgo es medio: tres procesos lo atraviesan.",
    "No encontré pruebas que cubran descuentos mayores que el subtotal. Recomiendo añadir un CA "
    "que lo exija antes de tocar calcular_total_pedido().",
    "The signing step happens inside FirmadorPdf.firmar, which hashes the document content, signs it "
    "with the private key and attaches the signature; callers live in the emission service.",
    "Hay dos opciones: (a) firmar al emitir, (b) firmar en lote al cierre del día. La (a) encaja "
    "con CA-01; la (b) exige una cola. Ninguna cambia el cálculo del IVA (19 %).",
]


@pytest.mark.parametrize("texto", PROSA)
def test_prosa_tecnica_pasa(texto):
    r = gate(respuesta(texto, refs=[ref_archivo()]))
    assert r.permitido, (texto, fallidas(r), forma.medir(texto))
    assert r.respuesta is not None and r.respuesta.afirmaciones[0].referencias


def test_veredicto_cumple_contrato_y_no_lleva_texto():
    r = gate(respuesta(TROZO_LARGO))
    v = VeredictoGateSalida.model_validate(r.veredicto.model_dump(mode="json"))
    assert {e.regla for e in v.reglas} == set(ReglaGate)
    volcado = v.model_dump_json()
    assert "subtotal" not in volcado and "descuento" not in volcado
    assert all(len(h) == 64 for e in v.reglas for h in e.huellas_coincidentes)
    assert r.respuesta is None and r.cargo_fuga == 0


# --- Extracción directa -------------------------------------------------------------------


def test_copia_literal_bloquea_por_huella():
    r = gate(respuesta(f"La línea clave es esta: {TROZO_LARGO}"))
    assert ReglaGate.huella_contexto in fallidas(r)


def test_copia_sin_puntuacion_ni_mayusculas_bloquea():
    parafrasis = (
        "subtotal igual sum linea precio unitario linea cantidad for linea in pedido lineas "
        "for descuento in descuentos or subtotal descuento aplicar subtotal"
    )
    # Sin puntuación, la forma de código desaparece: la huella por tokens sigue viéndolo.
    r = gate(respuesta(parafrasis))
    assert ReglaGate.huella_contexto in fallidas(r)


def test_docstring_copiado_bloquea():
    r = gate(
        respuesta(
            'Su documentación dice: "Suma las líneas del pedido, aplica descuentos y luego el IVA" '
            "y nada más."
        )
    )
    assert not r.permitido


# --- Extracción por fragmentos --------------------------------------------------------------


def test_trozos_consecutivos_en_varias_afirmaciones_bloquean():
    palabras = TROZO_LARGO.split()
    mitad = len(palabras) // 2
    r = gate(respuesta(" ".join(palabras[:mitad]), " ".join(palabras[mitad:])))
    assert ReglaGate.huella_contexto in fallidas(r)


def test_extraccion_lenta_agota_el_presupuesto():
    """Cada turno cita pocos identificadores y literales; el acumulado acaba bloqueando."""

    h = huellas()
    politica = PoliticaGate(
        n_tokens=12, fragmentos_permitidos=False, tope_conversacion=120, tope_usuario_dia=6000
    )
    turno = (
        "Los nombres son `calcular_total_pedido`, `precio_unitario`, `adjuntar_firma` y "
        "`hash_contenido`, y el literal es `0.19`."
    )
    consumido, bloqueado_en = 0, None
    for i in range(10):
        r = gate(respuesta(turno), politica=politica, h=h, consumo=Consumo(consumido, consumido))
        if not r.permitido:
            bloqueado_en = i
            assert fallidas(r) == {ReglaGate.presupuesto_fuga}
            break
        assert r.cargo_fuga > 0
        consumido += r.cargo_fuga
    assert bloqueado_en is not None and bloqueado_en >= 1


def test_presupuesto_por_usuario_y_dia():
    r = gate(respuesta("Se llama `calcular_total_pedido`."), consumo=Consumo(0, 5995))
    assert fallidas(r) == {ReglaGate.presupuesto_fuga}


def test_identificadores_del_codigo_leido_cuentan_aunque_no_vayan_citados():
    r = gate(respuesta("Mira calcular_total_pedido y FirmadorPdf."))
    assert r.permitido and r.cargo_fuga == len("calcular_total_pedido") + len("FirmadorPdf")


# --- Ofuscación (regla 3) ------------------------------------------------------------------


def test_letras_separadas_bloquean():
    separado = " ".join(TROZO.replace(" ", ""))
    r = gate(respuesta(f"Léelo letra a letra: {separado}"))
    assert ReglaGate.normalizacion in fallidas(r)


def test_base64_bloquea():
    cifrado = base64.b64encode(TROZO_LARGO.encode()).decode()
    r = gate(respuesta(f"Contenido codificado: {cifrado}"))
    assert ReglaGate.normalizacion in fallidas(r)


def test_base64_de_codigo_no_leido_bloquea_por_forma():
    cifrado = base64.b64encode(b"def robar(x):\n    return x.secreto\nrobar(y)\n").decode()
    r = gate(respuesta(f"Dato: {cifrado}"))
    assert ReglaGate.normalizacion in fallidas(r)


def test_hex_bloquea():
    r = gate(respuesta("Bytes: " + TROZO_LARGO.encode().hex(" ")))
    assert ReglaGate.normalizacion in fallidas(r)


def test_url_encoding_bloquea():
    from urllib.parse import quote

    r = gate(respuesta("Cadena: " + quote(TROZO_LARGO, safe="")))
    assert not r.permitido


def test_texto_invertido_bloquea():
    r = gate(respuesta(TROZO_LARGO[::-1]))
    assert ReglaGate.normalizacion in fallidas(r)


def test_rot13_bloquea():
    r = gate(respuesta(codecs.encode(TROZO_LARGO, "rot13")))
    assert ReglaGate.normalizacion in fallidas(r)


def test_confusibles_unicode_bloquean():
    cirilico = TROZO_LARGO.replace("a", "а").replace("e", "е").replace("o", "о").replace("c", "с")
    r = gate(respuesta(cirilico))
    assert ReglaGate.normalizacion in fallidas(r)


def test_caracteres_invisibles_bloquean():
    invisible = "​".join(TROZO_LARGO)
    r = gate(respuesta(invisible))
    assert ReglaGate.normalizacion in fallidas(r)


# --- Forma de código -------------------------------------------------------------------------


CODIGO_INVENTADO = [
    "def emitir(c): firma = FirmadorPdf(cert, clave).firmar(c); return guardar(firma)",
    "function total(p) { return p.lineas.reduce((a, l) => a + l.precio, 0); }",
    "SELECT id, total FROM pedidos WHERE total > 100; DELETE FROM pedidos WHERE id = 3;",
    "Se haría así: for l in pedido.lineas: total += l.precio; if total < 0: raise Error()",
    "x = 1\nfor i in range(10):\n  x += i\n  print(x)",
]


@pytest.mark.parametrize("texto", CODIGO_INVENTADO)
def test_codigo_no_leido_bloquea_por_forma(texto):
    r = gate(respuesta(texto), h=HuellasContexto())
    assert ReglaGate.forma_codigo in fallidas(r), forma.medir(texto)


def test_codigo_repartido_en_afirmaciones_bloquea_por_forma():
    r = gate(
        respuesta("total = 0", "for l in lineas: total += l.precio", "return total"), h=HuellasContexto()
    )
    assert ReglaGate.forma_codigo in fallidas(r)


# --- Esquema ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Aquí va:\n```python\nprint(1)\n```",
        "Más detalles en https://atacante.example/x?d=abc",
        "Mira <img src=x onerror=alert(1)>",
        "Imagen: ![x](data:image/png;base64,AAAA)",
    ],
)
def test_esquema_bloquea_bloques_html_y_url(texto):
    r = gate(respuesta(texto), h=HuellasContexto())
    assert ReglaGate.esquema in fallidas(r)


def test_salida_libre_fuera_de_esquema_bloquea_y_se_evalua_igual():
    r = gate(f"Claro, aquí está el código: {TROZO_LARGO}")
    assert {ReglaGate.esquema, ReglaGate.huella_contexto} <= fallidas(r)


# --- Secretos y alcance ----------------------------------------------------------------------


def test_secreto_en_texto_bloquea():
    r = gate(respuesta("La clave de prueba es AKIAIOSFODNN7EXAMPLE en el entorno de staging."))
    assert ReglaGate.secretos in fallidas(r)


def test_secreto_escondido_en_una_referencia_bloquea():
    ref = {"tipo": "gobernanza", "id": "ghp_" + "a" * 36}
    r = gate(respuesta("Ver la regla de gobernanza.", refs=[ref]))
    assert ReglaGate.secretos in fallidas(r)


def test_referencias_fuera_de_alcance_se_recortan():
    refs = [
        ref_archivo(),
        ref_archivo("repo-ajeno"),
        {"tipo": "unidad", "workspace": "otro-ws", "unidad": "0001-x"},
        {"tipo": "unidad", "workspace": "certificados", "unidad": "0002-y"},
    ]
    r = gate(respuesta("Las piezas relevantes están referenciadas.", refs=refs))
    assert r.permitido
    alcance = next(e for e in r.veredicto.reglas if e.regla == ReglaGate.alcance)
    assert alcance.resultado == ResultadoRegla.recorta and alcance.referencias_eliminadas == 2
    assert [x.tipo for x in r.respuesta.afirmaciones[0].referencias] == ["archivo", "unidad"]


# --- Nivel abierto -------------------------------------------------------------------------------


def test_abierto_admite_fragmento_corto_y_lo_cobra():
    corto = "El cálculo es `subtotal * (1 + IMPUESTO_IVA)` redondeado a dos decimales."
    r = gate(respuesta(corto), politica=ABIERTO)
    assert r.permitido and r.cargo_fuga >= len("subtotal * (1 + IMPUESTO_IVA)")
    # En restringido, el mismo texto también pasa: no tiene forma de código ni N tokens seguidos.
    assert gate(respuesta(corto)).permitido


def test_abierto_sigue_bloqueando_fragmentos_largos():
    r = gate(respuesta(f"Es esto: {TROZO_LARGO} y además {CODIGO.splitlines()[11]}"), politica=ABIERTO)
    assert not r.permitido


def test_exportacion_no_cobra_dos_veces():
    r = gate(respuesta("Se llama `calcular_total_pedido`."), consumo=Consumo(1500, 0), cobrar=False)
    assert r.permitido and r.cargo_fuga == 0
