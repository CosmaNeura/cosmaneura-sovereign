# Production provider compiled panel admission

Risk: Tier 2, production artifact admission. The existing image-only consumer
compared Core's served panel digest to readable `app/panel.js`, rejecting the
compiled delivery actually vendored by Core. It now compares to the digest of
`app/panel.delivery.js` after checking the closed delivery manifest, both local
file hashes, pinned compiler metadata/options, and the source commit/blob in
the reviewed checkout's history. Missing, stale, malformed or linked inputs
fail closed before the Core runtime check.

Authenticated producer/current-main admission, the exact Core runtime receipt,
production origin and image controls, dark configuration, and all pre/post
promotion checks remain required. No image, workflow, configuration, Core or
Scribe source changes; this repair authorizes no publication or deployment.

Validation covers compiled delivery acceptance, rejection of the readable-only
digest, malformed/stale provenance, and existing promotion/candidate contracts.
Recovery reverts this consumer repair; it changes no deployed state or records.
Independent review must bind the final repair commit before publication.
