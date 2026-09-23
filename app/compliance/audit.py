"""
Audit logging.

HIPAA/SOC 2 require a defensible record of who did what, when. Anthropic's BAA
explicitly does NOT cover audit logging of prompts/responses — that is the
customer's responsibility, which is why it lives here.

Design rules baked in:
  * The audit record stores metadata and a content HASH, never raw content,
    so the log itself is not a new raw-content store.
  * With the provider platform boundary enabled, this module emits only
    value-free operational breadcrumbs; the platform owns durable ingestion.
  * The legacy local chain is for offline/dev compatibility only. It does not
    establish independent integrity across process restarts or replicas.
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
        if not self.s.platform_agent_api_enabled:
            self._lock = threading.Lock()
            self._prev_chain = "0" * 64  # legacy process-local continuity only

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
        if self.s.platform_agent_api_enabled:
            # Platform mode moves durable ownership to the platform. Keep only a
            # value-free operational breadcrumb here; no content, details,
            # local file, chain head, or integrity claim survives the
            # request process. The request-scoped Agent API writer records the
            # durable observation after the turn is resolved.
            line = json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "trace_id": trace_id,
                    "persona": profile.persona.value,
                    "event": event,
                    "durable_owner": "platform",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            print(f"[AUDIT-OP] {line}", file=sys.stderr)
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

        # Legacy offline/dev sink. The lock preserves continuity only within
        # this process; restarts and replicas begin independent chains, so this
        # has no production integrity claim.
        if not hasattr(self, "_lock") or not hasattr(self, "_prev_chain"):
            raise RuntimeError("Audit sink configuration changed after initialization")
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
