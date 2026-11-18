"""Error taxonomy regression — structural fallback classifier.

Enforces the sealed Brent lesson in code: a threshold/range claim on a
live-market subject, minted without assumptions and without a mint-time
market probe, classifies as RANGE_STALE_AT_MINT — not UNKNOWN — when it
misses. UNKNOWN-class lessons are diagnostics by design and can never
promote; naming the cause is what unblocks the promotion track.
"""

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "chron_prediction", Path(__file__).resolve().parents[1] / "chron_prediction.py"
)
assert _spec is not None and _spec.loader is not None
cp = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("chron_prediction", cp)
_spec.loader.exec_module(cp)


def _pred(claim, assumptions=None, evidence=None):
    return {
        "claim": claim,
        "expected_outcome": "",
        "assumptions": assumptions or [],
        "evidence_snapshot": evidence or [],
    }


def test_brent_shape_classifies_range_stale():
    """Live case pred-a1210fe9e53c: 'Brent 65–75' minted while spot was 98.3."""
    p = _pred("Brent crude trades between USD65 and USD75 through Q3")
    assert cp._classify_error(p) == "RANGE_STALE_AT_MINT"


def test_fuel_threshold_classifies_range_stale():
    """Live case family: RON95 ≤ RM2.05 threshold windows."""
    p = _pred("RON95 retail price at RM2.05 or below")
    assert cp._classify_error(p) == "RANGE_STALE_AT_MINT"


def test_assumptions_present_still_assumption_error():
    p = _pred(
        "Brent crude trades between USD65 and USD75", assumptions=["spot < 80 at mint"]
    )
    assert cp._classify_error(p) == "ASSUMPTION_ERROR"


def test_mint_checked_range_not_stale():
    """Range claim WITH a mint-time market probe: staleness cause not established."""
    p = _pred(
        "Brent crude trades between USD60 and USD70",
        evidence=[
            {
                "evidence_class": "MARKET_PROBE",
                "source_ref": "yfinance:CL=F",
                "spot": 64.2,
            }
        ],
    )
    assert cp._classify_error(p) == "UNKNOWN"


def test_no_signal_stays_unknown():
    """Event-timing claim, no structural signal: honest floor, never forced."""
    p = _pred("The bill passes parliament this session")
    assert cp._classify_error(p) == "UNKNOWN"


def test_regex_gating_subject_required():
    """Range shape on a NON-market subject must not classify as range-stale."""
    p = _pred("deploy takes between 5 and 10 minutes")
    assert cp._classify_error(p) == "UNKNOWN"


# ── Coupling contract (chron_learn.py L74: class-without-rule is classic drift) ──


def test_range_stale_is_interpretable():
    """Classifier output must be in INTERPRETABLE_ERROR_TYPES or lessons park it."""
    assert "RANGE_STALE_AT_MINT" in cp.INTERPRETABLE_ERROR_TYPES


def test_every_interpretable_type_has_lesson_text():
    """INTERPRETABLE_ERROR_TYPES (chron_prediction) and _LESSON_TEXT keys
    (chron_learn) must stay in lockstep — adding one without the other is the
    documented drift that silently parks lessons as INSUFFICIENT_DATA."""
    import importlib.util as _ilu
    import sys as _sys
    from pathlib import Path as _Path

    _spec2 = _ilu.spec_from_file_location(
        "chron_learn", _Path(__file__).resolve().parents[1] / "chron_learn.py"
    )
    assert _spec2 is not None and _spec2.loader is not None
    cl = _ilu.module_from_spec(_spec2)
    _sys.modules.setdefault("chron_learn", cl)
    _spec2.loader.exec_module(cl)

    missing = set(cp.INTERPRETABLE_ERROR_TYPES) - set(cl._LESSON_TEXT)
    assert not missing, (
        f"classes without lesson text (would park as INSUFFICIENT): {missing}"
    )
