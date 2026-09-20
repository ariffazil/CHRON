"""Report-only early-falsifier probes. No ledger writes."""

from chron.chron_early_falsifier_scan import (
    probe_brent_band,
    probe_systemd_venv,
    probe_tree777_crontab,
    probe_weekly_packet,
)


def test_weekly_packet_owns_only_its_claim():
    assert probe_weekly_packet("Brent crude will be in range USD65-75") is None
    state, observed = probe_weekly_packet(
        "f13-weekly-packet will produce 0 new packet files via its Mon 09:07 cron by 2026-09-28"
    )
    assert state in ("SIGNAL_PRESENT", "FALSIFIER_ALREADY_SATISFIED", "AWAITING_EVIDENCE")
    assert "packet file" in observed


def test_tree777_owns_only_its_claim():
    assert probe_tree777_crontab("RON95 price will remain") is None
    state, observed = probe_tree777_crontab(
        "TREE777 per-agent weekly anchor remains absent from every crontab on 2026-09-28"
    )
    assert state in ("SIGNAL_PRESENT", "FALSIFIER_ALREADY_SATISFIED")
    assert "tree777" in observed.lower()


def test_existing_probes_still_gate_on_claim_text():
    assert probe_brent_band("TREE777 per-agent weekly") is None
    assert probe_systemd_venv("f13-weekly-packet") is None
