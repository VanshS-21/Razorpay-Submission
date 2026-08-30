"""Load the three CSV sources into typed records.

CSV gives you strings. Every amount is converted to ``int`` paise here, once, at
the boundary -- so nothing downstream ever has to wonder whether it is holding a
string, a float, or rupees.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .models import (
    BankCredit,
    EntityType,
    OrderRecord,
    ReconUnit,
    SettlementLine,
)


def _int(v) -> int:
    v = (v or "").strip()
    return int(v) if v else 0


def _opt(v):
    v = (v or "").strip()
    return v or None


def read_settlement_lines(path: Path) -> list[SettlementLine]:
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(SettlementLine(
                entity_id=r["entity_id"],
                type=EntityType(r["type"]),
                debit=_int(r["debit"]),
                credit=_int(r["credit"]),
                amount=_int(r["amount"]),
                currency=r["currency"],
                fee=_int(r["fee"]),
                tax=_int(r["tax"]),
                settlement_id=r["settlement_id"],
                settlement_utr=r["settlement_utr"],
                created_at=r["created_at"],
                settled_at=r["settled_at"],
                payment_id=_opt(r.get("payment_id")),
                order_id=_opt(r.get("order_id")),
                method=_opt(r.get("method")),
                description=r.get("description", ""),
            ))
    return out


def read_bank(path: Path) -> list[BankCredit]:
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(BankCredit(
                txn_id=r["txn_id"],
                value_date=r["value_date"],
                narration=r["narration"],
                ref_no=(r.get("ref_no") or "").strip(),
                debit=_int(r["debit"]),
                credit=_int(r["credit"]),
            ))
    return out


def read_orders(path: Path) -> list[OrderRecord]:
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.append(OrderRecord(
                order_id=r["order_id"],
                order_date=r["order_date"],
                customer_id=r["customer_id"],
                gross_amount=_int(r["gross_amount"]),
                currency=r["currency"],
                status=r["status"],
                payment_id=_opt(r.get("payment_id")),
            ))
    return out


def build_units(lines: list[SettlementLine]) -> dict[str, ReconUnit]:
    """Group settlement lines into the unit of reconciliation: one payout."""
    grouped: dict[str, list[SettlementLine]] = defaultdict(list)
    for l in lines:
        grouped[l.settlement_id].append(l)

    units: dict[str, ReconUnit] = {}
    for sid, ls in grouped.items():
        units[sid] = ReconUnit(settlement_id=sid, utr=ls[0].settlement_utr, lines=ls)
    return units


def load(datadir: Path):
    """Read all three sources and return (units, bank_rows, orders)."""
    datadir = Path(datadir)
    lines = read_settlement_lines(datadir / "settlement_recon.csv")
    bank = read_bank(datadir / "bank_statement.csv")
    orders = read_orders(datadir / "order_ledger.csv")
    return build_units(lines), bank, orders
