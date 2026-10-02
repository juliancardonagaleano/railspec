"""``railspec sync`` e ``railspec insumo pull`` por la línea de comandos, contra el servidor doble.

``cli.crear_proxy`` se sustituye por el proxy de la prueba: lo que se ejercita es el comando
(argumentos, salida JSON, códigos de salida y mensajes), no la conexión HTTP.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fabricas import insumo
from local_fabricas import ServidorDoble, crear_proxy, orden_implementar, sh
from railspec.contracts.tools import CodigoError
from railspec.local import cli
from railspec.local.almacen import Almacen
from railspec.local.errores import ServidorRechazo


def correr(coro):
    return asyncio.run(coro)


@pytest.fixture
def en_cli(tmp_path, monkeypatch):
    """Arranca una unidad contra un doble y hace que el CLI use ese proxy."""

    def preparar(servidor: ServidorDoble, iniciar: bool = True):
        proxy = crear_proxy(tmp_path, servidor)
        worktree = None
        if iniciar:
            worktree = Path(
                correr(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))["worktree"]
            )
        monkeypatch.setattr(cli, "crear_proxy", lambda raiz: proxy)
        return proxy, worktree

    return preparar


def ejecutar(proxy, capsys, *args: str) -> tuple[int, str, str]:
    codigo = cli.main(["--repo", str(proxy.raiz), *args])
    captura = capsys.readouterr()
    return codigo, captura.out, captura.err


def reportar_sin_conexion(proxy, servidor: ServidorDoble) -> None:
    correr(proxy.avanzar())
    servidor.conectado = False
    assert correr(proxy.reportar())["encolado"] is True


# --- railspec sync -------------------------------------------------------------------------------


def test_sync_sin_conexion_avisa_y_conserva_la_cola(en_cli, capsys):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree = en_cli(servidor)
    reportar_sin_conexion(proxy, servidor)

    codigo, salida, error = ejecutar(proxy, capsys, "sync")

    assert codigo == 1 and salida == ""
    assert error.startswith("railspec: ") and "desconectado" in error
    assert len(Almacen(worktree).leer().cola_pendiente) == 2  # nada se perdió
    assert servidor.reportes == []


def test_sync_al_reconectar_envia_la_cola_y_la_vacia(en_cli, capsys):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree = en_cli(servidor)
    reportar_sin_conexion(proxy, servidor)
    servidor.conectado = True

    codigo, salida, _ = ejecutar(proxy, capsys, "sync")

    resultado = json.loads(salida)
    assert codigo == 0
    assert (
        resultado["unidad"] == "0001-sumar" and resultado["pendientes"] == 0 and resultado["rechazos"] == []
    )
    assert len(servidor.reportes) == 1
    assert [e.carga.tipo for e in servidor.eventos_subidos] == ["snapshot.subido", "orden.reportada"]
    estado = Almacen(worktree).leer()
    assert estado.cola_pendiente == [] and estado.ultima_secuencia_confirmada == 2

    # Repetirlo no manda nada de nuevo.
    codigo, salida, _ = ejecutar(proxy, capsys, "sync")
    assert codigo == 0 and json.loads(salida)["pendientes"] == 0
    assert len(servidor.reportes) == 1 and len(servidor.eventos_subidos) == 2


def test_sync_informa_el_rechazo_del_servidor_y_no_sube_sus_avisos(en_cli, capsys):
    servidor = ServidorDoble([orden_implementar])
    proxy, worktree = en_cli(servidor)
    reportar_sin_conexion(proxy, servidor)
    servidor.conectado = True
    servidor.rechazar_con = CodigoError.snapshot_invalido

    codigo, salida, _ = ejecutar(proxy, capsys, "sync")

    resultado = json.loads(salida)
    assert codigo == 0 and resultado["pendientes"] == 0
    (rechazo,) = resultado["rechazos"]
    assert rechazo["codigo"] == "snapshot-invalido"
    assert servidor.eventos_subidos == []
    assert Almacen(worktree).leer().cola_pendiente == []


def test_sync_de_una_unidad_por_nombre(en_cli, capsys):
    servidor = ServidorDoble([orden_implementar])
    proxy, _ = en_cli(servidor)
    reportar_sin_conexion(proxy, servidor)
    servidor.conectado = True

    codigo, salida, _ = ejecutar(proxy, capsys, "sync", "--unidad", "0001-sumar")

    assert codigo == 0 and json.loads(salida)["unidad"] == "0001-sumar"


def test_sync_de_una_unidad_que_no_existe_lo_dice(en_cli, capsys):
    proxy, _ = en_cli(ServidorDoble())

    codigo, salida, error = ejecutar(proxy, capsys, "sync", "--unidad", "9999-nada")

    assert codigo == 1 and salida == ""
    assert "No hay worktree local para la unidad 9999-nada" in error


def test_sync_sin_unidades_locales_pide_indicarla(en_cli, capsys):
    proxy, _ = en_cli(ServidorDoble(), iniciar=False)

    codigo, _, error = ejecutar(proxy, capsys, "sync")

    assert codigo == 1 and "Indica la unidad; unidades locales: ninguna." in error


# --- railspec insumo pull --------------------------------------------------------------------------


def test_insumo_pull_escribe_el_markdown_en_el_repositorio(en_cli, capsys):
    servidor = ServidorDoble()
    proxy, _ = en_cli(servidor, iniciar=False)
    id_insumo = insumo().id

    codigo, salida, _ = ejecutar(proxy, capsys, "insumo", "pull", str(id_insumo))

    resultado = json.loads(salida)
    ruta = Path(resultado["ruta"])
    assert codigo == 0 and resultado["insumo"] == str(id_insumo)
    assert resultado["objetivo"] == "Firmar los certificados PDF al emitirlos."
    assert ruta == proxy.raiz / ".railspec" / "insumos" / f"{id_insumo}.md"
    texto = ruta.read_text(encoding="utf-8")
    assert texto.startswith(f"# Insumo {id_insumo}") and "criterio `CA-01`" in texto
    assert ruta.with_suffix(".json").is_file()  # el JSON original queda junto al Markdown
    assert sh(proxy.raiz, "status", "--porcelain").strip() == ""  # nunca aparece como cambio en git
    llamada, argumentos = servidor.llamadas[-1]
    assert llamada == "insumo.get" and argumentos["id"] == str(id_insumo)


def test_insumo_pull_con_unidad_lo_deja_en_su_worktree(en_cli, capsys):
    proxy, worktree = en_cli(ServidorDoble())
    id_insumo = insumo().id

    codigo, salida, _ = ejecutar(proxy, capsys, "insumo", "pull", str(id_insumo), "--unidad", "0001-sumar")

    assert codigo == 0
    assert Path(json.loads(salida)["ruta"]) == worktree / ".railspec" / "insumos" / f"{id_insumo}.md"
    assert not (proxy.raiz / ".railspec" / "insumos").exists()
    assert sh(worktree, "status", "--porcelain").strip() == ""


def test_insumo_pull_de_una_unidad_que_no_existe_no_escribe_nada(en_cli, capsys):
    proxy, _ = en_cli(ServidorDoble())

    codigo, _, error = ejecutar(proxy, capsys, "insumo", "pull", str(insumo().id), "--unidad", "9999-nada")

    assert codigo == 1 and "No hay worktree local para la unidad 9999-nada" in error
    assert not (proxy.raiz / ".railspec" / "insumos").exists()


def test_insumo_pull_con_error_del_servidor_sale_con_1_sin_escribir(en_cli, capsys, monkeypatch):
    servidor = ServidorDoble()

    def no_existe(args):
        raise servidor._error(CodigoError.no_encontrado, "no existe ese insumo")

    monkeypatch.setattr(servidor, "_insumo_get", no_existe)
    proxy, _ = en_cli(servidor, iniciar=False)

    codigo, salida, error = ejecutar(proxy, capsys, "insumo", "pull", str(insumo().id))

    assert codigo == 1 and salida == ""
    assert "no-encontrado" in error and "no existe ese insumo" in error
    assert not (proxy.raiz / ".railspec" / "insumos").exists()


@pytest.mark.parametrize("id_malo", ["no-es-un-uuid", "123", "", "00000000-0000-4000-9000-00000000000g"])
def test_insumo_pull_con_un_id_invalido_sale_en_una_linea_sin_traza(en_cli, capsys, id_malo):
    servidor = ServidorDoble()
    proxy, _ = en_cli(servidor, iniciar=False)

    codigo, salida, error = ejecutar(proxy, capsys, "insumo", "pull", id_malo)

    assert codigo == 1 and salida == ""
    assert error.count("\n") == 1 and error.startswith("railspec: ")
    assert f"«{id_malo}» no es un id de insumo" in error and "Traceback" not in error
    assert servidor.llamadas == []  # ni se intentó hablar con el servidor
    assert not (proxy.raiz / ".railspec" / "insumos").exists()


def test_insumo_pull_rechazado_por_la_identidad_sale_en_una_linea_sin_traza(en_cli, capsys):
    servidor = ServidorDoble()
    servidor.fallos["insumo.get"] = ServidorRechazo(
        "insumo.get", "el token venció", "Ejecuta `railspec login`."
    )
    proxy, _ = en_cli(servidor, iniciar=False)

    codigo, salida, error = ejecutar(proxy, capsys, "insumo", "pull", str(insumo().id))

    assert codigo == 1 and salida == ""
    assert error.count("\n") == 1 and "el token venció" in error and "railspec login" in error
    assert "Traceback" not in error


def test_insumo_pull_sin_conexion_sale_en_una_linea_sin_traza(en_cli, capsys):
    servidor = ServidorDoble()
    proxy, _ = en_cli(servidor, iniciar=False)
    servidor.conectado = False

    codigo, salida, error = ejecutar(proxy, capsys, "insumo", "pull", str(insumo().id))

    assert codigo == 1 and salida == "" and error.count("\n") == 1 and "desconectado" in error
