# Chat de contexto (`src/chat/`)

Módulo autocontenido de la consola web: sólo depende de `react` (18/19). Sin framework CSS;
los estilos están en `chat.css` (clases `rs-chat-`, claro/oscuro con `prefers-color-scheme`).

## Montaje en el shell

El shell (`vistas/chat/RutaChat.tsx`) carga `ChatContexto` de forma diferida en la ruta `/$org/$ws/chat`
(el módulo también tiene `export default`):

```tsx
<ChatContexto
  apiBase=""
  token={token}
  org={org}
  workspace={ws}
  repositoriosDisponibles={vinculos}
  conversacionId={ultima}
  alCrearConversacion={guardar}
  alConversacionNoDisponible={olvidar}
  alNuevaConversacion={olvidar}
  alCrearUnidad={abrirDialogoDeUnidad}
/>
```

| Prop | Tipo | Notas |
|---|---|---|
| `apiBase` | `string` | `""` = mismo origen (rutas `/v1/...`). |
| `token` | `string` | Bearer vigente. Se renueva cada hora y vuelve a llegar como prop; cada petición usa el valor actual. |
| `org`, `workspace` | `string` | Alcance de la conversación. |
| `repositoriosDisponibles?` | `{id, nivel?}[]` | Repositorios vinculados al workspace: se ofrecen como casillas (todas marcadas). Vacío = el chat dice que no hay vínculos. Si falta, cae a un campo de texto con los nombres separados por coma, validado contra `^[a-z0-9][a-z0-9-]{0,62}$` (el servidor rechaza `owner/repo` con 422). |
| `repositorios?` | `string[]` | Si se da (y no hay `conversacionId`), no se pregunta: se crea la conversación con ellos. |
| `conversacionId?` | `string` | Carga una conversación existente en lugar de crear una. |
| `alCrearConversacion?` | `(id) => void` | Aviso al crear una conversación (el shell guarda el id para retomarla). |
| `alConversacionNoDisponible?` | `() => void` | El servidor respondió 404 a `conversacionId` (expiró o no es de esta persona); el shell la olvida. |
| `alNuevaConversacion?` | `() => void` | La persona pulsó «Nueva conversación» (o «Empezar otra conversación» tras un 404). |
| `alCrearUnidad?` | `(insumo) => void` | Si se pasa, el panel de exportar ofrece «Crear unidad con este insumo» y el shell decide qué hacer con él. |

`PaginaPruebaChat` es una página independiente (API base, token, org, workspace, repos) para probar
el chat sin el shell.

## Endpoints usados

Todas las peticiones llevan `Authorization: Bearer <token>`.

- `POST /v1/chat/conversaciones` → crea la conversación.
- `GET /v1/chat/conversaciones/{id}` → conversación + mensajes (también refresca contadores tras cada pregunta).
- `POST /v1/chat/conversaciones/{id}/mensajes` → `text/event-stream` (eventos `pregunta`, `progreso`,
  `respuesta`, `error`, `fin`); se lee con `fetch` + `ReadableStream` (`sse.ts`), no con `EventSource`.
- `PATCH /v1/chat/conversaciones/{id}/mensajes/{mid}` → `conservar_en_insumo`.
- `POST /v1/chat/conversaciones/{id}/insumo` → exporta el insumo (422 con `reglas_fallidas` si el gate lo rechaza).

El insumo nunca lleva texto de código: sólo afirmaciones y referencias. Se trae localmente con
`railspec insumo pull <id>`.

## Pruebas

`sse.test.ts`, `referencias.test.ts` y `ChatContexto.test.tsx` (selector de repositorios y conversación retomada), con vitest.
