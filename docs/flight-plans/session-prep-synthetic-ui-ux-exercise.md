# Session Prep synthetic UI/UX evidence exercise

## Purpose and authority boundary

This exercise produces **synthetic UX evidence only** for the local Session
Prep demo. It does not contact a live service, deploy software, change
configuration, or authorize staging, Auth/IAM, operational controls, a real
provider, production, or PHI. The result is a source-bound, unauthenticated
operator attestation. It does not prove who performed the exercise or that the
human observations occurred.

The receipt is closed metadata. It has no field for an observer name, patient
or provider identifier, record reference, note, clinical text, screenshot,
URL, credential, free-form comment, or attachment. Its variable fields are a
canonical UTC timestamp, two bounded random exercise tokens, a numeric browser
major, and closed browser/screen-reader enums.

The v1 verifier pins:

- Sovereign commit:
  `e0736289f36b200a3daee59db3732482bab6fc5d`
- Sovereign tree: `3391bc15f8a110ccac55d39668ba9f6c4eb9c175`
- Demo asset SHA-256:
  `sha256:f7b0c7f5cda44cb4a8a38e963c767c8d19da8d09aa351b2f54e80535c3a052c7`
- Source-manifest SHA-256:
  `sha256:8bcae9ab57f5b2d301e4d8b2e2ffdbfb373d6f7c556a84b76ad2686bd59f8152`

The manifest covers `app/panel.js`, `tools/session_prep_demo.html`,
`tools/run_session_prep_demo.py`, `tests/session_prep_demo_ui_harness.js`, and
`tests/panel_contract_harness.js`. The verifier reads those paths from the
exact local Git commit with replacement objects and lazy fetching disabled,
checks every digest, and runs both Node harnesses against temporary copies of
the exact objects. It never uses mutable working-tree bytes or an HTTP client.

## Before the exercise

Use a clean local checkout containing the exact commit above. Do not use a
deployed URL. Do not sign in. Do not paste, transcribe, photograph, or import
provider, patient, appointment, note, transcript, credential, or other real
data. Stop if the surface is not loopback-only or anything other than the
bundled synthetic fixture appears.

Run the existing offline checks, then start the loopback demo:

```bash
PYTHONPATH=. python tools/run_session_prep_demo.py --self-test
node tests/session_prep_demo_ui_harness.js tools/session_prep_demo.html
node tests/panel_contract_harness.js app/panel.js
PYTHONPATH=. python tools/run_session_prep_demo.py --port 8765
```

Open only `http://127.0.0.1:8765/demo/session-prep`. Keep the browser network
panel and storage inspector open. A failed, partial, ambiguous, or unobserved
manual check is not `pass`: stop without creating a receipt and record the
defect in the normal synthetic issue workflow. Never add explanatory text to
the JSON.

Immediately before starting, generate a fresh `uxrun_` exercise ID and
`uxchallenge_` challenge, each followed by 32 lowercase hexadecimal
characters. Keep them only in the local synthetic evidence record. They bind
the receipt fields to one declared exercise but are not signatures, identity
proof, or proof that the browser actions occurred. Finish validation within 24
hours of `observed_at_utc`; future timestamps are rejected without clock-skew
allowance.

## Manual observations

Exercise all four fixture controls at 390 x 844 CSS px and 1440 x 900 CSS px,
both at 100% browser zoom.

- `ready`: confirm the heading/status, bounded brief, and source count are
  understandable. No raw source or internal reference may appear.
- `refreshing`: confirm the stale state is unmistakable, only the brief from
  the current typed response is shown, and Refresh never claims fresh data.
- `pending`: confirm prior brief content disappears and the waiting state is
  understandable.
- `unavailable`: confirm prior brief content disappears and this state is
  distinct from both an error and an empty ready response.
- At both sizes, confirm the layout is readable, controls remain visible and
  operable, and there is no horizontal overflow.
- Using keyboard only, confirm logical focus order, visible focus, operable
  actions, and sensible focus preservation or rehoming after state changes.
- With the receipt's named screen reader, confirm named headings and controls,
  announced state changes, and announced busy state.
- Visually confirm that the rendered surface contains only the bounded typed
  projection and no raw source or internal reference.
- At both viewports, use **Switch synthetic record**. Confirm the prior content
  and trace clear immediately, transition status is announced, unsafe controls
  stay disabled during the switch, focus is preserved or sensibly rehomed, and
  only the new record's state can render.
- At both viewports, use **Revoke synthetic access**. Confirm prepared content
  and trace clear, the closed status is announced, state and switch controls
  disable, Reset enables, and focus is preserved or sensibly rehomed.
- At both viewports, use **Reset synthetic access**. Confirm the status remains
  explicitly local, content and trace remain clear, state/switch controls are
  restored, Reset disables, and focus is preserved or sensibly rehomed.

During verification, the exact pinned harnesses independently support record
and encounter isolation, late-response suppression, revocation/reset, request
minimization, generic-chat isolation, DOM projection, absence of panel
persistence calls, and loopback-only demo requests. Encounter switching has no
manual demo control, so it is automated support only. The demo intentionally
contains synthetic authority/context values; the boundary is that no real
credential, provider/patient data, raw note, or server-owned Session Prep
handle leaks across the tested surfaces.

## Closed receipt format

The JSON below is an intentionally non-acceptable, non-PHI schema specimen.
Its placeholders and zero browser version must fail validation. It is not
evidence that an exercise occurred and must not be reused as one. Create a new
local receipt only after every manual item passes. Replace `observed_at_utc`,
`exercise_id`, `challenge`, `browser_engine`, `browser_major`, and
`screen_reader` with the actual bounded metadata:

- timestamp: canonical UTC seconds (`YYYY-MM-DDTHH:MM:SSZ`), after the pinned
  commit, no more than 24 hours old, and not even one second in the future;
- exercise ID: `uxrun_` plus 32 fresh lowercase hexadecimal characters;
- challenge: `uxchallenge_` plus 32 independently generated lowercase
  hexadecimal characters;
- browser engine: `chromium`, `firefox`, or `webkit`;
- browser major: an integer from 1 through 999;
- screen reader: `jaws`, `nvda`, `orca`, `talkback`, or `voiceover`.

Every other field and value is fixed. Unknown, missing, non-pass, true
readiness, duplicate, or type-coerced values are rejected.

```json
{
  "evidence": {
    "schema": "cosmaneura.session-prep.synthetic-ui-ux-evidence.v1",
    "observed_at_utc": "<YYYY-MM-DDTHH:MM:SSZ>",
    "exercise_id": "<uxrun_ plus 32 lowercase hex characters>",
    "challenge": "<uxchallenge_ plus 32 lowercase hex characters>",
    "environment": {
      "browser_engine": "<chromium, firefox, or webkit>",
      "browser_major": 0,
      "screen_reader": "<jaws, nvda, orca, talkback, or voiceover>"
    },
    "scope": {
      "surface": "bundled-loopback-demo",
      "data_class": "synthetic-only",
      "manual_observation_completed": true,
      "network_scope": "loopback-only",
      "live_service_contacted": false
    },
    "source": {
      "sovereign_commit": "e0736289f36b200a3daee59db3732482bab6fc5d",
      "sovereign_tree": "3391bc15f8a110ccac55d39668ba9f6c4eb9c175",
      "demo_asset_path": "tools/session_prep_demo.html",
      "demo_asset_sha256": "sha256:f7b0c7f5cda44cb4a8a38e963c767c8d19da8d09aa351b2f54e80535c3a052c7",
      "source_manifest_sha256": "sha256:8bcae9ab57f5b2d301e4d8b2e2ffdbfb373d6f7c556a84b76ad2686bd59f8152"
    },
    "manual_observations": {
      "states": {
        "ready": {
          "response_state": "ready",
          "heading_and_status": "pass",
          "bounded_brief_readable": "pass",
          "source_count_understandable": "pass"
        },
        "refreshing": {
          "response_state": "refreshing",
          "heading_and_status": "pass",
          "stale_label_unambiguous": "pass",
          "returned_brief_only": "pass",
          "refresh_does_not_claim_freshness": "pass"
        },
        "pending": {
          "response_state": "pending",
          "heading_and_status": "pass",
          "prior_brief_cleared": "pass",
          "waiting_state_understandable": "pass"
        },
        "unavailable": {
          "response_state": "unavailable",
          "heading_and_status": "pass",
          "prior_brief_cleared": "pass",
          "distinct_from_error_or_empty_ready": "pass"
        }
      },
      "viewports": {
        "narrow": {
          "width_css_px": 390,
          "height_css_px": 844,
          "zoom_percent": 100,
          "layout_readable": "pass",
          "controls_visible_and_operable": "pass",
          "no_horizontal_overflow": "pass"
        },
        "wide": {
          "width_css_px": 1440,
          "height_css_px": 900,
          "zoom_percent": 100,
          "layout_readable": "pass",
          "controls_visible_and_operable": "pass",
          "no_horizontal_overflow": "pass"
        }
      },
      "accessibility": {
        "keyboard": {
          "logical_focus_order": "pass",
          "visible_focus": "pass",
          "all_actions_operable": "pass",
          "focus_preserved_or_rehomed": "pass"
        },
        "screen_reader": {
          "headings_and_controls_named": "pass",
          "state_changes_announced": "pass",
          "busy_state_announced": "pass"
        }
      },
      "visual_privacy": {
        "bounded_typed_projection_only": "pass",
        "raw_source_and_internal_refs_absent": "pass"
      },
      "host_controls": {
        "viewports": "narrow-and-wide",
        "record_switch": {
          "prior_content_cleared": "pass",
          "prior_trace_cleared": "pass",
          "transition_status_announced": "pass",
          "controls_disabled_while_switching": "pass",
          "focus_preserved_or_rehomed": "pass",
          "new_record_state_only": "pass"
        },
        "revocation": {
          "content_and_trace_cleared": "pass",
          "closed_status_announced": "pass",
          "typed_and_switch_controls_disabled": "pass",
          "reset_control_enabled": "pass",
          "focus_preserved_or_rehomed": "pass"
        },
        "reset": {
          "local_only_status_announced": "pass",
          "content_and_trace_remain_clear": "pass",
          "typed_and_switch_controls_restored": "pass",
          "reset_control_disabled": "pass",
          "focus_preserved_or_rehomed": "pass"
        }
      }
    },
    "automated_support": {
      "harnesses": {
        "demo_controller": "pass",
        "panel_contract": "pass"
      },
      "transitions": {
        "record_switch_clear_and_isolation": "pass",
        "encounter_switch_clear_and_isolation": "pass",
        "late_response_suppression": "pass",
        "revocation_and_reset": "pass"
      },
      "privacy": {
        "session_prep_body_client_turn_only": "pass",
        "session_prep_handle_absent_from_url_and_generic_chat": "pass",
        "dom_has_no_handle_raw_source_or_internal_ref": "pass",
        "local_storage_unused": "pass",
        "session_storage_unused": "pass",
        "indexed_db_unused": "pass",
        "cache_storage_unused": "pass",
        "demo_requests_loopback_only": "pass"
      }
    },
    "readiness_claims": {
      "staging_ready": false,
      "auth_iam_ready": false,
      "durable_operational_controls_ready": false,
      "real_provider_ready": false,
      "production_ready": false,
      "phi_ready": false
    }
  },
  "evidence_sha256": "sha256:<recalculate after replacing placeholders>"
}
```

`evidence_sha256` is the SHA-256 of canonical JSON for the nested `evidence`
object (`ensure_ascii=true`, keys sorted, and separators `,` and `:` without
extra whitespace). Recalculate it after setting the six variable metadata
fields. This self-digest detects accidental mutation; it is not a signature or
authentication mechanism. Do not place the receipt or any value from it on a
command line.

Validate from the Sovereign repository:

```bash
PYTHONPATH=. python tools/verify_session_prep_ui_evidence.py \
  /absolute/path/to/synthetic-ui-evidence.json
```

Success emits one bounded line stating only that the receipt structure and
pinned automated support validated and that the operator attestation remains
unproven. Failure emits one bounded rejection line and exits nonzero. Invalid
arguments use the same rejection boundary. No path prints the receipt, its
path, Git or harness output, subprocess errors, or supplied values.

## Limitations and deferred gates

The validator proves that the receipt has the exact closed pass-only shape,
its self-digest is internally consistent, the pinned source objects match, and
the exact offline harnesses pass. The operator attestation is unauthenticated
and unproven: the digest, run ID, challenge, and timestamp cannot prove a human
performed the observations or that the browser was configured as claimed. It
also cannot prove that a deployed artifact matches this source. Retain an
accepted receipt through the normal evidence process; never mutate it into a
staging or release attestation.

This evidence does not establish staging deployment, Auth/IAM readiness,
durable operational controls, real-provider readiness, production readiness,
or permission to use PHI. Real-provider and PHI use remain prohibited until
the separate Auth/IAM and operational-control work is complete, independently
evidenced, and explicitly approved. No value in this receipt can promote any
of those gates.

## Failure and recovery

On failure, stop the exercise, preserve default-off controls, discard the
unaccepted receipt, and investigate with synthetic data only. Do not replace a
failed observation with `pass`, add a notes field, weaken the schema, or point
the verifier at a live endpoint. This change has no runtime or deployed state;
withdrawing it requires only reverting its documentation, verifier, and tests.
