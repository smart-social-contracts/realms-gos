# Standalone realm UI e2e

The journey of a realm on realmsgos.org, in a browser, on your own machine and on a local replica:

| Spec                     | What it does                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wizard.spec.ts`         | The founder walks the setup wizard (the spinner shows while it loads and while it launches), the realm launches with every core extension and the codex's dependencies, a second member joins through the Terms step, every installed extension and sidebar page opens without errors.                                                                                                                                                |
| `proposals.spec.ts`      | A proposal of each kind is filed, voted yes by both members and executed: poll, extension install, extension upgrade, codex install, role assignment, Realms GOS release, realm-wide (federal) vote, treasury transaction (the realm gets an ic-python-token ledger in test mode, and the treasury is funded through a paid invoice).                                                                                                 |
| `extensions-ops.spec.ts` | One test per extension, from a table of page, action and expected result: its main operation. `fixme` rows say why they cannot run.                                                                                                                                                                                                                                                                                                   |
| `justice.spec.ts`        | The life of a case in the justice extension with real members: a member files against another, the administrator accepts it into a court and assigns the judge, the judge issues the verdict, its effect is read back from the realm, a party appeals and the court above grants it; and a refusal at each step (non-member, party, non-judge). Steps the page has no screen for call the realm's operations as the member who would. |
| `tax.spec.ts`            | The life of a tax. Realm operations: the administrator seats a finance officer and adopts a policy (root department 1 of 1, no vote), the officer assesses, a member declares revenue. The dashboard: a member pays (the treasury receives it) and an unpaid tax turns overdue. Refusals change nothing. `fixme`: tax_manager page (realms-extensions#40), paid notice (#41), bad amounts (realms-gos#466), waiving (#467).           |
| `budget.spec.ts`         | How the realm's money is budgeted and spent: a fiscal period, an allocation rule that splits a payment between department funds, a budget, a spend on the vault page that grows its actual, an overspend refused, and a vote that gives a member the finance role and takes it away. Refusals leave the realm unchanged. The budget_manager page cannot run here (`fixme`): budgets and rules are realm operations.                   |
| `land.spec.ts`           | The life of a parcel in the land registry with members: the administrator registers it for a member, a court rules on a neighbour's dispute about it, the parcel is marked disputed, moved to the neighbour under the verdict and cleared; a refusal at each step (overlap, member, stranger, party, no or wrong court order). Steps without a screen call the realm's operations. Minting an NFT is `fixme`: no LAND ledger locally. |

`scripts/e2e_standalone_realm.py` also deploys that test ledger (ic-python-token, downloaded the first time).

The wizard has no extension step: its launch installs the realm's core extensions (`core-extensions.json`) and the
codex's dependencies. `scripts/e2e_standalone_realm.py` installs the same list first, frontends included.

## Run it

From the repository root, with `icp`, `node` 20+ and the Python tools `scripts/up.sh` asks for:

```bash
realms files build            # the extension bundles
scripts/up.sh -e local --yes  # the product on a local replica (:8000), with every extension and codex published
scripts/e2e_ui.sh             # a fresh realm, then the seven specs, in order (about 20 minutes)
```

`scripts/e2e_ui.sh` takes `playwright test` arguments, for one spec or one row: `scripts/e2e_ui.sh --project=proposals`,
`scripts/e2e_ui.sh -g "import_export"`.

To keep another replica running beside it, give the stack its own `CASALS_HOME` and a port
(`CASALS_REPLICA_PORT=8371 CASALS_HOME=~/casals-home-e2e scripts/up.sh -e local --yes`, run twice: the first run
only learns where the replica is), and pass the same `CASALS_HOME` and an `E2E_DIR` to `scripts/e2e_ui.sh`.

Every test records a screenshot and a video, and a failed test also a trace, into `test-results/standalone`, and the HTML report goes to
`playwright-report/standalone` (`npx playwright show-report playwright-report/standalone`). CI runs the same
commands in the `Realms E2E` workflow and uploads both folders, whether the run passes or not.
