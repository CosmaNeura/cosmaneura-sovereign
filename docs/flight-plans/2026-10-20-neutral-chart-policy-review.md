# Neutral chart-excerpt doctrinal review

Risk: Tier 2, provider/PHI path. Owner: Session Prep engineering. This is a
receiver-only fix on top of the reviewed excerpt-mode implementation. No
Core contract, schema, configuration, dependency or capability changes.

## Observed problem

Synthetic staging acceptance on the previous synthesis release asked:

> Were potassium or creatinine ordered or resulted in the reviewed notes?
> Use the October 8 correction.

The clinical response was replaced by Magisterium's topic-scope refusal:

> Magisterium AI focuses on Catholic teaching and related topics, so I can’t
> assess or revise this medical-document wording.

That is not a clinical answer or a conformance judgment. The excerpt-mode
release correctly blocks nonconforming review results, but feeding routine
medical wording to a doctrine-only service can still cause this refusal.

## Narrow behavior

Only machine-validated ENGAGEMENT excerpts backed by ready full reviewed
sources may use the new fixed review projection. It asks about the Catholic
conformance of neutral, dated historical quotation rather than requesting
clinical editing. The assistant adds no clinical diagnosis, recommendation or
order; the response quotes source sections, attributes historical records and
states that it does not prove current medication/order status or completeness.

No patient text, numerical or qualitative chart values, dates, private handles
or medication/lab terms enter this fixed projection. It still passes through
the existing de-identification gate and Magisterium review. NONE sensitivity
is raised to LOW so this path cannot skip the review call.

HIGH routing, ethics/preparation synthesis, summary-only/free-prose answers,
invalid selections and any moral topic detected in the question, response or
entire selected source set retain the existing full deidentified review. The
source scan includes sections the evidence selector did not quote. Its
conservative vocabulary is a veto, not a complete moral classifier; HIGH
routing always retains full review independently of that vocabulary.

Explicit reviewer scope refusals fail closed before verdict parsing, even if
the parser would return a positive flag. This is only a negative guard and
never creates conformance. Other doctrinal assessments keep their existing
parsing/revision behavior. A refusal or nonconformance still blocks excerpt
publication, removes chart citations and records an unavailable canonical
outcome. Emergency, clinical authority, persistence and final source-freshness
checks remain unchanged.

## Validation and rollout

`tests/test_record_policy_review.py` reproduces the failed question with
synthetic correction evidence and covers fixed-projection non-leakage,
minimum sensitivity, moral/HIGH/source vetoes, untouched synthesis/ethics,
invalid selections, refusals with erroneous positive parser flags and genuine
doctrinal revisions. The focused provider/evidence/labs/ethics/conversation/
emergency/mount/panel/facts suite passes 509 tests on the consumer-71 stack.
Ruff and diff checks pass.

These are deterministic regressions, not proof that the live reviewer will
always accept the clarified task. After the exact reviewed commit is merged
and deployed with excerpt mode, repeat the same synthetic staging question
and confirm the actual reviewer result and canonical answer. Do not describe
the live refusal as fixed until that replay succeeds.

Independent review must cover the exact final commit before merge. Reconcile
the deployed Sovereign revision before deployment. This fix is stacked on
consumer commit `671419476064b160796496e34d7b29f197696710` and retains its
exact panel blob `e84200e7892a6c7b7b6bad3ff1fe3e3e5c2c65ab`. A later Core
pin can reference the combined final reviewed Sovereign commit; this fix does
not modify Core's current pin or a previously deployed source pair.

Recovery: revert this fix to restore the previous review input. Preparation
artifacts, receipt digests and database state require no rollback or cleanup.
Production promotion is outside this change.
