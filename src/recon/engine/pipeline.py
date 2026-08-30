"""The deterministic reconciliation run: ingest -> match -> classify.

No language model is involved at any point in this module. Everything here is
reproducible arithmetic, which is what makes it the baseline the agent layer has
to beat in order to justify its existence.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..ingest import load
from .classify import classify
from .matcher import match


def run(datadir: Path):
    """Reconcile a batch. Returns (findings, match_result, units, timing)."""
    t0 = time.perf_counter()
    units, bank, orders = load(datadir)
    t_load = time.perf_counter() - t0

    t1 = time.perf_counter()
    m = match(units, bank)
    t_match = time.perf_counter() - t1

    # Attach the matched bank rows so ReconUnit.delta is meaningful.
    for sid, u in units.items():
        u.bank_credits = m.assigned.get(sid) or []

    order_index = {o.order_id: o for o in orders}

    t2 = time.perf_counter()
    findings = [classify(units[sid], m, order_index) for sid in units]
    t_classify = time.perf_counter() - t2

    timing = {
        "load_s": round(t_load, 4),
        "match_s": round(t_match, 4),
        "classify_s": round(t_classify, 4),
        "total_s": round(time.perf_counter() - t0, 4),
        "settlements": len(units),
        "lines": sum(len(u.lines) for u in units.values()),
        "bank_rows": len(bank),
        "orders": len(orders),
    }
    return findings, m, units, timing
