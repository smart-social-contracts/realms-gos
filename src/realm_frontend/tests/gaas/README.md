# GaaS UI e2e

The gos.earth journey in a browser, on a local replica with the GaaS stack (`scripts/e2e_gaas.sh`, see
[Testing](../../../../docs/development/testing.md#the-gosearth-journey)). Each spec needs the registry in its own
state, so `scripts/e2e_gaas.sh` prepares the stack before each one:

| Spec            | What it does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scale.spec.ts` | The portal in test mode (Internet Identity bypass, no invitation needed): a visitor launches a realm, the founder walks the setup wizard, members join through the Terms until the capital is 90% full, a member files a proposal, the members vote it and the realm's next quarter through, and the next member lands in the new quarter. A second test checks a realm is live only once its frontend serves (gos-as-a-service#58).                                                                                           |
| `alpha.spec.ts` | The portal as an Alpha visitor sees it (invitation mode on, card billing off, no test mode). Signed out: the Alpha notice with the request link on the landing page, and `/deploy-gos` asking to sign in and saying a code is required. Signed in and not activated: the invitation gate; a wrong, a used and an expired code are each refused with the registry's message and change nothing; a good one activates the account with its 50 credits and opens the wizard. The billing tab offers no card payment (Top Up reads "Coming soon", no checkout request), a principal that skipped the gate cannot call `request_deployment`, and the activated applicant launches a realm, which costs its price in credits. The session is forged (the applicant's key delegates to a browser session key, as Internet Identity would), because the portal ignores invitation mode while its Internet Identity bypass is on. |

`scripts/e2e_gaas_scale.py` prepares the first, `scripts/e2e_gaas_alpha.py` the second (flags through the GaaS
multisig, and the invitation codes as gos-as-a-service's `scripts/invite.py` creates them). To run one spec on a
stack that is up, prepare it and name it:

```bash
python3 scripts/e2e_gaas_alpha.py --out /tmp/e2e/alpha.json
cd src/realm_frontend
ALPHA_JSON=/tmp/e2e/alpha.json npx playwright test -c playwright.gaas.config.ts alpha.spec.ts
```

Alpha mode stays on after the spec: run `scripts/e2e_gaas_scale.py` again before `scale.spec.ts` (it turns invitation
mode off and test mode on; `SKIP_STACK=1 scripts/e2e_gaas.sh` does it).
