"""Export filtered measurement subsets in CSV / JSON / Markdown formats."""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .format import format_imperial
from .store import Store

logger = logging.getLogger(__name__)

# Field order used by every export format. Keeping it stable means downstream
# spreadsheets / scripts don't break when we add new columns to the schema.
EXPORT_FIELDS = [
    "captured_at_iso", "device_address", "meas_id", "dev_mode", "ref_edge",
    "result_m", "result_imperial", "comp1_m", "comp2_m",
    "offset_in", "site_name", "latitude", "longitude", "loc_accuracy_m",
    "notes",
]


def _parse_date(s: str) -> datetime:
    """Accept YYYY-MM-DD or full ISO timestamps."""
    s = s.strip()
    if "T" in s or " " in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def _row_to_dict(row) -> dict:
    captured = datetime.fromtimestamp(row["captured_at"] / 1000)
    return {
        "captured_at_iso": captured.isoformat(timespec="milliseconds"),
        "device_address": row["device_address"],
        "meas_id": row["meas_id"],
        "dev_mode": row["dev_mode"],
        "ref_edge": row["ref_edge"],
        "result_m": row["result_m"],
        "result_imperial": format_imperial(row["result_m"]),
        "comp1_m": row["comp1_m"],
        "comp2_m": row["comp2_m"],
        "offset_in": row["offset_in"] if "offset_in" in row.keys() else 0.0,
        "site_name": row["site_name"] if "site_name" in row.keys() else None,
        "latitude": row["latitude"] if "latitude" in row.keys() else None,
        "longitude": row["longitude"] if "longitude" in row.keys() else None,
        "loc_accuracy_m": row["loc_accuracy_m"] if "loc_accuracy_m" in row.keys() else None,
        "notes": row["notes"] if "notes" in row.keys() else None,
    }


def to_csv(rows: Iterable[dict], out) -> None:
    writer = csv.DictWriter(out, fieldnames=EXPORT_FIELDS)
    writer.writeheader()
    for r in rows:
        writer.writerow(r)


def to_json(rows: Iterable[dict], out) -> None:
    json.dump(list(rows), out, indent=2, default=str)
    out.write("\n")


def to_markdown(rows: Iterable[dict], out) -> None:
    rows_list = list(rows)
    if not rows_list:
        out.write("(no rows)\n")
        return
    headers = ["Time", "Result", "Imperial", "Site", "Notes"]
    out.write("| " + " | ".join(headers) + " |\n")
    out.write("|" + "|".join(["---"] * len(headers)) + "|\n")
    for r in rows_list:
        cells = [
            r["captured_at_iso"][:19].replace("T", " "),
            f"{r['result_m']:.4f} m",
            r["result_imperial"],
            r.get("site_name") or "",
            (r.get("notes") or "").replace("|", "\\|"),
        ]
        out.write("| " + " | ".join(cells) + " |\n")


FORMATS = {"csv": to_csv, "json": to_json, "md": to_markdown}


def export_main() -> None:
    parser = argparse.ArgumentParser(
        description="Export measurements from the local SQLite store.",
    )
    parser.add_argument("--format", choices=sorted(FORMATS), default="csv")
    parser.add_argument("--since", metavar="DATE",
                        help="ISO date (YYYY-MM-DD) or timestamp; lower bound (inclusive)")
    parser.add_argument("--until", metavar="DATE",
                        help="ISO date or timestamp; upper bound (inclusive)")
    parser.add_argument("--site", metavar="NAME",
                        help="match this site name exactly")
    parser.add_argument("--device", metavar="ADDR",
                        help="match this BLE device address exactly")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="cap number of rows (newest first)")
    parser.add_argument("-o", "--output", metavar="PATH",
                        help="write to PATH instead of stdout")
    args = parser.parse_args()

    store = Store()
    try:
        since_ms = int(_parse_date(args.since).timestamp() * 1000) if args.since else None
        until_ms = int(_parse_date(args.until).timestamp() * 1000) if args.until else None
        rows = store.query(
            since_ms=since_ms, until_ms=until_ms,
            site=args.site, device_address=args.device, limit=args.limit,
        )
        dicts = [_row_to_dict(r) for r in rows]
        # Newest-first from query; export typically reads better oldest-first
        dicts.reverse()
        out_stream = (open(args.output, "w") if args.output else sys.stdout)
        try:
            FORMATS[args.format](dicts, out_stream)
        finally:
            if args.output:
                out_stream.close()
        if args.output:
            print(f"wrote {len(dicts)} row(s) to {args.output}", file=sys.stderr)
    finally:
        store.close()


if __name__ == "__main__":
    export_main()
