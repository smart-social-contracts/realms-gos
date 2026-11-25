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

`SMOKE_ENV=production` or `SMOKE_ENV=staging` checks one environment only.

Screenshots and video of every test land in `test-results/smoke/`; the HTML report in
`playwright-report/smoke/`. The GitHub workflow `prod-smoke.yml` (manual, and nightly)
runs the same command once for production and once for staging, and uploads both.

## When it is red

Each environment is its own job, so staging stays green while production is red. A red job
opens one issue, "Live-site smoke is red: production" (or staging), listing the checks that
failed and their errors, and updates it on every later run; the first green run closes it
(`scripts/smoke_issue.sh`). Watch the repository's issues to hear about it.

## What it checks

Every host in `targets.json`: every IPv4 address it resolves to accepts a connection on
port 443. A browser tries a host's addresses in turn, so one dead address among several
stalls a share of visits for 30 seconds or more; the page checks below would only time out.
If a host fails this check its page checks are skipped, and the failure names the address.

The assistant's service (`geister-api.realmsgos.dev`, one for every environment, checked with
production): it is up, lists Ashoka, offers sign-in, and refuses a question without a session.
This cannot tell whether a page manages to sign in, which needs a real Internet Identity: a
client that never asks for a session passes it.

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

`kind` is `portal`, `marketplace` or `realm`; `env` is `production` or `staging`.

## Known gap

Production (gos.earth) has no registered realm yet, so there is no production realm
target; the realm checks run on staging.gos.earth (`valencia`). Add a `realm` entry
for gos.earth once one is live.
