"""Búsqueda semántica local: vectores dentro del índice FTS5, fusión con BM25 y el codificador."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from local_fabricas import ServidorDoble, crear_proxy, orden_implementar  # noqa: E402
from railspec.contracts.snapshot import DeltaIndice, MotorIndice  # noqa: E402
from railspec.local import busqueda, codificador  # noqa: E402
from railspec.local.busqueda import BusquedaNoDisponible, IndiceTexto, fundir  # noqa: E402
from railspec.local.errores import ErrorRailspec  # noqa: E402
from test_local_busqueda import (  # noqa: E402
    PDF,
    VENCIMIENTO,
    IndexadorDefs,
    correr,
    simbolo,
)

#: Conceptos del codificador de pruebas: palabras distintas, mismo vector. Es lo que un modelo real aprende.
CONCEPTOS = {
    "vence": 0,
    "vencimiento": 0,
    "caduca": 0,
    "expira": 0,
    "validez": 0,
    "certificado": 1,
    "diploma": 1,
    "plantilla": 2,
    "pdf": 2,
    "documento": 2,
}
DIMENSIONES = 16


class CodificadorFalso:
    def __init__(self, nombre: str = "falso") -> None:
        self.nombre = nombre
        self.dimensiones = DIMENSIONES
        self.textos: list[str] = []

    def _uno(self, texto: str):
        v = np.zeros(DIMENSIONES, dtype=np.float32)
        for palabra in busqueda._TERMINO.findall(texto.lower()):
            for trozo in {palabra, *busqueda.partes(palabra)}:
                v[CONCEPTOS.get(trozo, 3 + hash_estable(trozo) % (DIMENSIONES - 3))] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def codificar(self, textos):
        self.textos += list(textos)
        return np.vstack([self._uno(t) for t in textos]) if textos else np.zeros((0, DIMENSIONES), np.float32)

    def codificar_consulta(self, texto):
        return self._uno(texto)


def hash_estable(palabra: str) -> int:
    return sum(ord(c) * (i + 1) for i, c in enumerate(palabra))


@pytest.fixture
def clon(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "pdf.py").write_text(PDF, encoding="utf-8")
    (tmp_path / "src" / "fechas.py").write_text(VENCIMIENTO, encoding="utf-8")
    return tmp_path


@pytest.fixture
def simbolos():
    return [
        simbolo("src/pdf.py", "emitirCertificado", 1, 3, PDF),
        simbolo("src/fechas.py", "calcular_vencimiento", 1, 3, VENCIMIENTO),
    ]


@pytest.fixture
def indice(clon, simbolos):
    i = IndiceTexto.de(clon)
    i.reemplazar(clon, simbolos, commit="c" * 40)
    return i


# --- vectores en el índice -------------------------------------------------------------------------


def test_codificar_pendientes_guarda_un_vector_int8_por_simbolo(indice):
    falso = CodificadorFalso()
    progreso: list[tuple[int, int]] = []
    assert indice.codificar_pendientes(falso, progreso=lambda h, t: progreso.append((h, t))) == 2
    assert indice.vectores("falso") == {"modelo": "falso", "codificados": 2, "total": 2}
    assert progreso == [(2, 2)]
    # nombre, ruta y cuerpo: lo que se codifica es lo que se busca por palabras
    assert any(
        "calcular_vencimiento" in t and "src/fechas.py" in t and "dias_validez" in t for t in falso.textos
    )
    assert indice.codificar_pendientes(falso) == 0  # idempotente: nada pendiente


def test_el_limite_deja_el_resto_pendiente_y_se_retoma(indice):
    falso = CodificadorFalso()
    assert indice.codificar_pendientes(falso, limite=1) == 1
    assert indice.vectores("falso")["codificados"] == 1
    assert indice.codificar_pendientes(falso) == 1
    assert indice.vectores("falso")["codificados"] == 2


def test_reconstruir_hereda_los_vectores_de_lo_que_no_cambio(clon, simbolos, indice):
    falso = CodificadorFalso()
    indice.codificar_pendientes(falso)
    falso.textos.clear()
    # el cuerpo de calcular_vencimiento cambia (otro hash); emitirCertificado queda igual
    nuevo = VENCIMIENTO.replace("dias_validez", "dias_de_vigencia")
    (clon / "src" / "fechas.py").write_text(nuevo, encoding="utf-8")
    indice.reemplazar(clon, [simbolos[0], simbolo("src/fechas.py", "calcular_vencimiento", 1, 3, nuevo)])
    assert indice.vectores("falso") == {"modelo": "falso", "codificados": 1, "total": 2}
    assert indice.codificar_pendientes(falso) == 1
    assert len(falso.textos) == 1 and "dias_de_vigencia" in falso.textos[0]


def test_aplicar_quita_el_vector_del_simbolo_que_cambia_y_conserva_el_que_solo_se_mueve(
    clon, simbolos, indice
):
    falso = CodificadorFalso()
    indice.codificar_pendientes(falso)
    movido = simbolo("src/pdf.py", "emitirCertificado", 1, 3, PDF)
    cambiado = simbolo("src/fechas.py", "calcular_vencimiento", 1, 3, VENCIMIENTO.replace("dias", "meses"))
    (clon / "src" / "fechas.py").write_text(VENCIMIENTO.replace("dias", "meses"), encoding="utf-8")
    delta = DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=[movido, cambiado])
    indice.aplicar(clon, delta, "d" * 40)
    assert indice.vectores("falso")["codificados"] == 1


def test_un_simbolo_borrado_se_lleva_su_vector(clon, simbolos, indice):
    falso = CodificadorFalso()
    indice.codificar_pendientes(falso)
    indice.aplicar(
        clon,
        DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_borrados=[simbolos[1].id]),
    )
    assert indice.vectores("falso") == {"modelo": "falso", "codificados": 1, "total": 1}


def test_un_indice_de_antes_de_los_codificadores_gana_la_tabla_sin_reconstruirse(indice):
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(indice.ruta)) as conn, conn:
        conn.execute("DROP TABLE vectores")
    assert indice.vectores("falso")["codificados"] == 0
    assert indice.codificar_pendientes(CodificadorFalso()) == 2


def test_cada_modelo_cuenta_solo_sus_vectores(indice):
    indice.codificar_pendientes(CodificadorFalso("uno"))
    assert indice.vectores("uno")["codificados"] == 2
    assert indice.vectores("otro")["codificados"] == 0
    assert indice.codificar_pendientes(CodificadorFalso("otro")) == 2  # recodifica: un vector por símbolo
    assert indice.vectores("uno")["codificados"] == 0


# --- búsqueda --------------------------------------------------------------------------------------


def test_la_similitud_encuentra_lo_que_las_palabras_no(indice):
    falso = CodificadorFalso()
    indice.codificar_pendientes(falso)
    assert indice.buscar("caduca") == []  # ninguna palabra coincide...
    (primero, *_) = indice.buscar_semantico(falso.codificar_consulta("caduca"), "falso")
    assert primero["nombre"] == "calcular_vencimiento"  # ...pero el significado sí
    assert 0 < primero["puntaje"] <= 1
    assert primero["fragmento"].startswith("def calcular_vencimiento")


def test_el_hibrido_funde_texto_y_similitud_y_dice_el_origen(indice):
    falso = CodificadorFalso()
    indice.codificar_pendientes(falso)
    r = indice.buscar_hibrido("caduca plantilla", falso.codificar_consulta("caduca plantilla"), "falso")
    assert {h["nombre"]: h["origen"] for h in r} == {
        "emitirCertificado": "ambos",  # «plantilla» está en su cuerpo y su significado se parece
        "calcular_vencimiento": "semantico",  # solo por el sinónimo
    }


def test_fundir_prefiere_lo_que_aparece_en_las_dos_listas_y_empata_por_texto():
    a = {"id": "a", "puntaje": 5.0, "fragmento": "«a»"}
    b = {"id": "b", "puntaje": 4.0, "fragmento": "«b»"}
    c = {"id": "c", "puntaje": 0.9, "fragmento": "c"}
    r = fundir([a, b], [c, dict(b, fragmento="b")])
    assert [x["id"] for x in r] == ["b", "a", "c"]
    assert [x["origen"] for x in r] == ["ambos", "texto", "semantico"]
    assert r[0]["fragmento"] == "«b»"  # conserva los términos marcados


def test_los_filtros_de_tipo_y_ruta_valen_para_la_similitud(indice):
    falso = CodificadorFalso()
    indice.codificar_pendientes(falso)
    v = falso.codificar_consulta("caduca")
    assert indice.buscar_semantico(v, "falso", ruta="src/pdf")[0]["nombre"] == "emitirCertificado"
    assert indice.buscar_semantico(v, "falso", tipos=["clase"]) == []


def test_otra_dimension_se_rechaza_con_el_remedio(indice):
    indice.codificar_pendientes(CodificadorFalso())
    with pytest.raises(BusquedaNoDisponible, match="indice --vectores"):
        indice.buscar_semantico(np.ones(DIMENSIONES + 4, dtype=np.float32), "falso")


def test_sin_vectores_la_similitud_no_devuelve_nada(indice):
    assert indice.buscar_semantico(np.ones(DIMENSIONES, dtype=np.float32), "falso") == []


def test_evaluar_compara_los_modos(indice):
    falso = CodificadorFalso()
    casos = [
        {"consulta": "caduca", "esperados": ["calcular_vencimiento"]},
        {"consulta": "plantilla", "esperados": ["emitirCertificado"]},
    ]
    solo_texto = busqueda.evaluar(indice, casos, falso)
    assert list(solo_texto["modos"]) == ["texto"]  # sin vectores no hay con qué comparar
    indice.codificar_pendientes(falso)
    r = busqueda.evaluar(indice, casos, falso)
    assert r["modos"]["texto"] == {"acierto": 0.5, "mrr": 0.5}
    assert r["modos"]["semantico"]["acierto"] == 1.0
    assert r["modos"]["hibrido"]["acierto"] == 1.0
    assert r["detalle"][0]["rango"] == {"texto": None, "semantico": 1, "hibrido": 1}


# --- el proxy --------------------------------------------------------------------------------------


def proxy_con_indice(tmp_path, falso=None):
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    if falso is not None:
        proxy.codificador = falso
    correr(proxy.indexar_codigo())
    return proxy


def test_sin_modelo_el_modo_auto_es_texto_y_no_molesta(tmp_path):
    proxy = proxy_con_indice(tmp_path)
    r = correr(proxy.buscar_codigo("suma"))
    assert r["modo"] == "texto" and r["avisos"] == [] and r["indice"]["vectores"] is None


def test_pedir_semantico_sin_modelo_busca_por_palabras_y_dice_como_instalarlo(tmp_path):
    proxy = proxy_con_indice(tmp_path)
    r = correr(proxy.buscar_codigo("suma", modo="semantico"))
    assert r["modo"] == "texto" and r["resultados"] and "railspec modelo instalar" in r["avisos"][0]


def test_modo_desconocido_se_rechaza(tmp_path):
    proxy = proxy_con_indice(tmp_path)
    with pytest.raises(ErrorRailspec, match="desconocido"):
        correr(proxy.buscar_codigo("suma", modo="magico"))


def test_con_modelo_pero_sin_vectores_avisa_y_busca_por_palabras(tmp_path):
    proxy = proxy_con_indice(tmp_path, CodificadorFalso())
    r = correr(proxy.buscar_codigo("suma"))
    assert r["modo"] == "texto" and "indice --vectores" in r["avisos"][0]


def test_indexar_con_vectores_codifica_todo_y_el_modo_auto_pasa_a_hibrido(tmp_path):
    falso = CodificadorFalso()
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    proxy.codificador = falso
    r = correr(proxy.indexar_codigo(vectores=True))
    assert r["vectores"] == {"modelo": "falso", "codificados": 1, "total": 1} and "avisos" not in r
    b = correr(proxy.buscar_codigo("suma"))
    assert b["modo"] == "hibrido" and b["indice"]["vectores"]["codificados"] == 1
    assert b["resultados"][0]["origen"] == "ambos"
    t = correr(proxy.buscar_codigo("suma", modo="texto"))
    assert t["modo"] == "texto" and "origen" not in t["resultados"][0]


def test_indexar_sin_vectores_dice_cuantos_faltan(tmp_path):
    proxy = crear_proxy(tmp_path, ServidorDoble(), indexador=IndexadorDefs())
    proxy.codificador = CodificadorFalso()
    r = correr(proxy.indexar_codigo())
    assert r["vectores"]["codificados"] == 0 and "indice --vectores" in r["avisos"][0]


def test_cobertura_parcial_avisa_el_porcentaje(tmp_path):
    proxy = proxy_con_indice(tmp_path, CodificadorFalso())
    indice = IndiceTexto.de(proxy._base_busqueda(None))
    indice.codificar_pendientes(proxy.codificador)
    (proxy.raiz / "src" / "otra.py").write_text("def resta(a, b):\n    return a - b\n")
    sh_add_commit(proxy)
    correr(proxy.indexar_codigo())  # hereda el vector de suma; resta queda pendiente
    r = correr(proxy.buscar_codigo("suma"))
    assert r["modo"] == "hibrido" and any("Vectores al 50 %" in a for a in r["avisos"])


def sh_add_commit(proxy):
    from local_fabricas import sh

    sh(proxy.raiz, "add", "-A")
    sh(proxy.raiz, "commit", "-q", "-m", "otra")


def test_un_modelo_instalado_pero_inutilizable_se_avisa_y_se_busca_por_palabras(tmp_path, monkeypatch):
    modelo = tmp_path / "modelo"
    modelo.mkdir()
    (modelo / codificador.MANIFIESTO).write_text("{}", encoding="utf-8")
    monkeypatch.setenv(codificador.ENV_MODELO, str(modelo))
    proxy = proxy_con_indice(tmp_path)
    r = correr(proxy.buscar_codigo("suma"))
    assert r["modo"] == "texto" and r["resultados"]
    assert "incompleto" in r["avisos"][0]


def test_cada_reporte_codifica_lo_que_cambio_hasta_el_tope(tmp_path):
    falso = CodificadorFalso()
    servidor = ServidorDoble([orden_implementar])
    proxy = crear_proxy(tmp_path, servidor, indexador=IndexadorDefs())
    proxy.codificador = falso
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    worktree = Path(inicio["worktree"])
    correr(proxy.avanzar())
    correr(proxy.indexar_codigo(vectores=True))
    (worktree / "src" / "extra.py").write_text("def promedioPonderado(xs):\n    return sum(xs)\n")
    correr(proxy.reportar())
    indice = IndiceTexto.de(worktree)
    estado = indice.vectores("falso")
    assert estado["codificados"] == estado["total"] == 2  # el nuevo símbolo ya tiene vector


def test_un_reporte_no_codifica_si_nadie_pidio_vectores(tmp_path):
    falso = CodificadorFalso()
    servidor = ServidorDoble([orden_implementar])
    proxy = crear_proxy(tmp_path, servidor, indexador=IndexadorDefs())
    proxy.codificador = falso
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    correr(proxy.avanzar())
    correr(proxy.indexar_codigo())
    (Path(inicio["worktree"]) / "src" / "extra.py").write_text("def otro():\n    return 1\n")
    correr(proxy.reportar())
    assert falso.textos == []  # ni un ciclo de CPU que la persona no pidió


def test_evaluar_busqueda_lee_el_archivo_de_casos(tmp_path):
    proxy = proxy_con_indice(tmp_path, CodificadorFalso())
    IndiceTexto.de(proxy._base_busqueda(None)).codificar_pendientes(proxy.codificador)
    casos = tmp_path / "casos.json"
    casos.write_text(json.dumps([{"consulta": "suma", "esperados": ["suma"]}]), encoding="utf-8")
    r = correr(proxy.evaluar_busqueda(casos))
    assert r["consultas"] == 1 and set(r["modos"]) == {"texto", "semantico", "hibrido"}
    casos.write_text("[]x", encoding="utf-8")
    with pytest.raises(ErrorRailspec, match="No se pudo leer"):
        correr(proxy.evaluar_busqueda(casos))


# --- el codificador --------------------------------------------------------------------------------


def test_sin_modelo_instalado_cargar_devuelve_none():
    assert codificador.cargar() is None


def test_un_manifiesto_sin_dependencias_o_roto_explica_el_remedio(tmp_path, monkeypatch):
    (tmp_path / codificador.MANIFIESTO).write_text("no es json", encoding="utf-8")
    monkeypatch.setenv(codificador.ENV_MODELO, str(tmp_path))
    with pytest.raises(codificador.CodificadorNoDisponible, match="railspec modelo instalar"):
        codificador.cargar()


def test_la_carpeta_de_modelos_sigue_xdg(tmp_path, monkeypatch):
    monkeypatch.delenv(codificador.ENV_MODELO, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert codificador.directorio_de() == tmp_path / "railspec" / "modelos" / codificador.PREDETERMINADO


def test_instalar_verifica_el_sha256_y_no_deja_a_medias(tmp_path, monkeypatch):
    class Respuesta(io.BytesIO):
        headers = {"Content-Length": "5"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(codificador.urllib.request, "urlopen", lambda *a, **k: Respuesta(b"hola!"))
    destino = tmp_path / "pesos.onnx"
    with pytest.raises(codificador.CodificadorNoDisponible, match="sha256"):
        codificador._descargar("https://huggingface.co/x", destino, "0" * 64, lambda m: None)
    assert not destino.exists() and not list(tmp_path.glob("*.parcial"))
    import hashlib

    codificador._descargar(
        "https://huggingface.co/x", destino, hashlib.sha256(b"hola!").hexdigest(), lambda m: None
    )
    assert destino.read_bytes() == b"hola!"


def test_instalar_un_modelo_desconocido_lista_los_que_hay():
    with pytest.raises(codificador.CodificadorNoDisponible, match="jina-v2-base-code"):
        codificador.instalar("inventado")


def test_los_modelos_conocidos_fijan_revision_y_hashes():
    for modelo in codificador.MODELOS.values():
        assert len(modelo.revision) == 40 and modelo.licencia
        assert all(len(sha) == 64 for sha in modelo.archivos.values())
        assert modelo.manifiesto["dimensiones"] == 768


def test_la_cuantizacion_int8_conserva_el_coseno():
    rng = np.random.default_rng(0)
    v = rng.normal(size=(2, 768)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    q = (
        np.frombuffer(b"".join(codificador.a_int8(v)), dtype=np.int8).reshape(2, -1).astype(np.float32)
        / 127.0
    )
    assert abs(float(v[0] @ v[1]) - float(q[0] @ q[1] / (np.linalg.norm(q[0]) * np.linalg.norm(q[1])))) < 0.02


@pytest.mark.skipif(
    not os.environ.get("RAILSPEC_PRUEBAS_MODELO"),
    reason="RAILSPEC_PRUEBAS_MODELO apunta a un modelo instalado (railspec modelo instalar)",
)
def test_el_modelo_real_codifica_y_acerca_lo_parecido():
    pytest.importorskip("onnxruntime")
    real = codificador.CodificadorOnnx(Path(os.environ["RAILSPEC_PRUEBAS_MODELO"]))
    v = real.codificar(
        [
            "def calcular_vencimiento(emision, dias): return emision + timedelta(days=dias)",
            "def expiration_date(issued, days): return issued + timedelta(days=days)",
            "class ConexionRedis: pass",
        ]
    )
    assert v.shape == (3, real.dimensiones) and np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-3)
    assert float(v[0] @ v[1]) > float(v[0] @ v[2])
    q = real.codificar_consulta("when does the certificate expire")
    assert q.shape == (real.dimensiones,)
