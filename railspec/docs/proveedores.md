# Proveedores de modelo, catálogo y herramientas de contexto

Este documento describe cómo `railspec-server` llama a los modelos, cómo
decide qué despliegue sirve a cada rol según el nivel del repositorio y cómo
conecta las herramientas de contexto (gobernanza, documentación, memoria) que
alimentan el prompt de los gates. El código vive en
`packages/railspec-server/src/railspec/server/proveedores/` y
`.../contexto/`.

## Resumen

| Proveedor | Cuándo | Cómo |
| --- | --- | --- |
| Azure AI Foundry | Primario, siempre que haya `RAILSPEC_FOUNDRY_ENDPOINT`. | Un solo recurso con dos APIs. Los despliegues de Claude van por `<endpoint>/anthropic` con el SDK oficial (`AsyncAnthropicFoundry`) y, sin API key, token de Entra ID con scope `https://ai.azure.com/.default`. El resto de modelos va por chat completions en `<endpoint>/openai/v1` con el SDK de OpenAI y scope `https://cognitiveservices.azure.com/.default`. Las dos APIs comparten endpoint, credencial y región, y se auditan como `foundry`. |
| Anthropic directo | Solo con `RAILSPEC_ANTHROPIC_HABILITADO=true` y solo para repositorios de nivel `abierto`. | SDK oficial (`AsyncAnthropic`) con `RAILSPEC_ANTHROPIC_API_KEY`. Su catálogo se lee con `GET /v1/models`. Su región es `global`. |

`ProveedorFoundry` decide la API por el id de catálogo del modelo: si empieza
por `claude`, Messages API; si no, chat completions. Las dos piden salida
estructurada con el esquema Pydantic del nodo (`messages.parse` con
`output_format`, `chat.completions.parse` con `response_format`) y pasan el
`effort` del perfil (`output_config.effort` en Claude, `reasoning_effort` en
chat completions, que solo conoce `low`, `medium` y `high`). El sistema del
prompt es el prefijo estable (rúbrica y gobernanza) y en Claude lleva
`cache_control` para aprovechar la caché de prompt.

Sin `RAILSPEC_FOUNDRY_API_KEY` el servidor usa `DefaultAzureCredential`, que en
AKS toma la identidad de Workload Identity (`RAILSPEC_AZURE_CLIENT_ID` en el
renderizado de manifiestos, ver [despliegue.md](despliegue.md)). Esa identidad
necesita acceso de inferencia al recurso y lectura del proyecto si se usa
`RAILSPEC_FOUNDRY_PROYECTO`.

## Variables de entorno

| Variable | Defecto | Dónde | Uso |
| --- | --- | --- | --- |
| `RAILSPEC_FOUNDRY_ENDPOINT` | vacío | ConfigMap | Endpoint del recurso (`https://<recurso>.services.ai.azure.com`). Sin él no hay Foundry. |
| `RAILSPEC_FOUNDRY_API_KEY` | vacío | Secret | Clave del recurso. Sin ella, Entra ID (Workload Identity en AKS). |
| `RAILSPEC_FOUNDRY_REGION` | vacío | ConfigMap | Región del recurso (`eastus2`). Es la región de los SKU Standard y Provisioned y la que se audita. Sin ella, `restringido` e `interno` no tienen modelo en zona y el servidor lo avisa al arrancar. |
| `RAILSPEC_FOUNDRY_ZONA_DATOS` | vacío | ConfigMap | Zona de datos del recurso (`us` o `eu`), la de los SKU DataZone. |
| `RAILSPEC_FOUNDRY_PROYECTO` | vacío | ConfigMap | Endpoint del proyecto de Foundry (`https://<recurso>.services.ai.azure.com/api/projects/<proyecto>`) para leer los despliegues por API. |
| `RAILSPEC_FOUNDRY_PROYECTO_API_VERSION` | `v1` | entorno del contenedor | `api-version` de la API de proyectos. No pasa por el renderizador. |
| `RAILSPEC_FOUNDRY_DESPLIEGUES` | vacío | ConfigMap | Despliegues declarados a mano, que se suman a los del proyecto y ganan por nombre. Forma compacta `despliegue=modelo[:SKU]` separada por comas, o una lista JSON de objetos con `despliegue`, `modelo`, `sku`, `efforts`, `structured_outputs` y `contexto`. En el ConfigMap solo cabe la forma compacta (ver más abajo). |
| `RAILSPEC_CATALOGO_TTL_S` | `3600` | ConfigMap | Vigencia del catálogo leído por API, en segundos. |
| `RAILSPEC_CACHE_NODOS_S` | `86400` | ConfigMap | Vigencia de la caché de nodos de modelo por hash de entradas, en segundos; `0` la desactiva. |
| `RAILSPEC_ANTHROPIC_HABILITADO` | `false` | ConfigMap | Activa Anthropic directo. Si está activa y falta la clave, el servidor no arranca. |
| `RAILSPEC_ANTHROPIC_API_KEY` | vacío | Secret | Clave de Anthropic directo. |
| `RAILSPEC_PCE_URL` | vacío | ConfigMap | URL MCP de PCE. Es la herramienta de gobernanza por defecto de toda organización que no configure la suya. |
| `RAILSPEC_PCE_API_KEY` | vacío | Secret | Clave de esa PCE por defecto (cabecera `X-API-Key`). |
| `RAILSPEC_CONTEXTO_CACHE_S` | `900` | ConfigMap | Caché de consultas a las herramientas de contexto, en segundos; `0` la desactiva. |
| `RAILSPEC_SECRETOS_DIR` | `/var/run/secrets/railspec` | entorno del contenedor | Directorio donde se montan los Secrets que resuelven `credencial_ref`. No pasa por el renderizador. |

`renderizar.py` pone cada valor entre comillas dobles en el ConfigMap y no
escapa. Por eso rechaza un `RAILSPEC_FOUNDRY_DESPLIEGUES` con comillas o barras
invertidas: en el clúster se usa la forma compacta, por ejemplo
`opus=claude-opus-5-5:DataZoneStandard,gpt5=gpt-5:Standard`. Si hace falta
declarar capacidades con JSON, la variable se pone en el Secret (que no pasa
por el renderizador) en vez del ConfigMap; el Secret va después del
ConfigMap en `envFrom` del Deployment y su valor gana.

## Catálogo de modelos

El catálogo dice qué modelos y despliegues existen, dónde corren y qué
admiten. Cada proveedor aporta una fuente:

- **Foundry, proyecto.** `GET {RAILSPEC_FOUNDRY_PROYECTO}/deployments?api-version=…`,
  con paginación por `nextLink`. Lee `name`, `modelName` y `sku.name` de cada
  despliegue de tipo `ModelDeployment` y descarta lo que no reconoce. La forma
  de la respuesta sigue la documentación de la API de proyectos y no está
  verificada contra un recurso real.
- **Foundry, declarados.** `RAILSPEC_FOUNDRY_DESPLIEGUES`. Se suman a los del
  proyecto; si un despliegue aparece en los dos, gana el declarado.
- **Anthropic.** `GET /v1/models` con el SDK, que pagina solo. Las capacidades
  salen del objeto `capabilities` de cada modelo.

Sin `RAILSPEC_FOUNDRY_PROYECTO` ni `RAILSPEC_FOUNDRY_DESPLIEGUES`, el catálogo
de Foundry queda vacío y ningún perfil se satisface: `unit.start` responde
`perfil-insatisfacible` y el servidor lo avisa al arrancar. Es deliberado: sin
catálogo no se sabe el SKU, y un despliegue Global no puede servir a
`restringido` ni a `interno`. Solo los dobles de prueba, que no pasan por
`app.py`, eligen sin catálogo; ahí la zona sale de la región del adaptador.

**Caché y persistencia.** El catálogo se lee una vez por
`RAILSPEC_CATALOGO_TTL_S` para todo el servidor, porque el proveedor responde
lo mismo a todas las organizaciones. Cada organización que lo usa guarda su
copia en la colección `catalogo` de Mongo, con la fecha de lectura
(`leido_en`); la copia de un proveedor se reemplaza entera, así que un
despliegue que desaparece del proveedor sale también de Mongo. Si una lectura
falla, rige la última copia leída en memoria o, tras un reinicio, la guardada
en Mongo. El catálogo nunca se inventa: si no hay ninguna copia, ese proveedor
no sirve a ningún rol y `unit.start` lo explica.

**Región por SKU.** La región de un despliegue de Foundry sale de su SKU:

| SKU | Región en el catálogo | ¿Sirve a `restringido` e `interno`? |
| --- | --- | --- |
| `Global*` (`GlobalStandard`, `GlobalProvisionedManaged`, …) | `global` | Nunca: la inferencia puede correr en cualquier región de Azure. |
| `DataZone*` | `zona-<RAILSPEC_FOUNDRY_ZONA_DATOS>` (`zona-us`) | Sí, si la zona coincide con la del workspace. Sin zona del recurso, la región queda desconocida y no sirve. |
| El resto (`Standard`, `Provisioned*`) o sin SKU | `RAILSPEC_FOUNDRY_REGION` | Sí, si la región o su zona coinciden con la del workspace. |

**Capacidades.** La API de Foundry no da las capacidades del modelo, así que
el servidor usa una tabla conservadora de modelos conocidos
(`capacidades_conocidas` en `catalogo.py`): familias Claude actuales con
salida estructurada, `effort` y contexto largo; `gpt-5`, `o3` y `o4` con
salida estructurada y `effort` hasta `high`; `gpt-4.1` y `gpt-4o` con salida
estructurada y sin `effort`. Un modelo desconocido queda sin salida
estructurada y con contexto mínimo, de modo que no satisface ningún rol del
gate hasta que se declare en `RAILSPEC_FOUNDRY_DESPLIEGUES` con la forma JSON
(`"structured_outputs": true`, `"efforts": […]`, `"contexto": …`).

> **Aviso para Julian.** Revisar el SKU de los despliegues de Claude en
> Foundry. Si son `GlobalStandard` (lo habitual al desplegar Claude), el
> catálogo los marca `global` y los repositorios `restringido` e `interno` no
> pueden usarlos: `unit.start` devolverá `perfil-insatisfacible`. Hace falta
> un despliegue `DataZoneStandard` o `Standard` del mismo modelo, o un modelo
> que no sea Claude desplegado en zona y asignado a esos niveles con claves de
> perfil (sección Perfiles).

## Política por nivel

La selección (`seleccion.py`) recorre los proveedores en orden y se queda con
el primero cuyo catálogo tenga el modelo pedido por el perfil y cumpla el
requisito del rol.

| Nivel del repositorio | Proveedores | Despliegues admitidos |
| --- | --- | --- |
| `restringido`, `interno` | Solo Foundry. | Hosting `azure`, región conocida y distinta de `global` y, si el workspace declara `Workspace.zona_datos_azure`, dentro de esa zona (coincide con la región o con la zona del despliegue). |
| `abierto` | Foundry primero, Anthropic después si está habilitado. | Cualquier modelo del catálogo. |

Un repositorio sin vínculo se trata como `restringido`. Además de la zona, la
entrada del catálogo tiene que cumplir el requisito del rol: salida
estructurada, el `effort` pedido y el contexto mínimo si el perfil lo fija.
Nunca se cambia de modelo para cumplirlo.

## Perfiles

Los perfiles por defecto (`motor/perfiles.py`) piden el mismo id de catálogo
a Foundry y a Anthropic, siempre con salida estructurada:

| Rol | `ligero` | `estandar` | `profundo` |
| --- | --- | --- | --- |
| `redactor` | `claude-sonnet-5-5`, `medium` | `claude-opus-5-5`, `medium` | `claude-opus-5-5`, `high` |
| `critico-estructural` | `claude-sonnet-5-5`, `low` | `claude-sonnet-5-5`, `medium` | `claude-opus-5-5`, `medium` |
| `critico-profundo` | `claude-sonnet-5-5`, `medium` | `claude-opus-5-5`, `high` | `claude-opus-5-5`, `xhigh` |
| `critico-cumplimiento` | `claude-sonnet-5-5`, `medium` | `claude-sonnet-5-5`, `high` | `claude-opus-5-5`, `high` |
| `refutador` | `claude-sonnet-5-5`, `medium` | `claude-opus-5-5`, `high` | `claude-opus-5-5`, `xhigh` |

La consola guarda `PerfilConfig` por organización o workspace. Además del rol
a secas, `PerfilConfig.roles` admite claves más específicas, y gana la más
específica que exista:

1. `<rol>@<gate>:<nivel>`, por ejemplo `critico-profundo@codigo:restringido`.
2. `<rol>@<gate>`, por ejemplo `critico-profundo@plan`.
3. `<rol>:<nivel>`, por ejemplo `refutador:interno`.
4. `<rol>`.

`<gate>` es el valor de `GateFase`: `spec`, `plan`, `tasks` o `codigo`.
`<nivel>` es `restringido`, `interno` o `abierto`. Un rol sin ninguna clave en
el perfil guardado toma el valor del perfil por defecto del mismo nombre.

Ejemplo: un workspace cuyo Claude solo está en despliegue Global y que tiene
`gpt-5` en zona de datos manda los críticos de `restringido` e `interno` a
`gpt-5` sin tocar el resto del perfil:

```json
{
  "nombre": "estandar",
  "roles": {
    "critico-profundo:restringido": {
      "modelo": {"foundry": "gpt-5"},
      "effort": "high",
      "structured_outputs": true
    },
    "critico-profundo:interno": {
      "modelo": {"foundry": "gpt-5"},
      "effort": "high",
      "structured_outputs": true
    },
    "critico-estructural:restringido": {
      "modelo": {"foundry": "gpt-5"},
      "effort": "medium",
      "structured_outputs": true
    },
    "critico-cumplimiento@codigo:restringido": {
      "modelo": {"foundry": "gpt-5"},
      "effort": "high",
      "structured_outputs": true
    },
    "refutador:restringido": {
      "modelo": {"foundry": "gpt-5"},
      "effort": "high",
      "structured_outputs": true
    }
  }
}
```

**Validación al arrancar.** `unit.start` lee el catálogo (si caducó), arma
cada `(rol, requisito)` que el gate puede pedir con ese perfil, el nivel del
repositorio primario y el riesgo del triaje (el refutador solo si el tope del
riesgo es adversarial), y comprueba que todos tienen un despliegue que los
sirva. Si alguno falla, la unidad no se crea y la tool responde con el error
`perfil-insatisfacible` (HTTP 422, `isError` por MCP) y un motivo por rol,
por ejemplo `rol critico-profundo en nivel restringido: foundry/claude-opus-5-5:
opus-global: fuera de la zona de datos (global)`. El servidor nunca degrada el
modelo en silencio a mitad de un gate.

## Herramientas de contexto

El servidor es cliente MCP de cada herramienta de contexto. Hay cuatro roles:

| Rol | Uso |
| --- | --- |
| `gobernanza` | ADR, políticas y principios que el gate pone en el prompt. Siempre estricta: sin proveedor, o con todas sus consultas fallidas, la gobernanza queda como no consultada y el gate escala antes de gastar tokens; con alguna fallida, `parcial`. |
| `documentacion` | Documentación de referencia. Con política `blanda` suma lo que responda y nunca bloquea; con `estricta`, su fallo cuenta como el de gobernanza. |
| `memoria` | Memoria de decisiones y sesiones previas, con la misma regla que `documentacion`. |
| `grafo-de-codigo` | Lo sirve el grafo central (`railspec-graph`); si se configura aquí, se ignora. |

**Configuración.** Cada herramienta es un `ProveedorContexto` en la colección
`proveedores_contexto` de Mongo, editable desde la consola:

| Campo | Significado |
| --- | --- |
| `org`, `workspace` | Sin `workspace`, vale para toda la organización. Con él, vale para ese workspace y sustituye a la de la organización con el mismo rol y nombre. |
| `rol` | Uno de los cuatro de arriba. |
| `nombre` | Nombre libre; junto con el rol identifica la herramienta. |
| `url` | Endpoint MCP por HTTPS (streamable HTTP). |
| `credencial_ref` | `secret://<secreto>/<clave>` o vacío. Nunca el valor. |
| `politica_fallo` | `estricta` o `blanda`. `gobernanza` solo admite `estricta`. |
| `fases` | Fases en las que aplica; vacía = todas. El gate `codigo` cuenta como la fase `implement`. |
| `presupuesto_tokens` | Recorta los ítems de esa herramienta (orden estable por id, unos 4 caracteres por token) para acotar el prefijo del prompt. |

`RAILSPEC_PCE_URL` y `RAILSPEC_PCE_API_KEY` dan una herramienta de gobernanza
por defecto. Un rol configurado en Mongo para el workspace o su organización
sustituye entero al valor por defecto del entorno para ese rol.

**Credenciales.** `credencial_ref` se resuelve en cada consulta, primero desde
el archivo `RAILSPEC_SECRETOS_DIR/<secreto>/<clave>` y, si no está montado,
desde la variable `RAILSPEC_SECRETO_<SECRETO>_<CLAVE>` (mayúsculas, con `-` y
`.` como `_`). El valor se envía como `X-API-Key` y nunca se registra. Si no
se encuentra, las consultas de esa herramienta cuentan como fallidas.

Los manifiestos de `deploy/k8s` no montan ningún Secret para esto. Si se usan
herramientas con `credencial_ref`, se añade al Deployment un volumen por
Secret; por ejemplo, para `secret://pce-equipo/api-key`:

```yaml
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: pce-equipo
              mountPath: /var/run/secrets/railspec/pce-equipo
              readOnly: true
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 256Mi
        - name: pce-equipo
          secret:
            secretName: pce-equipo
```

**Caché.** Cada consulta `(tipo, texto)` se cachea por URL y credencial
durante `RAILSPEC_CONTEXTO_CACHE_S`. Dos gates de la misma unidad con el mismo
objeto no repiten la consulta y reciben la misma respuesta, así que el
prefijo del prompt también es idéntico. Las consultas fallidas no se cachean.

**Compatibilidad.** Cualquier servidor MCP que exponga `search_catalog` (con
argumentos `query` y, opcionalmente, `type`) sirve como herramienta de
contexto. PCE es la primera implementación; la forma exacta de su respuesta
no está verificada contra el servicio real y el parseo descarta lo que no
reconoce.

## Auditoría y tokens

Cada llamada a modelo deja, también si falla:

- Un `RegistroAuditoria` con evento `llamada-modelo` en la colección
  `auditoria`: proveedor, modelo (id de catálogo, no el despliegue), región
  donde corrió la inferencia, `sha256` de lo enviado, nivel del repositorio y
  un `detalle` con nodo, rol y resultado (`ok` con los tokens, o `error` con
  el mensaje recortado) y, en Foundry, el nombre del despliegue.
- Una `TelemetriaNodo` en la colección `telemetria`: tokens de entrada,
  salida, lectura y escritura de caché, costo estimado en USD y duración. Las
  llamadas fallidas quedan con cero tokens.

El consumo acumulado (tokens, segundos y costo) vive en el estado de la unidad
y lo comparan los topes del presupuesto. El costo es una estimación con las
tarifas de primera parte (`PRECIOS_USD_MTOK` en `base.py`); la factura real de
Azure manda.

## Caché de nodos

Una llamada a modelo con las mismas entradas no se paga dos veces. La clave es
el `sha256` de proveedor, modelo, despliegue, effort, tope de tokens, esquema
de salida, sistema y contenido (`proveedores/cache.py`); el contenido incluye
el material, los criterios y los hallazgos previos, así que un gate que se
repite tras una caída, otra réplica que reanuda desde el checkpoint o un
material idéntico reciben la respuesta guardada. Se guarda por organización en
la colección `cache_nodos` (índice TTL en `_expira`), con la salida
estructurada validada y el uso de la llamada original; lo enviado no se guarda.

Un acierto no sale hacia el proveedor: no deja `RegistroAuditoria` ni suma al
consumo de la unidad, y su `TelemetriaNodo` queda con cero tokens. Si el
esquema de salida cambió, la entrada guardada no valida y se llama de nuevo.
`RAILSPEC_CACHE_NODOS_S` fija la vigencia (un día por defecto); `0` la apaga.

## Prueba de humo manual

`python -m railspec.server.humo` comprueba la configuración contra los
servicios reales sin arrancar el servidor. Antes, exportar las mismas
variables que tendría el contenedor:

```
export RAILSPEC_FOUNDRY_ENDPOINT=https://<recurso>.services.ai.azure.com
export RAILSPEC_FOUNDRY_REGION=eastus2
export RAILSPEC_FOUNDRY_ZONA_DATOS=us
export RAILSPEC_FOUNDRY_PROYECTO=https://<recurso>.services.ai.azure.com/api/projects/<proyecto>
# o bien RAILSPEC_FOUNDRY_DESPLIEGUES='opus=claude-opus-5-5:DataZoneStandard'
export RAILSPEC_FOUNDRY_API_KEY=…        # o az login / identidad de Entra ID
export RAILSPEC_PCE_URL=https://…/mcp    # opcional
export RAILSPEC_PCE_API_KEY=…            # opcional
python -m railspec.server.humo --org mi-org --nivel restringido --nivel abierto
```

| Opción | Significado |
| --- | --- |
| `--org` | Organización con la que se persiste el catálogo, en memoria (defecto `humo`). |
| `--nivel` | `restringido`, `interno` o `abierto`; se puede repetir. Por defecto, `restringido` y `abierto`. |
| `--zona` | Zona del workspace (`Workspace.zona_datos_azure`), por ejemplo `us`, para probar la restricción de zona. |
| `--sin-llamada` | No hace llamadas a modelo, así que no gasta tokens. |

Imprime, en orden:

1. El catálogo leído: proveedor, modelo, despliegue, región y capacidades, o
   el error de lectura de cada fuente.
2. La validación del perfil `estandar` en cada nivel pedido, con los motivos
   si es insatisfacible.
3. Salvo con `--sin-llamada`, una llamada estructurada mínima por nivel con
   el modelo elegido, su uso de tokens y la región donde corrió.
4. Si hay `RAILSPEC_PCE_URL`, una consulta de gobernanza a PCE con el número
   de ítems y el estado (`si`, `parcial`, `no`).

Sale con código distinto de cero si algo falla. En el entorno de desarrollo
de Claude no hubo credenciales, así que nada de esto se probó contra Foundry
ni PCE reales: la forma de la API de proyectos, las respuestas de PCE y el
uso de tokens de chat completions están escritos según la documentación y
cubiertos solo con dobles.

## Pendiente

- Probar el adaptador de Anthropic directo contra la API real.
- `contexto.yaml` por repositorio para declarar herramientas de contexto
  junto al código, además de la configuración de la consola.
