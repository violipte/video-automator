# Video Automator — Frontend2 (React)

Nova UI do Video Automator construída com **React 19 + Vite + CSS puro** (sem Tailwind),
alinhada com o stack do Link Tracker 2.0.

## Stack
- React 19 (sem TypeScript pra simplicidade — adicionar depois se necessário)
- Vite 6 (build/dev)
- React Router 7 (rotas)
- Zustand 5 (estado global, 3KB)

## Quick start

```bash
cd frontend2
npm install
npm run dev   # → http://localhost:3000 (proxy /api → FastAPI :8500)
```

## Build pra prod

```bash
npm run build
# Output: ../static-frontend2/  (servido pelo FastAPI)
```

## Estrutura

```
frontend2/
├── src/
│   ├── main.jsx              # entry point
│   ├── App.jsx               # router + routes
│   ├── api/
│   │   └── client.js         # fetch wrapper centralizado
│   ├── styles/
│   │   ├── tokens.css        # paleta + tipografia (dark premium)
│   │   └── globals.css       # reset + base + scrollbar
│   ├── components/
│   │   ├── Layout/
│   │   │   ├── AppShell.jsx  # sidebar + main wrapper
│   │   │   └── Sidebar.jsx   # nav + footer com health
│   │   └── Common/
│   │       ├── Button.jsx
│   │       ├── Card.jsx
│   │       ├── Modal.jsx
│   │       ├── Input.jsx     # Input + Textarea + Select
│   │       ├── Badge.jsx
│   │       ├── Toast.jsx     # toast() + ToastContainer
│   │       └── Spinner.jsx
│   └── pages/
│       ├── Showcase.jsx      # design system showcase (temporário)
│       └── PagePlaceholder.jsx  # pra rotas ainda não migradas
└── vite.config.js            # dev proxy + build config
```

## Rotas

| Path | Status | Migração |
|---|---|---|
| `/showcase`  | ✅ Pronto | Design system pra você ver |
| `/temas`     | 🚧 Placeholder | Fase C3 |
| `/backlog`   | 🚧 Placeholder | Fase C4 |
| `/templates` | 🚧 Placeholder | Fase C1 (mais complexa) |
| `/monitor`   | 🚧 Placeholder | Fase C2 (reformulado) |
| `/log`       | 🚧 Placeholder | Fase D1 |
| `/config`    | 🚧 Placeholder | Fase D2 |
