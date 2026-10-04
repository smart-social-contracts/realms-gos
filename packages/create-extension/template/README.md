# __EXTENSION_NAME__

__EXTENSION_DESCRIPTION__

Sandboxed Realms extension scaffolded with `npm create @realmsgos/extension`.

## Layout

```
__EXTENSION_ID__/
├── manifest.json       # runtime, capabilities, entry_access
├── backend/
│   └── entry.py        # Python backend (greet example)
└── frontend/
    ├── src/            # Svelte 5 + Vite app
    └── dist/           # produced by npm run build
```

## Getting started

```bash
cd frontend
npm install
npm run build
```

The build emits `frontend/dist/index.html` with relative asset paths (`base: './'`). In production the realm serves it at:

```
/ext/__EXTENSION_ID__/1.0.0/frontend/dist/index.html
```

## Local development

Use the Realms extension dev-server (mock host + sandboxed iframe):

```bash
# From a Realms monorepo checkout:
cd extensions/dev-server && npm install
node bin/dev.js __EXTENSION_ID__
```

Open http://localhost:5555 — the mock host enforces manifest capabilities and returns mock results for declared backend functions.

## Next steps

- Edit `manifest.json` (capabilities, `entry_access`, sidebar metadata).
- Add backend functions in `backend/entry.py` and declare them under `entry_access.functions`.
- Build UI in `frontend/src/` using `@realmsgos/extension-bridge` and `@realmsgos/extension-ui`.

Full authoring guide:
https://github.com/smart-social-contracts/realms-gos/blob/main/docs/guide/extension-authoring.md
