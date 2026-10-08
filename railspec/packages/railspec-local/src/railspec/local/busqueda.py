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
- por defecto no usa modelo. Con un codificador local instalado (``codificador.py``) guarda además un
  vector int8 por símbolo en la misma base y mezcla BM25 con similitud por rango recíproco (RRF); sin él,
  o sin vectores todavía, todo sigue por palabras.

El índice se construye entero con ``reemplazar`` (en un archivo nuevo que sustituye al anterior, de modo
que una construcción a medias nunca queda visible) y se mantiene con ``aplicar`` con el delta de cada
snapshot, solo si la construcción completa existe: un índice parcial haría creer que un símbolo no existe.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from railspec.contracts.snapshot import DeltaIndice, Simbolo

from . import secretos

if TYPE_CHECKING:
    from .codificador import Codificador

ARCHIVO = Path(".railspec") / "busqueda.sqlite"
#: Cambia con la forma de las tablas: un índice de otra versión se descarta y se pide reconstruir.
VERSION_ESQUEMA = 1
#: Lo que se guarda del cuerpo de un símbolo; una función de miles de líneas no necesita estar entera.
CUERPO_MAX = 8000
#: Pesos de BM25 por columna (nombre, partes, ruta, cuerpo): lo que está en el nombre pesa más.
PESOS = (10.0, 6.0, 2.0, 1.0)
TERMINOS_MAX = 12
LIMITE_MAX = 100
#: Constante de la fusión por rango recíproco: 60 es el valor habitual y no pide ajuste por corpus.
RRF_K = 60
#: Largo del fragmento de un resultado que sale solo de la similitud (no hay términos que marcar).
FRAGMENTO_SEMANTICO = 160

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
        IndiceTexto._asegurar_vectores(conn)

    @staticmethod
    def _asegurar_vectores(conn: sqlite3.Connection) -> None:
        """La tabla de vectores se crea al vuelo: un índice previo a los codificadores la gana sin rehacerse.

        ``sha256`` es el del símbolo cuando se codificó: si cambia el cuerpo, ``_borrar`` quita el vector."""

        conn.execute(
            "CREATE TABLE IF NOT EXISTS vectores ("
            "n INTEGER PRIMARY KEY, modelo TEXT NOT NULL, sha256 TEXT NOT NULL, vec BLOB NOT NULL)"
        )

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
                self._heredar_vectores(conn)
            nuevo.replace(self.ruta)
        finally:
            nuevo.unlink(missing_ok=True)
        return total

    def _heredar_vectores(self, conn: sqlite3.Connection) -> None:
        """Copia del índice anterior los vectores de los símbolos que no cambiaron: no se recodifica."""

        if not self.existe():
            return
        try:
            conn.execute("ATTACH DATABASE ? AS previo", (str(self.ruta),))  # fuera de toda transacción
            with conn:
                conn.execute(
                    "INSERT INTO vectores (n, modelo, sha256, vec) "
                    "SELECT s.n, v.modelo, v.sha256, v.vec FROM simbolos s "
                    "JOIN previo.simbolos p ON p.id = s.id AND p.sha256 = s.sha256 "
                    "JOIN previo.vectores v ON v.n = p.n AND v.sha256 = s.sha256"
                )
                modelo = conn.execute(
                    "SELECT valor FROM previo.meta WHERE clave = 'modelo_vectores'"
                ).fetchone()
                if modelo is not None:
                    self._poner_meta(conn, modelo_vectores=modelo[0])
        except sqlite3.DatabaseError:  # el anterior es de otra forma o está roto: se recodifica y ya
            pass
        finally:
            try:
                conn.execute("DETACH DATABASE previo")
            except sqlite3.DatabaseError:
                pass

    def aplicar(self, worktree: Path, delta: DeltaIndice, commit: str | None = None) -> int:
        """Aplica el delta de un snapshot a un índice completo; devuelve cuántos símbolos tocó.

        Un símbolo cuyo hash no cambió solo actualiza sus líneas: no se vuelve a leer su cuerpo."""

        if not self.completo():
            return 0
        cache: dict[str, list[str] | None] = {}
        tocados = 0
        with closing(self._conectar()) as conn, conn:
            self._asegurar_vectores(conn)
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
        conn.execute("DELETE FROM vectores WHERE n = ?", (fila["n"],))
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

    # --- vectores (opcional: solo con un codificador local) ---------------------------------------
    @staticmethod
    def _texto_de_codificacion(nombre: str, ruta: str, cuerpo: str) -> str:
        return f"{nombre}\n{ruta}\n{cuerpo}"

    def vectores(self, modelo: str | None = None) -> dict[str, Any]:
        """Cuántos símbolos tienen vector: ``{modelo, codificados, total}``; ``codificados`` es 0 sin modelo.

        Sin ``modelo`` usa el que el índice declara (``modelo_vectores``)."""

        meta = self.meta()
        if not meta:
            return {"modelo": modelo, "codificados": 0, "total": 0}
        modelo = modelo or meta.get("modelo_vectores")
        with closing(self._conectar()) as conn:
            self._asegurar_vectores_lectura(conn)
            total = conn.execute("SELECT count(*) FROM simbolos").fetchone()[0]
            hechos = (
                conn.execute("SELECT count(*) FROM vectores WHERE modelo = ?", (modelo,)).fetchone()[0]
                if modelo
                else 0
            )
        return {"modelo": modelo, "codificados": hechos, "total": total}

    def _asegurar_vectores_lectura(self, conn: sqlite3.Connection) -> None:
        # Un índice de antes de los codificadores no tiene la tabla; crearla en una lectura es inocuo.
        with conn:
            self._asegurar_vectores(conn)

    def codificar_pendientes(
        self,
        codificador: Codificador,
        limite: int | None = None,
        progreso: Callable[[int, int], None] | None = None,
    ) -> int:
        """Codifica los símbolos sin vector de este modelo (o con el cuerpo cambiado); devuelve cuántos.

        Se guarda por lotes: si se interrumpe, lo hecho queda y la próxima llamada sigue donde iba. Cada
        símbolo guarda un solo vector: cambiar de modelo recodifica todo, y mientras tanto la búsqueda usa
        solo los vectores del modelo pedido."""

        from .codificador import LOTE, a_int8

        if not self.completo():
            return 0
        with closing(self._conectar()) as conn:
            self._asegurar_vectores_lectura(conn)
            filas = conn.execute(
                "SELECT s.n, s.nombre, s.ruta, s.sha256, t.cuerpo FROM simbolos s "
                "JOIN texto t ON t.rowid = s.n "
                "LEFT JOIN vectores v ON v.n = s.n AND v.modelo = ? AND v.sha256 = s.sha256 "
                "WHERE v.n IS NULL ORDER BY s.n" + (" LIMIT ?" if limite is not None else ""),
                (codificador.nombre, *([limite] if limite is not None else [])),
            ).fetchall()
            hechos = 0
            for i in range(0, len(filas), LOTE):
                lote = filas[i : i + LOTE]
                vectores = a_int8(
                    codificador.codificar([self._texto_de_codificacion(f[1], f[2], f[4]) for f in lote])
                )
                with conn:
                    conn.executemany(
                        "INSERT INTO vectores (n, modelo, sha256, vec) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT (n) DO UPDATE SET modelo = excluded.modelo, "
                        "sha256 = excluded.sha256, vec = excluded.vec",
                        [(f[0], codificador.nombre, f[3], v) for f, v in zip(lote, vectores, strict=True)],
                    )
                    self._poner_meta(conn, modelo_vectores=codificador.nombre)
                hechos += len(lote)
                if progreso is not None:
                    progreso(hechos, len(filas))
        return hechos

    def _filtros(self, tipos: list[str] | None, ruta: str | None) -> tuple[str, list[Any]]:
        filtros: list[str] = []
        args: list[Any] = []
        if tipos:
            filtros.append(f"s.tipo IN ({','.join('?' * len(tipos))})")
            args += tipos
        if ruta:
            filtros.append("substr(s.ruta, 1, ?) = ?")
            args += [len(ruta), ruta]
        return "".join(" AND " + f for f in filtros), args

    def buscar_semantico(
        self,
        vector: Any,
        modelo: str,
        limite: int = 20,
        tipos: list[str] | None = None,
        ruta: str | None = None,
    ) -> list[dict[str, Any]]:
        """Los símbolos más parecidos (coseno) al ``vector`` de la consulta entre los del ``modelo``."""

        import numpy as np

        if not self.completo():
            return []
        limite = max(1, min(limite, LIMITE_MAX))
        extra, args = self._filtros(tipos, ruta)
        with closing(self._conectar()) as conn:
            self._asegurar_vectores_lectura(conn)
            filas = conn.execute(
                "SELECT v.n, v.vec FROM vectores v JOIN simbolos s ON s.n = v.n "
                f"WHERE v.modelo = ? {extra} ORDER BY v.n",
                [modelo, *args],
            ).fetchall()
            if not filas:
                return []
            matriz = np.frombuffer(b"".join(f["vec"] for f in filas), dtype=np.int8).reshape(len(filas), -1)
            consulta = np.asarray(vector, dtype=np.float32)
            if matriz.shape[1] != consulta.shape[0]:
                raise BusquedaNoDisponible(
                    f"Los vectores del índice tienen {matriz.shape[1]} dimensiones y la consulta "
                    f"{consulta.shape[0]}: cambió el modelo; ejecuta `railspec indice --vectores`."
                )
            # por bloques: convertir toda la matriz a float32 cuadruplicaría su memoria
            similitud = (
                np.concatenate(
                    [
                        matriz[i : i + 16384].astype(np.float32) @ consulta
                        for i in range(0, len(matriz), 16384)
                    ]
                )
                / 127.0
            )
            orden = np.argsort(-similitud, kind="stable")[:limite]
            ns = [int(filas[i]["n"]) for i in orden]
            detalle = {
                f["n"]: f
                for f in conn.execute(
                    "SELECT s.n, s.id, s.nombre, s.tipo, s.ruta, s.inicio, s.fin, t.cuerpo FROM simbolos s "
                    f"JOIN texto t ON t.rowid = s.n WHERE s.n IN ({','.join('?' * len(ns))})",
                    ns,
                )
            }
        return [
            {
                "id": detalle[n]["id"],
                "nombre": detalle[n]["nombre"],
                "tipo": detalle[n]["tipo"],
                "ruta": detalle[n]["ruta"],
                "linea_inicio": detalle[n]["inicio"],
                "linea_fin": detalle[n]["fin"],
                "puntaje": round(float(similitud[i]), 3),
                "fragmento": secretos.redactar((detalle[n]["cuerpo"] or "")[:FRAGMENTO_SEMANTICO]),
            }
            for n, i in zip(ns, orden, strict=True)
        ]

    def buscar_hibrido(
        self,
        texto: str,
        vector: Any,
        modelo: str,
        limite: int = 20,
        tipos: list[str] | None = None,
        ruta: str | None = None,
    ) -> list[dict[str, Any]]:
        """BM25 y similitud fundidos por rango recíproco (RRF); cada resultado dice de dónde vino."""

        limite = max(1, min(limite, LIMITE_MAX))
        profundidad = min(max(limite * 3, 30), LIMITE_MAX)
        por_texto = self.buscar(texto, profundidad, tipos, ruta)
        por_similitud = self.buscar_semantico(vector, modelo, profundidad, tipos, ruta)
        return fundir(por_texto, por_similitud)[:limite]


def fundir(por_texto: list[dict[str, Any]], por_similitud: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """RRF de dos listas ya ordenadas; el empate lo gana quien vino mejor por texto."""

    puntos: dict[str, float] = {}
    origen: dict[str, set[str]] = {}
    base: dict[str, dict[str, Any]] = {}
    for etiqueta, lista in (("texto", por_texto), ("semantico", por_similitud)):
        for rango, r in enumerate(lista):
            puntos[r["id"]] = puntos.get(r["id"], 0.0) + 1.0 / (RRF_K + rango + 1)
            origen.setdefault(r["id"], set()).add(etiqueta)
            base.setdefault(r["id"], r)  # conserva el fragmento con los términos marcados si lo hay
    salida = []
    for i, p in sorted(puntos.items(), key=lambda x: -x[1]):
        r = dict(base[i])
        r["puntaje"] = round(p, 5)
        r["origen"] = "ambos" if len(origen[i]) == 2 else next(iter(origen[i]))
        salida.append(r)
    return salida


def evaluar(
    indice: IndiceTexto, casos: list[dict[str, Any]], codificador: Codificador | None = None, k: int = 5
) -> dict[str, Any]:
    """Compara los modos de búsqueda en ``casos`` (``consulta`` y ``esperados``: nombres de símbolo).

    Por modo: ``acierto`` (fracción con algún esperado entre los ``k`` primeros) y ``mrr`` (media de 1/rango).
    Sin codificador, o sin vectores, solo se mide el texto: el resto no tiene qué comparar."""

    modos = ["texto"]
    if codificador is not None and indice.vectores(codificador.nombre)["codificados"] > 0:
        modos += ["semantico", "hibrido"]
    rangos: dict[str, list[int | None]] = {m: [] for m in modos}
    detalle = []
    for caso in casos:
        consulta, esperados = str(caso["consulta"]), set(caso["esperados"])
        vector = codificador.codificar_consulta(consulta) if len(modos) > 1 and codificador else None
        fila: dict[str, int | None] = {}
        for m in modos:
            if m == "texto":
                encontrados = indice.buscar(consulta, k)
            elif m == "semantico":
                encontrados = indice.buscar_semantico(vector, codificador.nombre, k)  # type: ignore[union-attr]
            else:
                encontrados = indice.buscar_hibrido(consulta, vector, codificador.nombre, k)  # type: ignore[union-attr]
            rango = next((i + 1 for i, r in enumerate(encontrados[:k]) if r["nombre"] in esperados), None)
            rangos[m].append(rango)
            fila[m] = rango
        detalle.append({"consulta": consulta, "rango": fila})
    n = max(len(casos), 1)
    resumen = {
        m: {
            "acierto": round(sum(r is not None for r in rs) / n, 3),
            "mrr": round(sum(1 / r for r in rs if r) / n, 3),
        }
        for m, rs in rangos.items()
    }
    return {"consultas": len(casos), "k": k, "modos": resumen, "detalle": detalle}
