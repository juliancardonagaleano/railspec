"""Base común de todos los modelos de contrato.

Los modelos Pydantic son la fuente de verdad; los JSON Schema de
``railspec/schemas/v1`` se generan desde aquí y una prueba verifica que no
deriven. Todo modelo es inmutable y rechaza campos desconocidos: un mensaje
que no encaja en el contrato se rechaza en el borde, nunca llega al motor.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: Versión del contrato. Mayor cambia solo con rupturas; menor añade campos
#: opcionales. Ver ``railspec/docs/contratos.md`` § Versionado.
VERSION_CONTRATO = "1.6"
VERSION_MAYOR = 1

VersionContrato = Literal["1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6"]


class Contrato(BaseModel):
    """Modelo base: inmutable, estricto con campos extra y serializable a JSON."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        # Las rutas relativas usan lookahead, que el motor de Rust no admite.
        regex_engine="python-re",
        use_enum_values=False,
        ser_json_timedelta="float",
    )


class Mensaje(Contrato):
    """Mensaje de primer nivel que cruza la frontera servidor/proxy/arnés.

    Lleva la versión del contrato para que el receptor rechace mayores que no
    conoce en vez de interpretar a medias.
    """

    version_contrato: VersionContrato = VERSION_CONTRATO
