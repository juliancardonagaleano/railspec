"""Arnés simulado: un cliente MCP que lanza ``railspec mcp`` por stdio, como Claude Code.

Hace lo que haría el modelo del arnés con cada orden (escribir el artefacto o
el código en el worktree de la unidad) y lo que haría el humano con cada
checkpoint (responder el formulario de *elicitation*). Nunca decide fase ni
gate: solo sigue ``unit_advance`` hasta ``cerrada``.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mcp.types as types
from mcp import Client, StdioServerParameters

#: Aparece solo dentro del código y de la salida de las pruebas del repositorio de
#: ejemplo. En nivel ``restringido`` nunca debe llegar al servidor, a Mongo ni al grafo.
MARCA_CODIGO = "MARCA-CODIGO-e2e-7f3a91"

SPEC = """# Emitir PDF firmado

## Problema
Los certificados salen sin firma.

## Alcance
Firmar el PDF al emitir.

## Fuera de alcance
Revocación.

## Criterios de aceptación
- CA-01: El PDF emitido lleva firma verificable.
- CA-02: Un PDF sin firma se rechaza al emitir.
"""

PLAN = """# Plan

## Enfoque
Añadir un firmador mínimo junto al emisor.

## Grupos
### G1 — Firma
Archivos: `src/firma.py`, `tests/test_firma.py`
Firmar al emitir y rechazar lo que no lleve firma.

## Riesgos
- Certificado caducado: validar vigencia.

## Validación
Comando: `python -m pytest -q -s tests/test_firma.py`
"""

TASKS = """# Tareas

## G1 — Firma
- [ ] T-01: Firmar el PDF al emitir (CA-01)
- [ ] T-02: Rechazar PDF sin firma (CA-02)
"""

ARTEFACTOS = {"spec": SPEC, "plan": PLAN, "tasks": TASKS}

CODIGO = {
    "src/firma.py": f'''"""Firmador del emisor."""

_CLAVE = "{MARCA_CODIGO}"  # {MARCA_CODIGO}


def firmar(pdf: bytes) -> bytes:
    return pdf + b"|firma:" + _CLAVE.encode()


def verificar(pdf: bytes) -> bool:
    return pdf.endswith(b"|firma:" + _CLAVE.encode())
''',
    "tests/test_firma.py": f'''import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from firma import firmar, verificar


def test_firma_verificable():
    print("{MARCA_CODIGO}")
    assert verificar(firmar(b"%PDF"))


def test_sin_firma_se_rechaza():
    assert not verificar(b"%PDF")
''',
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def preparar_repositorio(raiz: Path, org: str, workspace: str, repositorio: str) -> Path:
    """Clon de ejemplo con un remoto desnudo e instalado con ``railspec instalar``."""

    remoto = raiz / "remoto.git"
    clon = raiz / repositorio
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remoto)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(clon)], check=True)
    git(clon, "config", "user.email", "e2e@railspec.test")
    git(clon, "config", "user.name", "Railspec e2e")
    (clon / "README.md").write_text("# Certificados\n", encoding="utf-8")
    (clon / "src").mkdir()
    (clon / "src" / "emisor.py").write_text("def emitir(datos):\n    return b'%PDF'\n", encoding="utf-8")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "railspec.local.cli",
            "--repo",
            str(clon),
            "instalar",
            "--org",
            org,
            "--workspace",
            workspace,
            "--repositorio",
            repositorio,
            "--arnes",
            "claude-code",
        ],
        check=True,
        capture_output=True,
    )
    git(clon, "add", "-A")
    git(clon, "commit", "-q", "-m", "inicial")
    git(clon, "remote", "add", "origin", str(remoto))
    git(clon, "push", "-q", "origin", "main")
    return clon


@dataclass
class ArnesSimulado:
    clon: Path
    url: str
    token: str
    decisiones: Callable[[str], dict[str, str]] = lambda pregunta: {"decision": "aprobado", "comentario": ""}
    preguntas: list[str] = field(default_factory=list)
    recorrido: list[str] = field(default_factory=list)
    cliente: Client | None = None
    esperas: int = 0
    espera_max_s: float = 0.5

    def parametros(self) -> StdioServerParameters:
        env = {
            k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "VIRTUAL_ENV", "SYSTEMROOT")
        }
        env |= {"RAILSPEC_URL": self.url, "RAILSPEC_TOKEN": self.token}
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "railspec.local.cli", "--repo", str(self.clon), "mcp"],
            env=env,
            cwd=str(self.clon),
        )

    async def _elicitar(self, contexto: Any, params: Any) -> types.ElicitResult:
        self.preguntas.append(params.message)
        return types.ElicitResult(action="accept", content=self.decisiones(params.message))

    async def __aenter__(self) -> ArnesSimulado:
        self.cliente = Client(self.parametros(), elicitation_callback=self._elicitar)
        await self.cliente.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        assert self.cliente is not None
        await self.cliente.__aexit__(*exc)

    async def tools(self) -> set[str]:
        assert self.cliente is not None
        return {t.name for t in (await self.cliente.list_tools()).tools}

    async def llamar(self, tool: str, argumentos: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.cliente is not None
        resultado = await self.cliente.call_tool(tool, argumentos or {})
        texto = resultado.content[0].text if resultado.content else ""
        if resultado.is_error:
            raise ErrorTool(tool, texto)
        return json.loads(texto)

    def ejecutar_orden(self, worktree: Path, orden: dict[str, Any]) -> dict[str, Any]:
        """Lo que haría el modelo del arnés; devuelve los argumentos de ``unit_report``."""

        if orden["tipo"] in ("redactar", "refinar"):
            destino = worktree / orden["ruta_artefacto"]
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_text(ARTEFACTOS[orden["artefacto"]], encoding="utf-8")
            return {}
        if orden["tipo"] == "implementar":
            for ruta, contenido in CODIGO.items():
                archivo = worktree / ruta
                archivo.parent.mkdir(parents=True, exist_ok=True)
                archivo.write_text(contenido, encoding="utf-8")
            return {"tareas_completadas": [t["id"] for t in orden.get("tareas", [])]}
        return {}

    async def recorrer(self, unidad: str, worktree: Path, max_pasos: int = 200) -> list[str]:
        """Bucle del cliente: ``unit_advance`` → ejecutar → ``unit_report`` hasta ``cerrada``."""

        for _ in range(max_pasos):
            avance = await self.llamar("unit_advance", {"unidad": unidad})
            tipo = avance["tipo"]
            if tipo == "orden":
                orden = avance["orden"]
                self.recorrido.append(f"orden:{orden['tipo']}:{orden['fase']}")
                argumentos = self.ejecutar_orden(worktree, orden)
                reporte = await self.llamar(
                    "unit_report", {"unidad": unidad, "modelo": "arnes-e2e", **argumentos}
                )
                if not reporte.get("aceptado"):
                    raise AssertionError(f"reporte no aceptado: {reporte}")
            elif tipo == "checkpoint":
                self.recorrido.append(f"checkpoint:{avance['checkpoint']['tipo']}")
                await self.llamar("unit_checkpoint", {"unidad": unidad})
            elif tipo == "en-espera":
                # El motor corre gates y transiciones en segundo plano; el arnés reintenta.
                self.esperas += 1
                await asyncio.sleep(min(avance["reintentar_en_s"], self.espera_max_s))
            elif tipo == "cerrada":
                self.recorrido.append("cerrada")
                return self.recorrido
            else:
                raise AssertionError(f"avance inesperado: {avance}")
        raise AssertionError(f"la unidad no cerró en {max_pasos} pasos: {self.recorrido}")


class ErrorTool(Exception):
    def __init__(self, tool: str, texto: str) -> None:
        super().__init__(f"{tool}: {texto}")
        self.tool = tool
        self.texto = texto
