#!/usr/bin/env python3
"""Put a local GaaS stack (`scripts/local_up.sh --gaas`) in Alpha mode and write its ALPHA_JSON.

Alpha is how staging and production run: access by invitation code, no card billing, no test-mode shortcuts.
Through the GaaS multisig (the deployer is its signer on a local stack) this sets the registry the way a
controller does there (docs/INVITATIONS.md of gos-as-a-service): invitation mode on, card billing off, the
Internet Identity bypass off (the portal ignores invitation mode while it is on), and credits taken for a
deployment (can_test_mode off). The settings are read back. Then it creates the invitation codes the spec uses,
as scripts/invite.py does, all of 50 credits: two good ones (one for the invitation gate, one for the dashboard),
one that has expired, and one more for the spec to use up before it tries it again.

    python3 scripts/e2e_gaas_alpha.py --out /tmp/e2e/alpha.json
    ALPHA_JSON=/tmp/e2e/alpha.json npx playwright test -c playwright.gaas.config.ts alpha.spec.ts   (src/realm_frontend)

It undoes nothing: run it after the spec that needs the test-mode portal (scripts/e2e_gaas.sh does).
Inputs: ``--casals-home`` (default ``$CASALS_HOME`` or ``~/casals-home``), ``--identity`` (default local-dev);
``$GAAS_DIR`` (default: gos-as-a-service next to this checkout) for scripts/invite.py.
"""
import argparse
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

from e2e_gaas_scale import bindings, call, through_multisig

GAAS_DIR = Path(os.environ.get("GAAS_DIR") or Path(__file__).resolve().parents[2] / "gos-as-a-service")
sys.path.insert(0, str(GAAS_DIR / "scripts"))
import invite  # noqa: E402  (found through GAAS_DIR)

# What staging runs: every test flag off but the card billing one.
FLAGS = {"test_mode": False, "ii_bypass": False, "user_self_registration": False, "skip_terms": False,
         "disable_card_billing": True}
# Where get_runtime_flags reports each of them.
RUNTIME_KEY = {"test_mode": "test_mode", "ii_bypass": "test_mode_ii_bypass",
               "user_self_registration": "test_mode_user_self_registration", "skip_terms": "test_mode_skip_terms",
               "disable_card_billing": "test_mode_disable_card_billing"}
CREDITS = 50
DAY = 86400


def create_invitation(project: Path, identity: str, multisig: str, registry: str, expires_at: int) -> str:
    """A new invitation of 50 credits that expires at ``expires_at`` (unix seconds); returns its code."""
    code = invite.new_code()
    payload = invite.invitation_payload(code, CREDITS, expires_at)
    proposed = call(project, identity, multisig, "propose", invite.propose_args(registry, payload))
    proposal = call(project, identity, multisig, "get_proposal", f"({invite.proposal_id(proposed)} : nat)", query=True)
    invite.check_executed(proposal)
    return code


def runtime_flags(project: Path, identity: str, registry: str) -> dict:
    """The registry's get_runtime_flags, parsed out of icp's Candid text: ("{\\"success\\":true,...}")."""
    out = call(project, identity, registry, "get_runtime_flags", "()", query=True)
    text = re.fullmatch(r'\s*\(\s*"(.*)"\s*,?\s*\)\s*', out, re.S).group(1)
    return json.loads(json.loads(f'"{text}"'))


def check_alpha_mode(project: Path, identity: str, registry: str):
    """Read the settings back: a proposal that the registry refused still counts as executed."""
    mode = call(project, identity, registry, "get_invitation_mode", "()", query=True)
    flags = runtime_flags(project, identity, registry)
    env = call(project, identity, registry, "get_env_config", "()", query=True)
    expected = {f"flag {name}": flags.get(RUNTIME_KEY[name]) is value for name, value in FLAGS.items()}
    expected["invitation mode on"] = '"enabled"' in mode
    expected["credits taken for a deployment"] = '\\"can_test_mode\\":false' in env
    wrong = [name for name, ok in expected.items() if not ok]
    if wrong:
        raise SystemExit(f"the registry is not in Alpha mode ({', '.join(wrong)}): {mode} {flags} {env}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--casals-home", default=os.environ.get("CASALS_HOME") or str(Path.home() / "casals-home"))
    p.add_argument("--identity", default="local-dev")
    p.add_argument("--out", default="/tmp/e2e/alpha.json")
    args = p.parse_args()
    home = Path(args.casals_home)
    project = home / "gaas" / "projects" / "gaas.local"
    gaas, product = bindings(home, "gaas"), bindings(home, "realms-product")
    network_url = re.search(r"url: (\S+)", (project / "icp.yaml").read_text()).group(1)
    port = network_url.rsplit(":", 1)[-1].rstrip("/")
    registry, multisig = gaas["realm-registry-backend"], gaas["multisig"]

    through_multisig(project, args.identity, multisig, registry, "set_canister_config_json",
                     {"network": "local", "test_flags": FLAGS})
    through_multisig(project, args.identity, multisig, registry, "configure", {"can_test_mode": False})
    through_multisig(project, args.identity, multisig, registry, "set_invitation_mode", True)
    check_alpha_mode(project, args.identity, registry)

    now = int(time.time())
    out = {
        "network_url": network_url,
        "portal_url": f"http://{gaas['realm-registry-frontend']}.localhost:{port}",
        "registry": registry,
        "installer": gaas["realm-installer"],
        "token_ledger": product.get("token-backend", ""),
        "valid_code": create_invitation(project, args.identity, multisig, registry, now + 30 * DAY),
        "dashboard_code": create_invitation(project, args.identity, multisig, registry, now + 30 * DAY),
        "used_code": create_invitation(project, args.identity, multisig, registry, now + 30 * DAY),
        "expired_code": create_invitation(project, args.identity, multisig, registry, now - DAY),
        "realm_name": os.environ.get("ALPHA_REALM", f"alpha-{secrets.token_hex(2)}"),
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
