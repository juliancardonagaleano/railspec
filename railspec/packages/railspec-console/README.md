# railspec-console

Consola web de Railspec (fase 7): SPA en React + TypeScript servida por `railspec-server` en `/consola/`.
Habla solo con la API de consola (`/consola/api`, cookie de sesión + cabecera anti-CSRF `X-Railspec-Consola: 1`).

Stack: Vite, TanStack Router (rutas en código, basepath `/consola`) y TanStack Query, Tailwind CSS v4 con
componentes estilo shadcn/ui propios (`src/componentes/ui/`), React Flow (DAG de fases), Sigma.js + graphology
(grafo de código), ECharts (estadísticas), Vitest + Testing Library.

## Desarrollo

Requiere Node 22.12+ y el servidor local escuchando en `http://localhost:8080`.

```sh
npm ci
npm run dev        # http://localhost:5173/consola/ ; /consola/api se reenvía a :8080
```

Para entrar sin GitHub, arranca el servidor con tokens de desarrollo y usa "Entrar con token" en `/consola/login`.

## Build

```sh
npm run build      # tsc -b && vite build → dist/ (base /consola/)
```

El servidor sirve `dist/` en `/consola/` con *fallback* a `index.html` para las rutas de la SPA.

## Pruebas y tipos

```sh
npm run typecheck
npm test           # vitest run
```

## Estructura

- `src/api/`: cliente (`pedir`, `invocarTool`, `ErrorApi`), endpoints y tipos del contrato.
- `src/componentes/`: marco, estados de carga/error/vacío, gráficos y `ui/`.
- `src/vistas/`: login, tablero, unidad, grafo, auditoría, estadísticas, administración, configuración y chat.
- `src/chat/`: **no** es de este paquete; lo aporta el hilo del chat. La ruta `/$org/$ws/chat` lo carga con
  `import.meta.glob` si existe (export `ChatContexto` o default, props `{apiBase, token, org, workspace}`).
