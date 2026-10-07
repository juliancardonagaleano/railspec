"""Búsqueda de texto completo sobre el código del clon: el complemento local de ``codebase-memory-mcp``.

El indexador da estructura (símbolos, aristas, hashes) y el servidor responde por nombre; ninguno de
los dos contesta «dónde se maneja el vencimiento del certificado» si ese texto no está en un nombre.
Este índice guarda, por símbolo, su nombre, su ruta y su cuerpo en una base SQLite con FTS5 y ordena
con BM25. Es solo local:

- vive en ``.railspec/busqueda.sqlite`` del clon o del worktree de la unidad, que ``git`` no versiona;
- nunca viaja al servidor ni al modelo de Railspec; el arnés lo consulta por ``code_search`` igual que
  leería el archivo;
- sale de lo que el indexador ya reportó (los mismos símbolos, ids y exclusiones que el snapshot y el
  índice de CI), así que no indexa ningún archivo que el snapshot excluiría por secretos;
- no usa modelo ni vectores. Los embeddings locales son un paso posterior (``Indexador.embedding_consulta``)
  y entrarán aquí sin cambiar la forma de las respuestas.

El índice se construye entero con ``reemplazar`` (en un archivo nuevo que sustituye al anterior, de modo
que una construcción a medias nunca queda visible) y se mantiene con ``aplicar`` con el delta de cada
snapshot, solo si la construcción completa existe: un índice parcial haría creer que un símbolo no existe.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from railspec.contracts.snapshot import DeltaIndice, Simbolo

from . import secretos

ARCHIVO = Path(".railspec") / "busqueda.sqlite"
#: Cambia con la forma de las tablas: un índice de otra versión se descarta y se pide reconstruir.
VERSION_ESQUEMA = 1
#: Lo que se guarda del cuerpo de un símbolo; una función de miles de líneas no necesita estar entera.
CUERPO_MAX = 8000
#: Pesos de BM25 por columna (nombre, partes, ruta, cuerpo): lo que está en el nombre pesa más.
PESOS = (10.0, 6.0, 2.0, 1.0)
TERMINOS_MAX = 12
LIMITE_MAX = 100

_IDENTIFICADOR = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_PARTES = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_TERMINO = re.compile(r"[^\W_]+", re.UNICODE)


class BusquedaNoDisponible(Exception):
    """SQLite sin FTS5 o índice ilegible; el mensaje dice qué hacer."""


def partes(identificador: str) -> list[str]:
    """``emitirCertificado_PDF2`` → ``emitir certificado pdf 2`` (minúsculas)."""

    return [p.lower() for p in _PARTES.findall(identificador)]


def _tokens_de_identificadores(texto: str) -> str:
    """Las partes de camelCase de los identificadores de ``texto`` que el tokenizador no separa solo."""

    vistas: dict[str, None] = {}
    for ident in _IDENTIFICADOR.findall(texto):
        trozos = partes(ident)
        if len(trozos) > 1:
            for t in trozos:
                vistas.setdefault(t, None)
    return " ".join(vistas)


def _consulta_fts(texto: str) -> str | None:
    """Los términos de la búsqueda como OR de prefijos entrecomillados; ``None`` si no queda ninguno.

    Entrecomillar evita que un término se lea como operador de FTS5 (``AND``, ``NOT``, ``col:``)."""

    terminos: dict[str, None] = {}
    for crudo in _TERMINO.findall(texto):
        terminos.setdefault(crudo.lower(), None)
        trozos = partes(crudo)
        if len(trozos) > 1:  # ``emitirCertificado`` también se busca por ``emitir`` y ``certificado``
            for t in trozos:
                terminos.setdefault(t, None)
    lista = list(terminos)[:TERMINOS_MAX]
    if not lista:
        return None
    return " OR ".join(f'"{t}"*' if len(t) >= 3 else f'"{t}"' for t in lista)


class IndiceTexto:
    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta

    @classmethod
    def de(cls, raiz: Path) -> IndiceTexto:
        return cls(raiz / ARCHIVO)

    # --- conexión ------------------------------------------------------------
    def _conectar(self, ruta: Path | None = None) -> sqlite3.Connection:
        conn = sqlite3.connect(ruta or self.ruta, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _crear(conn: sqlite3.Connection) -> None:
        try:
            conn.executescript(
                """
                CREATE TABLE meta (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
                CREATE TABLE simbolos (
                    n INTEGER PRIMARY KEY,
                    id TEXT NOT NULL UNIQUE,
                    nombre TEXT NOT NULL,
                    tipo TEXT NOT NULL,
                    ruta TEXT NOT NULL,
                    inicio INTEGER NOT NULL,
                    fin INTEGER NOT NULL,
                    sha256 TEXT NOT NULL
                );
                CREATE INDEX simbolos_ruta ON simbolos (ruta);
                CREATE VIRTUAL TABLE texto USING fts5(
                    nombre, partes, ruta, cuerpo, tokenize = "unicode61 remove_diacritics 2"
                );
                """
            )
        except sqlite3.OperationalError as exc:
            raise BusquedaNoDisponible(
                f"Este Python trae un SQLite sin FTS5 ({exc}); la búsqueda de texto no está disponible."
            ) from exc

    def existe(self) -> bool:
        return self.ruta.is_file()

    def meta(self) -> dict[str, str]:
        """Metadatos del índice; vacío si no existe o es de otra versión."""

        if not self.existe():
            return {}
        try:
            with closing(self._conectar()) as conn:
                filas = {f["clave"]: f["valor"] for f in conn.execute("SELECT clave, valor FROM meta")}
        except sqlite3.DatabaseError:
            return {}
        return filas if filas.get("version_esquema") == str(VERSION_ESQUEMA) else {}

    def completo(self) -> bool:
        return self.meta().get("completo") == "1"

    # --- escritura -------------------------------------------------------------
    @staticmethod
    def _cuerpo(cache: dict[str, list[str] | None], worktree: Path, s: Simbolo) -> str:
        if s.ruta not in cache:
            archivo = worktree / s.ruta
            try:
                datos = archivo.read_bytes() if archivo.is_file() else None
            except OSError:
                datos = None
            cache[s.ruta] = (
                None
                if datos is None or b"\0" in datos[:8192]
                else datos.decode("utf-8", "replace").splitlines()
            )
        lineas = cache[s.ruta]
        if lineas is None:
            return ""
        return "\n".join(lineas[max(s.linea_inicio - 1, 0) : s.linea_fin])[:CUERPO_MAX]

    @classmethod
    def _insertar(
        cls, conn: sqlite3.Connection, cache: dict[str, list[str] | None], worktree: Path, s: Simbolo
    ) -> None:
        cuerpo = cls._cuerpo(cache, worktree, s)
        cur = conn.execute(
            "INSERT INTO simbolos (id, nombre, tipo, ruta, inicio, fin, sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (s.id, s.nombre, s.tipo.value, s.ruta, s.linea_inicio, s.linea_fin, s.sha256),
        )
        conn.execute(
            "INSERT INTO texto (rowid, nombre, partes, ruta, cuerpo) VALUES (?, ?, ?, ?, ?)",
            (
                cur.lastrowid,
                s.nombre,
                _tokens_de_identificadores(s.nombre + " " + cuerpo),
                s.ruta,
                cuerpo,
            ),
        )

    @staticmethod
    def _poner_meta(conn: sqlite3.Connection, **valores: str) -> None:
        conn.executemany(
            "INSERT INTO meta (clave, valor) VALUES (?, ?) "
            "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
            list(valores.items()),
        )

    def reemplazar(self, worktree: Path, simbolos: Iterable[Simbolo], **meta: str) -> int:
        """Construye el índice entero con ``simbolos`` y sustituye al anterior (sea de la versión que sea).

        ``meta`` se guarda tal cual (``commit``, ``repositorio``, ``motor``…)."""

        nuevo = self.ruta.with_name(self.ruta.name + ".nuevo")
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo.unlink(missing_ok=True)
        cache: dict[str, list[str] | None] = {}
        total = 0
        try:
            with closing(self._conectar(nuevo)) as conn:
                self._crear(conn)
                with conn:
                    for s in {s.id: s for s in simbolos}.values():
                        self._insertar(conn, cache, worktree, s)
                        total += 1
                    self._poner_meta(
                        conn,
                        version_esquema=str(VERSION_ESQUEMA),
                        completo="1",
                        simbolos=str(total),
                        construido=datetime.now(UTC).isoformat(timespec="seconds"),
                        **meta,
                    )
            nuevo.replace(self.ruta)
        finally:
            nuevo.unlink(missing_ok=True)
        return total

    def aplicar(self, worktree: Path, delta: DeltaIndice, commit: str | None = None) -> int:
        """Aplica el delta de un snapshot a un índice completo; devuelve cuántos símbolos tocó.

        Un símbolo cuyo hash no cambió solo actualiza sus líneas: no se vuelve a leer su cuerpo."""

        if not self.completo():
            return 0
        cache: dict[str, list[str] | None] = {}
        tocados = 0
        with closing(self._conectar()) as conn, conn:
            for i in delta.simbolos_borrados:
                tocados += self._borrar(conn, i)
            for s in delta.simbolos_upsert:
                previo = conn.execute("SELECT n, sha256 FROM simbolos WHERE id = ?", (s.id,)).fetchone()
                if previo is not None and previo["sha256"] == s.sha256:
                    conn.execute(
                        "UPDATE simbolos SET inicio = ?, fin = ? WHERE n = ?",
                        (s.linea_inicio, s.linea_fin, previo["n"]),
                    )
                    continue
                if previo is not None:
                    self._borrar(conn, s.id)
                self._insertar(conn, cache, worktree, s)
                tocados += 1
            n = conn.execute("SELECT count(*) FROM simbolos").fetchone()[0]
            self._poner_meta(
                conn,
                simbolos=str(n),
                actualizado=datetime.now(UTC).isoformat(timespec="seconds"),
                **({} if commit is None else {"commit_actualizado": commit}),
            )
        return tocados

    @staticmethod
    def _borrar(conn: sqlite3.Connection, id_simbolo: str) -> int:
        fila = conn.execute("SELECT n FROM simbolos WHERE id = ?", (id_simbolo,)).fetchone()
        if fila is None:
            return 0
        conn.execute("DELETE FROM texto WHERE rowid = ?", (fila["n"],))
        conn.execute("DELETE FROM simbolos WHERE n = ?", (fila["n"],))
        return 1

    # --- lectura -----------------------------------------------------------------
    def buscar(
        self, texto: str, limite: int = 20, tipos: list[str] | None = None, ruta: str | None = None
    ) -> list[dict[str, Any]]:
        """Símbolos ordenados por BM25 para ``texto``; vacío si no queda ningún término buscable.

        ``ruta`` es un prefijo de ruta (``src/pdf/``). El fragmento sale del cuerpo con los términos
        marcados entre «» y con los secretos redactados."""

        consulta = _consulta_fts(texto)
        if consulta is None or not self.completo():
            return []
        limite = max(1, min(limite, LIMITE_MAX))
        filtros, args = [], [consulta]
        if tipos:
            filtros.append(f"s.tipo IN ({','.join('?' * len(tipos))})")
            args += tipos
        if ruta:
            filtros.append("substr(s.ruta, 1, ?) = ?")
            args += [len(ruta), ruta]
        sql = (
            "SELECT s.id, s.nombre, s.tipo, s.ruta, s.inicio, s.fin, "
            f"bm25(texto, {', '.join(map(str, PESOS))}) AS puntaje, "
            "snippet(texto, 3, '«', '»', ' … ', 14) AS fragmento "
            "FROM texto JOIN simbolos s ON s.n = texto.rowid "
            f"WHERE texto MATCH ? {''.join(' AND ' + f for f in filtros)} "
            "ORDER BY puntaje, s.ruta, s.inicio LIMIT ?"
        )
        with closing(self._conectar()) as conn:
            try:
                filas = conn.execute(sql, [*args, limite]).fetchall()
            except sqlite3.OperationalError as exc:  # sintaxis de FTS5 que el saneado no previó
                raise BusquedaNoDisponible(f"No se pudo interpretar la búsqueda: {exc}") from exc
        return [
            {
                "id": f["id"],
                "nombre": f["nombre"],
                "tipo": f["tipo"],
                "ruta": f["ruta"],
                "linea_inicio": f["inicio"],
                "linea_fin": f["fin"],
                # BM25 de SQLite es negativo (menor = mejor): se expone como puntaje creciente.
                "puntaje": round(-f["puntaje"], 3),
                "fragmento": secretos.redactar(f["fragmento"] or ""),
            }
            for f in filas
        ]
