"""Búsqueda de texto local (FTS5): el índice, su mantenimiento con cada snapshot y sus tools."""

from __future__ import annotations

import asyncio
import hashlib
import re
import sqlite3
from pathlib import Path

import pytest
from local_fabricas import ServidorDoble, crear_proxy, orden_implementar, sh
from railspec.contracts.snapshot import DeltaIndice, MotorIndice, Simbolo, TipoSimbolo, id_simbolo
from railspec.local import busqueda
from railspec.local.busqueda import BusquedaNoDisponible, IndiceTexto, partes
from railspec.local.errores import ErrorRailspec

REPO = "certificados-api"
PDF = """\
def emitirCertificado(datos):
    plantilla = cargar_plantilla("certificado")
    return render_pdf(plantilla, datos)
"""
VENCIMIENTO = """\
def calcular_vencimiento(emision, dias_validez):
    # el certificado vence a los dias_validez de la emision
    return emision + timedelta(days=dias_validez)
"""


def correr(coro):
    return asyncio.run(coro)


def simbolo(ruta: str, nombre: str, inicio: int, fin: int, texto: str) -> Simbolo:
    lineas = texto.splitlines(keepends=True)[inicio - 1 : fin]
    return Simbolo(
        id=id_simbolo(REPO, ruta, "funcion", nombre),
        nombre=nombre,
        tipo=TipoSimbolo.funcion,
        ruta=ruta,
        linea_inicio=inicio,
        linea_fin=fin,
        sha256=hashlib.sha256("".join(lineas).encode()).hexdigest(),
    )


@pytest.fixture
def clon(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pdf.py").write_text(PDF, encoding="utf-8")
    (tmp_path / "src" / "fechas.py").write_text(VENCIMIENTO, encoding="utf-8")
    return tmp_path


@pytest.fixture
def simbolos(clon):
    return [
        simbolo("src/pdf.py", "emitirCertificado", 1, 3, PDF),
        simbolo("src/fechas.py", "calcular_vencimiento", 1, 3, VENCIMIENTO),
    ]


@pytest.fixture
def indice(clon, simbolos):
    i = IndiceTexto.de(clon)
    i.reemplazar(clon, simbolos, commit="c" * 40)
    return i


def test_las_partes_separan_camel_snake_y_numeros():
    assert partes("emitirCertificado_PDF2") == ["emitir", "certificado", "pdf", "2"]
    assert partes("HTTPServer") == ["http", "server"]


def test_busca_por_palabras_del_cuerpo_aunque_no_esten_en_el_nombre(indice):
    (primero, *_) = indice.buscar("plantilla")
    assert primero["nombre"] == "emitirCertificado"
    assert primero["linea_inicio"] == 1 and primero["linea_fin"] == 3
    assert "«plantilla»" in primero["fragmento"]


def test_un_identificador_camelcase_se_encuentra_por_sus_partes_y_por_entero(indice):
    por_parte = indice.buscar("certificado emitir")
    entero = indice.buscar("emitirCertificado")
    assert por_parte[0]["nombre"] == entero[0]["nombre"] == "emitirCertificado"


def test_snake_case_se_encuentra_por_una_de_sus_palabras(indice):
    assert [h["nombre"] for h in indice.buscar("vencimiento")] == ["calcular_vencimiento"]
    assert [h["nombre"] for h in indice.buscar("dias validez")][0] == "calcular_vencimiento"


def test_el_nombre_pesa_mas_que_el_cuerpo(clon):
    texto = (
        "def vencimiento():\n    pass\n\ndef otra():\n    # vencimiento vencimiento vencimiento\n    pass\n"
    )
    (clon / "src" / "x.py").write_text(texto, encoding="utf-8")
    i = IndiceTexto.de(clon)
    i.reemplazar(
        clon, [simbolo("src/x.py", "vencimiento", 1, 2, texto), simbolo("src/x.py", "otra", 4, 6, texto)]
    )
    assert [h["nombre"] for h in i.buscar("vencimiento")] == ["vencimiento", "otra"]


def test_filtra_por_tipo_y_por_prefijo_de_ruta(indice):
    assert indice.buscar("certificado", ruta="src/fechas") and not indice.buscar(
        "plantilla", ruta="src/fechas"
    )
    assert indice.buscar("plantilla", tipos=["funcion"]) and not indice.buscar("plantilla", tipos=["clase"])


@pytest.mark.parametrize("texto", ["AND", "NOT x", "nombre:vencimiento", '"', "(", "*", "a OR", "--", "   "])
def test_operadores_y_basura_de_fts5_no_rompen_la_busqueda(indice, texto):
    indice.buscar(texto)  # no lanza


def test_sin_terminos_buscables_no_devuelve_nada(indice):
    assert indice.buscar("!!! ??") == []


def test_la_busqueda_no_distingue_mayusculas_ni_tildes(clon):
    texto = "def índice_país():\n    return 'Ñandú'\n"
    (clon / "src" / "t.py").write_text(texto, encoding="utf-8")
    i = IndiceTexto.de(clon)
    i.reemplazar(clon, [simbolo("src/t.py", "índice_país", 1, 2, texto)])
    assert i.buscar("INDICE PAIS") and i.buscar("nandu")


def test_el_fragmento_sale_con_los_secretos_redactados(clon):
    texto = 'def conectar():\n    clave = "AKIAIOSFODNN7EXAMPLE"  # clave aws de conexion\n'
    (clon / "src" / "s.py").write_text(texto, encoding="utf-8")
    i = IndiceTexto.de(clon)
    i.reemplazar(clon, [simbolo("src/s.py", "conectar", 1, 2, texto)])
    (hit,) = i.buscar("conexion")
    assert "AKIAIOSFODNN7EXAMPLE" not in hit["fragmento"]


def test_un_cuerpo_enorme_se_recorta_y_un_binario_no_se_lee(clon):
    grande = "def g():\n" + "    x = 1  # palabra\n" * 2000
    (clon / "src" / "g.py").write_text(grande, encoding="utf-8")
    (clon / "src" / "b.bin").write_bytes(b"\0\1\2 secreto_binario")
    i = IndiceTexto.de(clon)
    i.reemplazar(
        clon,
        [simbolo("src/g.py", "g", 1, 2001, grande), simbolo("src/b.bin", "bin", 1, 1, "x")],
    )
    with sqlite3.connect(i.ruta) as conn:
        (largo,) = conn.execute("SELECT length(cuerpo) FROM texto WHERE nombre = 'g'").fetchone()
    assert largo <= busqueda.CUERPO_MAX
    assert i.buscar("secreto_binario") == []


def test_la_construccion_es_atomica_y_un_fallo_deja_el_indice_anterior(clon, simbolos):
    i = IndiceTexto.de(clon)
    i.reemplazar(clon, simbolos)

    def explota():
        yield simbolos[0]
        raise RuntimeError("indexador roto")

    with pytest.raises(RuntimeError):
        i.reemplazar(clon, explota())
    assert i.buscar("plantilla") and not (clon / ".railspec" / "busqueda.sqlite.nuevo").exists()


def test_meta_e_indice_de_otra_version_se_ignoran(indice):
    assert indice.completo() and indice.meta()["simbolos"] == "2" and indice.meta()["commit"] == "c" * 40
    with sqlite3.connect(indice.ruta) as conn:
        conn.execute("UPDATE meta SET valor = '0' WHERE clave = 'version_esquema'")
    assert indice.meta() == {} and not indice.completo() and indice.buscar("plantilla") == []


def test_un_archivo_que_no_es_sqlite_se_trata_como_sin_indice(clon):
    ruta = clon / ".railspec" / "busqueda.sqlite"
    ruta.parent.mkdir()
    ruta.write_text("no soy sqlite", encoding="utf-8")
    assert IndiceTexto(ruta).meta() == {}


def test_aplicar_borra_actualiza_y_agrega(clon, simbolos, indice):
    nuevo = "def revocarCertificado(id):\n    return marcar_revocado(id)\n"
    (clon / "src" / "rev.py").write_text(nuevo, encoding="utf-8")
    cambiado = "def calcular_vencimiento(emision):\n    return emision + un_anio\n"
    (clon / "src" / "fechas.py").write_text(cambiado, encoding="utf-8")
    delta = DeltaIndice(
        motor=MotorIndice(version="0.11.0"),
        simbolos_upsert=[
            simbolo("src/rev.py", "revocarCertificado", 1, 2, nuevo),
            simbolo("src/fechas.py", "calcular_vencimiento", 1, 2, cambiado),
        ],
        simbolos_borrados=[simbolos[0].id],
    )
    assert indice.aplicar(clon, delta, "d" * 40) == 3  # un borrado, un nuevo y un cuerpo cambiado
    assert indice.buscar("plantilla") == []  # el borrado ya no sale
    assert [h["nombre"] for h in indice.buscar("revocado")] == ["revocarCertificado"]
    assert indice.buscar("un_anio")[0]["nombre"] == "calcular_vencimiento"
    assert indice.buscar("dias_validez") == []  # el cuerpo viejo se reemplazó
    meta = indice.meta()
    assert meta["simbolos"] == "2" and meta["commit_actualizado"] == "d" * 40 and meta["commit"] == "c" * 40


def test_aplicar_con_el_mismo_hash_solo_mueve_las_lineas_y_no_relee_el_cuerpo(clon, simbolos, indice):
    desplazado = "\n\n" + VENCIMIENTO
    (clon / "src" / "fechas.py").write_text(desplazado, encoding="utf-8")
    s = simbolos[1].model_copy(update={"linea_inicio": 3, "linea_fin": 5})
    assert indice.aplicar(clon, DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=[s])) == 0
    (hit,) = indice.buscar("dias_validez")
    assert (hit["linea_inicio"], hit["linea_fin"]) == (3, 5)


def test_aplicar_sin_indice_completo_no_crea_nada(clon, simbolos):
    i = IndiceTexto.de(clon)
    assert i.aplicar(clon, DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=simbolos)) == 0
    assert not i.existe()


def test_sin_fts5_el_error_dice_que_falta(clon, monkeypatch):
    class SinFts5(sqlite3.Connection):
        def executescript(self, sql):
            raise sqlite3.OperationalError("no such module: fts5")

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **k: SinFts5(*a, **{kk: v for kk, v in k.items() if kk != "timeout"})
    )
    with pytest.raises(BusquedaNoDisponible, match="FTS5"):
        IndiceTexto.de(clon).reemplazar(clon, [])


# --- el proxy y las tools -------------------------------------------------------------


class IndexadorDefs:
    """Un símbolo por ``def`` de cada .py (hasta la línea anterior al siguiente ``def`` o el final)."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, list[str]]] = []

    def delta(self, worktree: Path, repositorio: str, base_commit: str, rutas: list[str], excluir: list[str]):
        self.llamadas.append((base_commit, sorted(rutas)))
        simbolos = []
        for ruta in rutas:
            archivo = worktree / ruta
            if not ruta.endswith(".py") or not archivo.is_file():
                continue
            texto = archivo.read_text(encoding="utf-8")
            lineas = texto.splitlines(keepends=True)
            inicios = [n for n, linea in enumerate(lineas, 1) if linea.startswith("def ")]
            for k, ini in enumerate(inicios):
                fin = (inicios[k + 1] - 1) if k + 1 < len(inicios) else len(lineas)
                nombre = re.match(r"def (\w+)", lineas[ini - 1]).group(1)
                simbolos.append(simbolo(ruta, nombre, ini, fin, texto))
        return DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=simbolos)

    def embedding_consulta(self, texto: str):
        return None


def test_code_search_sin_indice_avisa_y_no_falla(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    r = correr(proxy.buscar_codigo("suma"))
    assert r["resultados"] == [] and r["indice"] is None and "code_index" in r["avisos"][0]


def test_code_index_y_code_search_sobre_el_clon(tmp_path):
    indexador = IndexadorDefs()
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=indexador)
    r = correr(proxy.indexar_codigo())
    assert r["simbolos"] == 1
    base, rutas = indexador.llamadas[0]
    assert base == sh(proxy.raiz, "hash-object", "-t", "tree", "/dev/null").strip()
    assert "src/calc.py" in rutas

    hallazgo = correr(proxy.buscar_codigo("suma restar"))
    (hit,) = hallazgo["resultados"]
    assert hit["ruta"] == "src/calc.py" and hit["nombre"] == "suma"
    assert hallazgo["indice"]["simbolos"] == 1 and hallazgo["avisos"] == []


def test_code_search_avisa_si_el_indice_va_por_detras_de_la_rama(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    correr(proxy.indexar_codigo())
    (proxy.raiz / "NOTAS.md").write_text("x\n", encoding="utf-8")
    sh(proxy.raiz, "add", "-A")
    sh(proxy.raiz, "commit", "-q", "-m", "otro")
    r = correr(proxy.buscar_codigo("suma"))
    assert r["resultados"] and "va en" in r["avisos"][0]


def test_sin_coincidencias_explica_el_alcance(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    correr(proxy.indexar_codigo())
    r = correr(proxy.buscar_codigo("nadaquecoincida"))
    assert r["resultados"] == [] and "Sin coincidencias" in r["avisos"][0]


def test_code_index_sin_indexador_dice_que_hacer(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble())
    with pytest.raises(ErrorRailspec, match="railspec doctor"):
        correr(proxy.indexar_codigo())


def test_el_indice_no_aparece_como_cambio_en_git_ni_viaja(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy = crear_proxy(tmp_path, servidor, indexador=IndexadorDefs())
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    worktree = Path(inicio["worktree"])
    correr(proxy.avanzar())
    correr(proxy.indexar_codigo())  # una sola unidad local: indexa su worktree
    assert (worktree / ".railspec" / "busqueda.sqlite").is_file()
    assert sh(worktree, "status", "--porcelain").strip() == ""


def test_cada_reporte_mantiene_el_indice_de_la_unidad(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy = crear_proxy(tmp_path, servidor, indexador=IndexadorDefs())
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    worktree = Path(inicio["worktree"])
    correr(proxy.avanzar())
    correr(proxy.indexar_codigo())
    assert (
        correr(proxy.buscar_codigo("restar", unidad="0001-sumar"))["resultados"] == []
    )  # aún no hay palabra
    (worktree / "src" / "calc.py").write_text("def suma(a, b):\n    # antes restaba\n    return a + b\n")
    (worktree / "src" / "extra.py").write_text("def promedioPonderado(xs):\n    return sum(xs)\n")
    correr(proxy.reportar())
    nombres = {
        h["nombre"]
        for h in correr(proxy.buscar_codigo("promedio ponderado", unidad="0001-sumar"))["resultados"]
    }
    assert nombres == {"promedioPonderado"}
    assert (
        correr(proxy.buscar_codigo("antes restaba", unidad="0001-sumar"))["resultados"][0]["nombre"] == "suma"
    )


def test_un_reporte_sin_indice_previo_no_lo_crea_y_un_fallo_del_indice_no_rompe_el_reporte(
    tmp_path, monkeypatch
):
    servidor = ServidorDoble([orden_implementar])
    proxy = crear_proxy(tmp_path, servidor, indexador=IndexadorDefs())
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    worktree = Path(inicio["worktree"])
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b\n")
    correr(proxy.reportar())
    assert not (worktree / ".railspec" / "busqueda.sqlite").exists()

    monkeypatch.setattr(busqueda.IndiceTexto, "aplicar", lambda *a, **k: 1 / 0)
    servidor2 = ServidorDoble([orden_implementar])
    otro = crear_proxy(tmp_path / "otro", servidor2, indexador=IndexadorDefs())
    inicio2 = correr(otro.iniciar("Corregir suma", "x"))
    correr(otro.avanzar())
    (Path(inicio2["worktree"]) / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b\n")
    correr(otro.reportar())  # no lanza
    assert servidor2.reportes


def test_unidad_inexistente_se_rechaza(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    with pytest.raises(ErrorRailspec, match="No hay worktree local"):
        correr(proxy.buscar_codigo("x", unidad="9999-nada"))
