# __CODEX_NAME__

__CODEX_DESCRIPTION__

A Realms codex scaffolded with `realms new codex`. A codex is the realm's
constitution: onboarding, lifecycle, fees and governance live in
`manifest.json`, and the realm calls the hooks in `backend/entry.py`.
A realm runs exactly one codex.

## Layout

```
__CODEX_ID__/
├── manifest.json       # kind: codex, policy blocks, dependencies (extensions installed with it)
└── backend/
    └── entry.py        # hooks: get_config, init
```

## Try it in a realm on your machine

Publish the extensions it depends on first, then the codex, then set the realm up with it:

```bash
realms local start
realms local publish ../my_extension .
realms local setup --codex __CODEX_ID__
```

The Agora codex in the Realms repo (`codices/codices/agora`) shows the full set of hooks.
