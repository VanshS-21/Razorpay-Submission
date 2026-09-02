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
    MoneyParseError,
    EntityType,
    OrderRecord,
    ReconUnit,
    SettlementLine,
    to_paise,
)


class IngestError(Exception):
    """A source file that cannot be read, reported in terms of the file."""


def _int(v, *, path=None, row=None, col=None, blank_ok=False) -> int:
    """One money cell to integer paise.

    A value carrying a decimal point is rupees (the shape a real Razorpay
    export and most bank exports use) and is converted here, at the boundary.
    A value without one is already paise, which is what this project's own
    generator writes. Everything downstream sees paise and only paise.

    A blank money cell is REFUSED, not read as zero. `to_paise` was hardened to
    do that and this function short-circuited before ever calling it, so the
    fix never reached the boundary that mattered. models.py puts the reason
    best: in a reconciler a wrong zero is worse than a crash, because it
    balances. Callers that genuinely permit a blank pass blank_ok=True.
    """
    raw = (v or "").strip() if v is not None else ""
    if not raw:
        if blank_ok:
            return 0
        where = f"{path}" if path else "input"
        raise IngestError(
            f"{where}: row {row}, column '{col}': money cell is empty. "
            f"A blank is refused rather than read as zero -- a wrong zero "
            f"balances, and would be invisible in every total.")
    try:
        return to_paise(raw) if "." in raw else int(raw.replace(",", ""))
    except (ValueError, MoneyParseError) as e:
        where = f"{path}" if path else "input"
        raise IngestError(
            f"{where}: row {row}, column '{col}': {raw!r} is not an amount "
            f"({e})") from e


#: Marker DictReader inserts for a row with FEWER fields than the header.
#: Without it a short row silently yields None for the missing columns, and a
#: truncated export produced a settlement whose id was literally `None` -- fully
#: classified, reported, and exited 0. Column names were checked; row widths
#: were not.
_SHORT = "<<MISSING>>"


def _require(path, reader, needed):
    """Fail on a missing column by NAME, before the first row is parsed."""
    have = set(reader.fieldnames or [])
    missing = [c for c in needed if c not in have]
    if missing:
        raise IngestError(
            f"{path}: missing required column(s): {', '.join(missing)}. "
            f"Found: {', '.join(sorted(have)) or '(no header row)'}")


def _check_row(path, n, row, width):
    """Reject a row that is not exactly as wide as the header."""
    short = sorted(k for k, v in row.items() if v == _SHORT)
    if short:
        raise IngestError(
            f"{path}: row {n} has fewer fields than the header; "
            f"no value for: {', '.join(short)}. The file looks truncated.")
    extra = row.get(_SHORT)
    if extra:
        raise IngestError(
            f"{path}: row {n} has {width + len(extra)} fields but the header "
            f"has {width}. Unexpected trailing value(s): {extra}")


def _opt(v):
    v = (v or "").strip()
    return v or None


def read_settlement_lines(path: Path) -> list[SettlementLine]:
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh, restkey=_SHORT, restval=_SHORT)
        _require(path, rd, ("entity_id", "type", "debit", "credit", "amount",
                            "currency", "fee", "tax", "settlement_id",
                            "settlement_utr", "created_at", "settled_at"))
        for n, r in enumerate(rd, start=2):
            _check_row(path, n, r, len(rd.fieldnames or []))
            _i = lambda c: _int(r[c], path=path, row=n, col=c)   # noqa: E731
            try:
                kind = EntityType(r["type"])
            except ValueError as e:
                raise IngestError(
                    f"{path}: row {n}: unknown entity type {r['type']!r}") from e
            out.append(SettlementLine(
                entity_id=r["entity_id"],
                type=kind,
                debit=_i("debit"),
                credit=_i("credit"),
                amount=_i("amount"),
                currency=r["currency"],
                fee=_i("fee"),
                tax=_i("tax"),
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
        rd = csv.DictReader(fh, restkey=_SHORT, restval=_SHORT)
        _require(path, rd, ("txn_id", "value_date", "narration", "debit", "credit"))
        for n, r in enumerate(rd, start=2):
            _check_row(path, n, r, len(rd.fieldnames or []))
            out.append(BankCredit(
                txn_id=r["txn_id"],
                value_date=r["value_date"],
                narration=r["narration"],
                ref_no=(r.get("ref_no") or "").strip(),
                debit=_int(r["debit"], path=path, row=n, col="debit",
                           blank_ok=True),
                credit=_int(r["credit"], path=path, row=n, col="credit",
                            blank_ok=True),
            ))
    return out


def read_orders(path: Path) -> list[OrderRecord]:
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh, restkey=_SHORT, restval=_SHORT)
        _require(path, rd, ("order_id", "order_date", "customer_id",
                            "gross_amount", "currency", "status"))
        for n, r in enumerate(rd, start=2):
            _check_row(path, n, r, len(rd.fieldnames or []))
            out.append(OrderRecord(
                order_id=r["order_id"],
                order_date=r["order_date"],
                customer_id=r["customer_id"],
                gross_amount=_int(r["gross_amount"], path=path, row=n,
                                  col="gross_amount"),
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
        # One payout carries one UTR. If the export disagrees with itself, the
        # first value used to win silently and every bank row filed under the
        # other UTR became an invisible orphan -- the settlement then escalated
        # as short, for a reason nothing in the output could explain.
        utrs = {l.settlement_utr for l in ls if l.settlement_utr}
        if len(utrs) > 1:
            raise IngestError(
                f"settlement {sid} carries {len(utrs)} different UTRs "
                f"({', '.join(sorted(utrs))}). One payout has one UTR; this "
                f"export is inconsistent and matching it would silently drop "
                f"the bank rows filed under all but the first.")
        # Use the validated UTR, not the first row's. A settlement whose first
        # exported line has a blank reference -- an ordinary shape for a refund
        # or adjustment row -- got utr="", silently lost the pass-1 join, and
        # fell through to the fallback or escalated as never credited.
        units[sid] = ReconUnit(settlement_id=sid, utr=next(iter(utrs), ""),
                               lines=ls)
    return units


def load(datadir: Path):
    """Read all three sources and return (units, bank_rows, orders)."""
    datadir = Path(datadir)
    missing = [n for n in ("settlement_recon.csv", "bank_statement.csv",
                           "order_ledger.csv") if not (datadir / n).exists()]
    if missing:
        raise IngestError(
            f"{datadir}: missing source file(s): {', '.join(missing)}. "
            f"Three-way reconciliation needs all three; run "
            f"'python -m recon.generate --out {datadir}' to rebuild them.")
    lines = read_settlement_lines(datadir / "settlement_recon.csv")
    bank = read_bank(datadir / "bank_statement.csv")
    orders = read_orders(datadir / "order_ledger.csv")
    return build_units(lines), bank, orders
