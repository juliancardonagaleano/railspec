"""Suite contra Postgres: ``RAILSPEC_PRUEBAS_POSTGRES=<url>`` cambia ``almacen_en_memoria`` por Postgres.

Cada llamada crea su propio esquema (``t_<id>``), así que las pruebas siguen aisladas entre sí y pueden
correr en paralelo; todos se borran al terminar. Sin la variable no cambia nada y rige ``mongomock``.
Las pruebas que construyen su propio ``mongomock`` (espías de aislamiento) siguen sobre Mongo simulado.
"""

from __future__ import annotations

import atexit
import os
import uuid

URL = os.environ.get("RAILSPEC_PRUEBAS_POSTGRES")

if URL:
    from psycopg_pool import ConnectionPool
    from railspec.server import estado
    from railspec.server.estado import mongo, postgres

    # Un solo pool para todos los esquemas: un pool por prueba agotaría las conexiones de Postgres.
    _pool = ConnectionPool(
        URL, min_size=1, max_size=8, kwargs={"prepare_threshold": None, "autocommit": True}, open=True
    )
    _esquemas: list[str] = []

    def almacen_en_memoria() -> mongo.AlmacenMongo:
        esquema = "t_" + uuid.uuid4().hex[:12]
        _esquemas.append(esquema)
        return mongo.AlmacenMongo(postgres.BaseDocumentosPg(URL, esquema=esquema, pool=_pool))

    estado.almacen_en_memoria = almacen_en_memoria
    mongo.almacen_en_memoria = almacen_en_memoria

    @atexit.register
    def _limpiar() -> None:
        with _pool.connection() as conn:
            for esquema in _esquemas:
                conn.execute(f'DROP SCHEMA IF EXISTS "{esquema}" CASCADE')
        _pool.close()


def pytest_collection_modifyitems(config, items):  # noqa: ANN001
    """Pruebas que describen un detalle de Mongo (``ObjectId`` heredados) sin equivalente en Postgres."""

    if not URL:
        return
    import pytest

    solo_mongo = {"test_documentos_antiguos_con_object_id_conviven"}
    for item in items:
        if item.name in solo_mongo:
            item.add_marker(pytest.mark.skip(reason="los ObjectId heredados solo existen en Mongo"))
