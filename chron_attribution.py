"""Observer attribution — derived, conservative, coverage-reported.

WHY THIS EXISTS (measured 2026-10-01, /root/chron/data/episodes.jsonl: 103,503 episodes,
178,165 observations, every one of them carrying a confidence value):

  - No episode carries an `author`; `provenance` is null on 99.9% of rows.
  - `principal`, whose declared semantics are a PRIVACY AUDIENCE, has been overwritten
    with agent identity by the ingest bridges — 134 distinct values, led by 'A-FORGE'
    (39,656), 'aed-v1' (13,196), 'hermes-asi' (12,230), 'grok-build' (9,503), 'a-forge'
    (6,671), '333-AGI/dynamic-gate' (6,482), 'arif' (466), 'Arif' (308).
    chron_ariflow_bridge.py:122 writes principal="federation"; bridge_flow.py:105 writes
    principal=<actor>. One field, two live meanings.
  - BL12's per-actor curve keys on a prediction's `created_by`
    (bl02_calibration_curves.py:30-31), and every prediction is minted by CHRON itself,
    so the curve reports effective_n 1/2/3 for three CHRON-internal generators while
    178,165 agent assertions sit unmeasured. The instrument is measuring its own author.

THE RULES, each pinned by tests/test_attribution.py:

  1. DERIVE, do not store. Nothing here writes an episode. A materialized identity field
     is what resurrected ad-3a2c111e for three cycles tonight; the ratified Temporal
     Derivation Law says state like this is a query.
  2. NEVER GUESS. Attribution requires membership in KNOWN_AGENTS. Audience tokens,
     human-as-subject values, and unrecognized strings return AMBIGUOUS_SEMANTICS or
     UNATTRIBUTED. Reading `arif` as an observer would fabricate an observation — the one
     thing an attribution layer must not do, and it is also how a privacy-scoped field
     would silently become an identity ledger.
  3. NORMALIZE from an explicit alias table, never by folding everything case-insensitively.
     'A-FORGE'/'a-forge' are one agent split across 46,327 rows; '333-AGI' plus its
     '/dynamic-gate' and '/agentic-web' sub-vehicles are one lane across 10,464 rows.
     (Kernel precedent: SCT actor comparisons normalize both sides, 2026-09-20.)
  4. REPORT COVERAGE. attribution_coverage() is part of the API: a layer that resolves 60%
     of assertions silently is worse than no layer, because the gap becomes invisible to
     the consumers that need it most.

KNOWN_AGENTS is seeded from OBSERVED values in the store, not from a roster of who ought
to exist. New agents land in UNATTRIBUTED until registered here — an explicit, grevable
decision rather than an automatic one.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Sentinel states. These are RESULTS, not failures — an unresolved observer is honest.
AMBIGUOUS_SEMANTICS = "AMBIGUOUS_SEMANTICS"
UNATTRIBUTED = "UNATTRIBUTED"

# Values that mean "whose business is this", not "who saw it".
AUDIENCE_TOKENS = frozenset(
    {"", "arif", "syed", "both", "shared", "all", "federation", "none", "null", "unknown"}
)

# Registered observers, canonical key -> what it accepted as written. Roll-up targets for
# lanes are explicit so a lane root and its sub-vehicles count as ONE actor.
KNOWN_AGENTS = {
    "a-forge": "a-forge",
    "aed-v1": "aed-v1",
    "hermes-asi": "hermes-asi",
    "hermes-rsi-loop": "hermes-rsi-loop",
    "hermes-cron": "hermes-cron",
    "grok-build": "grok-build",
    "claude-code": "claude-code",
    "qwen-code": "qwen-code",
    "kimi-code": "kimi-code",
    "orchestrator-v1": "orchestrator-v1",
    # 333-AGI lane: the numeral is a LANE identity (FATWA K1), sub-vehicles roll up.
    "333-agi": "333-agi",
    "333-agi/dynamic-gate": "333-agi",
    "333-agi/agentic-web": "333-agi",
}


def _normalize(raw: Any) -> str:
    """Whitespace-tolerant, case-folded, hyphen-stable key for lookup ONLY.

    Returns a comparison key; it is never surfaced as an identity.
    """
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    return s.replace("_", "-").replace("–", "-")


def _lane_root(key: str) -> tuple[str, Optional[str]]:
    """(rollup target, sub-vehicle) — a lane name like '333-agi/dynamic-gate' resolves to
    its lane root while keeping the sub-vehicle recoverable for audit."""
    if "/" in key:
        head, _, tail = key.partition("/")
        return head, tail
    return key, None


def observer_of(episode: Any, include_subkey: bool = False) -> str:
    """Derive the observer of an episode. Never invents one.

    Order of authority:
      1. claims[].asserted_by — the slot that literally means "who asserted this"
      2. author
      3. created_by / actor_id — the authorship fields CHRON and arifFlow actually write
      4. principal — repurposed by the bridges; used only when it resolves to a
         registered agent
    A registered lane rolls up: '333-agi/fi-003' is the lane root '333-agi', because the
    stated rule is that a lane root and its sub-vehicles count as ONE actor. Previously only
    two sub-vehicles were enumerated by hand, so an unlisted one silently lost its identity.
    Anything else returns AMBIGUOUS_SEMANTICS (a value whose field means something else)
    or UNATTRIBUTED (no usable identity).
    """
    if not isinstance(episode, dict):
        return UNATTRIBUTED

    for candidate in _candidates(episode):
        key = _normalize(candidate)
        if not key:
            continue
        if key in AUDIENCE_TOKENS:
            continue  # privacy scope is not authorship — keep looking, then fall through
        if key in KNOWN_AGENTS:
            if include_subkey and "/" in key:
                return key
            return KNOWN_AGENTS[key]
        root, sub = _lane_root(key)
        if root in KNOWN_AGENTS:
            if include_subkey and sub:
                return key
            return KNOWN_AGENTS[root]

    # Nothing resolved. Distinguish "the field means something else" from "no identity".
    principal = _normalize(episode.get("principal"))
    if principal in AUDIENCE_TOKENS and principal not in ("", "none", "null"):
        return AMBIGUOUS_SEMANTICS
    return UNATTRIBUTED


def _candidates(episode: dict) -> list[str]:
    out: list[str] = []
    for claim in episode.get("claims") or []:
        if isinstance(claim, dict) and claim.get("asserted_by"):
            out.append(str(claim["asserted_by"]))
    if episode.get("author"):
        out.append(str(episode["author"]))
    # created_by (CHRON predictions) and actor_id (arifFlow receipts) are the ONLY authorship
    # fields those two stores actually populate. Until 2026-10-01 this resolver read neither, so
    # every real row fell to UNATTRIBUTED/AMBIGUOUS however good the identity was: the lookup was
    # blind to the field holding the answer. Placed after the explicit authorship slots
    # (asserted_by, author) and before principal, which is a privacy scope, not authorship.
    for field in ("created_by", "actor_id", "observer", "steward"):
        if episode.get(field):
            out.append(str(episode[field]))
    if episode.get("principal"):
        out.append(str(episode["principal"]))
    return out


def observer_detail(episode: Any) -> dict[str, Any]:
    """Same decision, with what it saw — for receipts and for the ambiguity backlog."""
    principal = episode.get("principal") if isinstance(episode, dict) else None
    asserted = None
    if isinstance(episode, dict):
        for claim in episode.get("claims") or []:
            if isinstance(claim, dict) and claim.get("asserted_by"):
                asserted = claim["asserted_by"]
                break
    return {
        "observer": observer_of(episode),
        "raw_principal": principal,
        "raw_asserted_by": asserted,
        "registered": observer_of(episode) not in (AMBIGUOUS_SEMANTICS, UNATTRIBUTED),
    }


def attribution_coverage(episodes: Iterable[dict]) -> dict[str, Any]:
    """Resolve a population and report what fraction could be resolved.

    The gap is a first-class number: BL12 must not fire on a curve built from 60% of
    assertions while reading the other 40% as silence.
    """
    total = attributed = ambiguous = unattributed = 0
    agents: dict[str, int] = {}
    for ep in episodes:
        total += 1
        obs = observer_of(ep)
        if obs == AMBIGUOUS_SEMANTICS:
            ambiguous += 1
        elif obs == UNATTRIBUTED:
            unattributed += 1
        else:
            attributed += 1
            agents[obs] = agents.get(obs, 0) + 1
    return {
        "episodes_total": total,
        "attributed": attributed,
        "ambiguous_semantics": ambiguous,
        "unattributed": unattributed,
        "distinct_agents": len(agents),
        "attributed_ratio": round(attributed / total, 4) if total else 0.0,
        "per_agent": dict(sorted(agents.items(), key=lambda kv: -kv[1])),
    }
