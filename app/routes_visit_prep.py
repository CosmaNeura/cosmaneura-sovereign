"""Dormant Core-workload receiver: one protected topic, no browser model access."""

import asyncio
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.clients.llm import LLMClient
from app.clients.platform_usage import (
    activate_usage_context,
    deactivate_usage_context,
    successful_usage_refs,
)
from app.clients.visit_prep_clarification import ProtectedTopic, generate_clarification
from app.clients.visit_prep_patient_authority import PatientAuthorityClient
from app.config import get_settings
from app.security.auth import verify_visit_prep_core_workload

router = APIRouter()


@router.post("/service/v1/visit-prep/clarification", include_in_schema=False)
async def clarify_patient_topic(request: Request):
    headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    try:
        async with asyncio.timeout(25):
            settings = get_settings()
            if not settings.visit_prep_patient_clarification_enabled:
                raise ValueError
            if (
                request.scope.get("query_string")
                or request.headers.getlist("content-type") != ["application/json"]
                or request.headers.getlist("content-encoding")
                or len(request.headers.getlist("authorization")) != 1
                or len(request.headers.getlist("x-visit-prep-analysis-grant")) != 1
            ):
                raise ValueError
            auth = request.headers["authorization"]
            if not auth.startswith("Bearer "):
                raise ValueError
            await verify_visit_prep_core_workload(auth[7:], settings)
            grant = request.headers["x-visit-prep-analysis-grant"]
            if not 1 <= len(grant) <= 8192 or not grant.isascii():
                raise ValueError
            body = bytearray()
            async for part in request.stream():
                body.extend(part)
                if len(body) > 20000:
                    raise ValueError

            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError
                    result[key] = value
                return result

            topic = ProtectedTopic.model_validate_json(
                json.dumps(json.loads(body, object_pairs_hook=unique))
            )
            digest = hashlib.sha256(
                json.dumps(
                    topic.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            authority = PatientAuthorityClient(settings, grant=grant)
            subject, model = await authority.claim(digest)
            if model != settings.reasoning_model:
                raise ValueError
            activate_usage_context(turn=subject, client=authority)
            try:
                reply = await generate_clarification(topic, client=LLMClient(settings))
                return JSONResponse(
                    content={
                        "analysis_id": str(subject.client_turn_id),
                        "usage_ref": successful_usage_refs()[0],
                        "reply": reply.model_dump(mode="json"),
                    },
                    headers=headers,
                )
            finally:
                deactivate_usage_context()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"detail": "Visit Prep is unavailable."},
            headers=headers,
        )
