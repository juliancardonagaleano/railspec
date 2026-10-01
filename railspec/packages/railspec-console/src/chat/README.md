# Chat de contexto (`src/chat/`)

Módulo autocontenido de la consola web: sólo depende de `react` (18/19). Sin framework CSS;
los estilos están en `chat.css` (clases `rs-chat-`, claro/oscuro con `prefers-color-scheme`).

## Montaje en el shell

El shell carga `src/chat/index.tsx` (vía `import.meta.glob`) en la ruta `/$org/$ws/chat` y usa su
`export default` (equivale al export nombrado `ChatContexto`):

```tsx
import ChatContexto from "./chat";

<Route
  path="/:org/:ws/chat"
  element={<ChatContexto apiBase="" token={token} org={org} workspace={ws} />}
/>
```

| Prop | Tipo | Notas |
|---|---|---|
| `apiBase` | `string` | `""` = mismo origen (rutas `/v1/...`). |
| `token` | `string` | Bearer vigente. Se renueva cada hora y vuelve a llegar como prop; cada petición usa el valor actual. |
| `org`, `workspace` | `string` | Alcance de la conversación. |
| `repositorios?` | `string[]` | Si falta (y no hay `conversacionId`), el chat pide la lista separada por comas antes de crear la conversación. |
| `conversacionId?` | `string` | Carga una conversación existente en lugar de crear una. |
| `alCrearConversacion?` | `(id) => void` | Aviso al crear una conversación (p. ej. para reflejar el id en la URL). |

`PaginaPruebaChat` es una página independiente (API base, token, org, workspace, repos) para probar
el chat mientras no exista el shell.

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

`sse.test.ts` y `referencias.test.ts` (vitest).
