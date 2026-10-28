"""Loss-aware mapping of source cost records into a FOCUS 1.3-aligned export.

This deliberately does not claim complete FOCUS conformance. Missing billing data
is blank, never zero. Source records remain byte-identical beside the export.
"""
import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
from .util import sha256, write_json, utc_now

FIELDS = ("ProviderName", "BillingAccountId", "BillingCurrency", "BillingPeriodStart", "BillingPeriodEnd",
          "ChargePeriodStart", "ChargePeriodEnd", "ChargeCategory", "ChargeDescription", "ServiceName",
          "ResourceId", "ResourceName", "RegionId", "ConsumedQuantity", "ConsumedUnit", "ListCost", "BilledCost",
          "EffectiveCost", "x_BenchmarkCategory", "x_AllocationFraction", "x_AllocationBasis")
CATEGORIES = {"application", "database", "network", "storage", "telemetry", "model", "generator", "controller", "other"}


def import_costs(source, mapping, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Cost import destination already exists; retain previous imports")
    if set(mapping.get("columns", {})) - set(FIELDS):
        raise ValueError("Unknown target cost field")
    basis = mapping.get("basis")
    if basis not in ("list_estimate", "effective_actual"):
        raise ValueError("Cost basis must be list_estimate or effective_actual")
    amount_field = "ListCost" if basis == "list_estimate" else "EffectiveCost"
    rows, costs, missing, currencies = [], [], set(), set()
    with source.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        for i, raw in enumerate(reader, 2):
            row = {field: raw.get(mapping.get("columns", {}).get(field, ""), mapping.get("defaults", {}).get(field, "")) for field in FIELDS}
            missing.update(k for k, v in row.items() if v == "")
            currency = row["BillingCurrency"]
            currencies.add(currency)
            category = row["x_BenchmarkCategory"]
            if category not in CATEGORIES:
                raise ValueError(f"Source row {i}: explicit benchmark cost category required")
            fraction = row["x_AllocationFraction"]
            try:
                allocation = Decimal(str(fraction))
                amount = Decimal(str(row[amount_field]))
                if not allocation.is_finite() or not amount.is_finite() or not 0 <= allocation <= 1:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise ValueError(f"Source row {i}: finite amount and allocation fraction 0..1 required") from None
            if not row["x_AllocationBasis"] or not currency:
                raise ValueError(f"Source row {i}: currency and allocation rationale required")
            costs.append({"category": category, "amount": float(amount * allocation), "basis": basis,
                          "allocation": "measurement", "source_row": i, "allocation_basis": row["x_AllocationBasis"]})
            rows.append(row)
    if len(currencies) != 1 or not rows:
        raise ValueError("Import requires nonempty records in one currency; do not silently convert exchange rates")
    output.mkdir(parents=True)
    copied = output / "source-billing.csv"
    shutil.copyfile(source, copied)
    with (output / "focus-aligned.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    write_json(output / "mapping.json", mapping)
    document = {"currency": next(iter(currencies)), "complete": False, "records": costs,
                "source_files": [{"path": str(copied.resolve()), "sha256": sha256(copied)},
                                 {"path": str((output / "mapping.json").resolve()), "sha256": sha256(output / "mapping.json")}],
                "note": "Completeness requires reconciliation against the entire campaign resource inventory and billing window."}
    write_json(output / "costs.json", document)
    report = {"created_at": utc_now(), "source_sha256": sha256(copied), "rows": len(rows), "basis": basis,
              "focus_version": "1.3", "conformance": "aligned subset; not certified", "missing_fields": sorted(missing),
              "source_preserved": True, "coverage_requires_review": True}
    write_json(output / "import.json", report)
    return report
