"""Motor sobre Postgres: el grafo vive en la misma base que el estado, sin servicio ni licencia aparte.

Cada grafo físico (el nombre que construye ``acceso``) es un conjunto de filas con su nombre en la
columna ``grafo``; hay una tabla por tipo de dato, en el esquema ``railspec`` por defecto:

- ``grafo_grafos(grafo, meta, actualizado)``: el grafo existe mientras tenga fila aquí. ``meta`` es el
  json de ``Meta`` sin ``actualizado``, que va en su propia columna (``sellar`` lo fija sin pisar el resto).
- ``grafo_simbolos(grafo, id, nombre, tipo, ruta, linea_inicio, linea_fin, sha256, embedding)``: solo
  estructura y hashes, nunca texto de código. El embedding es ``real[]`` y es opcional.
- ``grafo_aristas(grafo, origen, destino, relacion)``: sin clave foránea a los símbolos, porque un extremo
  puede ser una referencia a otro repositorio del workspace (un stub en FalkorDB): aquí basta el id.
- ``grafo_clusters``, ``grafo_procesos`` y ``grafo_trazas``.

Todas las tablas cuelgan de ``grafo_grafos`` con ``ON DELETE CASCADE``: ``borrar`` es un ``DELETE``.

Lo que este motor no hace, a propósito:

- ``knn`` compara en Python los vectores del grafo (fuerza bruta, coseno). Sirve mientras el servidor
  guarde pocos o ningún embedding; con muchos, el paso siguiente es pgvector (no está en todos los
  Postgres administrados) y no cambia el protocolo.
- No recorre el grafo en SQL: el impacto, los clusters y los procesos se calculan en Python por encima de
  ``MotorGrafo``, igual que con FalkorDB, y aquí solo hacen falta las aristas a un salto.

Cada operación abre su propia transacción, así que el pooler de Supabase vale en modo sesión y en modo
transacción (sin sentencias preparadas).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from .motor import PROPIEDADES_SIMBOLO, AristaMotor, Cluster, Meta, Proceso, Traza

#: Clave del bloqueo asesor para crear el esquema entre réplicas que arrancan a la vez (distinta de la del
#: estado: ``estado/postgres.py``).
_BLOQUEO_ESQUEMA = 7_262_045_002
_TABLAS = (
    "grafo_grafos",
    "grafo_simbolos",
    "grafo_aristas",
    "grafo_clusters",
    "grafo_procesos",
    "grafo_trazas",
)
_CAMPOS = ", ".join(PROPIEDADES_SIMBOLO)
#: Sufijos que cuentan como "nombre calificado" en la búsqueda exacta (``.x``, ``::x``, ``/x``, ``#x``).
_SEPARADORES = (".", "::", "/", "#")


def _utc(valor: datetime) -> datetime:
    return valor.astimezone(UTC) if valor.tzinfo else valor.replace(tzinfo=UTC)


def _meta_a_json(meta: Meta) -> str:
    return json.dumps(
        {
            "commit": meta.commit,
            "unidad": meta.unidad,
            "borrados": sorted(set(meta.borrados)),
            "aristas_borradas": sorted({tuple(a) for a in meta.aristas_borradas}),
            "base": meta.base,
            "lotes": meta.lotes,
            "recibidos": sorted(set(meta.recibidos)),
            "integrado": meta.integrado,
            # El orden importa: los más recientes primero.
            "cubiertos": list(meta.cubiertos),
            "contenido_verificado": meta.contenido_verificado,
            "divergencias_total": meta.divergencias_total,
            "rutas_divergentes": list(meta.rutas_divergentes),
            "resumen": None if meta.resumen is None else [list(r) for r in meta.resumen],
            "retenido_en": meta.retenido_en and _utc(meta.retenido_en).isoformat(),
        }
    )


def _meta_de_json(crudo: str | dict | None, actualizado: datetime | None) -> Meta:
    # Sin json: ``sellar`` llegó antes que la primera ``escribir_meta`` del grafo.
    d = (json.loads(crudo) if isinstance(crudo, str) else crudo) or {}
    d["aristas_borradas"] = [tuple(a) for a in d.get("aristas_borradas", [])]
    if d.get("resumen") is not None:
        d["resumen"] = [tuple(r) for r in d["resumen"]]
    if d.get("retenido_en"):
        d["retenido_en"] = datetime.fromisoformat(d["retenido_en"])
    d["actualizado"] = _utc(actualizado) if actualizado else None
    return Meta(**d)


def _unicos_por_id(simbolos: list[dict]) -> list[dict]:
    """Un símbolo por id (el último gana): un ``INSERT .. ON CONFLICT`` no admite el mismo id dos veces."""

    return list({s["id"]: s for s in simbolos}.values())


class MotorPostgres:
    #: Nombre de su sonda en ``/healthz`` y en ``railspec_sonda_ok``; el pool del grafo es propio.
    nombre_sonda = "grafo"

    def __init__(
        self,
        url: str | None = None,
        *,
        esquema: str = "railspec",
        tamano_pool: int = 5,
        pool: Any | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", esquema):
            raise ValueError("el esquema debe ser un identificador simple")
        if pool is None and not url:
            raise ValueError("hace falta la URL de Postgres o un pool")
        from psycopg_pool import ConnectionPool

        self._esquema = esquema
        # Nombres de tabla calificados; ``_crear_esquema`` y las consultas los interpolan.
        self._grafos, self._simbolos, self._aristas, self._clusters, self._procesos, self._trazas = (
            self._t(t) for t in _TABLAS
        )
        # ``prepare_threshold=None``: el pooler de Supabase en modo transacción no admite sentencias
        # preparadas.
        self._pool = pool or ConnectionPool(
            url,
            min_size=1,
            max_size=tamano_pool,
            kwargs={"prepare_threshold": None, "autocommit": True},
            check=ConnectionPool.check_connection,
            open=True,
        )
        self._pool.wait(timeout=30)
        self._crear_esquema()

    @classmethod
    def desde_url(cls, url: str, esquema: str = "railspec", tamano_pool: int = 5) -> MotorPostgres:
        return cls(url, esquema=esquema, tamano_pool=tamano_pool)

    def cerrar(self) -> None:
        self._pool.close()

    def ping(self) -> None:
        """Sonda ligera para ``/healthz``; lanza si Postgres no responde."""

        with self._pool.connection() as conn:
            conn.execute("SELECT 1")

    # --- conexión y esquema ------------------------------------------------
    @contextmanager
    def _tx(self) -> Iterator[Any]:
        with self._pool.connection() as conn, conn.transaction():
            yield conn

    def _t(self, tabla: str) -> str:
        return f'"{self._esquema}"."{tabla}"'

    def _crear_esquema(self) -> None:
        with self._tx() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_BLOQUEO_ESQUEMA,))
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._esquema}"')
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._grafos} (
                    grafo text PRIMARY KEY,
                    meta json,
                    actualizado timestamptz
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._simbolos} (
                    grafo text NOT NULL REFERENCES {self._grafos} ON DELETE CASCADE,
                    id text NOT NULL,
                    nombre text NOT NULL,
                    tipo text NOT NULL,
                    ruta text NOT NULL,
                    linea_inicio integer,
                    linea_fin integer,
                    sha256 text,
                    embedding real[],
                    PRIMARY KEY (grafo, id)
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._aristas} (
                    grafo text NOT NULL REFERENCES {self._grafos} ON DELETE CASCADE,
                    origen text NOT NULL,
                    destino text NOT NULL,
                    relacion text NOT NULL,
                    PRIMARY KEY (grafo, origen, destino, relacion)
                )"""
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS grafo_aristas_destino ON {self._aristas} (grafo, destino)"
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._clusters} (
                    grafo text NOT NULL REFERENCES {self._grafos} ON DELETE CASCADE,
                    id text NOT NULL,
                    nombre text NOT NULL,
                    miembros text[] NOT NULL,
                    PRIMARY KEY (grafo, id)
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._procesos} (
                    grafo text NOT NULL REFERENCES {self._grafos} ON DELETE CASCADE,
                    id text NOT NULL,
                    nombre text NOT NULL,
                    entrada text NOT NULL,
                    pasos text[] NOT NULL,
                    PRIMARY KEY (grafo, id)
                )"""
            )
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._trazas} (
                    grafo text NOT NULL REFERENCES {self._grafos} ON DELETE CASCADE,
                    unidad text NOT NULL,
                    criterio text NOT NULL,
                    simbolo text NOT NULL,
                    PRIMARY KEY (grafo, unidad, criterio, simbolo)
                )"""
            )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS grafo_trazas_simbolo ON {self._trazas} (grafo, simbolo)"
            )
            # Supabase expone por su API REST las tablas sin RLS; sin políticas el acceso anónimo queda
            # denegado y el rol de la conexión (dueño) no se ve afectado.
            for tabla in _TABLAS:
                activa = conn.execute(
                    "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (self._t(tabla),)
                ).fetchone()[0]
                if not activa:
                    conn.execute(f"ALTER TABLE {self._t(tabla)} ENABLE ROW LEVEL SECURITY")

    def _registrar(self, conn: Any, grafo: str) -> None:
        conn.execute(f"INSERT INTO {self._grafos} (grafo) VALUES (%s) ON CONFLICT DO NOTHING", (grafo,))

    # --- ciclo de vida -----------------------------------------------------
    def existe(self, grafo: str) -> bool:
        with self._pool.connection() as conn:
            return (
                conn.execute(f"SELECT 1 FROM {self._grafos} WHERE grafo = %s", (grafo,)).fetchone()
                is not None
            )

    def borrar(self, grafo: str) -> None:
        with self._tx() as conn:
            conn.execute(f"DELETE FROM {self._grafos} WHERE grafo = %s", (grafo,))

    def listar(self, prefijo: str) -> list[str]:
        with self._pool.connection() as conn:
            filas = conn.execute(
                f'SELECT grafo FROM {self._grafos} WHERE starts_with(grafo, %s) ORDER BY grafo COLLATE "C"',
                (prefijo,),
            ).fetchall()
        return [f[0] for f in filas]

    def leer_meta(self, grafo: str) -> Meta:
        with self._pool.connection() as conn:
            fila = conn.execute(
                f"SELECT meta, actualizado FROM {self._grafos} WHERE grafo = %s", (grafo,)
            ).fetchone()
        return _meta_de_json(*fila) if fila else Meta()

    def escribir_meta(self, grafo: str, meta: Meta) -> None:
        with self._tx() as conn:
            conn.execute(
                f"""INSERT INTO {self._grafos} (grafo, meta, actualizado) VALUES (%s, %s, %s)
                    ON CONFLICT (grafo) DO UPDATE
                    SET meta = EXCLUDED.meta, actualizado = EXCLUDED.actualizado""",
                (grafo, _meta_a_json(meta), meta.actualizado and _utc(meta.actualizado)),
            )

    def sellar(self, grafo: str, instante: datetime) -> None:
        # Un solo ``UPDATE``: atómico frente a un ``escribir_meta`` concurrente, que ya trae su propio sello.
        with self._tx() as conn:
            conn.execute(
                f"UPDATE {self._grafos} SET actualizado = coalesce(actualizado, %s) WHERE grafo = %s",
                (_utc(instante), grafo),
            )

    # --- símbolos y aristas ------------------------------------------------
    def upsert_simbolos(self, grafo: str, simbolos: list[dict]) -> None:
        if not simbolos:
            return
        unicos = _unicos_por_id([{p: s[p] for p in PROPIEDADES_SIMBOLO} for s in simbolos])
        with self._tx() as conn:
            self._registrar(conn, grafo)
            conn.execute(
                f"""INSERT INTO {self._simbolos} (grafo, {_CAMPOS})
                    SELECT %s, * FROM unnest(
                        %s::text[], %s::text[], %s::text[], %s::text[], %s::int[], %s::int[], %s::text[]
                    )
                    ON CONFLICT (grafo, id) DO UPDATE SET nombre = EXCLUDED.nombre, tipo = EXCLUDED.tipo,
                        ruta = EXCLUDED.ruta, linea_inicio = EXCLUDED.linea_inicio,
                        linea_fin = EXCLUDED.linea_fin, sha256 = EXCLUDED.sha256""",
                (grafo, *([s[p] for s in unicos] for p in PROPIEDADES_SIMBOLO)),
            )

    def borrar_simbolos(self, grafo: str, ids: list[str]) -> None:
        if not ids:
            return
        with self._tx() as conn:
            conn.execute(f"DELETE FROM {self._simbolos} WHERE grafo = %s AND id = ANY(%s)", (grafo, ids))
            conn.execute(
                f"DELETE FROM {self._aristas} WHERE grafo = %s AND (origen = ANY(%s) OR destino = ANY(%s))",
                (grafo, ids, ids),
            )

    def agregar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        if not aristas:
            return
        with self._tx() as conn:
            self._registrar(conn, grafo)
            conn.execute(
                f"""INSERT INTO {self._aristas} (grafo, origen, destino, relacion)
                    SELECT %s, * FROM unnest(%s::text[], %s::text[], %s::text[]) ON CONFLICT DO NOTHING""",
                (
                    grafo,
                    [a.origen for a in aristas],
                    [a.destino for a in aristas],
                    [a.relacion for a in aristas],
                ),
            )

    def borrar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        if not aristas:
            return
        with self._tx() as conn:
            conn.execute(
                f"""DELETE FROM {self._aristas} a USING
                    unnest(%s::text[], %s::text[], %s::text[]) AS b(origen, destino, relacion)
                    WHERE a.grafo = %s AND a.origen = b.origen AND a.destino = b.destino
                    AND a.relacion = b.relacion""",
                (
                    [a.origen for a in aristas],
                    [a.destino for a in aristas],
                    [a.relacion for a in aristas],
                    grafo,
                ),
            )

    def _filas_simbolo(self, filas: list[tuple]) -> list[dict]:
        return [dict(zip(PROPIEDADES_SIMBOLO, f, strict=True)) for f in filas]

    def simbolos(self, grafo: str, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT {_CAMPOS} FROM {self._simbolos} WHERE grafo = %s AND id = ANY(%s)",
                (grafo, ids),
            ).fetchall()
        return {s["id"]: s for s in self._filas_simbolo(filas)}

    def buscar_nombre(
        self, grafo: str, texto: str, tipos: list[str], exacto: bool, limite: int
    ) -> list[dict]:
        if exacto:
            sufijos = [sep + texto for sep in _SEPARADORES]
            # ``right(nombre, n) = sufijo`` y no ``LIKE``: el texto buscado puede traer ``%`` o ``_``.
            cond = "(nombre = %s OR " + " OR ".join("right(nombre, %s) = %s" for _ in sufijos) + ")"
            args: list[Any] = [texto]
            for s in sufijos:
                args += [len(s), s]
        else:
            cond = "position(%s in lower(nombre)) > 0"
            args = [texto.lower()]
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT {_CAMPOS} FROM {self._simbolos} WHERE grafo = %s "
                f"AND (cardinality(%s::text[]) = 0 OR tipo = ANY(%s)) AND {cond} "
                'ORDER BY nombre COLLATE "C", id COLLATE "C" LIMIT %s',
                (grafo, tipos, tipos, *args, limite),
            ).fetchall()
        return self._filas_simbolo(filas)

    def aristas(self, grafo: str, ids: list[str], relaciones: list[str], direccion: str) -> list[AristaMotor]:
        if not ids:
            return []
        extremo = "origen" if direccion == "salida" else "destino"
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT origen, destino, relacion FROM {self._aristas} "
                f"WHERE grafo = %s AND {extremo} = ANY(%s) "
                "AND (cardinality(%s::text[]) = 0 OR relacion = ANY(%s)) "
                'ORDER BY origen COLLATE "C", destino COLLATE "C", relacion COLLATE "C"',
                (grafo, ids, relaciones, relaciones),
            ).fetchall()
        return [AristaMotor(*f) for f in filas]

    def todos_simbolos(self, grafo: str) -> list[dict]:
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT {_CAMPOS} FROM {self._simbolos} WHERE grafo = %s", (grafo,)
            ).fetchall()
        return self._filas_simbolo(filas)

    def todas_aristas(self, grafo: str) -> list[AristaMotor]:
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT origen, destino, relacion FROM {self._aristas} WHERE grafo = %s "
                'ORDER BY origen COLLATE "C", destino COLLATE "C", relacion COLLATE "C"',
                (grafo,),
            ).fetchall()
        return [AristaMotor(*f) for f in filas]

    # --- vectores ----------------------------------------------------------
    def fijar_embeddings(self, grafo: str, vectores: dict[str, list[float]]) -> None:
        if not vectores:
            return
        with self._tx() as conn:
            self._registrar(conn, grafo)
            with conn.cursor() as cur:
                # Un ``UPDATE`` por símbolo: ``unnest`` de arreglos de arreglos exigiría igualar longitudes.
                cur.executemany(
                    f"UPDATE {self._simbolos} SET embedding = %s::real[] WHERE grafo = %s AND id = %s",
                    [(list(v), grafo, i) for i, v in vectores.items()],
                )

    def leer_embeddings(self, grafo: str, ids: list[str]) -> dict[str, list[float]]:
        if not ids:
            return {}
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT id, embedding FROM {self._simbolos} "
                "WHERE grafo = %s AND id = ANY(%s) AND embedding IS NOT NULL",
                (grafo, ids),
            ).fetchall()
        return {i: [float(x) for x in e] for i, e in filas}

    def knn(self, grafo: str, vector: list[float], k: int) -> list[tuple[str, float]]:
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT id, embedding FROM {self._simbolos} WHERE grafo = %s AND embedding IS NOT NULL",
                (grafo,),
            ).fetchall()
        norma = math.sqrt(sum(x * x for x in vector)) or 1.0
        puntos = []
        for i, v in filas:
            nv = math.sqrt(sum(x * x for x in v)) or 1.0
            puntos.append((i, sum(a * b for a, b in zip(vector, v, strict=True)) / (norma * nv)))
        return sorted(puntos, key=lambda p: (-p[1], p[0]))[:k]

    # --- analítica ---------------------------------------------------------
    def reemplazar_analitica(self, grafo: str, clusters: list[Cluster], procesos: list[Proceso]) -> None:
        with self._tx() as conn:
            self._registrar(conn, grafo)
            conn.execute(f"DELETE FROM {self._clusters} WHERE grafo = %s", (grafo,))
            conn.execute(f"DELETE FROM {self._procesos} WHERE grafo = %s", (grafo,))
            with conn.cursor() as cur:
                if clusters:
                    cur.executemany(
                        f"INSERT INTO {self._clusters} (grafo, id, nombre, miembros) VALUES (%s, %s, %s, %s)",
                        [(grafo, c.id, c.nombre, list(c.miembros)) for c in clusters],
                    )
                if procesos:
                    cur.executemany(
                        f"INSERT INTO {self._procesos} (grafo, id, nombre, entrada, pasos) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        [(grafo, p.id, p.nombre, p.entrada, list(p.pasos)) for p in procesos],
                    )

    def analitica_de(self, grafo: str, simbolo: str) -> tuple[list[Cluster], list[Proceso]]:
        with self._pool.connection() as conn:
            cs = conn.execute(
                f"SELECT id, nombre, miembros FROM {self._clusters} WHERE grafo = %s AND %s = ANY(miembros) "
                'ORDER BY id COLLATE "C"',
                (grafo, simbolo),
            ).fetchall()
            ps = conn.execute(
                f"SELECT id, nombre, entrada, pasos FROM {self._procesos} "
                'WHERE grafo = %s AND %s = ANY(pasos) ORDER BY id COLLATE "C"',
                (grafo, simbolo),
            ).fetchall()
        return [Cluster(i, n, tuple(m)) for i, n, m in cs], [Proceso(i, n, e, tuple(s)) for i, n, e, s in ps]

    def procesos(self, grafo: str) -> list[Proceso]:
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT id, nombre, entrada, pasos FROM {self._procesos} WHERE grafo = %s "
                'ORDER BY id COLLATE "C"',
                (grafo,),
            ).fetchall()
        return [Proceso(i, n, e, tuple(s)) for i, n, e, s in filas]

    # --- trazabilidad CA-NN ------------------------------------------------
    def agregar_trazas(self, grafo: str, trazas: list[Traza]) -> None:
        if not trazas:
            return
        with self._tx() as conn:
            self._registrar(conn, grafo)
            conn.execute(
                f"""INSERT INTO {self._trazas} (grafo, unidad, criterio, simbolo)
                    SELECT %s, * FROM unnest(%s::text[], %s::text[], %s::text[]) ON CONFLICT DO NOTHING""",
                (
                    grafo,
                    [t.unidad for t in trazas],
                    [t.criterio for t in trazas],
                    [t.simbolo for t in trazas],
                ),
            )

    def trazas(
        self, grafo: str, unidad: str | None, criterio: str | None, simbolo: str | None
    ) -> list[Traza]:
        with self._pool.connection() as conn:
            filas = conn.execute(
                f"SELECT unidad, criterio, simbolo FROM {self._trazas} WHERE grafo = %s "
                "AND (%s::text IS NULL OR unidad = %s) AND (%s::text IS NULL OR criterio = %s) "
                "AND (%s::text IS NULL OR simbolo = %s) "
                'ORDER BY unidad COLLATE "C", criterio COLLATE "C", simbolo COLLATE "C"',
                (grafo, unidad, unidad, criterio, criterio, simbolo, simbolo),
            ).fetchall()
        return [Traza(*f) for f in filas]
