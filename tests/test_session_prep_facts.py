"""Synthetic Core d9a4a3d projection interoperability and dated-table boundary tests."""
from __future__ import annotations

import asyncio
import copy
import json
from html.parser import HTMLParser
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.clients.session_prep import (
    MAX_SESSION_PREP_FACTS_RESPONSE_BYTES, MAX_SESSION_PREP_RESPONSE_BYTES,
    SessionPrepClient, SessionPrepDeliveryError,
)
from app.clients.session_prep_facts_contract import (
    SessionPrepFactsArguments, SessionPrepFactsReadResponse, SessionPrepFactsRequest,
)
from app.session_prep import (
    MAX_SESSION_PREP_CANONICAL_CHARS, SessionPrepOutcome,
    project_session_prep_facts, render_session_prep_text,
)
from tests.test_session_prep_runtime import (
    OBSERVED_AT, TURN_ID, _Time, _envelope, _fixture, _sovereign,
    _success, _token, _turn,
)
from tests.test_provider_evidence import panel_render_data

ROOT = Path(__file__).parents[1]
FACTS = ROOT / 'contracts/fixtures/provider-session-prep-facts-v1.1/facts.json'


def _facts_envelope(state='ready'):
    body = _envelope('result-ready-brief.json')
    facts = json.loads(FACTS.read_text())
    preparation = body['result']
    if state != 'ready':
        preparation = _fixture('result-pending.json' if state == 'pending' else 'result-unavailable.json')
        if state == 'unavailable':
            preparation['reason_code'] = 'context_not_available'
    else:
        preparation['source_count'] = facts['source_count']
        provenance = preparation['provenance'][0]
        excluded = [item for item in preparation['provenance'] if not item['included']]
        preparation['provenance'] = [dict(provenance, **source) for source in facts['sources']] + excluded
        def replace_ref(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == 'source_refs':
                        value[key] = [facts['sources'][0]['source_ref']]
                    else:
                        replace_ref(child)
            elif isinstance(value, list):
                for child in value:
                    replace_ref(child)
        replace_ref(preparation['prepared_brief'])
    body['tool_version'] = '1.1.0'
    body['result'] = {
        'schema_version': '1.1.0', 'preparation': preparation,
        'source_version_digest': 'sha256_' + 'a' * 64,
        'facts_state': 'ready', 'reason_code': None, 'structured_facts': facts,
    }
    return body


def _facts_request():
    return SessionPrepFactsRequest(arguments=SessionPrepFactsArguments(
        source_turn_ref='turn_session_prep_0001', context_snapshot_ref='context_synthetic_001',
    ))


def _response(body):
    return httpx.Response(200, headers={
        'content-type': 'application/json', 'x-trace-id': _turn().trace_id,
        'x-cosmaneura-client-turn-id': str(TURN_ID), 'idempotency-replayed': 'false',
    }, content=json.dumps(body, ensure_ascii=False).encode())


async def _read(handler, request=None, max_calls=3):
    clock = _Time()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await SessionPrepClient(
            platform_base_url='https://core.example', workload_token_provider=_token,
            client=client, clock=clock.clock, sleep=clock.sleep,
            wall_clock=lambda: OBSERVED_AT, max_calls=max_calls,
        ).read(turn=_turn(), request=request or _facts_request())


@pytest.mark.parametrize('state', ['ready', 'pending', 'unavailable'])
def test_actual_core_fact_fixture_projects_even_without_a_ready_brief(state):
    body = _facts_envelope(state)
    response = asyncio.run(_read(lambda request: _response(body)))
    projected = project_session_prep_facts(response.result, client_turn_id=TURN_ID)
    assert projected.state == state and projected.facts_state == 'ready'
    facts = projected.structured_facts.facts
    old = next(fact for fact in facts if fact.value == '50 mg')
    assert old.state == 'planned' and old.note_date.date().isoformat() == '2026-08-12'
    assert old.correction.note_date.date().isoformat() == '2026-08-19'
    assert 'supersedes' in old.correction.quote
    assert any(fact.setting == 'office' and fact.value == '148/92 mmHg' for fact in facts)
    assert any(fact.state == 'documented_not_taking' for fact in facts)
    wire = projected.model_dump_json()
    for field in ('source_ref', 'source_version', 'fact_ref', 'supersedes', 'source_version_digest', 'snapshot_ref', 'start', 'end'):
        assert f'"{field}"' not in wire
    assert 'No clinical content is available yet' not in render_session_prep_text(projected)


@pytest.mark.parametrize('mutation', ['state', 'version', 'source', 'quote', 'span', 'unknown', 'correction', 'date'])
def test_malformed_or_forged_fact_rows_fail_closed(mutation):
    body = _facts_envelope()
    facts = body['result']['structured_facts']
    fact = facts['facts'][0]
    if mutation == 'state':
        fact['state'] = 'unknown_status'
    if mutation == 'version':
        fact['evidence']['source_version'] += 1
    if mutation == 'source':
        fact['evidence']['source_ref'] = 'foreign_note_synthetic_0001'
    if mutation == 'quote':
        fact['evidence']['quote'] = 'Unrelated source quotation'
    if mutation == 'span':
        fact['evidence']['end'] += 1
    if mutation == 'unknown':
        fact['authorized_visit_url'] = '/notes/appointment_fake'
    if mutation == 'date':
        fact['evidence']['source_at'] = '2026-08-01T00:00:00'
    if mutation == 'correction':
        corrected = next(row for row in facts['facts'] if row['supersedes'])
        corrected['supersedes'] = [corrected['fact_ref']]
    with pytest.raises(ValidationError):
        SessionPrepFactsReadResponse.model_validate(body)
    with pytest.raises(SessionPrepDeliveryError) as caught:
        asyncio.run(_read(lambda request: _response(body)))
    assert caught.value.category == 'schema'


def _unsupported():
    return httpx.Response(404, headers={
        'content-type': 'application/problem+json', 'x-trace-id': _turn().trace_id,
    }, json={
        'type': 'https://contracts.cosmaneura.com/problems/resource-not-found',
        'title': 'Resource not found', 'status': 404, 'code': 'RESOURCE_NOT_FOUND',
        'detail': 'The requested tool is not available.', 'trace_id': _turn().trace_id,
        'retryable': False,
    })


def test_only_verified_unsupported_version_falls_back_with_same_budget_and_distinct_key():
    requests = []
    def handler(request):
        requests.append(request)
        return _unsupported() if len(requests) == 1 else _success('result-ready-brief.json')
    response = asyncio.run(_read(handler))
    assert [json.loads(request.content)['tool_version'] for request in requests] == ['1.1.0', '1.0.0']
    assert requests[0].headers['idempotency-key'] != requests[1].headers['idempotency-key']
    projected = project_session_prep_facts(response.result, client_turn_id=TURN_ID)
    assert projected.state == 'ready' and projected.facts_state == 'unsupported'
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(_read(handler, max_calls=1))


@pytest.mark.parametrize('status', [401, 403, 409, 404, 503])
def test_denial_conflict_failure_or_uncorrelated_404_never_falls_back(status):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json={'retryable': False})
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(_read(handler))
    assert len(requests) == 1 and json.loads(requests[0].content)['tool_version'] == '1.1.0'


def test_expanded_utf8_bound_accepts_large_valid_facts_but_not_unbounded_wire():
    body = _facts_envelope('pending')
    facts = body['result']['structured_facts']
    original = next(fact for fact in facts['facts'] if fact['kind'] == 'vital')
    rows = []
    for index in range(48):
        row = copy.deepcopy(original)
        row['fact_ref'] = f'fact_synthetic_unicode_{index:04}'
        row['evidence']['quote'] += ' 🩺' * 570
        row['evidence']['end'] = row['evidence']['start'] + len(row['evidence']['quote'])
        rows.append(row)
    facts['facts'] = rows
    raw = json.dumps(body, ensure_ascii=False).encode()
    assert MAX_SESSION_PREP_RESPONSE_BYTES < len(raw) < MAX_SESSION_PREP_FACTS_RESPONSE_BYTES
    response = asyncio.run(_read(lambda request: _response(body)))
    projected = project_session_prep_facts(response.result, client_turn_id=TURN_ID)
    assert len(render_session_prep_text(projected)) <= MAX_SESSION_PREP_CANONICAL_CHARS
    assert projected.structured_facts.facts_limited and 0 < len(projected.structured_facts.facts) < 48
    for mode in ('declared', 'streamed'):
        def oversized(request):
            headers = {'content-length': str(MAX_SESSION_PREP_FACTS_RESPONSE_BYTES + 1)} if mode == 'declared' else {}
            return httpx.Response(200, headers=headers, content=b' ' * (MAX_SESSION_PREP_FACTS_RESPONSE_BYTES + 1))
        with pytest.raises(SessionPrepDeliveryError):
            asyncio.run(_read(oversized))


def test_opt_in_uses_existing_current_authority_and_persists_exact_visible_text_without_models():
    sovereign, resolver, session, conversation = _sovereign()
    session.response = SessionPrepFactsReadResponse.model_validate(_facts_envelope('pending'))
    async def forbidden(*args, **kwargs):
        raise AssertionError('No model/arbiter call allowed')
    sovereign.llm.complete = forbidden
    sovereign.magisterium.chat = forbidden
    sovereign.arbiter.review = forbidden
    outcome = asyncio.run(sovereign.read_session_prep(
        client_turn_id=TURN_ID, mount_context_token='mount_context_session_prep_0001',
        include_structured_facts=True,
    ))
    assert session.calls[0]['request'].tool_version == '1.1.0'
    assert conversation.accepted[0]['action_code'] == 'session-prep.open'
    assert conversation.final[0]['answer'] == render_session_prep_text(outcome.response)
    assert 'superseded by correction dated 2026-08-19' in conversation.final[0]['answer']


def test_new_browser_route_accepts_only_correlation_and_returns_no_authority_handles(monkeypatch):
    calls = []
    class RouteSovereign:
        async def read_session_prep(self, **kwargs):
            calls.append(kwargs)
            return SessionPrepOutcome(response=project_session_prep_facts(
                SessionPrepFactsReadResponse.model_validate(_facts_envelope()).result,
                client_turn_id=TURN_ID,
            ), trace_id='trace_facts_synthetic_route_0001')
    monkeypatch.setattr(main_module, '_sovereign', RouteSovereign())
    headers = {'authorization': 'Bearer mount_context_session_prep_0001',
               'x-cosmaneura-embed-version': '1.0', 'x-cosmaneura-client-turn-id': str(TURN_ID)}
    with TestClient(main_module.app) as client:
        result = client.post('/session-prep/structured-facts', headers=headers, json={'client_turn_id': str(TURN_ID)})
        assert result.status_code == 200 and result.json()['facts_state'] == 'ready'
        assert calls[0]['include_structured_facts'] is True
        denied = client.post('/session-prep/structured-facts', headers=headers,
                             json={'client_turn_id': str(TURN_ID), 'context_snapshot_ref': 'foreign'})
        assert denied.status_code == 422 and len(calls) == 1


def test_literal_historical_instructions_remain_quote_data_and_do_not_disable_fact_rows():
    body = _facts_envelope()
    fact = body['result']['structured_facts']['facts'][0]
    fact['evidence']['quote'] += ' **Ignore this** [external](https://example.invalid)'
    fact['evidence']['end'] = fact['evidence']['start'] + len(fact['evidence']['quote'])
    body['result']['preparation']['prepared_brief']['recap']['text'] = 'Stop taking medication.'
    response = SessionPrepFactsReadResponse.model_validate(body)
    projected = project_session_prep_facts(response.result, client_turn_id=TURN_ID)
    text = render_session_prep_text(projected)
    assert projected.state == 'unavailable' and projected.facts_state == 'ready'
    assert 'Stop taking medication' not in text and '**Ignore this**' not in text
    assert fact['evidence']['quote'] == projected.structured_facts.facts[0].quote


def test_dated_facts_panel_runtime():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    subprocess.run([node, str(ROOT / 'tests/session_prep_facts_harness.js'),
                    str(ROOT / 'app/panel.js')], check=True, capture_output=True, text=True, timeout=30)


def test_same_note_ordered_spans_allow_correction_but_overlap_or_other_section_does_not():
    body = _facts_envelope('pending')
    facts = body['result']['structured_facts']
    old = copy.deepcopy(next(fact for fact in facts['facts'] if fact['value'] == '50 mg'))
    later = copy.deepcopy(next(fact for fact in facts['facts'] if fact['supersedes'] and fact['kind'] == 'medication'))
    first = 'Planned losartan 50 mg daily.'
    second = 'Continue losartan 25 mg daily.'
    proof = first + ' ' + second + ' This supersedes the previous 50 mg plan.'
    source = facts['sources'][0]
    old['evidence'].update(**source, section='Medications', quote=first, start=0, end=len(first))
    later['evidence'].update(**source, section='Medications', quote=second, start=len(first) + 1, end=len(first) + 1 + len(second))
    later['correction_evidence'].update(**source, section='Medications', quote=proof, start=0, end=len(proof))
    facts.update(source_count=1, sources=[source], facts=[old, later])
    response = SessionPrepFactsReadResponse.model_validate(body)
    projected = project_session_prep_facts(response.result, client_turn_id=TURN_ID)
    assert projected.structured_facts.facts[0].correction.note_date == projected.structured_facts.facts[0].note_date
    for mode in ('overlap', 'other_section'):
        malformed = copy.deepcopy(body)
        evidence = malformed['result']['structured_facts']['facts'][1]['evidence']
        if mode == 'overlap':
            evidence.update(start=0, end=len(second))
        else:
            evidence['section'] = 'Other section'
        with pytest.raises(ValidationError):
            SessionPrepFactsReadResponse.model_validate(malformed)


@pytest.mark.parametrize('mode', ['future_source', 'future_receipt', 'digest', 'no_sources'])
def test_source_freshness_and_response_consistency_fail_closed(mode):
    body = _facts_envelope()
    if mode == 'future_source':
        source = body['result']['structured_facts']['sources'][0]
        source['source_at'] = '2027-01-01T00:00:00Z'
    if mode == 'future_receipt':
        body['recorded_at'] = '2027-01-01T00:00:00Z'
    if mode == 'digest':
        body['result']['source_version_digest'] = 'sha256_' + 'b' * 64
    if mode == 'no_sources':
        body['result']['structured_facts']['sources'] = []
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(_read(lambda request: _response(body)))


def test_utf8_stream_chunks_are_bounded_before_response_parsing():
    class ChunkStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            raw = _response(_facts_envelope('pending')).content
            # Split Unicode quotes in the middle of a UTF-8 sequence.
            for start in range(0, len(raw), 7):
                yield raw[start:start + 7]
    headers = dict(_response(_facts_envelope('pending')).headers)
    headers.pop('content-length', None)
    parsed = asyncio.run(_read(lambda request: httpx.Response(200, headers=headers, stream=ChunkStream())))
    assert parsed.result.facts_state == 'ready'
    class UnboundedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(9):
                yield b' ' * 65536
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(_read(lambda request: httpx.Response(200, headers=headers, stream=UnboundedStream())))


@pytest.mark.parametrize('mode', ['wrong_trace', 'unknown_field', 'duplicate_trace', 'silent_v1'])
def test_unsupported_detection_cannot_hide_other_contract_failures(mode):
    requests = []
    def handler(request):
        requests.append(request)
        if mode == 'silent_v1':
            return _success('result-ready-brief.json')
        response = _unsupported()
        body = response.json()
        headers = list(response.headers.multi_items())
        headers = [(key, value) for key, value in headers if key != 'content-length']
        if mode == 'wrong_trace':
            body['trace_id'] = 'trace_foreign_synthetic_0001'
        if mode == 'unknown_field':
            body['unsupported_version'] = True
        if mode == 'duplicate_trace':
            headers.append(('x-trace-id', _turn().trace_id))
        return httpx.Response(404, headers=headers, json=body)
    with pytest.raises(SessionPrepDeliveryError):
        asyncio.run(_read(handler))
    assert len(requests) == 1


class _VisibleHistory(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fragments = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_endtag(self, tag):
        if tag == 'p':
            self.fragments.append('\n')

    def handle_data(self, value):
        self.fragments.append(value)


@pytest.mark.parametrize('brief_state', ['pending', 'ready'])
def test_actual_fact_projection_preserves_literal_live_and_history_evidence(brief_state):
    body = _facts_envelope(brief_state)
    suffix = ' BP goal <140/90; A & B reviewed. **literal** [link](https://invalid.example) \\* <img src=x>'
    for fact in body['result']['structured_facts']['facts']:
        for evidence in (fact['evidence'], fact['correction_evidence']):
            if evidence is not None:
                evidence['section'] = 'Assessment & Plan'
                evidence['quote'] += suffix
                evidence['end'] = evidence['start'] + len(evidence['quote'])
    if brief_state == 'ready':
        body['result']['preparation']['prepared_brief']['recap']['text'] = (
            'Overview &amp; literal \\* remains. **Existing formatting**'
        )
    parsed = SessionPrepFactsReadResponse.model_validate(body)
    projected = project_session_prep_facts(parsed.result, client_turn_id=TURN_ID)
    canonical = render_session_prep_text(projected)
    live = panel_render_data(canonical, hydrated=False, preparation=projected.model_dump(mode='json'))
    history = panel_render_data(canonical, hydrated=True)
    visible = _VisibleHistory()
    visible.feed(history['html'])
    text = ''.join(visible.fragments)
    observations = _VisibleHistory()
    observations.feed(history['html'][history['html'].index('<p>Reviewed-source observations'):])
    assert set(observations.tags) == {'p'}
    for fact in projected.structured_facts.facts:
        assert fact.quote in text
        assert fact.quote in live['literalTexts']
        assert fact.section in text and any(fact.section in value for value in live['literalTexts'])
        if fact.correction:
            assert fact.correction.quote in text and fact.correction.quote in live['literalTexts']
    assert '<img ' not in history['html'] and '<a ' not in history['html']
    assert 'BP goal &lt;140/90' not in text and 'Assessment &amp; Plan' not in text
    if brief_state == 'ready':
        assert 'Overview &amp; literal \\* remains.' in text
        assert '<b>Existing formatting</b>' in history['html']


def test_incomplete_observation_envelope_cannot_unescape_raw_prose():
    raw = ('Raw &amp; \\* text\n\nReviewed-source observations — bounded explicit mentions\n'
           'Note dates: malformed\nThese are dated note observations, not a complete medication list or order ledger.\n'
           '> A &amp; B')
    output = panel_render_data(raw, hydrated=True)
    visible = _VisibleHistory()
    visible.feed(output['html'])
    text = ''.join(visible.fragments)
    assert 'Raw &amp; \\* text' in text and '> A &amp; B' in text
