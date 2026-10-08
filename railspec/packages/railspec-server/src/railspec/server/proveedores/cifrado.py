"""Cifrado en reposo de las claves de las suscripciones de modelos (AES-256-GCM, clave maestra del entorno).

La clave maestra llega en ``RAILSPEC_CLAVE_MAESTRA``: 32 bytes en base64 (``openssl rand -base64 32``).
Para rotarla se pone la nueva en ``RAILSPEC_CLAVE_MAESTRA`` y la anterior en
``RAILSPEC_CLAVE_MAESTRA_ANTERIOR`` (varias, separadas por coma): lo nuevo se cifra con la primera y lo
guardado con una anterior se sigue descifrando hasta que se reescribe la clave de la suscripción.

Cada clave guardada es ``v1.<id de clave maestra>.<nonce y texto cifrado en base64>``; el id es un
prefijo del SHA-256 de la clave maestra (no la revela) y sirve para decir *qué* clave falta. El nombre de
la organización y el de la suscripción van como datos asociados (AAD): un valor cifrado copiado a otra
suscripción no descifra.

Nada de lo que sale de aquí lleva el valor de la clave: los errores nombran la causa, no el contenido.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VARIABLE = "RAILSPEC_CLAVE_MAESTRA"
VARIABLE_ANTERIOR = "RAILSPEC_CLAVE_MAESTRA_ANTERIOR"
_VERSION = "v1"
_NONCE = 12
_FORMA = re.compile(r"^v1\.([0-9a-f]{8})\.([A-Za-z0-9_-]+={0,2})$")


class ErrorCifrado(ValueError):
    """No se pudo cifrar o descifrar; ``codigo`` es estable y el texto se puede mostrar tal cual."""

    def __init__(self, codigo: str, detalle: str) -> None:
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle


class ClaveMaestraAusente(ErrorCifrado):
    def __init__(self) -> None:
        super().__init__(
            "clave-maestra-ausente",
            f"el servidor no tiene {VARIABLE}: sin ella no puede guardar ni usar claves de suscripciones. "
            "Genera una con `openssl rand -base64 32` y defínela como secreto del servidor "
            "(railspec/docs/proveedores.md, § Suscripciones).",
        )


def _decodificar(valor: str, nombre: str) -> bytes:
    try:
        clave = base64.b64decode(valor.strip(), validate=True)
    except (binascii.Error, ValueError):
        try:
            clave = base64.urlsafe_b64decode(valor.strip() + "=" * (-len(valor.strip()) % 4))
        except (binascii.Error, ValueError):
            raise ErrorCifrado(
                "clave-maestra-invalida",
                f"{nombre} no es base64 (se esperan 32 bytes: openssl rand -base64 32)",
            ) from None
    if len(clave) != 32:
        raise ErrorCifrado(
            "clave-maestra-invalida",
            f"{nombre} debe ser de 32 bytes y trae {len(clave)} (openssl rand -base64 32)",
        )
    return clave


def _id(clave: bytes) -> str:
    return hashlib.sha256(b"railspec-clave-maestra\0" + clave).hexdigest()[:8]


class Cifrador:
    """Cifra con la primera clave maestra y descifra con cualquiera de las conocidas."""

    def __init__(self, claves: list[bytes]) -> None:
        if not claves:
            raise ClaveMaestraAusente()
        self._claves = {_id(c): AESGCM(c) for c in claves}
        self._actual = _id(claves[0])

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> Cifrador | None:
        """``None`` si no hay clave maestra; ``ErrorCifrado`` si la hay pero está mal formada."""

        env = os.environ if entorno is None else entorno
        actual = (env.get(VARIABLE) or "").strip()
        if not actual:
            if (env.get(VARIABLE_ANTERIOR) or "").strip():
                raise ErrorCifrado(
                    "clave-maestra-invalida", f"{VARIABLE_ANTERIOR} sin {VARIABLE}: falta la clave actual"
                )
            return None
        claves = [_decodificar(actual, VARIABLE)]
        for crudo in re.split(r"[,\s]+", env.get(VARIABLE_ANTERIOR) or ""):
            if crudo:
                claves.append(_decodificar(crudo, VARIABLE_ANTERIOR))
        if len({_id(c) for c in claves}) != len(claves):
            raise ErrorCifrado("clave-maestra-invalida", f"{VARIABLE_ANTERIOR} repite una clave")
        return cls(claves)

    @staticmethod
    def _aad(org: str, suscripcion: str, dominio: str = "suscripcion") -> bytes:
        return f"railspec/{dominio}/{org}/{suscripcion}".encode()

    def cifrar(self, secreto: str, org: str, suscripcion: str, dominio: str = "suscripcion") -> str:
        """``dominio`` separa los usos de la clave: un valor cifrado para otro uso no descifra aquí."""

        nonce = os.urandom(_NONCE)
        cifrado = self._claves[self._actual].encrypt(
            nonce, secreto.encode(), self._aad(org, suscripcion, dominio)
        )
        return f"{_VERSION}.{self._actual}.{base64.urlsafe_b64encode(nonce + cifrado).decode()}"

    def descifrar(self, valor: str, org: str, suscripcion: str, dominio: str = "suscripcion") -> str:
        m = _FORMA.match(valor or "")
        if m is None:
            raise ErrorCifrado("cifrado-ilegible", "la clave guardada no tiene el formato cifrado esperado")
        clave = self._claves.get(m.group(1))
        if clave is None:
            raise ErrorCifrado(
                "clave-maestra-desconocida",
                "la clave de esta suscripción se cifró con otra clave maestra que el servidor ya no tiene "
                f"(id {m.group(1)}): vuelve a poner la clave anterior en {VARIABLE_ANTERIOR} o "
                "reescribe la clave de la suscripción.",
            )
        try:
            crudo = base64.urlsafe_b64decode(m.group(2))
            return clave.decrypt(
                crudo[:_NONCE], crudo[_NONCE:], self._aad(org, suscripcion, dominio)
            ).decode()
        except (InvalidTag, binascii.Error, ValueError):
            raise ErrorCifrado(
                "cifrado-ilegible",
                "la clave guardada no se pudo descifrar (valor alterado o copiado de otra suscripción): "
                "reescribe la clave de la suscripción.",
            ) from None
