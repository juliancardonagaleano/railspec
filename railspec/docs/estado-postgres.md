# Estado en Postgres

El servidor guarda su estado (unidades, eventos, órdenes, reportes, snapshots, checkpoints, telemetría, auditoría, configuración de la consola, chat e insumos) en Mongo o en Postgres. Se elige con una variable y no cambia nada más: las mismas reglas de aislamiento por organización y workspace valen en las dos bases.

| Variable | Qué hace |
| --- | --- |
| `RAILSPEC_POSTGRES_URL` | URL de conexión (`postgresql://usuario:clave@host:5432/base`). Activa Postgres. Excluyente con `RAILSPEC_MONGO_URI`: con las dos el servidor no arranca. |
| `RAILSPEC_POSTGRES_ESQUEMA` | Esquema donde vive la tabla. Por defecto `railspec`. |

Sin ninguna de las dos el servidor solo arranca con `RAILSPEC_PERMITIR_DESARROLLO=1` (estado en memoria, solo desarrollo), igual que antes. El FalkorDB del grafo es aparte (`RAILSPEC_FALKORDB_URL`) y sigue siendo opcional.

## Cómo está hecho

`railspec.server.estado.postgres.BaseDocumentosPg` ofrece la API de colecciones de pymongo que usan los almacenes (`find`, `find_one`, `insert_one`, `replace_one`, `update_one`, `find_one_and_update`, `delete_one`, `delete_many`, `count_documents`, `aggregate`, `create_index`) sobre una sola tabla, `<esquema>.railspec_docs`:

| Columna | Contenido |
| --- | --- |
| `n` | Orden de inserción (lo que devuelve `find` sin orden, como Mongo). |
| `coleccion`, `id` | Colección y `_id` del documento; clave primaria. |
| `doc` | El documento en `json` (no `jsonb`: este reordena las claves de los diccionarios y el código depende de su orden). |
| `expira` | Copia de `_expira` para el TTL. |

- Las consultas se acotan en Postgres por colección, `_id` e igualdades de campos escalares; el resto del filtro (`$in`, `$or`, `$and`, `$gt`, `$gte`, `$lt`, `$lte`, `$ne`, `$exists`, `null` igual a ausente), el orden, el límite y la proyección los aplica Python. Es suficiente para un MVP; con mucho volumen hay que traducir más filtros a SQL.
- Las escrituras que leen y luego escriben bloquean las filas con `FOR UPDATE` en una transacción. El contador de unidades y el turno exclusivo por unidad son atómicos (hay una prueba con hilos).
- Los índices únicos son índices únicos parciales de Postgres. Los demás índices de Mongo no se crean.
- El TTL no lo hace la base: cada escritura purga lo vencido, como mucho una vez por minuto. Igual que con Mongo, hay un retraso entre vencer y desaparecer; quien necesita exactitud (la caché de nodos) comprueba la fecha al leer.
- Un operador sin soporte lanza `NotImplementedError`; nunca se ignora en silencio. Al añadir una consulta nueva a un almacén, la suite contra Postgres lo detecta.
- Los caracteres NUL de un texto se guardan como `U+FFFD`, porque Postgres los rechaza en JSON.

## Supabase

- Usa la cadena del **pooler en modo sesión** (puerto 5432 del host `…pooler.supabase.com`): la conexión directa es solo IPv6 y muchos hosts gratuitos no la alcanzan. El modo transacción debería servir también (el servidor no usa sentencias preparadas y cada operación va en su propia transacción), pero no se ha probado contra Supabase.
- La tabla vive en el esquema `railspec`, no en `public`, porque Supabase expone `public` por su API REST. Además la tabla se crea con seguridad por filas activa y sin políticas, así que la clave anónima no lee nada. El rol de la conexión es el dueño y no se ve afectado.
- El plan gratuito pausa el proyecto tras una semana sin actividad, tiene 500 MB y no hace respaldos: conviene un `pg_dump` periódico.
- Los datos quedan en Supabase, fuera de la zona de datos de Azure donde corren los modelos. Para repositorios propietarios es una decisión de política antes que técnica ([chat.md](chat.md), [proveedores.md](proveedores.md)).

## Pruebas

La coincidencia de filtros, el orden y las actualizaciones se prueban sin base (`tests/test_postgres_documentos.py`). Con una base:

```sh
export RAILSPEC_PRUEBAS_POSTGRES=postgresql://usuario@localhost/railspec_test
python -m pytest railspec/packages/railspec-server/tests/test_postgres_documentos.py   # la capa
python -m pytest railspec/packages/railspec-server/tests                               # toda la suite sobre Postgres
```

Con la variable, `tests/conftest.py` cambia `almacen_en_memoria()` por un esquema nuevo de Postgres por llamada (se borran al terminar). Sin ella rige `mongomock`. Una prueba de ObjectId heredados se salta, porque solo existen en Mongo.
