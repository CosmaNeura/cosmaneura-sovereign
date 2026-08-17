# Clerk provider-pilot role operator

`tools/clerk_pilot_roles.py` is the narrow operator for the Clerk claim consumed
by the `cosmaneura-sovereign` JWT template. It resolves every supplied email to
exactly one existing Clerk user and changes only
`public_metadata.sovereign_role`. It never creates users or edits other public
metadata.

The default is a read-only dry run. `--apply` is required for a metadata change.
Both modes query Clerk, so run them only from the approved private operator
terminal against the intended Clerk instance.

## Secret handling

The tool has no secret or token command-line argument and never renders Clerk
response bodies, request URLs, metadata, user IDs, or authorization headers.
Provide the Clerk Backend API secret in one of two ways:

- `CLERK_SECRET_KEY` in the protected process environment; or
- a non-interactive stdin pipe with `--secret-stdin`.

For 1Password, prefer a direct pipe from the reviewed production Backend API
item; the value is captured by the tool and is not printed:

```bash
op read 'op://Production/clerk-production-api/secret_key' |
  python tools/clerk_pilot_roles.py grant \
    --secret-stdin \
    --email john@cosmaneura.com
```

Do not put a Clerk secret, session JWT, or `op read` result on the command line,
in an env file containing literal values, or in a pasted transcript. The tool
rejects simultaneous environment and stdin secret sources.

## Grant workflow

Run the dry run first and review the complete email/action list:

```bash
op read 'op://Production/clerk-production-api/secret_key' |
  python tools/clerk_pilot_roles.py grant \
    --secret-stdin \
    --email john@cosmaneura.com
```

Then repeat the exact reviewed list with `--apply`:

```bash
op read 'op://Production/clerk-production-api/secret_key' |
  python tools/clerk_pilot_roles.py grant \
    --secret-stdin \
    --email john@cosmaneura.com \
    --apply
```

For a later controlled batch, repeat `--email` once per approved pilot user.
All users are resolved and validated before the first update:

```bash
op read 'op://Production/clerk-production-api/secret_key' |
  python tools/clerk_pilot_roles.py grant \
    --secret-stdin \
    --email first-approved@cosmaneura.com \
    --email second-approved@cosmaneura.com \
    --email third-approved@cosmaneura.com
```

The batch is intentionally capped at 25 users. Duplicate emails, multiple
users matching one email, two emails resolving to the same user, and an
existing non-provider `sovereign_role` all fail closed before mutation.

## Revoke workflow

Revocation removes only a current `sovereign_role=provider`; it refuses to
delete or overwrite any other role value. Dry-run, review, and then apply:

```bash
op read 'op://Production/clerk-production-api/secret_key' |
  python tools/clerk_pilot_roles.py revoke \
    --secret-stdin \
    --email john@cosmaneura.com

op read 'op://Production/clerk-production-api/secret_key' |
  python tools/clerk_pilot_roles.py revoke \
    --secret-stdin \
    --email john@cosmaneura.com \
    --apply
```

Clerk updates are individually idempotent but the Clerk API has no batch
transaction. If an apply stops after a partial success, reconcile the listed
accounts in Clerk and rerun the same command; already-correct users are no-ops.

## Reserved administrator

`dev@cosmaneura.com` is rejected for grants and revocations because it is the
reserved administrator. Only explicit, separately approved break-glass work may
add `--break-glass-reserved-admin`. The flag bypasses only that email guard; the
exact-user, role-conflict, dry-run, and explicit-apply controls still apply.

After an approved grant, mint a fresh `cosmaneura-sovereign` token and verify
the correct-role and wrong-role smoke cases. A provider role does not authorize
real PHI or multi-tenant use; the synthetic Athena Preview and deployment gates
remain independently required.
