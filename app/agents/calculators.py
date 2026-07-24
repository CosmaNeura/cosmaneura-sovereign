"""
Deterministic clinical calculators for the plan workbench.

THE RULE: Python computes, the LLM narrates. This exists because of a direct
user failure report: a nutritionist asked consumer AI for a 1,500–1,600 kcal
meal plan and got 1,400s — twice. One wrong number destroys trust in
everything else, so any calories/macros/BMI the workbench states are computed
here and injected into the prompt as authoritative values the model may quote
but never recompute.

Pure functions, no I/O, defensive parsing: bad or missing inputs yield {} /
None rather than exceptions — the agent degrades to "numbers unavailable",
never to invented numbers.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# kcal per gram, standard Atwater factors.
_KCAL_PROTEIN = 4
_KCAL_CARB = 4
_KCAL_FAT = 9


def _to_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def bmi(*, weight_lb: Any = None, height_in: Any = None,
        weight_kg: Any = None, height_cm: Any = None) -> Optional[float]:
    """BMI from US or metric units; None if inputs are missing/invalid."""
    lb, inch = _to_float(weight_lb), _to_float(height_in)
    if lb and inch:
        return round(703.0 * lb / (inch * inch), 1)
    kg, cm = _to_float(weight_kg), _to_float(height_cm)
    if kg and cm:
        m = cm / 100.0
        return round(kg / (m * m), 1)
    return None


def mifflin_st_jeor(*, sex: Any, age: Any, weight_lb: Any = None,
                    height_in: Any = None, weight_kg: Any = None,
                    height_cm: Any = None) -> Optional[int]:
    """Resting energy expenditure (BMR), kcal/day. None on bad input."""
    kg = _to_float(weight_kg) or (
        _to_float(weight_lb) * 0.453592 if _to_float(weight_lb) else None)
    cm = _to_float(height_cm) or (
        _to_float(height_in) * 2.54 if _to_float(height_in) else None)
    yrs = _to_float(age)
    s = str(sex or "").strip().lower()
    if not (kg and cm and yrs) or s[:1] not in ("m", "f"):
        return None
    base = 10.0 * kg + 6.25 * cm - 5.0 * yrs
    return int(round(base + (5 if s.startswith("m") else -161)))


def parse_calorie_target(target: Any) -> Optional[tuple[int, int]]:
    """'1500-1600', '1500–1600', or '1550' -> (min, max). None if unparseable."""
    nums = re.findall(r"\d{3,5}", str(target or ""))
    if not nums:
        return None
    lo = int(nums[0])
    hi = int(nums[1]) if len(nums) > 1 else lo
    if lo > hi:
        lo, hi = hi, lo
    return (lo, hi)


def macro_grams(calories: int, *, protein_pct: int = 30, carb_pct: int = 40,
                fat_pct: int = 30) -> Optional[dict]:
    """Gram targets for a calorie level. None if the split isn't 100%."""
    if protein_pct + carb_pct + fat_pct != 100 or calories <= 0:
        return None
    return {
        "calories": calories,
        "protein_g": round(calories * protein_pct / 100 / _KCAL_PROTEIN),
        "carbs_g": round(calories * carb_pct / 100 / _KCAL_CARB),
        "fat_g": round(calories * fat_pct / 100 / _KCAL_FAT),
        "split": f"{protein_pct}/{carb_pct}/{fat_pct}",
    }


def within_target(value: Any, target: Any) -> Optional[bool]:
    """Does a calorie value honor the clinician's stated range? (Sandy's bug.)"""
    rng = parse_calorie_target(target)
    v = _to_float(value)
    if rng is None or v is None:
        return None
    return rng[0] <= v <= rng[1]


def compute(metrics: dict | None, calorie_target: Any = None,
            macro_split: Any = None) -> dict:
    """
    Everything the workbench can state numerically, computed in one place.
    `metrics`: {sex, age, weight_lb|weight_kg, height_in|height_cm}.
    `macro_split`: '30/40/30'-style string, protein/carb/fat.
    Returns {} when nothing is computable — the agent then says so instead of
    estimating.
    """
    metrics = metrics if isinstance(metrics, dict) else {}
    out: dict = {}

    b = bmi(weight_lb=metrics.get("weight_lb"), height_in=metrics.get("height_in"),
            weight_kg=metrics.get("weight_kg"), height_cm=metrics.get("height_cm"))
    if b is not None:
        out["bmi"] = b
    r = mifflin_st_jeor(sex=metrics.get("sex"), age=metrics.get("age"),
                        weight_lb=metrics.get("weight_lb"),
                        height_in=metrics.get("height_in"),
                        weight_kg=metrics.get("weight_kg"),
                        height_cm=metrics.get("height_cm"))
    if r is not None:
        out["bmr_kcal_day"] = r

    rng = parse_calorie_target(calorie_target)
    if rng:
        out["calorie_target"] = {"min": rng[0], "max": rng[1],
                                 "source": "clinician-set"}
        split = re.findall(r"\d{1,2}", str(macro_split or ""))
        pcts = ([int(x) for x in split[:3]] if len(split) >= 3 else [30, 40, 30])
        mid = (rng[0] + rng[1]) // 2
        m = macro_grams(mid, protein_pct=pcts[0], carb_pct=pcts[1],
                        fat_pct=pcts[2])
        if m:
            out["macros_at_target_midpoint"] = m
    return out
