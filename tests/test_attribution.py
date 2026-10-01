"""Observer attribution must be DERIVED, and must never guess.

Measured state that motivated this module (2026-10-01, /root/chron/data/episodes.jsonl,
103,503 episodes, 178,165 observations — all of them carrying a confidence value):

  - `provenance` is null on 99.9% of episodes, and no episode carries `author`.
  - `principal` — whose declared semantics are a PRIVACY AUDIENCE (arif/syed/both,
    per chron_active_events' audience contract) — has been overwritten with AGENT
    IDENTITY by the ingest bridges: 'A-FORGE' 39,656, 'aed-v1' 13,196,
    'hermes-asi' 12,230, 'grok-build' 9,503, 'a-forge' 6,671, '333-AGI/dynamic-gate'
    6,482, 'arif' 466, 'Arif' 308 … 134 distinct values.
    chron_ariflow_bridge.py:122 writes principal="federation"; bridge_flow.py:105 writes
    principal=<actor>. One field, two meanings, both live.
  - So the federation holds 178,165 confidence-bearing assertions by named agents, and
    BL12's per-actor curve reads none of them: bl02_calibration_curves.py:30-31 keys on
    a PREDICTION's `created_by`, and every prediction is minted by CHRON itself
    (generate_from_chron_events / chron_price_predictions / chron_loop_closure_test) —
    effective_n 1/2/3, no actor above the n>=8 floor. The instrument measures its own
    author while the real assertions sit in a field it never opens.

Design rules, each pinned by a test below:

  1. DERIVE, do not store. No episode is rewritten and no `author` is materialized into
     the append-only ledger — per the ratified Temporal Derivation Law, temporal/identity
     state is a query, and a materialized copy is exactly what resurrected ad-3a2c111e.
  2. NEVER GUESS. A value is attributed only if it appears in an explicit known-agent
     registry. Audience tokens, human principals, and unrecognized strings return
     AMBIGUOUS_* / UNATTRIBUTED — not an inferred actor. Conflating `arif` (the sovereign
     as SUBJECT of an episode) with `arif` (an actor who observed something) would be
     fabricating an observation, which is the one thing an attribution layer cannot do.
  3. NORMALIZE identity, from an explicit alias table, not by case-folding everything.
     'A-FORGE' and 'a-forge' are one agent holding 46,327 rows across two keys;
     '333-AGI', '333-AGI/dynamic-gate', '333-AGI/agentic-web' are one lane across three.
     Precedent: SCT actor comparisons must normalize both sides (kernel scar 2026-09-20).
  4. REPORT COVERAGE. An attribution layer that attributes 60% silently is worse than
     none: consumers must see what fraction of assertions were resolved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chron.chron_attribution import (  # noqa: E402
    AMBIGUOUS_SEMANTICS,
    UNATTRIBUTED,
    attribution_coverage,
    observer_of,
)


def test_t1_audience_token_is_never_an_observer():
    """principal carries privacy semantics on seed rows — those rows have no observer."""
    for audience in ("arif", "syed", "both", "shared", "all", "federation"):
        assert observer_of({"principal": audience}) in (AMBIGUOUS_SEMANTICS, UNATTRIBUTED), (
            f"audience token {audience!r} was resolved into an actor"
        )


def test_t2_sovereign_as_subject_is_not_an_actor():
    """'arif' means the episode is ABOUT Arif on seed rows. Reading it as "Arif observed
    this" would manufacture an observation, so it must not become an actor key."""
    assert observer_of({"principal": "arif"}) != "arif"
    assert observer_of({"principal": "Arif"}) != "arif"


def test_t3_case_variants_merge_to_one_agent():
    a = observer_of({"principal": "A-FORGE"})
    b = observer_of({"principal": "a-forge"})
    assert a == b and a not in (AMBIGUOUS_SEMANTICS, UNATTRIBUTED)
    assert observer_of({"principal": " A-FORGE "}) == a  # whitespace tolerated


def test_t4_lane_variants_roll_up_to_the_lane():
    """333-AGI, 333-AGI/dynamic-gate, 333-AGI/agentic-web are one lane (10,464 rows across
    three keys). Roll up to the lane root while keeping the sub-vehicle recoverable."""
    base = observer_of({"principal": "333-AGI"})
    gated = observer_of({"principal": "333-AGI/dynamic-gate"}, include_subkey=True)
    assert base == "333-agi"
    assert observer_of({"principal": "333-AGI/dynamic-gate"}) == "333-agi"
    assert gated == "333-agi/dynamic-gate"


def test_t5_unknown_value_is_not_invented():
    assert observer_of({"principal": "some-brand-new-organ-v9"}) == UNATTRIBUTED
    assert observer_of({"principal": ""}) == UNATTRIBUTED
    assert observer_of({}) == UNATTRIBUTED
    assert observer_of(None) == UNATTRIBUTED


def test_t6_nested_claim_author_wins_when_present():
    """claims[].asserted_by is the semantically correct slot (it means "who asserted").
    When populated it outranks the repurposed principal field."""
    ep = {"principal": "A-FORGE", "claims": [{"asserted_by": "hermes-asi"}]}
    assert observer_of(ep) == "hermes-asi"


def test_t7_coverage_is_reported_not_hidden():
    episodes = [
        {"principal": "A-FORGE"},
        {"principal": "a-forge"},
        {"principal": "both"},
        {"principal": "mystery-thing"},
    ]
    cov = attribution_coverage(episodes)
    assert cov["episodes_total"] == 4
    assert cov["attributed"] == 2
    assert cov["ambiguous_semantics"] == 1
    assert cov["unattributed"] == 1
    assert cov["distinct_agents"] == 1  # the two A-FORGE rows are ONE agent
    assert cov["attributed_ratio"] == 0.5
