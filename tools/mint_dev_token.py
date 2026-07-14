"""
Mint a short-lived HS256 test token for local AUTH_MODE=dev_hs256.

DEV ONLY — never use HS256 shared-secret tokens in production (use your OIDC
identity provider). This just lets you exercise the authenticated /chat flow
without standing up an IdP.

Usage:
    AUTH_DEV_SECRET=devsecret python tools/mint_dev_token.py --role provider
    # then:
    curl -s localhost:8000/chat \
      -H "Authorization: Bearer <token>" \
      -H 'content-type: application/json' \
      -d '{"persona":"patient","message":"help me understand my labs"}'
    # (persona in the body is ignored; the token's role decides.)
"""
from __future__ import annotations

import argparse
import os
import time

import jwt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="patient",
                    help="role claim value (e.g. 'provider' to unlock the PHI path)")
    ap.add_argument("--subject", default="dev-user")
    ap.add_argument("--ttl", type=int, default=3600, help="seconds until expiry")
    ap.add_argument("--audience", default=os.environ.get("OIDC_AUDIENCE") or None)
    ap.add_argument("--issuer", default=os.environ.get("OIDC_ISSUER") or None)
    ap.add_argument("--role-claim", default=os.environ.get("AUTH_ROLE_CLAIM", "roles"))
    args = ap.parse_args()

    secret = os.environ.get("AUTH_DEV_SECRET")
    if not secret:
        raise SystemExit("Set AUTH_DEV_SECRET to the same value the server uses.")

    now = int(time.time())
    claims = {"sub": args.subject, args.role_claim: [args.role],
              "iat": now, "exp": now + args.ttl}
    if args.audience:
        claims["aud"] = args.audience
    if args.issuer:
        claims["iss"] = args.issuer

    print(jwt.encode(claims, secret, algorithm="HS256"))


if __name__ == "__main__":
    main()
