"""Bucle del proxy local contra el servidor doble: worktree, reportes, política y cola."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from local_fabricas import (
    IndexadorDoble,
    ServidorDoble,
    crear_proxy,
    orden_implementar,
    orden_redactar,
    sh,
)
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.estado import BloqueoInstancia, EstadoLocal
from railspec.contracts.eventos import OrdenReportada, SnapshotSubido
from railspec.contracts.reporte import ResultadoOrden
from railspec.contracts.snapshot import EstadoArchivo, ModoDelta
from railspec.contracts.tools import CodigoError
from railspec.local.almacen import Almacen
from railspec.local.errores import FueraDeAlcance, SecretosDetectados, UnidadEnUso

CORREGIDO = "def suma(a, b):\n    return a + b\n"


def correr(coro):
    return asyncio.run(coro)


def arrancar(tmp_path: Path, servidor: ServidorDoble, **kw):
    proxy = crear_proxy(tmp_path, servidor, **kw)
    inicio = correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    return proxy, Path(inicio["worktree"]), inicio


def test_inicio_crea_worktree_hermano_y_estado_local(tmp_path):
    servidor = ServidorDoble()
    proxy, worktree, inicio = arrancar(tmp_path, servidor)

    assert worktree == tmp_path / "certificados-api.railspec" / "0001-sumar"
    assert inicio["rama"] == "railspec/0001-sumar"
    assert sh(worktree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "railspec/0001-sumar"
    estado = Almacen(worktree).leer()
    assert estado.espejo_remoto is not None and estado.bloqueo is not None
    assert proxy.unidades_locales() == {"0001-sumar": worktree}
    # El estado local del proxy nunca aparece como cambio en git.
    assert sh(worktree, "status", "--porcelain").strip() == ""
    entrada = servidor.llamadas[0][1]
    assert entrada["version_contrato_cliente"] == "1.2"
    assert "modo" not in entrada  # sin petición del humano, nace interactivo
    assert entrada["repositorios"][0]["rama"] == "main"


def test_bucle_completo_restringido(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)

    avance = correr(proxy.avanzar())
    assert avance["tipo"] == "orden" and avance["orden"]["tipo"] == "implementar"
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")

    resultado = correr(proxy.reportar(tareas_completadas=["T-01"]))
    assert resultado["aceptado"] is True
    reporte = servidor.reportes[-1]
    snap = reporte.snapshot
    assert snap is not None and snap.nivel_codigo == NivelCodigo.restringido
    assert snap.diff is None and snap.fragmentos is None
    assert snap.modo_delta == ModoDelta.solo_hashes  # sin indexador local
    assert [(a.ruta, a.estado) for a in snap.archivos] == [("src/calc.py", EstadoArchivo.modificado)]
    # En restringido la salida de la validación no sale del clon, pero queda en local.
    assert reporte.validacion is not None and reporte.validacion.salida == ""
    assert reporte.validacion.codigo_salida == 0
    assert "2 passed" in Path(resultado["validacion"]["log"]).read_text()

    estado = Almacen(worktree).leer()
    assert estado.cola_pendiente == [] and estado.orden_en_curso is None
    assert estado.ultima_secuencia_confirmada == 2
    assert correr(proxy.avanzar())["tipo"] == "cerrada"


def test_hash_arbol_incluye_cambios_sin_commit_sin_tocar_el_indice(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "nuevo.py").write_text("X = 1\n", encoding="utf-8")
    correr(proxy.reportar())
    snap = servidor.reportes[-1].snapshot
    assert {a.ruta: a.estado for a in snap.archivos} == {"src/nuevo.py": EstadoArchivo.agregado}
    assert sh(worktree, "diff", "--cached", "--name-only").strip() == ""
    assert snap.hash_arbol != sh(worktree, "rev-parse", "HEAD^{tree}").strip()


def test_archivos_fuera_de_alcance_bloquean_el_reporte(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "README.md").write_text("otro\n", encoding="utf-8")
    (worktree / "src" / "generado").mkdir()
    (worktree / "src" / "generado" / "x.py").write_text("Y = 2\n", encoding="utf-8")
    with pytest.raises(FueraDeAlcance) as exc:
        correr(proxy.reportar())
    assert exc.value.rutas == ["README.md", "src/generado/x.py"]
    assert servidor.reportes == [] and Almacen(worktree).leer().cola_pendiente == []


def test_secretos_impiden_el_snapshot_sin_revelar_el_valor(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    clave = "AKIA" + "ABCDEFGHIJKLMNOP"
    (worktree / "src" / "calc.py").write_text(f'KEY = "{clave}"\n', encoding="utf-8")
    with pytest.raises(SecretosDetectados) as exc:
        correr(proxy.reportar())
    assert exc.value.hallazgos == ["src/calc.py:1 (aws-access-key)"]
    assert clave not in str(exc.value)
    assert servidor.reportes == []


def test_archivos_excluidos_no_viajan_ni_por_hash(tmp_path):
    servidor = ServidorDoble([lambda e, s, i: orden_implementar(e, s, i, alcance={"permitidos": ["**"]})])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    (worktree / ".railspecignore").write_text("privado/\n", encoding="utf-8")
    (worktree / "privado").mkdir()
    (worktree / "privado" / "notas.txt").write_text("nada\n", encoding="utf-8")
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")
    resultado = correr(proxy.reportar())
    rutas = [a.ruta for a in servidor.reportes[-1].snapshot.archivos]
    assert rutas == [".railspecignore", "src/calc.py"]
    assert sorted(resultado["excluidos_del_snapshot"]) == [".env", "privado/notas.txt"]


def test_sin_conexion_encola_y_se_envia_al_reconectar(tmp_path):
    servidor = ServidorDoble([orden_implementar, orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")

    servidor.conectado = False
    resultado = correr(proxy.reportar(tareas_completadas=["T-01"]))
    assert resultado["encolado"] is True
    estado = Almacen(worktree).leer()
    EstadoLocal.model_validate(estado.model_dump())  # la cola cumple el contrato
    assert [type(e.carga) for e in estado.cola_pendiente] == [SnapshotSubido, OrdenReportada]
    assert [e.secuencia for e in estado.cola_pendiente] == [1, 2]
    assert estado.cola_pendiente[1].causado_por == estado.cola_pendiente[0].id

    # Sin red, advance devuelve la orden en curso para que el desarrollador siga.
    sin_red = correr(proxy.avanzar())
    assert sin_red["tipo"] == "sin-conexion" and sin_red["eventos_pendientes"] == 2

    servidor.conectado = True
    siguiente = correr(proxy.avanzar())
    assert len(servidor.reportes) == 1 and "reportes_rechazados" not in siguiente
    assert siguiente["tipo"] == "orden"
    estado = Almacen(worktree).leer()
    assert estado.cola_pendiente == [] and estado.ultima_secuencia_confirmada == 2
    assert not (worktree / ".railspec" / "pendientes" / f"{servidor.reportes[0].orden_id}.json").exists()


def test_respuesta_perdida_se_reintenta_sin_duplicar(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    servidor.perder_respuesta = True
    assert correr(proxy.reportar())["encolado"] is True
    assert len(servidor.reportes) == 1  # el servidor sí lo aceptó

    resultado = correr(proxy.sincronizar())
    assert resultado == {"unidad": "0001-sumar", "pendientes": 0, "rechazos": []}
    assert len(servidor.reportes) == 1


def test_un_rechazo_del_servidor_gana_y_se_informa(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    servidor.rechazar_con = CodigoError.snapshot_invalido
    resultado = correr(proxy.reportar())
    assert resultado["aceptado"] is False
    assert resultado["rechazos"][0]["codigo"] == "snapshot-invalido"
    estado = Almacen(worktree).leer()
    assert estado.cola_pendiente == [] and estado.orden_en_curso is None


@pytest.mark.parametrize(
    ("nivel", "con_diff", "con_fragmentos"),
    [
        (NivelCodigo.restringido, False, False),
        (NivelCodigo.interno, False, True),
        (NivelCodigo.abierto, True, False),
    ],
)
def test_politica_por_nivel_con_indexador(tmp_path, nivel, con_diff, con_fragmentos):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor, nivel=nivel, indexador=IndexadorDoble())
    correr(proxy.avanzar())
    (worktree / "src" / "calc.py").write_text(CORREGIDO, encoding="utf-8")
    correr(proxy.reportar())
    reporte = servidor.reportes[-1]
    snap = reporte.snapshot
    assert snap.modo_delta == ModoDelta.completo and snap.delta_indice.embeddings
    assert (snap.diff is not None) == con_diff
    assert (snap.fragmentos is not None) == con_fragmentos
    if con_fragmentos:
        assert snap.fragmentos[0].texto == CORREGIDO
    if con_diff:
        assert "+    return a + b" in snap.diff
    salida = reporte.validacion.salida
    assert (salida == "") == (nivel == NivelCodigo.restringido)


def test_orden_de_redactar_reporta_el_artefacto_del_disco(tmp_path):
    servidor = ServidorDoble([orden_redactar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    ruta = worktree / "specs" / "0001" / "spec.md"
    ruta.parent.mkdir(parents=True)
    ruta.write_text("# Spec\n\nCA-01: suma correcta.\n", encoding="utf-8")
    correr(proxy.reportar())
    artefacto = servidor.reportes[-1].artefacto
    assert artefacto is not None and artefacto.contenido.startswith("# Spec")
    assert servidor.reportes[-1].snapshot is None


def test_reporte_fallido_no_construye_nada_y_exige_motivo(tmp_path):
    servidor = ServidorDoble([orden_redactar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    correr(proxy.avanzar())
    correr(proxy.reportar(resultado=ResultadoOrden.bloqueado, motivo="Falta decidir el formato."))
    reporte = servidor.reportes[-1]
    assert reporte.artefacto is None and reporte.motivo == "Falta decidir el formato."


def test_una_sola_sesion_por_unidad_y_maquina(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    almacen = Almacen(worktree)
    estado = almacen.leer()
    import socket

    ajeno = BloqueoInstancia(host=socket.gethostname(), pid=os.getppid(), desde=estado.bloqueo.desde)
    almacen.escribir(estado.model_copy(update={"bloqueo": ajeno}))
    with pytest.raises(UnidadEnUso):
        correr(proxy.avanzar())


def test_busqueda_semantica_lleva_el_vector_calculado_en_local(tmp_path):
    servidor = ServidorDoble()
    indexador = IndexadorDoble()
    proxy = crear_proxy(tmp_path, servidor, indexador=indexador)
    correr(proxy.consultar_grafo({"verbo": "search", "texto": "firmar pdf", "semantica": True}))
    consulta = servidor.consultas_grafo[-1].consulta
    assert consulta.vector_b64 is not None and consulta.modelo_embedding == "nomic-embed-code"
    assert indexador.consultas == ["firmar pdf"]
    correr(proxy.consultar_grafo({"verbo": "search", "texto": "firmar pdf"}))
    assert servidor.consultas_grafo[-1].consulta.vector_b64 is None


def test_insumo_pull_escribe_markdown_sin_codigo(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    from fabricas import insumo

    resultado = correr(proxy.traer_insumo(insumo().id))
    texto = Path(resultado["ruta"]).read_text(encoding="utf-8")
    assert texto.startswith(f"# Insumo {insumo().id}")
    assert "símbolo `pdf.emitir` (funcion) en `certificados-api:src/pdf.py`" in texto
    assert "criterio `CA-01` de la unidad `0001-emitir-pdf`" in texto
    assert "No cambiar el formato del número de certificado." in texto
    assert sh(proxy.raiz, "status", "--porcelain").strip() == ""


def test_cambio_de_modo_solo_cuando_el_servidor_lo_admite(tmp_path):
    from railspec.contracts.comun import Modo
    from railspec.local.errores import ErrorServidor

    servidor = ServidorDoble([orden_implementar])
    proxy, worktree, _ = arrancar(tmp_path, servidor)
    resultado = correr(proxy.cambiar_modo(None, Modo.semi_autonomo, "El humano lo pidió tras el spec."))
    assert resultado["modo"] == "semi-autonomo"
    assert Almacen(worktree).leer().espejo_remoto.modo == Modo.semi_autonomo

    correr(proxy.avanzar())  # pasa a implement
    with pytest.raises(ErrorServidor) as exc:
        correr(proxy.cambiar_modo(None, Modo.interactivo, "Volver."))
    assert exc.value.codigo == CodigoError.conversion_no_permitida


def test_modo_inicial_con_mandato_viaja_en_unit_start(tmp_path):
    from railspec.contracts.comun import Modo

    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    with pytest.raises(Exception, match="exige un mandato"):
        correr(proxy.iniciar("Sumar", "Arregla.", modo=Modo.supervisado))
    correr(proxy.iniciar("Sumar", "Arregla.", modo=Modo.semi_autonomo))
    assert servidor.llamadas[-1][1]["modo"] == "semi-autonomo"
