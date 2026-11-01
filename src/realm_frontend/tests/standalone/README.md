# Standalone realm UI e2e

The journey of a realm on realmsgos.org, in a browser, on your own machine and on a local replica:

| Spec | What it does |
|---|---|
| `wizard.spec.ts` | The founder walks the setup wizard (the spinner shows while it loads and while it launches), the realm launches with every core extension and the codex's dependencies, a second member joins through the Terms step, every installed extension and sidebar page opens without errors. |
| `proposals.spec.ts` | A proposal of each kind is filed, voted yes by both members and executed: poll, extension install, extension upgrade, codex install, role assignment, Realms GOS release, realm-wide (federal) vote, treasury transaction (the realm gets an ic-python-token ledger in test mode, and the treasury is funded through a paid invoice). |
| `extensions-ops.spec.ts` | One test per extension, from a table of page, action and expected result: its main operation. `fixme` rows say why they cannot run. |
| `tax.spec.ts` | The life of a tax: a finance officer is seated by a vote, an administrator drafts a policy and the members vote it in on the voting page, the officer assesses the members (once per period), a member pays on the member dashboard and the treasury receives it, an unpaid tax turns overdue, a member declares their revenue. Every refusal leaves the realm as it was. The tax_manager page and waiving an invoice are `fixme`s with their reasons. |

`scripts/e2e_standalone_realm.py` also deploys that test ledger (ic-python-token, downloaded the first time).

The wizard has no extension step: its launch installs the realm's core extensions (`core-extensions.json`) and the
codex's dependencies. `scripts/e2e_standalone_realm.py` installs the same list first, frontends included.

## Run it

From the repository root, with `icp`, `node` 20+ and the Python tools `scripts/up.sh` asks for:

```bash
realms files build            # the extension bundles
scripts/up.sh -e local --yes  # the product on a local replica (:8000), with every extension and codex published
scripts/e2e_ui.sh             # a fresh realm, then the four specs, in order (about 20 minutes)
```

`scripts/e2e_ui.sh` takes `playwright test` arguments, for one spec or one row: `scripts/e2e_ui.sh --project=proposals`,
`scripts/e2e_ui.sh -g "import_export"`.

To keep another replica running beside it, give the stack its own `CASALS_HOME` and a port
(`CASALS_REPLICA_PORT=8371 CASALS_HOME=~/casals-home-e2e scripts/up.sh -e local --yes`, run twice: the first run
only learns where the replica is), and pass the same `CASALS_HOME` and an `E2E_DIR` to `scripts/e2e_ui.sh`.

Every test records a screenshot, a video and a trace into `test-results/standalone`, and the HTML report goes to
`playwright-report/standalone` (`npx playwright show-report playwright-report/standalone`). CI runs the same
commands in the `Realms E2E` workflow and uploads both folders, whether the run passes or not.
