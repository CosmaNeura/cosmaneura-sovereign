"""Finite value-free frames emitted by the approved actual-turn binding module.

This grammar validates review task input, never authority or conformance. A
changed module frame fails closed until this internal contract is reviewed.
"""
from __future__ import annotations

_PREFIX = (
    'ACTUAL DISPLAYED RECORD-QUOTATION REVIEW CONTEXT. This frame describes a particular '
    'canonical answer, not a policy for all possible future excerpts. The server '
    'validated whole source-section membership and rendered literal quotes with source '
    'attribution and fixed scope notices. This structural validation does not establish '
    'clinical accuracy, semantic completeness, provider authority or moral conformance. '
    'Within the covered inference boundary, the semantic selector assessed the complete '
    'actual question and the whole enumerated server-supplied reviewed-source context. '
    'Its observations are model interpretations, not a doctrinal verdict or certification '
    'that moral risk is absent. The actual question was interpreted as retrieval of past '
    'documentation, rather than a request for new clinical advice, endorsement or '
    'execution. The supplied source contexts were interpreted as historical documentary '
    'material. The selected passages were interpreted as '
)

_SUFFIX = (
    'An instruction recorded in a source is displayed as attributed prior documentation, '
    'not an assistant-authored instruction to carry it out. The renderer adds no '
    'generated clinical recommendation. The scope notices warn that quotations establish '
    'neither the current regimen nor an outstanding-order ledger nor exhaustive answer '
    'coverage. The context is limited to the bounded selected reviewed notes, not the '
    'entire chart. All patient-specific text, values, dates, findings and source handles '
    'are withheld. Review the moral use and posture of this represented particular '
    'answer, including whether quotation functions as endorsement or practical '
    'assistance. Do not certify every future excerpt or infer moral permissibility from a '
    'negative keyword screen. When this represented context is insufficient for that '
    'judgment, refuse rather than guess. Clinical accuracy and medical-document editing '
    'remain outside the task.'
)

_FRAMES = frozenset(_PREFIX + posture + _SUFFIX for posture in (
    "historical descriptions. ",
    "historical descriptions or source-authored historical instructions. ",
))


def is_actual_record_review_frame(value: object) -> bool:
    return type(value) is str and value in _FRAMES
