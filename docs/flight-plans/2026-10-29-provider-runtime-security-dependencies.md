# Session Prep runtime dependency security

Risk: Tier 2 because the existing libraries supply provider TLS trust and token
verification. This is a source-only checkpoint based on merged main
`75e12994556151768e405061380a8f5dd96a9dc3`; the dependency fix is
`24e89573123730ee24225d3194536fde7955a8e4`.

## Exact scope

| Lock | Package | Previous | Updated |
| --- | --- | --- | --- |
| Runtime | certifi | 2026.7.22 | 2026.10.23 |
| Runtime | PyJWT | 2.13.0 | 2.15.1 |
| Runtime | urllib3 | 2.7.0 | 2.8.0 |
| CI only | pytest | 9.0.2 | 9.0.3 |

The runtime graph has no added or removed packages. Existing transitive
certifi/urllib3 security floors and the PyJWT compatible major bound prevent
reintroducing the affected versions. The official uv 0.12.5 compiler retains
its August 16 resolution cutoff for unrelated packages and advances only these
four packages to the fixed October 29 cutoff. Runtime and CI remain separate
hash locks; the runtime image still installs only `requirements.lock`.

The relevant TLS advisory is
[GHSA-6q48-6c7w-9gw2](https://github.com/advisories/GHSA-6q48-6c7w-9gw2).
Its compromised emSign ECC Root CA - G3 is removed from the updated TLS trust
store. PyJWT 2.15.1 also rejects an empty HMAC JWK. The platform mount continues
to use its existing opaque Core-token authority with legacy JWT auth disabled;
this checkpoint changes no authentication or mount application code.

No deployment, registry/image publication, live configuration, secret access,
workflow enablement, feature activation, resource change or chart-data operation
is included. Core and Scribe source remain outside this checkpoint. Schema and
cross-repository contracts are N/A: this change alters neither.

## Local verification

The official compiler's `--check` reproduces both locks. The hash-checked
Python 3.13 install and package compatibility check pass for 46 runtime/CI
packages. Focused offline TLS, client, authentication, mount, pipeline and labs
tests pass (299 tests); the final TLS-test refinement passes both security
regressions. Both regressions fail against the previous installed packages,
showing the old trusted root and accepted empty HMAC JWK. An OSV scan of all
46 updated runtime/CI pins returns zero findings. Full Ruff, shell syntax and
diff checks pass. Exact-commit independent review remains required after the
final substantive push.

## Rebuild hold and recovery

The earlier `66bba1e6` candidate contains the affected runtime lock. Existing
archive/OCI identity evidence proves its identity, not security readiness. Keep
provider production activation held until a separately authorized build creates
a new immutable Linux/amd64 image from the reviewed, merged security-fix source,
with fresh source/digest identity evidence and runtime TLS/package checks.
This source checkpoint performs no build or promotion and selects no live
baseline or recovery image.

If the updated libraries cause a compatibility failure, retain the activation
hold and fix forward with the secure pins. A future live recovery may use only
a compatible retained image whose installed versions and TLS bundle are verified
to exclude these findings; otherwise prepare a corrected image retaining the
previous application behavior and the secure dependency versions. Do not restore
an image containing the compromised root or affected security pins. A source
revert alone is not permission to build or promote the unsafe old lock. There is
no schema, stored-data or infrastructure cleanup to perform as recovery.
