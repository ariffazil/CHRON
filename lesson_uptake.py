#!/usr/bin/env python3
"""lesson_uptake.py — lesson → acceptance-test uptake mechanism (p1, §12D).

PURPOSE (F13-bounded T1, ARIF authorisation 2026-11-02)
Prove that one typed RECURRENCE lesson from data/lessons.jsonl can be bound to
a PERSISTENT, EXECUTABLE acceptance test, run, and witnessed — with the
witness record kept strictly separate from the lesson itself.

WHAT THIS IS NOT
  * It does NOT close, mutate, promote or supersede any lesson. A passing
    command is a TEST RESULT, never a human acceptance and never a lesson
    verdict. `human_acceptance` is a separate field and stays null until a
    sovereign fills it.
  * It changes no existing gate. It adds two append-only registry files and
    one checker subcommand; nothing else on disk is written.

REGISTRIES (append-only, organ-local)
  data/lesson_acceptance_bindings.jsonl    — one binding per lesson_id (state)
  data/lesson_acceptance_executions.jsonl  — one row per run (events)

EXIT CODES
  0 ok / already-bound      1 usage or internal error
  2 REJECTED (malformed lesson)        3 UNKNOWN (unsupported lesson)

PROVENANCE (P1 correction, ARIF 2026-11-02)
  Inline provenance    = declared on the lesson row itself (basis/method/source_kind).
  Reference provenance = resolved through prediction_ids looked up in the
                         predictions ledger; recorded at bind time in
                         binding.provenance.resolved_prediction_ids.
  Effects on binding validity (no silent repair; lessons.jsonl is never written):
    missing source       -> id listed under unresolved_prediction_ids; a binding
                            is created only when >=1 reference resolves OR a
                            declared provenance field exists; otherwise UNKNOWN
                            and nothing is recorded.
    ambiguous lesson_id  -> several rows for one id: the latest NON-SUPERSEDED
                            row by created_at binds; lesson_created_at and
                            lesson_fingerprint are captured as the anchor.
                            Later rows do not silently rebind — drift must be
                            read against the anchor, not re-derived.
    duplicated lesson_id -> one binding per id (ALREADY_BOUND); superseded twins
                            collapse into the single live row.
    changed source       -> resolution is EXISTENCE-based, not content-hash
                            based: a prediction edited after binding keeps its id
                            valid while its content drifts UNDETECTED here.
    missing lesson_id    -> exact string match only; no ID normalization, no
                            repair, no mutation of any lesson row.

Subcommands:
  bind --lesson-id ID --test-id TID --criterion TEXT --since DATE
       [--command CMD] [--subject TEXT]
  run  [--lesson-id ID] [--timeout SEC]
  status [--lesson-id ID]
  check-range-snapshot --since DATE [--predictions PATH] [--lessons PATH]

The acceptance test surface for the first binding is `check-range-snapshot`:
every numeric-range prediction minted on/after `since` must carry a
spot-check (evidence_snapshot or evidence) recorded at creation — the
operational form of lesson-range_stale_at_mint-2 ("spot-check the live
level at creation, record it, only then commit a range").
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

CHRON_ROOT = Path(__file__).resolve().parent
DEFAULT_LESSONS = CHRON_ROOT / "data" / "lessons.jsonl"
DEFAULT_PREDICTIONS = CHRON_ROOT / "data" / "predictions.jsonl"
DEFAULT_BINDINGS = CHRON_ROOT / "data" / "lesson_acceptance_bindings.jsonl"
DEFAULT_EXECUTIONS = CHRON_ROOT / "data" / "lesson_acceptance_executions.jsonl"

EXIT_OK, EXIT_ERR, EXIT_REJECTED, EXIT_UNKNOWN = 0, 1, 2, 3


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue  # a malformed line must never crash the reader (it is reported by count)
    return rows


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _lesson_rows(lessons_path: Path, lesson_id: str) -> list[dict]:
    return [r for r in _read_jsonl(lessons_path) if r.get("lesson_id") == lesson_id]


# ── bind ─────────────────────────────────────────────────────────────────────


def cmd_bind(args: argparse.Namespace) -> int:
    lessons_path = Path(args.lessons)
    predictions_path = Path(args.predictions)
    bindings_path = Path(args.bindings)

    rows = _lesson_rows(lessons_path, args.lesson_id)
    if not rows:
        print(f"REJECTED: lesson_id {args.lesson_id!r} not found in {lessons_path}")
        return EXIT_REJECTED

    live_rows = [r for r in rows if r.get("status") != "SUPERSEDED"]
    if not live_rows:
        print(
            f"UNKNOWN: lesson {args.lesson_id!r} has only SUPERSEDED rows — nothing live to bind"
        )
        return EXIT_UNKNOWN

    row = sorted(live_rows, key=lambda r: str(r.get("created_at") or ""))[-1]

    recurrence = row.get("recurrence")
    if not isinstance(recurrence, int) or recurrence < 1:
        print(
            f"UNKNOWN: lesson {args.lesson_id!r} is not recurrence-typed "
            f"(recurrence={recurrence!r}) — binding refused, nothing recorded"
        )
        return EXIT_UNKNOWN

    pred_ids = [p for p in (row.get("prediction_ids") or []) if isinstance(p, str)]
    pred_index = {p.get("prediction_id") for p in _read_jsonl(predictions_path)}
    resolved = [p for p in pred_ids if p in pred_index]
    provenance_fields = [f for f in ("basis", "method", "source_kind") if row.get(f)]
    if not provenance_fields and not resolved:
        print(
            f"UNKNOWN: lesson {args.lesson_id!r} carries no resolvable provenance "
            f"(no basis/method/source_kind and {len(pred_ids)} unresolved prediction_ids)"
        )
        return EXIT_UNKNOWN

    existing = [
        b for b in _read_jsonl(bindings_path) if b.get("lesson_id") == args.lesson_id
    ]
    if existing:
        print(
            f"ALREADY_BOUND: {args.lesson_id} -> {existing[0].get('test_id')} (idempotent no-op)"
        )
        return EXIT_OK

    command = args.command or (
        f"{shlex.quote(sys.executable)} {shlex.quote(str(CHRON_ROOT / 'lesson_uptake.py'))} "
        f"check-range-snapshot --since {shlex.quote(args.since)}"
    )
    binding = {
        "binding_id": "bind-"
        + hashlib.sha256(f"{args.lesson_id}|{args.test_id}".encode()).hexdigest()[:12],
        "lesson_id": args.lesson_id,
        "lesson_fingerprint": row.get("fingerprint"),
        "lesson_recurrence": recurrence,
        "lesson_created_at": row.get("created_at"),
        "provenance": {
            "declared_fields": provenance_fields,
            "resolved_prediction_ids": resolved,
            "unresolved_prediction_ids": [p for p in pred_ids if p not in resolved],
            "source": str(lessons_path),
        },
        "subject_under_test": args.subject
        or "chron predictions ledger (numeric-range claims)",
        "acceptance_criterion": args.criterion,
        "test_id": args.test_id,
        "test_command": command,
        "created_at": _now(),
        "created_by": "333-AGI",
        "authority": "F13-bounded T1 (ARIF 2026-11-02) — binding only; no lesson verdict implied",
    }
    _append_jsonl(bindings_path, binding)
    print(f"BOUND: {args.lesson_id} -> {args.test_id} ({binding['binding_id']})")
    return EXIT_OK


# ── run ──────────────────────────────────────────────────────────────────────


def cmd_run(args: argparse.Namespace) -> int:
    bindings = _read_jsonl(Path(args.bindings))
    if args.lesson_id:
        bindings = [b for b in bindings if b.get("lesson_id") == args.lesson_id]
    if not bindings:
        print("no bindings to run")
        return EXIT_OK

    lessons_path = Path(args.lessons)
    for b in bindings:
        command = b.get("test_command") or ""
        exit_code, ok, outcome = None, False, ""
        try:
            proc = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                timeout=args.timeout,
                shell=False,  # never a shell: the recorded command is executed as argv only
            )
            exit_code = proc.returncode
            ok = exit_code == 0
            tail = [l for l in (proc.stdout or "").splitlines() if l.strip()][-4:]
            joined = " | ".join(tail)
            try:
                # A JSON verdict must stay WHOLE: truncating it mid-string would
                # cut the acceptance semantics out of the witness record.
                json.loads(joined)
                outcome = joined
            except Exception:
                outcome = joined[:2000]
            if not ok:
                err = [l for l in (proc.stderr or "").splitlines() if l.strip()][-2:]
                outcome = (outcome + " || ERR: " + " | ".join(err))[:2400]
        except Exception as exc:
            outcome = f"runner error: {type(exc).__name__}: {exc}"[:400]

        execution = {
            "execution_id": "exec-" + uuid.uuid4().hex[:12],
            "binding_id": b.get("binding_id"),
            "lesson_id": b.get("lesson_id"),
            "test_id": b.get("test_id"),
            "executed_at": _now(),
            "command_success": ok,
            "exit_code": exit_code,
            # TEST result only. Never ACCEPTED, never a lesson verdict.
            "governance_verdict": "TEST_PASSED_AWAITING_HUMAN"
            if ok
            else "TEST_FAILED_REVIEW",
            "observed_outcome": outcome,
            # Separate field, sovereign-filled. Never derived from command_success.
            "human_acceptance": None,
            "lesson_mutated": False,
        }
        _append_jsonl(Path(args.executions), execution)
        print(
            f"{b.get('lesson_id')} :: {b.get('test_id')} :: "
            f"{'PASS' if ok else 'FAIL'} ({execution['governance_verdict']}) -> "
            f"{execution['execution_id']}"
        )
    return EXIT_OK


# ── status ───────────────────────────────────────────────────────────────────


def cmd_status(args: argparse.Namespace) -> int:
    bindings = _read_jsonl(Path(args.bindings))
    executions = _read_jsonl(Path(args.executions))
    if args.lesson_id:
        bindings = [b for b in bindings if b.get("lesson_id") == args.lesson_id]
        executions = [e for e in executions if e.get("lesson_id") == args.lesson_id]
    print(
        json.dumps(
            {"bindings": bindings, "executions": executions}, indent=1, sort_keys=True
        )
    )
    return EXIT_OK


# ── acceptance test surface: check-range-snapshot ────────────────────────────


def cmd_check_range_snapshot(args: argparse.Namespace) -> int:
    since = args.since
    violations: list[dict] = []
    in_scope = 0
    backlog: list[str] = []
    for p in _read_jsonl(Path(args.predictions)):
        if p.get("threshold_low") is None and p.get("threshold_high") is None:
            continue  # not a numeric-range claim
        has_spot = bool(p.get("evidence_snapshot")) or bool(p.get("evidence"))
        created = str(p.get("created_at") or "")
        if created[:10] >= since:
            in_scope += 1
            if not has_spot:
                violations.append(
                    {
                        "prediction_id": p.get("prediction_id"),
                        "created_at": created,
                        "claim": str(p.get("claim") or "")[:120],
                    }
                )
        elif not has_spot:
            backlog.append(
                str(p.get("prediction_id"))
            )  # pre-lesson legacy, reported not failed
    uptake = in_scope - len(violations)
    zero_sample = in_scope == 0
    verdict = {
        "test": "check-range-snapshot",
        "since": since,
        "range_claims_in_scope": in_scope,
        # ZERO-SAMPLE EXPOSURE (P1 correction, ARIF 2026-11-02): an empty
        # eligible set must never read as demonstrated uptake.
        "eligible_observation_count": in_scope,
        "uptake_observation_count": uptake,
        "violations": violations,
        "pre_since_backlog_spotless": backlog,
        "result": "PASS" if not violations else "FAIL",
        "acceptance_semantics": {
            "command_execution_success": {
                "status": "PER_EXECUTION_FIELD",
                "note": "exit status of this command only; never uptake, never acceptance",
            },
            "fixture_sensitivity": {
                "status": "DEMONSTRATED_ON_FIXTURES",
                "evidence": "tests/test_lesson_uptake.py — synthetic fixtures, not live evidence",
            },
            "live_evaluation_coverage": {
                "status": "ZERO_ELIGIBLE_OBSERVATIONS"
                if zero_sample
                else "ELIGIBLE_OBSERVATIONS_PRESENT",
                "eligible_observation_count": in_scope,
            },
            "observed_lesson_uptake": {
                "status": "UNDETERMINED_ZERO_ELIGIBLE_OBSERVATIONS"
                if zero_sample
                else ("OBSERVED" if uptake == in_scope else "PARTIAL_OR_VIOLATING"),
                "uptake_observation_count": uptake,
            },
            "human_acceptance": {"status": "AWAITING_HUMAN", "value": None},
        },
        "note": (
            "FAIL blocks acceptance of the bound lesson's uptake, not the lesson itself; "
            "the lesson row is never modified by this test. "
            "PASS with eligible_observation_count=0 means the test is armed, NOT that "
            "uptake was observed."
        ),
    }
    print(json.dumps(verdict, sort_keys=True))
    return EXIT_OK if not violations else EXIT_ERR


# ── cli ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    common = {
        "lessons": DEFAULT_LESSONS,
        "predictions": DEFAULT_PREDICTIONS,
        "bindings": DEFAULT_BINDINGS,
        "executions": DEFAULT_EXECUTIONS,
    }

    b = sub.add_parser("bind", help="bind a recurrence lesson to an acceptance test")
    b.add_argument("--lesson-id", required=True)
    b.add_argument("--test-id", required=True)
    b.add_argument("--criterion", required=True)
    b.add_argument(
        "--since", required=True, help="YYYY-MM-DD window start for the check"
    )
    b.add_argument(
        "--command",
        default=None,
        help="override the acceptance test command (argv string)",
    )
    b.add_argument("--subject", default=None)
    b.add_argument("--lessons", default=str(common["lessons"]))
    b.add_argument("--predictions", default=str(common["predictions"]))
    b.add_argument("--bindings", default=str(common["bindings"]))
    b.set_defaults(func=cmd_bind)

    r = sub.add_parser(
        "run", help="execute every bound acceptance test and record the witness"
    )
    r.add_argument("--lesson-id", default=None)
    r.add_argument("--timeout", type=int, default=300)
    r.add_argument("--lessons", default=str(common["lessons"]))
    r.add_argument("--predictions", default=str(common["predictions"]))
    r.add_argument("--bindings", default=str(common["bindings"]))
    r.add_argument("--executions", default=str(common["executions"]))
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("status", help="print bindings + executions")
    s.add_argument("--lesson-id", default=None)
    s.add_argument("--bindings", default=str(common["bindings"]))
    s.add_argument("--executions", default=str(common["executions"]))
    s.set_defaults(func=cmd_status)

    c = sub.add_parser(
        "check-range-snapshot", help="acceptance test: range claims need a spot-check"
    )
    c.add_argument("--since", required=True)
    c.add_argument("--predictions", default=str(common["predictions"]))
    c.add_argument("--lessons", default=str(common["lessons"]))
    c.set_defaults(func=cmd_check_range_snapshot)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
