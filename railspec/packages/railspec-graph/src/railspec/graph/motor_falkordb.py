"""Motor sobre FalkorDB: un grafo físico por nombre, vectores en el propio nodo.

Esquema por grafo:

- ``(:Meta {json, actualizado, ext})``: commit, unidad y lápidas de la superposición en el json;
  la última actividad (ISO 8601, UTC) y lo que se añadió en 1.10 (commits cubiertos, resultado de la
  reconciliación, resumen de CI, instante de retención; ver ``Meta``) aparte, cada cual en su
  propiedad, para que una réplica anterior siga leyendo el json.
- ``(:Simbolo {id, nombre, tipo, ruta, linea_inicio, linea_fin, sha256, stub, embedding})``;
  un stub solo lleva ``id`` y ``stub = true``.
- ``(:Simbolo)-[:REL {tipo}]->(:Simbolo)``.
- ``(:Cluster {id, nombre, miembros})`` y ``(:Proceso {id, nombre, entrada, pasos})``.
- ``(:Traza {unidad, criterio, simbolo})``: solo en el grafo de trazas del repositorio.

FalkorDB es SSPL: sirve para uso interno; venderlo como servicio exige su
licencia comercial o cambiar a otro ``MotorGrafo``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .motor import PROPIEDADES_SIMBOLO, AristaMotor, Cluster, Meta, Proceso, Traza

_CAMPOS = ", ".join(f"s.{p} AS {p}" for p in PROPIEDADES_SIMBOLO)


class MotorFalkor:
    def __init__(self, db: Any, dimensiones: int = 768) -> None:
        self._db = db
        self._dimensiones = dimensiones
        self._preparados: set[str] = set()

    @classmethod
    def desde_url(cls, url: str, dimensiones: int = 768) -> MotorFalkor:
        from falkordb import FalkorDB

        return cls(FalkorDB.from_url(url), dimensiones)

    def ping(self) -> None:
        """Sonda ligera para la disponibilidad del servidor; lanza si FalkorDB no responde."""

        if not self._db.connection.ping():
            raise ConnectionError("FalkorDB no respondió al ping")

    # --- utilidades --------------------------------------------------------
    def _escribir(self, grafo: str, cypher: str, params: dict | None = None) -> list[list]:
        g = self._db.select_graph(grafo)
        if grafo not in self._preparados:
            self._preparar(g)
            self._preparados.add(grafo)
        return g.query(cypher, params or {}).result_set

    def _leer(self, grafo: str, cypher: str, params: dict | None = None) -> list[list]:
        if not self.existe(grafo):
            return []
        return self._db.select_graph(grafo).ro_query(cypher, params or {}).result_set

    def _preparar(self, g: Any) -> None:
        for cypher in (
            "CREATE INDEX FOR (s:Simbolo) ON (s.id)",
            "CREATE INDEX FOR (s:Simbolo) ON (s.nombre)",
            "CREATE VECTOR INDEX FOR (s:Simbolo) ON (s.embedding) "
            f"OPTIONS {{dimension: {self._dimensiones}, similarityFunction: 'cosine'}}",
        ):
            try:
                g.query(cypher)
            except Exception as exc:  # índice ya existente
                if "already" not in str(exc).lower():
                    raise

    @staticmethod
    def _filas_simbolo(filas: list[list]) -> list[dict]:
        return [dict(zip(PROPIEDADES_SIMBOLO, f, strict=True)) for f in filas]

    # --- ciclo de vida -----------------------------------------------------
    def existe(self, grafo: str) -> bool:
        return bool(self._db.connection.exists(grafo))

    def borrar(self, grafo: str) -> None:
        if self.existe(grafo):
            self._db.select_graph(grafo).delete()
        self._preparados.discard(grafo)

    def listar(self, prefijo: str) -> list[str]:
        return sorted(n for n in self._db.list_graphs() if n.startswith(prefijo))

    def leer_meta(self, grafo: str) -> Meta:
        filas = self._leer(grafo, "MATCH (m:Meta) RETURN m.json, m.actualizado, m.ext")
        if not filas:
            return Meta()
        crudo, actualizado, extra = filas[0]
        # Sin json: ``sellar`` llegó antes que la primera ``escribir_meta`` del grafo.
        d = json.loads(crudo) if crudo else {}
        d["aristas_borradas"] = [tuple(a) for a in d.get("aristas_borradas", [])]
        if actualizado:
            d["actualizado"] = datetime.fromisoformat(actualizado)
        if extra:
            e = json.loads(extra)
            # Una réplica anterior que reescribe el json (p. ej. al aplicar un índice) no toca ``ext``:
            # si el commit ya no es el suyo, lo que dice describe otro índice y no vale.
            if e.pop("commit", None) == d.get("commit"):
                if "resumen" in e:
                    e["resumen"] = [tuple(r) for r in e["resumen"]]
                if "retenido_en" in e:
                    e["retenido_en"] = datetime.fromisoformat(e["retenido_en"])
                d.update(e)
        return Meta(**d)

    def sellar(self, grafo: str, instante: datetime) -> None:
        # ``existe`` antes: una consulta sobre una clave que no está crearía un grafo vacío.
        if self.existe(grafo):
            self._escribir(
                grafo,
                "MERGE (m:Meta) SET m.actualizado = coalesce(m.actualizado, $a)",
                {"a": instante.isoformat()},
            )

    def escribir_meta(self, grafo: str, meta: Meta) -> None:
        datos = {
            "commit": meta.commit,
            "unidad": meta.unidad,
            "borrados": sorted(set(meta.borrados)),
            "aristas_borradas": sorted({tuple(a) for a in meta.aristas_borradas}),
            "base": meta.base,
            "lotes": meta.lotes,
            "recibidos": sorted(set(meta.recibidos)),
        }
        if meta.integrado is not None:
            # Solo las superposiciones retenidas lo llevan: durante un despliegue gradual una
            # réplica anterior sigue leyendo el resto de las metas, que no cambian de forma.
            datos["integrado"] = meta.integrado
        # ``actualizado`` es propiedad del nodo y no va en el json: toda superposición y todo grafo de
        # preparación lo llevan, y una réplica anterior no admite claves que no conoce al leerlo.
        actualizado = meta.actualizado.isoformat() if meta.actualizado else None
        self._escribir(
            grafo,
            "MERGE (m:Meta) SET m.json = $j, m.actualizado = $a, m.ext = $e",
            {"j": json.dumps(datos), "a": actualizado, "e": self._extra(meta)},
        )

    @staticmethod
    def _extra(meta: Meta) -> str | None:
        """Lo que la meta añadió en 1.10, como json para ``m.ext``; ``None`` si nada se aparta del defecto.

        Lleva el commit de la meta a la que pertenece: ``leer_meta`` lo ignora si ya no coincide."""

        e: dict[str, Any] = {}
        if meta.cubiertos:
            e["cubiertos"] = list(meta.cubiertos)
        if meta.contenido_verificado is not None:
            e["contenido_verificado"] = meta.contenido_verificado
        if meta.divergencias_total:
            e["divergencias_total"] = meta.divergencias_total
        if meta.rutas_divergentes:
            e["rutas_divergentes"] = list(meta.rutas_divergentes)
        if meta.resumen is not None:
            e["resumen"] = [list(r) for r in meta.resumen]
        if meta.retenido_en is not None:
            e["retenido_en"] = meta.retenido_en.isoformat()
        if not e:
            return None
        e["commit"] = meta.commit
        return json.dumps(e)

    # --- símbolos y aristas ------------------------------------------------
    def upsert_simbolos(self, grafo: str, simbolos: list[dict]) -> None:
        if simbolos:
            self._escribir(
                grafo,
                "UNWIND $s AS i MERGE (s:Simbolo {id: i.id}) SET s += i, s.stub = false",
                {"s": [{p: s[p] for p in PROPIEDADES_SIMBOLO} for s in simbolos]},
            )

    def borrar_simbolos(self, grafo: str, ids: list[str]) -> None:
        if ids and self.existe(grafo):
            self._escribir(grafo, "UNWIND $ids AS i MATCH (s:Simbolo {id: i}) DETACH DELETE s", {"ids": ids})
            self._limpiar_stubs(grafo)

    def _limpiar_stubs(self, grafo: str) -> None:
        self._escribir(grafo, "MATCH (s:Simbolo {stub: true}) WHERE NOT (s)--() DELETE s")

    def agregar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        if aristas:
            self._escribir(
                grafo,
                "UNWIND $a AS a "
                "MERGE (o:Simbolo {id: a.o}) ON CREATE SET o.stub = true "
                "MERGE (d:Simbolo {id: a.d}) ON CREATE SET d.stub = true "
                "MERGE (o)-[:REL {tipo: a.r}]->(d)",
                {"a": [{"o": a.origen, "d": a.destino, "r": a.relacion} for a in aristas]},
            )

    def borrar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        if aristas and self.existe(grafo):
            self._escribir(
                grafo,
                "UNWIND $a AS a "
                "MATCH (:Simbolo {id: a.o})-[r:REL {tipo: a.r}]->(:Simbolo {id: a.d}) DELETE r",
                {"a": [{"o": a.origen, "d": a.destino, "r": a.relacion} for a in aristas]},
            )
            self._limpiar_stubs(grafo)

    def simbolos(self, grafo: str, ids: list[str]) -> dict[str, dict]:
        if not ids:
            return {}
        filas = self._leer(
            grafo,
            f"UNWIND $ids AS i MATCH (s:Simbolo {{id: i}}) WHERE s.stub = false RETURN {_CAMPOS}",
            {"ids": ids},
        )
        return {s["id"]: s for s in self._filas_simbolo(filas)}

    def buscar_nombre(
        self, grafo: str, texto: str, tipos: list[str], exacto: bool, limite: int
    ) -> list[dict]:
        if exacto:
            cond = (
                "(s.nombre = $t OR s.nombre ENDS WITH $p OR s.nombre ENDS WITH $c "
                "OR s.nombre ENDS WITH $b OR s.nombre ENDS WITH $h)"
            )
        else:
            cond = "toLower(s.nombre) CONTAINS $l"
        filas = self._leer(
            grafo,
            f"MATCH (s:Simbolo) WHERE s.stub = false AND (size($tipos) = 0 OR s.tipo IN $tipos) AND {cond} "
            f"RETURN {_CAMPOS} ORDER BY nombre, id LIMIT $lim",
            {
                "t": texto,
                "p": "." + texto,
                "c": "::" + texto,
                "b": "/" + texto,
                "h": "#" + texto,
                "l": texto.lower(),
                "tipos": tipos,
                "lim": limite,
            },
        )
        return self._filas_simbolo(filas)

    def aristas(self, grafo: str, ids: list[str], relaciones: list[str], direccion: str) -> list[AristaMotor]:
        if not ids:
            return []
        extremo = "o" if direccion == "salida" else "d"
        filas = self._leer(
            grafo,
            f"UNWIND $ids AS i MATCH (o:Simbolo)-[r:REL]->(d:Simbolo) WHERE {extremo}.id = i "
            "AND (size($rel) = 0 OR r.tipo IN $rel) RETURN o.id, d.id, r.tipo",
            {"ids": ids, "rel": relaciones},
        )
        return sorted({AristaMotor(*f) for f in filas}, key=lambda a: (a.origen, a.destino, a.relacion))

    def todos_simbolos(self, grafo: str) -> list[dict]:
        return self._filas_simbolo(
            self._leer(grafo, f"MATCH (s:Simbolo) WHERE s.stub = false RETURN {_CAMPOS}")
        )

    def todas_aristas(self, grafo: str) -> list[AristaMotor]:
        filas = self._leer(grafo, "MATCH (o:Simbolo)-[r:REL]->(d:Simbolo) RETURN o.id, d.id, r.tipo")
        return sorted({AristaMotor(*f) for f in filas}, key=lambda a: (a.origen, a.destino, a.relacion))

    # --- vectores ----------------------------------------------------------
    def fijar_embeddings(self, grafo: str, vectores: dict[str, list[float]]) -> None:
        if vectores:
            self._escribir(
                grafo,
                "UNWIND $v AS v MATCH (s:Simbolo {id: v.id}) WHERE s.stub = false "
                "SET s.embedding = vecf32(v.e)",
                {"v": [{"id": i, "e": e} for i, e in vectores.items()]},
            )

    def leer_embeddings(self, grafo: str, ids: list[str]) -> dict[str, list[float]]:
        if not ids:
            return {}
        filas = self._leer(
            grafo,
            "UNWIND $ids AS i MATCH (s:Simbolo {id: i}) WHERE s.embedding IS NOT NULL "
            "RETURN s.id, s.embedding",
            {"ids": ids},
        )
        return {i: [float(x) for x in e] for i, e in filas}

    def knn(self, grafo: str, vector: list[float], k: int) -> list[tuple[str, float]]:
        if not self.existe(grafo):
            return []
        filas = (
            self._db.select_graph(grafo)
            .ro_query(
                "CALL db.idx.vector.queryNodes('Simbolo', 'embedding', $k, vecf32($v)) YIELD node, score "
                "RETURN node.id, score",
                {"k": k, "v": vector},
            )
            .result_set
        )
        # FalkorDB devuelve distancia coseno (0 = idéntico); se expone similitud.
        return sorted(((i, 1.0 - float(d)) for i, d in filas), key=lambda p: (-p[1], p[0]))

    # --- analítica ---------------------------------------------------------
    def reemplazar_analitica(self, grafo: str, clusters: list[Cluster], procesos: list[Proceso]) -> None:
        self._escribir(grafo, "MATCH (n) WHERE n:Cluster OR n:Proceso DELETE n")
        if clusters:
            self._escribir(
                grafo,
                "UNWIND $c AS c CREATE (:Cluster {id: c.id, nombre: c.nombre, miembros: c.m})",
                {"c": [{"id": c.id, "nombre": c.nombre, "m": list(c.miembros)} for c in clusters]},
            )
        if procesos:
            self._escribir(
                grafo,
                "UNWIND $p AS p CREATE (:Proceso {id: p.id, nombre: p.nombre, entrada: p.e, pasos: p.s})",
                {
                    "p": [
                        {"id": p.id, "nombre": p.nombre, "e": p.entrada, "s": list(p.pasos)} for p in procesos
                    ]
                },
            )

    def analitica_de(self, grafo: str, simbolo: str) -> tuple[list[Cluster], list[Proceso]]:
        cs = self._leer(
            grafo,
            "MATCH (c:Cluster) WHERE $s IN c.miembros RETURN c.id, c.nombre, c.miembros",
            {"s": simbolo},
        )
        ps = self._leer(
            grafo,
            "MATCH (p:Proceso) WHERE $s IN p.pasos RETURN p.id, p.nombre, p.entrada, p.pasos",
            {"s": simbolo},
        )
        return (
            sorted((Cluster(i, n, tuple(m)) for i, n, m in cs), key=lambda c: c.id),
            sorted((Proceso(i, n, e, tuple(s)) for i, n, e, s in ps), key=lambda p: p.id),
        )

    def procesos(self, grafo: str) -> list[Proceso]:
        filas = self._leer(grafo, "MATCH (p:Proceso) RETURN p.id, p.nombre, p.entrada, p.pasos")
        return sorted((Proceso(i, n, e, tuple(s)) for i, n, e, s in filas), key=lambda p: p.id)

    # --- trazabilidad CA-NN ------------------------------------------------
    def agregar_trazas(self, grafo: str, trazas: list[Traza]) -> None:
        if trazas:
            self._escribir(
                grafo,
                "UNWIND $t AS t MERGE (:Traza {unidad: t.u, criterio: t.c, simbolo: t.s})",
                {"t": [{"u": t.unidad, "c": t.criterio, "s": t.simbolo} for t in trazas]},
            )

    def trazas(
        self, grafo: str, unidad: str | None, criterio: str | None, simbolo: str | None
    ) -> list[Traza]:
        filas = self._leer(
            grafo,
            "MATCH (t:Traza) WHERE ($u IS NULL OR t.unidad = $u) AND ($c IS NULL OR t.criterio = $c) "
            "AND ($s IS NULL OR t.simbolo = $s) RETURN t.unidad, t.criterio, t.simbolo",
            {"u": unidad, "c": criterio, "s": simbolo},
        )
        return sorted((Traza(u, c, s) for u, c, s in filas), key=lambda t: (t.unidad, t.criterio, t.simbolo))
