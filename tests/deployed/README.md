# Realm wasms on PocketIC

`pytest -q tests/deployed` packs the realm wasm (`wasm_of` in `conftest.py`, once per session and variant) and runs
it on a local PocketIC server, because basilisk stubs parts of the standard library inside a canister and only the
built artifact shows how the code behaves. Set `POCKET_IC_BIN` to a PocketIC server; `pic.py` defaults to the one in
the dfx cache. Create instances with `pic.new_pocket_ic()` (the classes below do): on a loaded machine the server
answers 409 "busy" to anything sent during a slow round, such as the install of an 11 MB wasm, and it waits instead
of failing.

| File | What it gives a test |
|---|---|
| `pic.py` | `Canister`: one canister on its own PocketIC instance. `call` runs an operation, `shell` runs Python in its heap, `wait` moves the clock, `upgrade` upgrades it in place. `Wasm`: a wasm uploaded once, for canisters of one instance to install from |
| `network.py` | `Network`: several realms of one wasm on **one** instance, for flows that cross canisters |
| `outcalls.py` | `Web`: the HTTPS outcalls of those canisters, answered from a table of URLs |
| `costs.py` | `costs`: the instruction counts tests report, printed as a table at the end of the session |

## A linked-realms test

```python
from network import Network

@pytest.fixture(scope="module")
def net(wasm_of, people):
    return Network(wasm_of("production"), people)      # uploads the wasm once

@pytest.fixture(scope="module")
def capital(net):
    return net.realm("capital")                        # launched; the founder, Alice and Bob are members

@pytest.fixture(scope="module")
def north(net):
    return net.realm("north", members=("alice",))
```

A realm installs in about ten seconds, so build realms in module-scoped fixtures and let the tests of a module use
them in order, as `test_effective_at.py` does. `realm.ok(op, who)` runs an operation and expects it to succeed:
`who` is a name in the `people` fixture (`founder`, `alice`, `bob`, `ctl` for the controller, ...) or a principal,
and `query=True` reads. A realm's id is `realm.cid`.

- **A governed call.** `realm.govern(op, args)` has the founder file a proposal that runs `op` as the realm, every
  member vote yes, finalizes it if it is still open and returns its id, executed. With `effective_at=` it returns
  once the proposal is scheduled, and `realm.run_at_effective_at(id)` lets the time pass and returns it, executed.
  Linking two realms by both votes is two such calls, one on each side.
- **Quarters.** `net.register_quarters(capital, north, south)` makes each a quarter of `capital`, the way
  `api.quarters.adopt` does, and lets them sync until each knows the others.
- **Time.** `net.wait(seconds)` moves the clock of the whole instance and runs the timers that fall due and the
  calls they make. `net.wait_until(reached, step=1, rounds=1)` moves it a step at a time until `reached()` (it fails
  after `limit` steps, 200 by default), to stop a flow of timer steps between two of them. Wait on the network, not
  on a realm's own `pic`: the clock is shared.
- **An upgrade in the middle of a flow.** `net.upgrade_when(realm, reached)` runs the flow until `reached()` holds
  and upgrades `realm` in place right there. The timers of that realm are gone until `post_upgrade`, as in
  production. Test what survives: `test_linked_realms.py` upgrades a capital between the vote and the time a
  proposal is scheduled for.
- **HTTPS outcalls.** `net.web.serve(url, bytes)` answers requests for `url`; serve it again to change the bytes.
  A URL nobody serves is a 404, `net.web.unreachable(url)` fails the outcall, and `net.web.no_answer(url)` leaves it
  pending: PocketIC rejects it after 60 seconds of instance time (code 2, "Canister http request timed out"), as
  mainnet does. A body larger than the request's `max_response_bytes` (2 MB if it sets none) fails the outcall too.
  `net.web.asked` lists the requests seen, `net.web.pending()` those not yet answered. The table is answered on every
  round `net` runs, so no test calls `answer()`.
  **Make the outcall from a timer, never inside an update call the test waits for:** that call returns only once the
  answer is in, and nothing answers while the test waits, so `update_call` gives up after 100 rounds. The install
  task makes its outcalls from a timer, and `test_linked_realms.py` schedules one through `__shell__`. A `transform`
  function is not exercised: the subnet runs it before the replicas compare the answers, and the install task adds
  `http_transform` and its test.
- **Public fixtures only.** `tests/fixtures/legislation` is a synthetic source, `arcadia`, with two releases and the
  chapter files their checksums name. `legislation.chapter(checksum)` gives the bytes to serve.
- **Costs.** `costs.measure(realm, "what it was", code)` runs `code` in the realm's heap, reports the instructions it
  spent and returns them. `costs.report("what it was", n)` records a count measured some other way. The table is
  printed once, at the end of the session.

## The same thing in-process

Most of a flow can be tested without a wasm in `tests/ggg`, where the realm runs in the test's process. There
`fake_chain.peer(canister_id, method=reply, ...)` plays another canister, for example a sync source or a follower:
it answers by method name from a fixture or a function, records what it was sent (`peer.sent(method)`), and fails on
purpose with `rejects(...)` or `traps(...)`. See `tests/ggg/test_scripted_peers.py`. Use PocketIC for what only the
wasm shows: timers, upgrades, the replica's limits and the instruction cost.
