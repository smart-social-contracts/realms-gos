# Live-site smoke test

Read-only check of the deployed sites. Run it after every deploy. It signs in nowhere,
submits nothing, and fails on load errors.

```bash
# from the repo root, once
npm install --legacy-peer-deps --ignore-scripts
npx playwright install chromium

# every run
cd src/realm_frontend && npm run test:smoke
```

Screenshots and video of every test land in `test-results/smoke/`; the HTML report in
`playwright-report/smoke/`. The GitHub workflow `prod-smoke.yml` (manual, and nightly)
runs the same command and uploads both.

## What it checks

Every site in `targets.json` (portal, marketplace): HTTP 200, `/canister_ids.js` loads,
the sign-in button is visible, no console errors, no failed requests to the site's own origins.

Every realm in `targets.json`: the public dashboard renders, the sign-in button and the
extension list show, and each public extension page opens without a stuck "Loading..."
or the word "error" (the extension pages share no error element, so the whole page body is searched).

## Other targets

`targets.json` is the default list. To check test.* or demo.* write another file of the
same shape and point `SMOKE_TARGETS_FILE` at it:

```bash
SMOKE_TARGETS_FILE=/path/to/demo-targets.json npm run test:smoke
```

`kind` is `portal`, `marketplace` or `realm`.

## Known gap

Production (gos.earth) has no registered realm yet, so there is no production realm
target; the realm checks run on staging.gos.earth (`valencia`). Add a `realm` entry
for gos.earth once one is live.
