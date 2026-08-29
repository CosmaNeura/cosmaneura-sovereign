# Local named-provider walkthrough

This is a loopback-only, synthetic walkthrough of the Sovereign host. It does
not deploy anything, connect to a live record, call Athena, or use the platform
agent API. `BAA_ATTESTED` and `SOVEREIGN_DEPLOY_ENABLED` remain false.

## Start

From this repository and branch:

```bash
make install                 # only if .venv is missing
./tools/run_demo_web.sh --named-provider
```

The launcher creates a random development signing secret, mints an eight-hour
`roles: ["provider"]` fixture token with `tools/mint_dev_token.py`, copies the
token to the macOS clipboard, and starts a mock provider host on
`http://127.0.0.1:8000`. It binds only to loopback. The token is not written to
disk, a URL, browser storage, or a cookie.

If the browser does not open, visit
`http://127.0.0.1:8000/demo/dashboard`. Paste the copied token into the access
field and choose **Open panel**.

## Walkthrough

1. Confirm the page says **Local synthetic fixture**.
2. Paste the token and open the panel. Before verification, the panel remains
   unavailable and does not request fixture context or history.
3. Select **Trace synthetic session prep**. Review the generated synthetic
   prompt, then choose **Send**. Free-form entry is disabled in this fixture.
4. Confirm the response shows the `visit prep` route and final review. This runs
   the real router, capability gate, visit-prep agent, arbiter, and renderer with
   deterministic mock clients.
5. Optionally run **Trace an offline referral** to see bundled directory cards.
6. Stop the server with `Ctrl-C`. Closing the page clears its in-memory token.

Use only the provided prompts. Do not paste health, visit, account, note, or
record content into this local fixture.

## Fail-closed checks

- Open the dashboard directly in a separate tab without the token: the panel
  stays unavailable.
- A missing, expired, malformed, wrong-role, or no-role token cannot reveal the
  panel or request fixture context/history.
- The fixture accepts only the two guided requests with its exact synthetic
  context. Its legacy `/digest` bridge is unavailable.
- The local launcher pins `MOCK_MODE=true`, `PLATFORM_AGENT_API_ENABLED=false`,
  `FEATURE_ATHENA_LABS=false`, and `BAA_ATTESTED=false`.

The local Clerk-shaped OIDC contract can also be rehearsed without a Clerk
account or metadata mutation:

```bash
PYTHONPATH=. .venv/bin/python tools/clerk_oidc_local_demo.py
```

Expected result: `11 passed, 0 failed`. This starts a temporary loopback JWKS
server and does not use `tools/clerk_pilot_roles.py --apply`. Follow
`CLERK_PILOT_ROLES.md` for any later founder-controlled role dry run.

## Release boundary

This walkthrough is source readiness only. Do not run Build & Publish,
Terraform, Clerk role apply, Azure OIDC/Foundry configuration, or any production
promotion. Merging PR #33 later still does not deploy Sovereign; the human-held
deployment latch remains off.
