# Proveedores de modelo, catálogo y herramientas de contexto

Este documento describe cómo `railspec-server` llama a los modelos, cómo
decide qué despliegue sirve a cada rol y cómo
conecta las herramientas de contexto (gobernanza, documentación, memoria) que
alimentan el prompt de los gates. El código vive en
`packages/railspec-server/src/railspec/server/proveedores/` y
`.../contexto/`.

## Resumen

| Proveedor | Cuándo | Cómo |
| --- | --- | --- |
| Azure AI Foundry | Primario, siempre que haya `RAILSPEC_FOUNDRY_ENDPOINT`. | Un solo recurso con dos APIs. Los despliegues de Claude van por `<endpoint>/anthropic` con el SDK oficial (`AsyncAnthropicFoundry`) y, sin API key, token de Entra ID con scope `https://ai.azure.com/.default`. El resto de modelos va por chat completions en `<endpoint>/openai/v1` con el SDK de OpenAI y scope `https://cognitiveservices.azure.com/.default`. Las dos APIs comparten endpoint, credencial y región, y se auditan como `foundry`. |
| Anthropic directo | Solo con `RAILSPEC_ANTHROPIC_HABILITADO=true`; sirve a cualquier repositorio (decisión consciente del usuario, ver [Política por nivel](#política-por-nivel)). | SDK oficial (`AsyncAnthropic`) con `RAILSPEC_ANTHROPIC_API_KEY`. Su catálogo se lee con `GET /v1/models`. Su región es `global`. |
| Compatible (1.8) | Solo desde una suscripción de la consola; sirve a cualquier repositorio (decisión consciente del usuario). | OpenCode Zen, MiniMax o un endpoint propio con la API de Anthropic o de OpenAI; ver [Proveedores compatibles](#proveedores-compatibles). |

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

Estas variables fijan el proveedor **del servidor**, que sigue valiendo como respaldo; las
conexiones por organización se registran desde la consola ([Suscripciones de modelos](#suscripciones-de-modelos)).

| Variable | Defecto | Dónde | Uso |
| --- | --- | --- | --- |
| `RAILSPEC_FOUNDRY_ENDPOINT` | vacío | ConfigMap | Endpoint del recurso (`https://<recurso>.services.ai.azure.com`). Sin él no hay Foundry. |
| `RAILSPEC_FOUNDRY_API_KEY` | vacío | Secret | Clave del recurso. Sin ella, Entra ID (Workload Identity en AKS). |
| `RAILSPEC_FOUNDRY_REGION` | vacío | ConfigMap | Región del recurso (`eastus2`). Es la región de los SKU Standard y Provisioned y la que se audita. Sin ella, la región de los despliegues Standard queda sin auditar y el servidor lo avisa al arrancar. |
| `RAILSPEC_FOUNDRY_ZONA_DATOS` | vacío | ConfigMap | Zona de datos del recurso (`us` o `eu`), la de los SKU DataZone. Solo etiqueta la región que se audita; no restringe qué repositorios sirve el modelo. |
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
| `RAILSPEC_PROVEEDORES_HOSTS` | vacío | ConfigMap | Hosts a los que una organización puede apuntar un proveedor de contexto: nombres separados por coma o espacio, `*.dominio` para sus subdominios (no el propio dominio). Se suma el host de `RAILSPEC_PCE_URL`. Vacía y sin `RAILSPEC_PCE_URL`: ninguna organización puede configurar proveedores. Ver «Herramientas de contexto». |

`renderizar.py` pone cada valor entre comillas dobles en el ConfigMap y no
escapa. Por eso rechaza un `RAILSPEC_FOUNDRY_DESPLIEGUES` con comillas o barras
invertidas: en el clúster se usa la forma compacta, por ejemplo
`opus=claude-opus-5-5:DataZoneStandard,gpt5=gpt-5:Standard`. Si hace falta
declarar capacidades con JSON, la variable se pone en el Secret (que no pasa
por el renderizador) en vez del ConfigMap; el Secret va después del
ConfigMap en `envFrom` del Deployment y su valor gana.

## Suscripciones de modelos

Desde el contrato 1.6 una organización registra desde la consola sus propias
conexiones a Foundry y a Anthropic, sin tocar variables de entorno ni
redesplegar. Una **suscripción** (`SuscripcionModelo`) lleva el proveedor, su
endpoint, región y zona de datos (Foundry), la autenticación y la clave. Con
ella se **descubren** los modelos que el proveedor ofrece, se **eligen** los
que quedan disponibles y un **perfil** se asocia a una suscripción y solo
puede usar los modelos elegidos en ella.

| Campo | Foundry | Anthropic |
| --- | --- | --- |
| `endpoint` | Obligatorio: `https://<recurso>.services.ai.azure.com` (https y dominio de Azure; ver `RAILSPEC_FOUNDRY_HOSTS`). Solo se guarda el origen. | No lleva. |
| `proyecto`, `region`, `zona_datos` | Nombre del proyecto (para leer los despliegues), región del recurso y zona de datos (`us` o `eu`, la de los SKU DataZone). | No llevan: su región es `global`. |
| `autenticacion` | `api-key` (clave del recurso) o `identidad-servidor` (Entra ID/Workload Identity del servidor, sin clave). | Solo `api-key`. |

**Claves.** Se escriben en el formulario, se cifran con AES-256-GCM antes de
llegar a la base (ligadas a su organización y suscripción) y viven en una
colección aparte. **Ninguna respuesta de la API, log ni evento de auditoría las
devuelve**: la consola solo sabe si hay una (`clave_configurada`). Cambiar el
endpoint obliga a escribirla otra vez. El cifrado exige una clave maestra:

| Variable | Dónde | Uso |
| --- | --- | --- |
| `RAILSPEC_CLAVE_MAESTRA` | Secret | 32 bytes en base64 (`openssl rand -base64 32`). Sin ella el servidor arranca y avisa, pero la consola no puede guardar ni usar suscripciones con clave (responde 503 con la variable que falta). Si se pierde, las claves guardadas son irrecuperables y hay que escribirlas otra vez. |
| `RAILSPEC_CLAVE_MAESTRA_ANTERIOR` | Secret | Una o varias claves anteriores, separadas por coma, que solo sirven para descifrar. **Rotación**: pon la nueva en `RAILSPEC_CLAVE_MAESTRA` y la vieja aquí; cada clave se vuelve a cifrar con la nueva la próxima vez que se escribe, y la vieja se puede quitar cuando ninguna suscripción la use. |
| `RAILSPEC_FOUNDRY_HOSTS` | entorno del contenedor | Dominios extra permitidos para el endpoint de una suscripción (nubes soberanas, Private Link con dominio propio): `host` o `*.dominio`, separados por coma. Por defecto, `*.services.ai.azure.com`, `*.openai.azure.com` y `*.cognitiveservices.azure.com`. |

**Quién.** Crear, editar, borrar, descubrir y elegir modelos es de un
`org-admin` (o de quien administra la plataforma); leerlas, de cualquier rol
de la organización, porque quien edita un perfil tiene que ver qué puede elegir.

**Descubrir.** `POST …/suscripciones/{id}/descubrir` llama a la API real del
proveedor con un tope de 45 s: los despliegues del proyecto de Foundry
(`proyecto` es obligatorio) o `GET /v1/models` de Anthropic. Los modelos nuevos
quedan listados sin elegir; los ya elegidos que dejan de aparecer pasan a
`ausente` (no se borran ni se sirven). Si falla, la elección anterior se
conserva y la respuesta es 502 con un `codigo` (`autenticacion`, `permiso`,
`no-encontrado`, `limite`, `proveedor`, `tiempo`, `red`, `forma`, `cifrado-ilegible`,
`clave-maestra-ausente`…)
y un `detalle` que nombra la suscripción pero nunca la URL ni la clave. El
último resultado queda en `ultima_lectura`. La forma de la API de proyectos de
Foundry sigue sin verificarse contra un recurso real; si no responde o no
acepta la clave, un despliegue se **declara a mano** (nombre, modelo, SKU y,
si hace falta, capacidades) y queda elegido igual.

**Elegir.** Solo los modelos elegidos están disponibles para los perfiles de
esa suscripción. La región de cada uno se deriva de la suscripción y del SKU:
`Global*` → `global`, `DataZone*` → la zona de la suscripción, el resto → su
región. Sin SKU conocido la región queda sin determinar (se audita así).

**Política de datos.** Cambió el 2026-10-06 (ver [Política por nivel](#política-por-nivel)):
la región, el SKU y la zona del workspace se auditan pero ya no restringen
qué repositorios sirve un modelo; cualquier modelo elegido de una suscripción
sirve a cualquier repositorio. Una suscripción `habilitada: false` no sirve a
nadie.

**Perfiles.** `PerfilConfig.suscripcion` apunta a una suscripción. Al guardar
un perfil la consola comprueba (422 si falla) que la suscripción exista y esté
habilitada y que cada modelo de sus roles esté elegido en ella, admita el
`effort`, las salidas estructuradas y el contexto pedidos. Ya no avisa por el
nivel de los repositorios ni por la región del modelo. Si la organización ya
tiene suscripciones, un perfil nuevo o editado tiene que elegir una. No se puede
borrar una suscripción que algún perfil usa (409 con la lista).

**Respaldo con variables de entorno.** Un perfil **sin** suscripción sigue
resolviéndose como antes: catálogo de la organización y los proveedores que
fijan `RAILSPEC_FOUNDRY_*` y `RAILSPEC_ANTHROPIC_*`. Un perfil **con**
suscripción no cae nunca a ese respaldo: si la suscripción se borra, se
deshabilita o pierde el modelo, la unidad falla con `perfil-insatisfacible` en
lugar de enviar código a otro destino. El chat usa la suscripción del perfil por
defecto del workspace. La auditoría de cada llamada incluye la suscripción.

**Migrar de `RAILSPEC_FOUNDRY_*` a una suscripción.**

1. Define `RAILSPEC_CLAVE_MAESTRA` y redespliega.
2. En Configuración → Suscripciones crea una suscripción de Foundry con los
   mismos valores que tenías (`RAILSPEC_FOUNDRY_ENDPOINT`, `_REGION`,
   `_ZONA_DATOS`, el nombre del proyecto de `_PROYECTO`) y la clave de
   `RAILSPEC_FOUNDRY_API_KEY` (o `identidad-servidor` si no usabas clave).
3. Pulsa Descubrir y elige los modelos. Los de `RAILSPEC_FOUNDRY_DESPLIEGUES`
   se declaran a mano con su SKU.
4. Edita cada perfil y asócialo a la suscripción; la consola lo valida.
5. Cuando ningún perfil dependa del respaldo, quita las variables
   `RAILSPEC_FOUNDRY_*`. Hasta entonces siguen valiendo.

## Proveedores compatibles

Desde el contrato 1.8 una suscripción puede ser de un proveedor `compatible`: un
endpoint que habla la API de mensajes de Anthropic (`anthropic-messages`) o la de
chat completions de OpenAI (`openai-chat`). Sirve para OpenCode Zen, MiniMax y
cualquier otro servicio así. El código está en `proveedores/compatible.py`
(adaptador) y `proveedores/servicios_compatibles.py` (servicios conocidos y
descubrimiento).

**Decisión consciente del usuario.** Ninguno corre en Azure, así que su
`hosting` es `externo` y su región queda desconocida; eso se audita pero ya no
los excluye de ningún repositorio (el nivel de código solo gobierna qué material
se les envía). Úsalos sabiendo que **pueden tener otras condiciones de
retención, entrenamiento y región que Foundry**; lo que declaran sus páginas
(consultadas el 2026-10-06, antes de elegir uno léelas de nuevo):

| Servicio | Datos |
| --- | --- |
| OpenCode Zen ([docs](https://opencode.ai/docs/zen/)) | «All our models are hosted in the US». Cero retención y sin entrenamiento, salvo OpenAI y Anthropic (30 días) y los modelos gratis o de prueba, que pueden usarse para mejorar el modelo. Añade un intermediario entre Railspec y el modelo. |
| MiniMax ([términos](https://platform.minimax.io/protocol/terms-of-service)) | «We may use the input and generated content to provide, maintain, develop, and improve our Services»: sin garantía de no entrenamiento. |

**Servicios conocidos.** Al crear la suscripción se elige `opencode-zen`,
`minimax` o personalizado. Los conocidos fijan sus URL base (`servicio` y los
endpoints no se escriben):

| `servicio` | `endpoint` (chat) | `endpoint_mensajes` |
| --- | --- | --- |
| `opencode-zen` | `https://opencode.ai/zen/v1` | `https://opencode.ai/zen` |
| `minimax` | `https://api.minimax.io/v1` | `https://api.minimax.io/anthropic` |

Zen enruta por modelo (Claude y algunos Qwen por mensajes, el resto de los
abiertos por chat completions) y su `GET /models` solo trae `id`, `object`,
`created` y `owned_by`. Por eso el protocolo de cada modelo sale de una tabla del
servicio según su documentación. Al descubrir **no se ofrecen**: los que hablan
Responses (GPT, Grok, Muse), Gemini o Jev; los que Zen documenta como recolectores
de datos (`*-free`, `big-pickle`, `*contributor*`); y OpenCode Go, cuya página lo
describe como pensado para agentes de código, con vigilancia de tráfico y
cabeceras de sesión, no para un servidor. Cualquiera de ellos se puede
**declarar a mano** con su protocolo.

Una suscripción **personalizada** lleva `endpoint` y/o `endpoint_mensajes`, y su
host tiene que estar en `RAILSPEC_COMPATIBLES_HOSTS` (nombres separados por coma o
espacio; `*.dominio` admite subdominios). Los hosts de los servicios conocidos
siempre valen. Sin la variable no hay endpoints personalizados.

**Salida estructurada emulada.** Ninguna de esas APIs documenta `json_schema` ni
`output_config.format`. El adaptador pide el JSON en el prompt (el esquema
Pydantic del nodo va tras el sistema estable), quita el razonamiento
(`<think>…</think>`) y las cercas de código, valida contra el esquema y, si no
encaja, repite la llamada una vez diciéndole al modelo el error. Si sigue sin
encajar es `ErrorProveedor`: el gate escala con `error-proveedor`, nunca aprueba.
Por eso el catálogo marca `structured_outputs` verdadero en estos modelos.
Tampoco envía `cache_control` ni `effort`: `efforts` queda vacío y un perfil que
use un modelo compatible no pide `effort` en sus roles.

**Precio y contexto.** Un modelo sin `precio_usd_mtok` cuenta 0 USD (salvo los
`claude-*` de `PRECIOS_USD_MTOK`), así que `costo_usd_max` no frena nada: declara
la tarifa del modelo a mano. El contexto de un modelo que el servicio no
declara queda en 8.192 tokens (el valor conservador de siempre); declara el real
si un rol pide `contexto_min_tokens`. MiniMax: 1.000.000 (M3) y 204.800 (M2.x),
según su documentación.

**Descubrir.** `GET <endpoint>/models` (o `<endpoint_mensajes>/v1/models` si solo hay
mensajes) con la clave como `Bearer`, con los mismos códigos de error que el resto.

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
catálogo no se sabe el SKU (de él sale la región que se audita). Solo los
dobles de prueba, que no pasan por `app.py`, eligen sin catálogo; ahí la región
auditada sale del adaptador.

**Caché y persistencia.** El catálogo se lee una vez por
`RAILSPEC_CATALOGO_TTL_S` para todo el servidor, porque el proveedor responde
lo mismo a todas las organizaciones. Cada organización que lo usa guarda su
copia en la colección `catalogo` de Mongo, con la fecha de lectura
(`leido_en`); la copia de un proveedor se reemplaza entera, así que un
despliegue que desaparece del proveedor sale también de Mongo. Si una lectura
falla, rige la última copia leída en memoria o, tras un reinicio, la guardada
en Mongo. El catálogo nunca se inventa: si no hay ninguna copia, ese proveedor
no sirve a ningún rol y `unit.start` lo explica.

**Sincronizar desde la consola.** Un `org-admin` puede forzar la lectura desde
Configuración → Catálogo de modelos (todos los proveedores o uno solo); ver
[consola.md](consola.md#catálogo-de-modelos). Es la misma lectura y la misma
copia por organización, y no pasa por el TTL: pero dos lecturas seguidas del
mismo proveedor llaman una sola vez a su API (la última lectura se reutiliza si
tiene menos de 30 s), y una lectura se corta a los 60 s (el cerrojo del
catálogo se sostiene mientras se lee, y el SDK de Anthropic espera hasta diez
minutos por defecto).

**Errores de lectura.** Cada fallo se clasifica con un código estable y un texto
que nombra al proveedor y la causa sin URLs, credenciales ni el cuerpo de la
respuesta (el detalle técnico va al log del servidor): `autenticacion` (401 o
token de Entra ID que no se obtiene), `permiso` (403), `no-encontrado` (404:
endpoint o proyecto mal configurados), `limite` (429), `proveedor` (otro HTTP
de error), `red`, `tiempo`, `forma`, `configuracion` y `interno`. Un fallo no
borra nada: rige el catálogo anterior y la consola lo dice.

La forma de la respuesta del proyecto de Foundry sigue sin verificarse contra un
recurso real, así que ahora una respuesta que no se reconoce **se dice** en vez
de vaciar el catálogo: sin la lista `value`, o con elementos y ninguno con
`name` y `modelName` (o de otro `type`), es un error `forma` y se conserva el
catálogo anterior. Una lista vacía, o solo de conexiones u otros tipos, sí es un
catálogo sin modelos. El parseo de los elementos reconocidos no cambió.

**Región por SKU.** La región de un despliegue de Foundry sale de su SKU:

| SKU | Región en el catálogo (se audita) |
| --- | --- |
| `Global*` (`GlobalStandard`, `GlobalProvisionedManaged`, …) | `global`: la inferencia puede correr en cualquier región de Azure. |
| `DataZone*` | `zona-<RAILSPEC_FOUNDRY_ZONA_DATOS>` (`zona-us`). Sin zona del recurso, la región queda desconocida. |
| El resto (`Standard`, `Provisioned*`) o sin SKU | `RAILSPEC_FOUNDRY_REGION`. |

Antes del 2026-10-06 la región decidía qué despliegues podían servir a
`restringido` e `interno`; ya no: cualquier SKU sirve a cualquier repositorio.

**Capacidades.** La API de Foundry no da las capacidades del modelo, así que
el servidor usa una tabla conservadora de modelos conocidos
(`capacidades_conocidas` en `catalogo.py`): familias Claude actuales con
salida estructurada, `effort` y contexto largo; `gpt-5`, `o3` y `o4` con
salida estructurada y `effort` hasta `high`; `gpt-4.1` y `gpt-4o` con salida
estructurada y sin `effort`. Un modelo desconocido queda sin salida
estructurada y con contexto mínimo, de modo que no satisface ningún rol del
gate hasta que se declare en `RAILSPEC_FOUNDRY_DESPLIEGUES` con la forma JSON
(`"structured_outputs": true`, `"efforts": […]`, `"contexto": …`).

> **Nota sobre los SKU.** Un despliegue `GlobalStandard` (lo habitual al
> desplegar Claude) queda marcado `global` en el catálogo y se audita así; ya
> no impide que lo usen los repositorios `restringido` e `interno`. Si tu
> organización necesita que el procesamiento se quede en una zona, elige tú un
> despliegue `DataZoneStandard` o `Standard` y asígnalo en el perfil.

## Política por nivel

**Desde el 2026-10-06 el nivel de código del repositorio ya no decide qué
proveedor, modelo, región o zona de datos se usa** (decisión del dueño del
producto: el uso de Railspec es consciente y usar modelos abiertos o distintos
de Foundry es decisión del usuario). El nivel se conserva solo como control de
qué material de código viaja al modelo y como dato de auditoría:

| Nivel del repositorio | Material de código que puede salir hacia el modelo |
| --- | --- |
| `restringido` | Nada de texto de código: rutas, hashes e índice (símbolos y relaciones). |
| `interno` | Además, fragmentos acotados a los símbolos tocados por la unidad. |
| `abierto` | Además, el diff unificado base..árbol. |

La selección (`seleccion.py`) no recibe nivel ni zona. Recorre los
proveedores en orden (Foundry y, si está configurado, Anthropic directo; con
suscripción en el perfil, solo la suya) y se queda con el primero cuyo catálogo
tenga el modelo pedido por el perfil y cumpla el requisito del rol: salida
estructurada, el `effort` pedido y el contexto mínimo si el perfil lo fija.
Nunca se cambia de modelo para cumplirlo. Los proveedores `compatible` solo se
usan desde una suscripción asociada al perfil. La región del despliegue se
audita (`Eleccion.region`) sin condicionar la elección.

Un repositorio sin vínculo se trata como `restringido` (el nivel más estricto
en cuanto al material). Con varios repositorios en una unidad rige el más
restrictivo, y ese nivel se **congela al crear la unidad**
(`RepositorioUnidad.nivel_codigo` y `EstadoUnidad.nivel_efectivo`): bajarlo o
subirlo en la consola no cambia las unidades en curso.

> **Advertencia.** Anthropic directo, OpenCode Zen, MiniMax y cualquier
> endpoint compatible son servicios de terceros con **otras condiciones de
> retención, entrenamiento y región que Foundry** (Zen y MiniMax lo declaran en
> sus términos; ver [Proveedores compatibles](#proveedores-compatibles)).
> Railspec ya no los bloquea por el nivel del repositorio: quien registra la
> suscripción o habilita Anthropic decide, con conocimiento, qué código le
> envía.

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

### Perfil con el que nace una unidad

`unit.start` elige el perfil en este orden: el `perfil` de la petición, si lo
trae; si no, el «perfil por defecto» del workspace (`Workspace.perfil_por_defecto`,
editable en la consola); si el workspace no está registrado, `estandar`. El
perfil elegido es el que se valida contra el catálogo antes de crear la unidad
(`perfil-insatisfacible`) y el que queda en `EstadoUnidad.perfil`. Cambiar el
perfil por defecto del workspace no toca las unidades ya creadas.

Las claves por nivel son una opción del usuario, no una restricción: sin
ellas, el nivel no cambia de modelo. Ejemplo: un workspace que quiere `gpt-5`
(más barato) para los críticos de `restringido` e `interno` sin tocar el resto
del perfil:

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
cada `(rol, requisito)` que el gate puede pedir con ese perfil, el nivel
efectivo de los repositorios (solo para escoger las claves por nivel del perfil)
y el riesgo del triaje (el refutador solo si el tope del
riesgo es adversarial), y comprueba que todos tienen un despliegue que los
sirva. Si alguno falla, la unidad no se crea y la tool responde con el error
`perfil-insatisfacible` (HTTP 422, `isError` por MCP) y un motivo por rol,
por ejemplo `rol critico-profundo: foundry/claude-opus-5-5: opus-g: sin salida
estructurada` o `foundry/gpt-5 no está en el catálogo de foundry`. El servidor nunca degrada el
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
| `url` | Endpoint MCP por HTTPS (streamable HTTP) en un host que permita la plataforma (`RAILSPEC_PROVEEDORES_HOSTS`). |
| `credencial_ref` | `secret://<org>--<nombre>/<clave>` o vacío. Nunca el valor. El secreto lleva la organización como prefijo. |
| `politica_fallo` | `estricta` o `blanda`. `gobernanza` solo admite `estricta`. |
| `fases` | Fases en las que aplica; vacía = todas. El gate `codigo` cuenta como la fase `implement`. |
| `presupuesto_tokens` | Recorta los ítems de esa herramienta (orden estable por id, unos 4 caracteres por token) para acotar el prefijo del prompt. |

`RAILSPEC_PCE_URL` y `RAILSPEC_PCE_API_KEY` dan una herramienta de gobernanza
por defecto. Un rol configurado en Mongo para el workspace o su organización
sustituye entero al valor por defecto del entorno para ese rol.

**Quién edita y qué se valida.** Lo que guarda una organización no es de
confianza: la `url` recibe la consulta del gate (hasta 500 caracteres del objeto) y
la credencial viaja en `X-API-Key`. Por eso:

- Solo un `org-admin` (o quien administra la plataforma) fija `url` y
  `credencial_ref`, también en proveedores de un workspace. El `workspace-admin`
  edita política de fallo, fases y presupuesto, pero no crea proveedores ni cambia
  a dónde va la consulta ni con qué clave.
- La `url` se valida al guardar **y cada vez que se usa** (los datos viejos de
  Mongo no se salvan): `https`, sin credenciales ni fragmento, y host en
  `RAILSPEC_PROVEEDORES_HOSTS` (más el de `RAILSPEC_PCE_URL`). **Por defecto no hay
  ninguno**: sin la variable ni `RAILSPEC_PCE_URL`, ninguna organización puede
  configurar proveedores y los ya guardados cuentan como consulta fallida.
- La IP *resuelta* se comprueba al conectar, y se conecta a esa IP ya comprobada:
  loopback, privadas, link-local (el metadata de la nube), CGNAT, multicast y las
  IPv6 que envuelven una IPv4 no pública se rechazan aunque el nombre esté
  permitido. El cliente no sigue redirecciones ni usa `HTTPS_PROXY` (un proveedor
  de organización necesita salida directa). Un servicio interno con IP privada solo
  sirve como el `RAILSPEC_PCE_URL` por defecto, que fija la plataforma y no pasa
  por estas comprobaciones.
- La consola audita cada alta, cambio y borrado con `url`, `credencial_ref` y, si
  cambiaron, `url_previa` y `credencial_ref_previa`.
- `GET …/proveedores-contexto` solo devuelve `credencial_ref` a `org-admin` y a la
  plataforma; el resto recibe `credencial_configurada` (si hay una).

**Credenciales.** `credencial_ref` se resuelve en cada consulta, primero desde
el archivo `RAILSPEC_SECRETOS_DIR/<secreto>/<clave>` y, si no está montado,
desde la variable `RAILSPEC_SECRETO_<SECRETO>_<CLAVE>` (mayúsculas, con `-` y
`.` como `_`). El valor se envía como `X-API-Key` y nunca se registra. Si no
se encuentra, las consultas de esa herramienta cuentan como fallidas.

El pool de secretos del servidor es uno solo, así que cada organización tiene
su **namespace**: el secreto se llama `<org>--<nombre>` (la organización `acme`
usa `secret://acme--pce/api-key`, montado en `RAILSPEC_SECRETOS_DIR/acme--pce/api-key`
o en `RAILSPEC_SECRETO_ACME__PCE_API_KEY`). El `<nombre>` no lleva `--` y la clave
empieza por letra o dígito. La consola rechaza al guardar toda referencia que no
sea del namespace de la organización de la ruta, y el resolutor la rechaza otra
vez al usarla, con la organización que consulta: la referencia de otro tenant ni
se lee ni sale hacia ninguna URL. **Compatibilidad**: las referencias sin prefijo
(`secret://pce-equipo/api-key`) dejan de resolverse; hay que renombrar el Secret
(y su montaje o variable) a `<org>--<nombre>` y actualizar la referencia en la
consola. Un secreto compartido por varias organizaciones se monta una vez por
organización, o se usa `RAILSPEC_PCE_API_KEY` si es la gobernanza por defecto.

Los manifiestos de `deploy/k8s` no montan ningún Secret para esto. Si se usan
herramientas con `credencial_ref`, se añade al Deployment un volumen por
Secret; por ejemplo, para `secret://acme--pce/api-key` (organización `acme`):

```yaml
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: acme-pce
              mountPath: /var/run/secrets/railspec/acme--pce
              readOnly: true
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: 256Mi
        - name: acme-pce
          secret:
            secretName: acme--pce
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
de salida, sistema, contenido y el commit del código evaluado
(`proveedores/cache.py`); el contenido incluye
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
| `--nivel` | `restringido`, `interno` o `abierto`; se puede repetir. Solo cambia las claves por nivel del perfil que se prueban: el nivel no restringe proveedores. Por defecto, `restringido` y `abierto`. |
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
