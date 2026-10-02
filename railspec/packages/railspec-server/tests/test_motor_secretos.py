"""El servidor vuelve a escanear el texto de código de un snapshot: no se fía de ``hallazgos == 0``."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from apoyo_motor import (
    BASE,
    JULIAN,
    JULIAN_CONSOLA,
    ORG,
    REPO,
    WS,
    aprobar,
    avanzar,
    construir,
    entrada_start,
    reporte,
)
from railspec.contracts.comun import AlcanceUnidad, NivelCodigo
from railspec.contracts.snapshot import (
    CambioArchivo,
    DeltaIndice,
    EscaneoSecretos,
    EstadoArchivo,
    Fragmento,
    ModoDelta,
    MotorIndice,
    Snapshot,
)
from railspec.contracts.tools import CodigoError
from railspec.server.chat import secretos
from railspec.server.motor import ErrorNegocio
from railspec.server.motor.escaneo import MAX_INFORMADOS, SOLAPE, VENTANA, hallazgos_de_secretos, resumen

RUTA = "src/pdf.py"
_ORDEN_SUELTA = SimpleNamespace(
    unidad=AlcanceUnidad(org=ORG, workspace=WS, unidad="0001-emitir-pdf-firmado"),
    repositorio=REPO,
    base_commit=BASE,
)
LIMPIO = "def firmar(pdf):\n    return pdf.firmar(os.environ['CLAVE_FIRMA'])\n"
CLAVE = "-----BEGIN OPENSSH PRIVATE KEY-----"

#: Un valor falso por patrón de ``chat.secretos``; armados por partes para que ningún
#: escáner del repositorio los tome por credenciales.
MUESTRAS: dict[str, str] = {
    "clave-privada": CLAVE,
    "aws-access-key": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github-token": "ghp_" + "a" * 36,
    "slack-token": "xoxb-" + "1234567890-abcdef",
    "google-api-key": "AIza" + "a" * 35,
    "stripe-key": "sk_live_" + "a" * 24,
    "anthropic-key": "sk-ant-" + "a" * 24,
    "openai-key": "sk-" + "a" * 40,
    "azure-storage": "AccountKey=" + "A" * 44,
    "azure-sas": "?sv=2024-01-01&sig=" + "a" * 40,
    "cadena-conexion": "mongodb://admin:s3cr3tpass@db:27017",
    "jwt": "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
    "asignacion-secreto": 'password = "correcthorsebattery"',
}


def correr(coro):
    return asyncio.run(coro)


def _snapshot(orden, nivel, *, diff=None, fragmentos=None, rutas=(RUTA,)) -> Snapshot:
    return Snapshot(
        id=uuid.uuid4(),
        unidad=orden.unidad,
        repositorio=orden.repositorio,
        base_commit=orden.base_commit,
        hash_arbol="a" * 40,
        creado_en=datetime(2026, 9, 30, 20, tzinfo=UTC),
        nivel_codigo=nivel,
        modo_delta=ModoDelta.completo,
        archivos=[
            CambioArchivo(
                ruta=r, estado=EstadoArchivo.agregado, sha256_despues=hashlib.sha256(r.encode()).hexdigest()
            )
            for r in rutas
        ],
        diff=diff,
        fragmentos=fragmentos,
        delta_indice=DeltaIndice(motor=MotorIndice(version="1.0.0")),
        escaneo_secretos=EscaneoSecretos(herramienta="railspec-secretos", version="0.1.0", hallazgos=0),
    )


def _fragmento(texto, ruta=RUTA) -> Fragmento:
    return Fragmento(ruta=ruta, sha256=hashlib.sha256(texto.encode()).hexdigest(), texto=texto)


def _diff(ruta, *lineas) -> str:
    cuerpo = "".join(f"+{linea}\n" for linea in lineas)
    return (
        f"diff --git a/{ruta} b/{ruta}\nnew file mode 100644\nindex 0000000..1111111\n"
        f"--- /dev/null\n+++ b/{ruta}\n@@ -0,0 +1,{len(lineas)} @@\n{cuerpo}"
    )


async def _hasta_implementar(motor):
    alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
    for _ in range(30):
        av = await avanzar(motor, alcance)
        if av.tipo == "orden" and av.orden.tipo == "implementar":
            return alcance, av.orden
        if av.tipo == "orden":
            await motor.report(reporte(av.orden), JULIAN)
        else:
            await aprobar(motor, alcance, av.checkpoint.id, actor=JULIAN_CONSOLA)
    raise AssertionError("no se llegó a la orden de implementar")


def _con_snapshot(orden, snapshot):
    return reporte(orden).model_copy(update={"snapshot": snapshot})


async def _rechazado(motor, alcance, orden, snapshot) -> str:
    """Reporta, exige ``snapshot-invalido`` y comprueba que no quedó nada guardado."""

    with pytest.raises(ErrorNegocio) as exc:
        await motor.report(_con_snapshot(orden, snapshot), JULIAN)
    assert exc.value.codigo == CodigoError.snapshot_invalido
    assert motor.n.almacen.obtener_snapshot(alcance, str(snapshot.id)) is None
    assert not motor.n.almacen.reporte_aceptado(alcance, orden.id, orden.secuencia)
    assert motor.n.almacen.obtener_estado(alcance).orden_vigente == orden.id
    return str(exc.value)


def test_clave_privada_en_el_diff_de_un_snapshot_abierto_se_rechaza():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.abierto)
        alcance, orden = await _hasta_implementar(motor)
        snapshot = _snapshot(orden, NivelCodigo.abierto, diff=_diff(RUTA, "import os", CLAVE, "x = 1"))
        mensaje = await _rechazado(motor, alcance, orden, snapshot)
        assert "clave-privada en el diff (src/pdf.py)" in mensaje

    correr(caso())


def test_secreto_en_un_fragmento_de_un_snapshot_interno_se_rechaza():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.interno)
        alcance, orden = await _hasta_implementar(motor)
        texto = f"def clave():\n    return '{MUESTRAS['github-token']}'\n"
        snapshot = _snapshot(orden, NivelCodigo.interno, fragmentos=[_fragmento(LIMPIO), _fragmento(texto)])
        mensaje = await _rechazado(motor, alcance, orden, snapshot)
        assert "github-token en el fragmento src/pdf.py" in mensaje

    correr(caso())


def test_el_error_nombra_tipo_y_ruta_pero_nunca_el_valor():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.abierto)
        alcance, orden = await _hasta_implementar(motor)
        aws = MUESTRAS["aws-access-key"]
        snapshot = _snapshot(
            orden,
            NivelCodigo.abierto,
            diff=_diff(RUTA, f"clave = '{aws}'"),
            fragmentos=[_fragmento(f"clave = '{aws}'")],
        )
        mensaje = await _rechazado(motor, alcance, orden, snapshot)
        assert "aws-access-key" in mensaje and RUTA in mensaje
        assert aws not in mensaje and "ABCDEFGHIJKLMNOP" not in mensaje

    correr(caso())


def test_el_rechazo_no_deja_nada_y_el_reporte_limpio_se_acepta_despues():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.abierto)
        alcance, orden = await _hasta_implementar(motor)
        sucio = _snapshot(orden, NivelCodigo.abierto, diff=_diff(RUTA, CLAVE))
        await _rechazado(motor, alcance, orden, sucio)
        limpio = _snapshot(orden, NivelCodigo.abierto, diff=_diff(RUTA, *LIMPIO.splitlines()))
        await motor.report(_con_snapshot(orden, limpio), JULIAN)
        assert motor.n.almacen.obtener_snapshot(alcance, str(limpio.id)) is not None
        assert motor.n.almacen.obtener_snapshot(alcance, str(sucio.id)) is None

    correr(caso())


@pytest.mark.parametrize("nivel", [NivelCodigo.interno, NivelCodigo.abierto])
def test_snapshot_limpio_con_texto_se_acepta(nivel):
    async def caso():
        motor, _ = construir(nivel=nivel)
        alcance, orden = await _hasta_implementar(motor)
        snapshot = _snapshot(
            orden, nivel, diff=_diff(RUTA, *LIMPIO.splitlines()), fragmentos=[_fragmento(LIMPIO)]
        )
        await motor.report(_con_snapshot(orden, snapshot), JULIAN)
        assert motor.n.almacen.obtener_snapshot(alcance, str(snapshot.id)) is not None

    correr(caso())


def test_restringido_sigue_igual_sin_texto_que_revisar():
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.restringido)
        alcance, orden = await _hasta_implementar(motor)
        r = reporte(orden)  # solo-hashes en restringido: el helper del arnés simulado
        assert r.snapshot.diff is None and r.snapshot.fragmentos is None
        assert hallazgos_de_secretos(r.snapshot) == []
        await motor.report(r, JULIAN)
        assert motor.n.almacen.obtener_snapshot(alcance, str(r.snapshot.id)) is not None

    correr(caso())


@pytest.mark.parametrize("tipo", sorted(MUESTRAS))
def test_cada_patron_rechaza_un_fragmento(tipo):
    async def caso():
        motor, _ = construir(nivel=NivelCodigo.interno)
        alcance, orden = await _hasta_implementar(motor)
        snapshot = _snapshot(orden, NivelCodigo.interno, fragmentos=[_fragmento(MUESTRAS[tipo] + "\n")])
        mensaje = await _rechazado(motor, alcance, orden, snapshot)
        assert f"{tipo} en el fragmento {RUTA}" in mensaje

    correr(caso())


def test_hay_una_muestra_por_cada_patron_del_servidor():
    assert set(MUESTRAS) == {nombre for nombre, _ in secretos.PATRONES}


def test_paridad_con_el_escaner_del_proxy():
    """Lo que el proxy deja pasar o frena línea a línea, el servidor lo trata igual."""

    local = pytest.importorskip("railspec.local.secretos")
    normales = [
        'password = os.environ["DB_PASSWORD"]',
        'password = "${DB_PASSWORD}"',
        "def suma(a, b): return a + b",
        "url = 'https://example.com/path'",
        "token = request.headers['Authorization']",
    ]
    for linea in [*MUESTRAS.values(), *normales]:
        proxy = [h.tipo for h in local.escanear(RUTA, linea.encode())]
        servidor = secretos.detectar(linea)
        assert bool(proxy) == bool(servidor), linea
        # El proxy informa el primer patrón de la línea; el servidor, todos los que coinciden.
        assert not proxy or proxy[0] in servidor, linea


def test_el_diff_se_atribuye_al_archivo_que_lo_lleva():
    rutas = ("src/a.py", "src/b.py")
    diff = _diff("src/a.py", "x = 1") + _diff("src/b.py", MUESTRAS["slack-token"])
    base = _snapshot(_ORDEN_SUELTA, NivelCodigo.abierto, diff=diff, rutas=rutas)
    assert hallazgos_de_secretos(base) == ["slack-token en el diff (src/b.py)"]


def test_ruta_del_diff_ajena_al_snapshot_no_se_nombra():
    """La ruta que se informa sale de los archivos del snapshot, no del texto del diff."""

    diff = _diff("src/otra.py", MUESTRAS["jwt"])
    base = _snapshot(_ORDEN_SUELTA, NivelCodigo.abierto, diff=diff)
    assert hallazgos_de_secretos(base) == ["jwt en el diff"]


@pytest.mark.parametrize("posicion", [0, 500, VENTANA - 10, VENTANA + 5, 1500, 3 * VENTANA, 29_000])
def test_secreto_dentro_de_una_linea_larga_se_encuentra(posicion):
    """Las líneas de más de ``VENTANA`` caracteres se revisan por tramos y ninguno se salta."""

    linea = "x " * (15_000) + "\n"
    linea = linea[:posicion] + " " + MUESTRAS["aws-access-key"] + " " + linea[posicion:]
    assert len(linea) > 30_000
    snapshot = _snapshot(_ORDEN_SUELTA, NivelCodigo.interno, fragmentos=[_fragmento(linea)])
    assert hallazgos_de_secretos(snapshot) == [f"aws-access-key en el fragmento {RUTA}"]


def test_secreto_que_cabe_en_el_solape_no_se_pierde_en_el_corte_de_tramos():
    paso = VENTANA - SOLAPE
    secreto = "ghp_" + "a" * (SOLAPE - 8)  # casi tan largo como el solape
    for corte in range(paso - len(secreto), paso + 1, 7):
        linea = "x " * 2_000
        linea = linea[:corte] + " " + secreto + " " + linea[corte:]
        snapshot = _snapshot(_ORDEN_SUELTA, NivelCodigo.interno, fragmentos=[_fragmento(linea)])
        assert hallazgos_de_secretos(snapshot) == [f"github-token en el fragmento {RUTA}"], corte


def test_un_diff_hecho_para_estancar_las_expresiones_regulares_no_estanca_el_servidor():
    """``cadena-conexion`` es cuadrática en una corrida larga de ``[a-z0-9+.-]``: sin tramos,
    un diff de 1 MB así tardaría unos 9 minutos y bloquearía el bucle de eventos."""

    diff = "+" + "a." * 500_000 + "\n"
    snapshot = _snapshot(_ORDEN_SUELTA, NivelCodigo.abierto, diff=diff)
    inicio = time.perf_counter()
    assert hallazgos_de_secretos(snapshot) == []
    assert time.perf_counter() - inicio < 30


def test_resumen_acota_los_hallazgos_informados():
    hallazgos = [f"clave-privada en el diff (src/f{i}.py)" for i in range(MAX_INFORMADOS + 3)]
    texto = resumen(hallazgos)
    assert "src/f0.py" in texto and f"src/f{MAX_INFORMADOS - 1}.py" in texto
    assert f"src/f{MAX_INFORMADOS}.py" not in texto and texto.endswith("y 3 más")
    assert resumen(hallazgos[:2]) == "; ".join(hallazgos[:2])
