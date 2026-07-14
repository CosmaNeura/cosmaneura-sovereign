"""
Audit logging.

HIPAA/SOC 2 require a defensible record of who did what, when. Anthropic's BAA
explicitly does NOT cover audit logging of prompts/responses — that is the
customer's responsibility, which is why it lives here.

Design rules baked in:
  * The audit record stores metadata and a content HASH, never raw content,
    so the log itself is not a new PHI store.
  * `audit_sink="stdout"` for local dev; point at an append-only,
    access-controlled store (e.g. WORM bucket, or a write-only DB table) in
    production and retain per the compliance profile.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import ComplianceProfile, Settings, get_settings


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class AuditLogger:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self._lock = threading.Lock()
        self._prev_chain = "0" * 64  # genesis value for the tamper-evident chain

    def record(
        self,
        *,
        trace_id: str,
        profile: ComplianceProfile,
        event: str,
        details: dict[str, Any] | None = None,
        content_for_hash: Optional[str] = None,
    ) -> None:
        if not profile.audit_required:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "persona": profile.persona.value,
            "hipaa": profile.hipaa,
            "event": event,
            "details": details or {},
        }
        if content_for_hash is not None:
            entry["content_sha256_16"] = _hash(content_for_hash)

        # Serialize and write under a lock so the hash chain stays consistent
        # under concurrency. Each record commits to the previous one via
        # `prev_chain`; a verifier can recompute the chain to detect tampering
        # or deletion. (In production, pair this with an append-only/WORM store.)
        with self._lock:
            entry["prev_chain"] = self._prev_chain
            body = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            chain = hashlib.sha256((self._prev_chain + body).encode("utf-8")).hexdigest()
            entry["chain"] = chain
            line = json.dumps(entry, ensure_ascii=False, sort_keys=True)

            if self.s.audit_sink == "file":
                # Create with owner-only permissions (0600) on first write.
                if not os.path.exists(self.s.audit_path):
                    fd = os.open(self.s.audit_path,
                                 os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
                    os.close(fd)
                with open(self.s.audit_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            else:
                print(f"[AUDIT] {line}", file=sys.stderr)

            self._prev_chain = chain
