# Session Prep factual-answer availability repair

Tier 2: this repair touches provider answer verification and its read transport.
It retains the existing authorization, schema, record, turn, content, citation,
and expiry checks. All test material is fictional.

The provider response emitted suppression names in construction order while
Core emitted the same set in sorted order. The pinned SDK compares serialized
presentation objects. In addition, `/embed/history-timed` was absent from the
middleware's integration-version response-header paths, so the SDK rejected
otherwise valid timed reads.

The provider now emits canonical suppression order for every presentation mode
and includes the existing version header on timed reads. These two server
changes work with Core's existing pinned SDK; no Core source, asset pin,
configuration, database, or Scribe image change is required. The readable panel
also compares valid closed presentations by their fields and suppression set,
and its matching delivery artifact is regenerated with the pinned compiler.

Validation covers every valid suppression set, unchanged wire property order,
actual public SDK acceptance and fresh export with reordered keys/members,
rejection of changed or malformed presentation values, and timed HTTP response
headers with the existing missing/unsupported-version and authorization bounds.
The current Core-pinned SDK is separately exercised with canonical provider
wire output and Core's source serializer through factual acceptance, dated
export, and read-only Check response recovery without inference replay.

Release sequence: independently review the final provider head, normally merge
the provider repair, then use the existing authorized provider rollout process.
This local patch performs no publication or deployment. Keep Core and Scribe
images stable. Recovery is to restore the previously verified provider revision
and repeat its readiness checks; do not change records or cached answer data.
