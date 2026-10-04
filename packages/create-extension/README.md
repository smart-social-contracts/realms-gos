# @realmsgos/create-extension

Scaffold a sandboxed [Realms](https://github.com/smart-social-contracts/realms-gos) extension project.

## Usage

```bash
npm create @realmsgos/extension my-extension
```

This downloads `@realmsgos/create-extension` and runs `create-realms-extension`.

### Non-interactive usage

When you pass a target directory, `--id` and `--name` are derived from its basename if omitted (e.g. `demo-greeter` → id `demo_greeter`, name `"Demo Greeter"`):

```bash
npm create @realmsgos/extension demo-greeter
```

Override defaults explicitly:

```bash
npm create @realmsgos/extension my-extension -- \
  --id my_extension \
  --name "My Extension" \
  --description "Does something useful"
```

| Flag | Description |
|------|-------------|
| `--id` | Extension identifier (`snake_case`, 3–32 chars; default: derived from target-dir basename) |
| `--name` | Display name (sidebar label, page title; default: derived from target-dir basename) |
| `--description` | Manifest description (defaults to `"<name> — a sandboxed Realms extension"`) |

The target directory must not exist or must be empty.

## Generated project

- `manifest.json` with `runtime: "sandboxed"` and bridge capabilities
- `frontend/` — Svelte 5 + Vite app using `@realmsgos/extension-bridge` and `@realmsgos/extension-ui` from npm
- `backend/entry.py` — minimal Python stub with a `greet` example

After scaffolding:

```bash
cd my-extension/frontend
npm install
npm run build
```

## Documentation

Extension authoring guide:
https://github.com/smart-social-contracts/realms-gos/blob/main/docs/guide/extension-authoring.md

Bridge protocol reference:
https://github.com/smart-social-contracts/realms-gos/blob/main/docs/reference/EXTENSION_SANDBOXING.md

## License

MIT
