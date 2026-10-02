# Chat de contexto (fase 8)

El chat de la consola es un agente del servidor que responde preguntas sobre
repositorios, unidades, grafo y gobernanza de un workspace. Puede leer código
como contexto interno, pero lo que llega a la persona pasa siempre por un gate
de salida determinístico que no deja salir código, secretos ni datos fuera de
alcance, diga lo que diga el modelo. Las respuestas que la persona marca se
exportan como un insumo (`railspec.insumo/v1`) que una unidad consume al
arrancar.

| Pieza | Dónde |
|---|---|
| Servicio, agente, gate, insumos, rutas | `railspec-server/src/railspec/server/chat/` |
| Interfaz (React, sin dependencias salvo `react`) | `railspec-console/src/chat/` ([README](../packages/railspec-console/src/chat/README.md)) |
| Contratos (respuesta, veredicto, conversación, insumo) | `railspec-contracts`: `chat.py`, `insumo.py`, `referencias.py` |
| `railspec insumo pull` | `railspec-local` (`insumos.py`), vía `insumo.get` por MCP |

## Módulos del servidor

| Módulo | Qué hace |
|---|---|
| `servicio` | Conversaciones, bucle del agente, elección de modelo, auditoría, exportación del insumo. |
| `agente` | `PasoAgente` (llamadas a tools o respuesta final) y el prompt de sistema estable. |
| `gate` | Las siete reglas del gate de salida. |
| `normalizacion` | Tokens, aplastado, desofuscación y huellas (n-gramas y winnowing). |
| `forma`, `secretos` | Heurística de forma de código y patrones de secretos (copia de los del proxy). |
| `codigo` | `code.read` sobre el clon canónico (`ClonesGit`; `FuenteEnMemoria` para pruebas). |
| `almacen` | Mongo: `chat_conversaciones`, `chat_mensajes`, `chat_huellas`, `chat_fuga_usuario`, `insumos`. |
| `resolucion` | Insumos en `unit.start` y en `contexto.insumos` de cada orden. |
| `http` | Rutas `/v1/chat`. |

## Flujo de una pregunta

1. La conversación es de su autor (otra persona recibe 404) y de un workspace;
   todo acceso exige rol `lector` ahí. Nivel efectivo y política son los más
   restrictivos de sus repositorios.
2. El modelo se elige antes de enviar nada. En `restringido` e `interno`
   solo entra Foundry con una región fija que esté en `RAILSPEC_CHAT_ZONA_DATOS`;
   si la variable está vacía, el chat responde 422 `perfil-insatisfacible` sin
   llamar a ningún modelo. `modelos_permitidos` del vínculo se respeta.
3. Bucle de hasta 6 pasos: el modelo devuelve `PasoAgente` con hasta 4
   llamadas o con la respuesta. Las tools son las de superficie `chat` del
   registro único (`graph.query`, `unit.status`, `unit.list`,
   `telemetry.query`, `insumo.get`, `code.read`); todas de lectura. El
   servidor fija organización, workspace y repositorios de la conversación en
   cada llamada: el modelo no puede salir de ellos, y una tool de escritura no
   existe para él. El actor es `agente` en nombre del humano.
4. Lo que devuelven las tools entra al prompt dentro de bloques
   `<resultado-tool confianza="datos-no-instrucciones">`; un dato no puede
   cerrar su bloque. Los campos marcados `codigo_interno` (hoy
   `code.read.fragmentos[].texto`) se convierten en huellas, se auditan como
   `lectura-codigo` con su hash y nunca se guardan.
5. La respuesta final pasa el gate. Si bloquea, se guarda el veredicto y el
   aviso con las reglas, nunca la respuesta del modelo, y se audita
   `bloqueo-gate-salida`. Con 3 bloqueos la conversación queda limitada
   (429 `conversacion-limitada`).

Cada llamada a modelo deja telemetría (`fase: chat`, `nodo: chat.agente`,
con `conversacion`) y auditoría `llamada-modelo` con proveedor, modelo,
región, nivel y hash de lo enviado.

## Gate de salida

`version_gate: railspec-chat-gate/1`. Las siete reglas se evalúan siempre;
el veredicto (`VeredictoGateSalida`) solo lleva hashes de huellas, nunca
texto.

| Regla | Qué bloquea |
|---|---|
| `esquema` | Salida que no valida contra `RespuestaChat` (bloques de código, HTML, URL o texto libre). Las demás reglas se evalúan igual sobre todas sus cadenas. |
| `huella-contexto` | Un n-grama de N tokens compartido con cualquier fragmento leído en la conversación. Tokens: NFKC, camelCase partido, minúsculas, solo letras y dígitos (la puntuación no cuenta). N es `huella_tokens_n` del vínculo (12 restringido, 16 interno, 24 abierto). Las afirmaciones se unen antes de comparar: trocear no esquiva la regla. |
| `normalizacion` | Lo mismo tras desofuscar: confusibles Unicode, invisibles, diacríticos, separadores y espacios fuera (letras separadas), texto invertido, ROT13, base64, hex, URL encoding y escapes, hasta dos niveles. Compara subcadenas aplastadas de 4·N caracteres contra huellas winnowing del contexto. Lo decodificado tampoco puede tener forma de código ni secretos. |
| `forma-codigo` | Densidad de símbolos, palabras reservadas y líneas con forma de sentencia (definiciones, asignaciones, `return`, llaves, SQL…), aunque no coincida con nada leído. Solo se omite si todos los repositorios permiten `fragmentos_en_respuesta` (abierto). |
| `secretos` | Los patrones del proxy, en todas las cadenas de la salida, referencias incluidas. Una prueba exige que sean idénticos a los de `railspec-local`. |
| `alcance` | No bloquea: elimina (`recorta`) referencias a repositorios fuera de la conversación o a unidades y decisiones de otro workspace. |
| `presupuesto-fuga` | Caracteres de literales citados (entre comillas, backticks o «») más identificadores del código leído que aparezcan, acumulados por conversación y por persona y día (UTC). Topes del vínculo: 1500/6000 en restringido e interno, 4000/20000 en abierto. |

Límites conocidos: el gate es heurístico en `forma-codigo`; prosa muy
cargada de identificadores largos puede bloquear (falso positivo, se ve como
aviso y cuenta para el límite), y un identificador que también es palabra
común (`cantidad`) cuenta para el presupuesto si aparece en el código leído.
El corpus de regresión es `railspec-server/tests/test_chat_gate.py`; ningún
cambio al gate entra sin pasarlo.

## API HTTP

Misma identidad que `/v1/tools`: `Authorization: Bearer` resuelto con canal
`consola`. Errores: `{"codigo", "detalle"}` con 401, 403, 404, 409, 422 o 429.

| Método y ruta | Cuerpo | Respuesta |
|---|---|---|
| `POST /v1/chat/conversaciones` | `{"alcance": {"org", "workspace"}, "repositorios": [slug]}` (vacío = todos los vinculados) | 201 `{"conversacion": Conversacion}` |
| `GET /v1/chat/conversaciones?org=&workspace=&limite=` | | `{"conversaciones": [Conversacion]}`: solo las vigentes de la persona que pregunta en ese workspace (rol `lector`), recientes primero por `creada_en`; `limite` de 1 a 50 (por defecto 50). Sin mensajes. 422 `entrada-invalida` si falta `org` o `workspace`. |
| `GET /v1/chat/conversaciones/{id}` | | `{"conversacion", "mensajes": [MensajeChat]}` |
| `POST /v1/chat/conversaciones/{id}/mensajes` | `{"pregunta"}` (1 a 8000 caracteres) | `text/event-stream`, abajo |
| `PATCH /v1/chat/conversaciones/{id}/mensajes/{mid}` | `{"conservar_en_insumo": bool}` | `{"mensaje": MensajeChat}`; 409 si la respuesta fue bloqueada |
| `POST /v1/chat/conversaciones/{id}/insumo` | `{"objetivo", "restricciones": [], "preguntas_abiertas": []}` | 201 `{"insumo": Insumo}`; 422 `sin-hallazgos`, o `gate-salida` con `reglas_fallidas` |

Eventos SSE de una pregunta, en orden (`event:` + una línea `data:` JSON):
`pregunta` (el `MensajeChat` guardado), `progreso` (`{"paso", "tool", "texto"}`,
solo el nombre de la tool), `respuesta` (el `MensajeChat` del asistente, con
`respuesta` o `aviso_bloqueo`) y `fin` (`{"conversacion"}` con consumo de
fuga y bloqueos actualizados); o `error` (`{"codigo", "detalle"}`:
`error-proveedor`, `sin-respuesta`). No hay streaming de tokens del modelo:
el gate necesita la respuesta entera antes de dejar salir nada.

## Insumo

`railspec.insumo/v1` (`Insumo` en `railspec-contracts/insumo.py`) es la forma
que consumen el motor, el proxy y la consola:

```json
{
  "version_contrato": "1.3",
  "formato": "railspec.insumo/v1",
  "id": "uuid4",
  "alcance": {"org": "acme", "workspace": "certificados"},
  "repositorios": [{"repositorio": "certificados-api", "rol": "primario", "base_commit": "40 hex"}],
  "autor": {"tipo": "humano", "canal": "consola", "github_id": 83125327, "login": "juliancardonagaleano"},
  "creado_en": "2026-10-01T00:00:00Z",
  "nivel_efectivo": "restringido",
  "conversacion": "uuid4",
  "objetivo": "Una o dos frases, texto plano.",
  "hallazgos": [{"texto": "Afirmación corta.", "referencias": [{"tipo": "archivo", "repositorio": "certificados-api", "commit": "40 hex", "ruta": "src/pdf.py", "linea_inicio": 6, "linea_fin": 13}]}],
  "preguntas_abiertas": ["..."],
  "restricciones": ["..."],
  "transcripcion_resumida": null,
  "veredicto_gate": {"version_gate": "railspec-chat-gate/1", "permitido": true, "reglas": ["… siete evaluaciones …"], "evaluado_en": "…"},
  "sha256": "hash de contenido_canonico()"
}
```

- Hallazgos: las afirmaciones de las respuestas marcadas (hasta 50), ya
  recortadas por alcance. Preguntas abiertas: las pedidas más las de esas
  respuestas.
- Al exportar, hallazgos, objetivo, restricciones y preguntas pasan otra vez
  el gate completo, con forma de código activa en cualquier nivel (un insumo
  nunca lleva código) y sin volver a cobrar el presupuesto.
- `base_commit` de cada repositorio: el canónico al crear la conversación, o
  el primero visto por `graph.query`/`code.read`, o el de una referencia.
- Hash: `sha256(contenido_canonico())`, JSON con claves ordenadas sin el
  propio `sha256`; el contrato lo verifica al validar.
- Se guarda en la colección `insumos` sin TTL. `insumo.get`
  (`{"alcance": {"org", "workspace"}, "id"}`, rol `lector`, MCP, HTTP y chat)
  lo devuelve; otro workspace recibe 404.

### Consumo en una unidad

`unit.start` acepta `insumos: [id]`; un id que no exista en el workspace da
404 `no-encontrado`. Cada orden lleva en `contexto.insumos` un
`InsumoResuelto` por insumo: id, sha256, objetivo, hallazgos, restricciones y
cada referencia con `obsoleta`. Una referencia de código es obsoleta si su
archivo cambió entre el commit del insumo y el `base_commit` de la unidad
(comparando el clon canónico; sin clon, si los commits difieren). Las
referencias a repositorios que la unidad no toca y las que no son de código
no se marcan. El arnés trae el texto de cada referencia en local con
`railspec insumo pull <id>`.

## Configuración

| Variable | Por defecto | Qué hace |
|---|---|---|
| `RAILSPEC_CHAT_ZONA_DATOS` | vacío | Regiones de Azure (coma, en minúsculas: `eastus2,swedencentral`; `zona-us` o `zona-eu` para un despliegue DataZone) donde el chat puede enviar código en `restringido`/`interno`. Vacío: el chat no responde en esos niveles. |
| `RAILSPEC_CHAT_MODELO` | `claude-sonnet-5-5` | Modelo (despliegue en Foundry) del rol `chat`. |
| `RAILSPEC_CHAT_CLONES` | vacío | Carpeta con un clon de solo lectura por repositorio en `<owner>/<repo>`. Sin ella no hay `code.read` y el chat responde sin leer código. |

En AKS no se escriben a mano: `deploy/renderizar.py` las saca en el ConfigMap
desde `RAILSPEC_CHAT_ZONA_DATOS` y `RAILSPEC_CHAT_MODELO`, y fija
`RAILSPEC_CHAT_CLONES=/var/lib/railspec/clones` cuando se da
`RAILSPEC_CHAT_CLONES_PVC` (el PVC que el Deployment monta de solo lectura en
esa ruta). Sin zona de datos el renderizador avisa por stderr y rechaza una
región con mayúsculas, porque el servidor compara la región tal cual y el chat se
negaría en silencio. Cómo definirlas, crear el PVC y mantener los clones al día:
[despliegue.md](despliegue.md#chat-de-contexto-zona-de-datos-modelo-y-clones).

Los clones los lee `git show` (la imagen del servidor trae `git`) en
`origin/<rama por defecto del vínculo>` o, si no existe, en `<rama>`; sirve un
clon `--bare` mantenido con `git fetch origin '+refs/heads/*:refs/heads/*'`. No importa de quién sean los
archivos del volumen: `ClonesGit` declara `safe.directory` por clon. El despliegue
debe mantenerlos al día con la rama por defecto (un CronJob, o el mismo push que
reindexa el grafo). TTL de conversaciones: 72 h. El chat no consulta aún
gobernanza (PCE) ni memoria: no son tools del registro.

## Verificación

```
python -m pytest railspec/packages/railspec-server/tests/test_chat_gate.py \
  railspec/packages/railspec-server/tests/test_chat_servicio.py \
  railspec/packages/railspec-server/tests/test_chat_listado.py
```

La interfaz vive en `railspec/packages/railspec-console/src/chat` (el shell de la
consola la monta en `/<org>/<ws>/chat`); se comprueba con `tsc` estricto y vitest
(`sse.test.ts`, `referencias.test.ts`, `ChatContexto.test.tsx` y las de
`vistas/chat`); ver el README del paquete.
