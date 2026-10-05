"""Caché de nodos de modelo por hash de sus entradas (idempotencia del DAG).

Clave: proveedor, modelo, despliegue, suscripción, effort, tope de tokens, esquema de
salida, sistema, contenido y los commits del código evaluado. La misma llamada con las mismas entradas no se
paga dos veces: un turno del DAG que se repite tras una caída, otra réplica
que reanuda desde el checkpoint o un gate que vuelve a evaluar el mismo
material con los mismos hallazgos previos reciben la respuesta guardada.

Se guarda por organización en la colección ``cache_nodos`` con caducidad
(``RAILSPEC_CACHE_NODOS_S``; 0 la desactiva). Solo guarda la salida
estructurada validada y el uso de la llamada original, nunca lo enviado.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from railspec.contracts.comun import Proveedor

from .base import PeticionModelo, RespuestaModelo, Uso


def clave_nodo(proveedor: Proveedor, peticion: PeticionModelo) -> str:
    esquema = json.dumps(peticion.esquema.model_json_schema(), sort_keys=True, ensure_ascii=False)
    partes = [
        proveedor.value,
        peticion.modelo,
        peticion.destino,
        peticion.effort.value if peticion.effort else "",
        str(peticion.max_tokens),
        esquema,
        peticion.sistema,
        peticion.contenido,
        ",".join(peticion.commits),
        peticion.suscripcion or "",
    ]
    return hashlib.sha256("\0".join(partes).encode("utf-8")).hexdigest()


class CacheNodos:
    def __init__(
        self,
        almacen: Any,
        ttl_s: float = 86_400.0,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._almacen = almacen
        self._ttl = ttl_s
        self._reloj = reloj

    @property
    def activa(self) -> bool:
        return self._ttl > 0

    def obtener(self, org: str, proveedor: Proveedor, peticion: PeticionModelo) -> RespuestaModelo | None:
        if not self.activa:
            return None
        doc = self._almacen.nodo_en_cache(org, clave_nodo(proveedor, peticion), self._reloj())
        if doc is None:
            return None
        try:
            valor = peticion.esquema.model_validate(doc["valor"])
        except Exception:  # esquema cambiado: se trata como fallo de caché
            return None
        return RespuestaModelo(
            valor=valor,
            uso=Uso(**doc.get("uso", {})),
            proveedor=Proveedor(doc["proveedor"]),
            modelo=doc["modelo"],
            region=doc.get("region"),
        )

    def guardar(self, org: str, peticion: PeticionModelo, r: RespuestaModelo) -> None:
        if not self.activa:
            return
        ahora = self._reloj()
        self._almacen.guardar_nodo_en_cache(
            org,
            clave_nodo(r.proveedor, peticion),
            {
                "valor": r.valor.model_dump(mode="json"),
                "uso": r.uso.__dict__,
                "proveedor": r.proveedor.value,
                "modelo": r.modelo,
                "region": r.region,
            },
            ahora + timedelta(seconds=self._ttl),
        )
