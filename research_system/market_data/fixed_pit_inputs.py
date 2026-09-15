"""Identity-bound transport for DB-validated historical dependency snapshots.

The producer must first validate the snapshot, its immutable hash and PIT
evidence. This receipt is a transport checksum, not independent proof that an
arbitrary caller's data was historically observable.
"""
import re
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from research_system.market_data.contracts import canonical_hash

VERSION = "fixed-profile-pit-input-v1"


def bind_pit_input(raw, *, symbol, dimension, day, cutoff, identity,
                   industry_basis=None, classification_system=None):
    value = deepcopy(raw)
    value.pop("_pit_context", None)
    value["_pit_context"] = {
        "version": VERSION, "safe": True, "symbol": symbol, "dimension": dimension,
        "requested_as_of_date": day.isoformat(), "cutoff_at": cutoff.isoformat(),
        "snapshot_id": identity["snapshot_id"], "snapshot_hash": identity["snapshot_hash"],
        "native_result_hash": canonical_hash(value),
        "industry_basis": industry_basis, "classification_system": classification_system,
    }
    return value


def validate_pit_input(raw, *, symbol, dimension, day, cutoff):
    proof = raw.get("_pit_context")
    valid = isinstance(proof, dict) and (
        proof.get("version") == VERSION and proof.get("safe") is True
        and proof.get("symbol") == symbol and proof.get("dimension") == dimension
        and proof.get("requested_as_of_date") == day.isoformat()
        and isinstance(proof.get("snapshot_id"), str) and bool(proof["snapshot_id"])
        and isinstance(proof.get("snapshot_hash"), str)
        and re.fullmatch(r"[0-9a-f]{64}", proof["snapshot_hash"])
    )
    if valid:
        try:
            actual_cutoff = datetime.fromisoformat(proof["cutoff_at"])
            valid = (cutoff.tzinfo is not None and cutoff.utcoffset() is not None
                     and actual_cutoff.tzinfo is not None and actual_cutoff == cutoff
                     and cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date() == day
                     and proof.get("native_result_hash") == canonical_hash(
                         {key: value for key, value in raw.items() if key != "_pit_context"}))
        except (KeyError, TypeError, ValueError):
            valid = False
    if not valid:
        raise ValueError("fixed_pit_binding_invalid")
    return deepcopy(proof)


def fixed_historical_profile(dependencies, *, symbol, day, cutoff=None):
    """Return the bound A-share input for an actual consumer, or no override.

    Historical source adapters also accept ordinary collection dependencies;
    those retain their existing contracts. Only this explicit binding overrides
    identity lookup. Symbol normalization never changes the immutable receipt.
    """
    raw = (dependencies or {}).get("0_basic") or {}
    proof = raw.get("_pit_context") or {}
    if day is None or proof.get("version") != VERSION:
        return None
    from research_system.adapters.vendors.market_router import MarketRouter
    target = MarketRouter().classify(symbol=str(proof.get("symbol") or ""))
    actual = MarketRouter().classify(symbol=symbol)
    if (target.market, target.normalized_symbol) != (actual.market, actual.normalized_symbol):
        raise ValueError("fixed_pit_binding_symbol_mismatch")
    try:
        cutoff = cutoff or datetime.fromisoformat(proof["cutoff_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("fixed_pit_binding_invalid") from None
    validate_pit_input(raw, symbol=proof["symbol"], dimension="0_basic", day=day, cutoff=cutoff)
    return deepcopy(raw)
