"""Archivos de configuración que viven en cada repositorio: ``contexto.yaml`` y ``.railspecignore``.

La consola los edita y valida, pero nunca los escribe en el repositorio: propone el cambio como PR
(``repo_github.py``). Aquí está lo que no toca la red: la lista cerrada de archivos editables, la
validación de cada uno y el diff que se muestra cuando la App no puede abrir el PR.

``contexto.yaml`` declara, junto al código, los proveedores de contexto del repositorio (la misma forma que
``ProveedorContexto`` de la consola, sin ``org`` ni ``workspace``, que salen del vínculo). Por ahora ningún
componente lo lee: la consola solo garantiza que lo que se versiona es válido. El formato es el de
``railspec/docs/consola.md#archivos-del-repositorio``.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from railspec.contracts.comun import Fase
from railspec.contracts.repositorio import RolContexto

from ..contexto.destinos import DestinoNoPermitido, PoliticaDestinos
from ..contexto.secretos import ReferenciaInvalida, validar_referencia

#: Tamaño máximo de un archivo editable (bytes). Ambos son configuración, no código.
MAX_BYTES = 64 * 1024
MAX_PROVEEDORES = 20
MAX_PATRONES = 1000
MAX_LARGO_PATRON = 255


@dataclass(frozen=True)
class Hallazgo:
    mensaje: str
    linea: int | None = None

    def como_dict(self) -> dict[str, Any]:
        return {"linea": self.linea, "mensaje": self.mensaje}


@dataclass
class Validacion:
    errores: list[Hallazgo] = field(default_factory=list)
    avisos: list[Hallazgo] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores

    def como_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errores": [h.como_dict() for h in self.errores],
            "avisos": [h.como_dict() for h in self.avisos],
        }


def _tamano(texto: str, v: Validacion) -> bool:
    if len(texto.encode("utf-8")) > MAX_BYTES:
        v.errores.append(Hallazgo(f"el archivo pesa más de {MAX_BYTES // 1024} KiB"))
        return False
    if "\x00" in texto:
        v.errores.append(Hallazgo("el archivo tiene bytes nulos"))
        return False
    return True


# --- contexto.yaml ---------------------------------------------------------------------------------------


class ProveedorDeclarado(BaseModel):
    """``ProveedorContexto`` sin alcance: lo que un repositorio declara de sus herramientas de contexto."""

    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=80)
    rol: RolContexto
    url: str = Field(pattern=r"^https://", max_length=512)
    credencial_ref: str | None = Field(default=None, pattern=r"^secret://[a-z0-9-]+/[A-Za-z0-9_.-]+$")
    politica_fallo: Literal["estricta", "blanda"]
    fases: list[Fase] = Field(default_factory=list)
    presupuesto_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _gobernanza_estricta(self) -> ProveedorDeclarado:
        if self.rol == RolContexto.gobernanza and self.politica_fallo != "estricta":
            raise ValueError("el rol gobernanza siempre tiene política de fallo estricta")
        return self


class ContextoRepo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    proveedores: list[ProveedorDeclarado] = Field(default_factory=list, max_length=MAX_PROVEEDORES)


PLANTILLA_CONTEXTO = """\
# Herramientas de contexto de este repositorio (Railspec).
# Hoy ningún componente de Railspec lee este archivo: se versiona aquí para declararlas junto al código.
version: 1
proveedores: []
# proveedores:
#   - nombre: documentacion-interna
#     rol: documentacion          # gobernanza | grafo-de-codigo | memoria | documentacion
#     url: https://docs.ejemplo.com/mcp
#     credencial_ref: secret://mi-org--docs/api-key   # opcional; nunca el valor
#     politica_fallo: blanda      # estricta | blanda (gobernanza siempre estricta)
#     fases: [spec, plan]         # opcional
#     presupuesto_tokens: 4000    # opcional
"""

PLANTILLA_IGNORE = """\
# Rutas que Railspec no indexa ni envía (sintaxis gitignore: *, ?, ** y barra final de directorio).
# Una por línea. Sin negaciones (!).
"""


class _Cargador(yaml.SafeLoader):
    """``safe_load`` que rechaza claves duplicadas (PyYAML se queda con la última sin avisar)."""

    def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
        vistas: set[Any] = set()
        for clave, _ in node.value:
            valor = self.construct_object(clave, deep=True)
            if isinstance(valor, str):
                if valor in vistas:
                    raise yaml.constructor.ConstructorError(
                        None, None, f"clave repetida: {valor}", clave.start_mark
                    )
                vistas.add(valor)
        return super().construct_mapping(node, deep)


def _linea_de(marca: Any) -> int | None:
    return None if marca is None else int(marca.line) + 1


def _nodo_en(raiz: yaml.Node | None, ruta: tuple[Any, ...]) -> yaml.Node | None:
    """El nodo del documento en ``ruta`` (claves y posiciones); el último que se pudo resolver si se corta."""

    nodo = raiz
    for paso in ruta:
        if isinstance(nodo, yaml.MappingNode):
            hijo = next((v for k, v in nodo.value if getattr(k, "value", None) == paso), None)
        elif isinstance(nodo, yaml.SequenceNode) and isinstance(paso, int) and paso < len(nodo.value):
            hijo = nodo.value[paso]
        else:
            hijo = None
        if hijo is None:
            break
        nodo = hijo
    return nodo


def _sintaxis(texto: str, v: Validacion) -> tuple[Any, yaml.Node | None] | None:
    try:
        # Sin anclas ni alias: no hacen falta aquí y un alias anidado multiplica la memoria al cargar.
        for ficha in yaml.scan(texto, Loader=yaml.SafeLoader):
            if isinstance(ficha, yaml.AnchorToken | yaml.AliasToken):
                v.errores.append(
                    Hallazgo("no se admiten anclas ni alias (&, *)", _linea_de(ficha.start_mark))
                )
                return None
        raiz = yaml.compose(texto, Loader=yaml.SafeLoader)
        datos = yaml.load(texto, Loader=_Cargador)  # noqa: S506 (SafeLoader sin alias)
    except yaml.YAMLError as exc:
        marca = getattr(exc, "problem_mark", None)
        problema = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        v.errores.append(Hallazgo(f"YAML inválido: {problema}", _linea_de(marca)))
        return None
    return datos, raiz


def validar_contexto(texto: str, org: str, politica: PoliticaDestinos | None = None) -> Validacion:
    """Valida ``contexto.yaml``: YAML sin alias ni claves repetidas y la forma de ``ContextoRepo``.

    Los errores impiden proponer el cambio. Es un aviso (no error) que el host de una ``url`` no esté en
    la allowlist de la plataforma o que la ``credencial_ref`` no sea del namespace de ``org``: el archivo
    es declarativo y quien lo lea decidirá; pero así se ve antes de que falle.
    """

    v = Validacion()
    if not _tamano(texto, v):
        return v
    if not texto.strip():
        v.errores.append(Hallazgo("el archivo está vacío (usa `version: 1`)"))
        return v
    cargado = _sintaxis(texto, v)
    if cargado is None:
        return v
    datos, raiz = cargado
    if not isinstance(datos, dict):
        v.errores.append(Hallazgo("la raíz debe ser un mapa con `version` y `proveedores`", 1))
        return v
    try:
        contexto = ContextoRepo.model_validate(datos)
    except ValidationError as exc:
        for e in exc.errors()[:50]:
            ruta = tuple(e["loc"])
            donde = "".join(
                f"[{p}]" if isinstance(p, int) else f".{p}" if i else str(p) for i, p in enumerate(ruta)
            )
            nodo = _nodo_en(raiz, ruta)
            mensaje = e["msg"].removeprefix("Value error, ")
            v.errores.append(
                Hallazgo(
                    f"{donde}: {mensaje}" if donde else mensaje, _linea_de(nodo.start_mark) if nodo else None
                )
            )
        return v
    politica = politica or PoliticaDestinos.desde_entorno()
    vistos: set[tuple[str, str]] = set()
    for i, p in enumerate(contexto.proveedores):
        linea = _linea_de(_nodo_en(raiz, ("proveedores", i)).start_mark)  # type: ignore[union-attr]
        clave = (p.rol.value, p.nombre)
        if clave in vistos:
            v.errores.append(Hallazgo(f"proveedores[{i}]: {p.rol.value}/{p.nombre} está repetido", linea))
        vistos.add(clave)
        try:
            politica.validar_url(p.url)
        except DestinoNoPermitido as exc:
            v.avisos.append(Hallazgo(f"proveedores[{i}].url: {exc}", linea))
        if p.credencial_ref is not None:
            try:
                validar_referencia(p.credencial_ref, org)
            except ReferenciaInvalida as exc:
                v.avisos.append(Hallazgo(f"proveedores[{i}].credencial_ref: {exc}", linea))
    return v


# --- .railspecignore -------------------------------------------------------------------------------------


def validar_ignore(texto: str, org: str = "", politica: PoliticaDestinos | None = None) -> Validacion:
    """Valida ``.railspecignore`` con el subconjunto de gitignore que entiende el proxy local.

    Un patrón que el proxy no interpretaría como parece (negación, clases ``[...]``, barras invertidas)
    es un error o un aviso según su efecto: ``!`` se leería como un carácter literal y dejaría pasar lo que
    se quería excluir, así que es error.
    """

    v = Validacion()
    if not _tamano(texto, v):
        return v
    patrones = 0
    for n, cruda in enumerate(texto.splitlines(), start=1):
        p = cruda.strip()
        if not p or p.startswith("#"):
            continue
        patrones += 1
        if any(ord(c) < 32 for c in p):
            v.errores.append(Hallazgo("el patrón tiene caracteres de control", n))
        elif p.startswith("!"):
            v.errores.append(Hallazgo("no se admite la negación (!): se leería como un carácter literal", n))
        elif len(p) > MAX_LARGO_PATRON:
            v.errores.append(Hallazgo(f"el patrón supera {MAX_LARGO_PATRON} caracteres", n))
        elif ".." in p.split("/"):
            v.errores.append(Hallazgo("el patrón no puede salir del repositorio (..)", n))
        else:
            if "\\" in p:
                v.avisos.append(Hallazgo("la barra invertida se toma literal; separa con /", n))
            if re.search(r"\[[^\]]*\]", p):
                v.avisos.append(Hallazgo("las clases [...] no se interpretan: se toman literales", n))
            if re.fullmatch(r"/?\*{1,2}/?", p):
                v.avisos.append(Hallazgo("excluye todo el repositorio", n))
    if patrones > MAX_PATRONES:
        v.errores.append(Hallazgo(f"hay {patrones} patrones; el máximo es {MAX_PATRONES}"))
    return v


# --- archivos editables ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchivoEditable:
    id: str
    ruta: str
    plantilla: str
    validar: Callable[[str, str, PoliticaDestinos | None], Validacion]


ARCHIVOS: dict[str, ArchivoEditable] = {
    "contexto": ArchivoEditable("contexto", "contexto.yaml", PLANTILLA_CONTEXTO, validar_contexto),
    "ignore": ArchivoEditable("ignore", ".railspecignore", PLANTILLA_IGNORE, validar_ignore),
}


def diff_unificado(antes: str | None, despues: str, ruta: str) -> str:
    """Diff en formato unificado (``a/`` y ``b/``); ``antes`` ``None`` es un archivo nuevo."""

    origen = "/dev/null" if antes is None else f"a/{ruta}"
    lineas = difflib.unified_diff(
        (antes or "").splitlines(keepends=True),
        despues.splitlines(keepends=True),
        fromfile=origen,
        tofile=f"b/{ruta}",
    )
    salida = []
    for linea in lineas:
        salida.append(linea if linea.endswith("\n") else linea + "\n\\ No newline at end of file\n")
    return "".join(salida)
