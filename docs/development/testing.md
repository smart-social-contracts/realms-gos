# Testing

| Suite | Where | Runs | Needs |
|---|---|---|---|
| Backend | [`tests/ggg/`](../../tests/ggg) | Every operation, Cedar, the sandbox, codices, against an in-memory realm | Python 3.10, `cedarpy` |
| Codex hooks | `codices/tests/` | Each first-party codex's hooks against canned reads | Python |
| Frontend | [`src/realm_frontend/`](../../src/realm_frontend) | Unit tests (Vitest), browser tests (Playwright) | Node |
| CLI | [`cli/tests/`](../../cli/tests) | The `realms` CLI | Python |

## Backend

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt cedarpy pytest
pytest tests/ggg -q                     # about 260 tests, a few seconds
pytest tests/ggg/test_treasury.py -q    # one area
```

No canister and no network: [`conftest.py`](../../tests/ggg/conftest.py) swaps the chain for
[`fake_chain.py`](../../tests/ggg/fake_chain.py) and runs the real Cedar policies with `cedarpy`.
Each test starts from an empty database.

### The pieces

**[`world.py`](../../tests/ggg/world.py)** builds the spec's running example, Agora: the
departments, the people (`alice`, `bob`, `dana`, `carla`, `dr-ana`, `elena`, `mallory`), their
seats, two organizations, funds and the `REALM` token. `ctl` is the canister's controller.

```python
world.build()                                    # Agora, in setup
world.policy(Department["Finance"], 2, 3)        # a 2-of-3 policy on Finance
```

**[`kit.py`](../../tests/ggg/kit.py)** calls operations exactly as the canister does (JSON in, JSON
out, through Cedar):

| Helper | Does |
|---|---|
| `call(op, caller, **args)` | The reply, whatever it is |
| `ok(op, caller, **args)` | The reply; fails the test unless `ok` |
| `denied(op, caller, **args)` | The error text; fails the test if it succeeded |
| `later(seconds)` | Moves the clock |
| `rows(Entity)` | Every row, to check results (never in canister code) |

**`fake_chain.state`** holds the caller, the controllers, the clock and the calls to other
canisters. Set `state["replies"][(canister, method)]` to answer one.

### A test

From [`test_justice.py`](../../tests/ggg/test_justice.py). `agora()` builds the world plus courts,
and `trial()` takes a dispute to a verdict; both are helpers in that file.

```python
def test_story_mallorys_fine():
    a = agora()
    fine = {"target": "User:mallory", "kind": "fine", "amount": 500, "symbol": "REALM"}
    case, v = trial(a, "alice", "mallory", "Spam", [fine])
    pen = Verdict[v].penalties[0].id
    assert "not final" in denied("penalty_execute", "dana", penalty=pen)
    later(15 * DAY)
    ok("verdict_finalize", "elena", verdict=v)
    denied("penalty_execute", "mallory", penalty=pen)
    denied("penalty_execute", "alice", penalty=pen)
    inv = ok("penalty_execute", "dana", penalty=pen)["invoice"]
    i = Invoice[inv]
    assert (i.payer, i.amount, i.symbol, i.reason) == ("User:mallory", 500, "REALM", f"Penalty:{pen}")
    assert i.due_at == fake_chain.now() + 30 * DAY and Penalty[pen].status == "executed"
    assert "executed" in denied("penalty_execute", "dana", penalty=pen)
```

Write tests as stories in the running example: who does what, and what the realm allows. When a
test needs a new person or seat, add it to `world.py` and to the spec's example.

### Spec and rules

Two files check the code against rules rather than behaviour:

- [`test_spec.py`](../../tests/ggg/test_spec.py): the operations match
  [`spec.md`](../ggg/spec.md), both ways. See [Contributing](contributing.md#the-spec-comes-first).
- [`test_canister_rules.py`](../../tests/ggg/test_canister_rules.py): rules the canister imposes.
  See [Contributing](contributing.md#rules-for-the-backend).

## Codex hooks

```bash
cd codices
REALM_BACKEND=../src/realm_backend pytest tests -q
```

`REALM_BACKEND` defaults to the realms-gos checkout the submodule is in. The pattern is shown in
[Build, test, publish a codex](../codices/build-test-publish.md#2-test-the-hooks-alone).

## Frontend

```bash
cd src/realm_frontend
npm install
npm run check          # svelte-check (types)
npm run test:unit      # Vitest
npm run test:e2e       # Playwright
```

### The gos.earth journey

One command builds the realm, brings a local GaaS stack up and plays the whole journey in a browser: a visitor
signs in on the portal, launches a realm (Alpha: credits, no payment), the founder finishes the setup wizard,
members join through the Terms, a member files a proposal through the voting form, the members vote it and the
realm's next quarter through, and the next member lands in the new quarter.

```bash
scripts/e2e_gaas.sh                 # about 25 minutes of stack, then about 15 of browser
SKIP_STACK=1 scripts/e2e_gaas.sh    # the stack is already up: a fresh voucher and the spec only
```

It needs sibling checkouts of Casals, file-registry and gos-as-a-service (or `CASALS_DIR`, `FILE_REGISTRY_DIR`,
`GAAS_DIR`) and the tools of [`scripts/local_up.sh`](../../scripts/local_up.sh). Every test records screenshots and
video into `src/realm_frontend/test-results/gaas`, and the HTML report is in `playwright-report/gaas`. The
workflow [`realms-gaas-e2e.yml`](../../.github/workflows/realms-gaas-e2e.yml) runs the same script nightly and on
demand, and uploads both folders.

## Not yet in CI

CI ([`.github/workflows/ci-pr.yml`](../../.github/workflows/ci-pr.yml)) still runs `tests/backend/`,
the suite of the previous backend, which does not load on this branch. `tests/ggg` and
`scripts/loc_budget.py` are not in CI yet, and `cedarpy` is not in `requirements-dev.txt`. Run them
locally before a pull request.
