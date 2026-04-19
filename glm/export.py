"""Export filtered measurement subsets in CSV / JSON / Markdown formats,
plus AutoCAD-targeted formats: per-setup MLEADER text blocks and
attribute-row CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .format import format_imperial
from .store import Store

logger = logging.getLogger(__name__)

# Stable field order for csv/json so downstream sheets don't break when
# we add columns to the schema.
EXPORT_FIELDS = [
    "captured_at_iso", "device_address", "meas_id", "dev_mode", "ref_edge",
    "result_m", "result_imperial", "comp1_m", "comp2_m",
    "offset_in", "site_name", "latitude", "longitude", "loc_accuracy_m",
    "notes",
    "setup_id", "setup_label", "setup_status", "deleted_at_iso",
]


def _parse_date(s: str) -> datetime:
    """Accept YYYY-MM-DD or full ISO timestamps."""
    s = s.strip()
    if "T" in s or " " in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    return datetime.fromisoformat(s)


def _maybe_iso(ms) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec="milliseconds")


def _row_to_dict(row) -> dict:
    captured = datetime.fromtimestamp(row["captured_at"] / 1000)
    keys = row.keys()

    def get(name, default=None):
        return row[name] if name in keys else default

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
        "offset_in": get("offset_in", 0.0),
        "site_name": get("site_name"),
        "latitude": get("latitude"),
        "longitude": get("longitude"),
        "loc_accuracy_m": get("loc_accuracy_m"),
        "notes": get("notes"),
        "setup_id": get("setup_id"),
        "setup_label": get("setup_label"),
        "setup_status": get("setup_status"),
        "deleted_at_iso": _maybe_iso(get("deleted_at")),
    }


# --- format renderers ------------------------------------------------------

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
    headers = ["Time", "Result", "Imperial", "Site", "Label", "Notes"]
    out.write("| " + " | ".join(headers) + " |\n")
    out.write("|" + "|".join(["---"] * len(headers)) + "|\n")
    for r in rows_list:
        cells = [
            r["captured_at_iso"][:19].replace("T", " "),
            f"{r['result_m']:.4f} m",
            r["result_imperial"],
            r.get("site_name") or "",
            r.get("setup_label") or "",
            (r.get("notes") or "").replace("|", "\\|"),
        ]
        out.write("| " + " | ".join(cells) + " |\n")


def _group_by_setup(rows: Iterable[dict]) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        sid = r.get("setup_id")
        if sid is None:
            continue
        groups[sid].append(r)
    # Sort each setup's members by Z (result_m ascending — bottom to top)
    for sid in groups:
        groups[sid].sort(key=lambda r: r["result_m"])
    return dict(groups)


def to_mleader(rows: Iterable[dict], out) -> None:
    """One text block per setup for AutoCAD MLEADER paste. Members listed
    HIGHEST Z first (matches how vertical sections are drawn in CAD).
    Setups separated by blank lines so the user can paste them one at a
    time.

    Default usage limits to a single setup per invocation (the most recent
    confirmed one, or the one passed via --setup) — pasting multi-setup
    output into a single MLEADER is rarely what you want.
    Use --all-setups to override."""
    groups = _group_by_setup(rows)
    if not groups:
        out.write("(no setups to export — only confirmed setups are included by default; use --include-drafts)\n")
        return
    sids = sorted(groups, reverse=True)  # newest first
    for i, sid in enumerate(sids):
        members = groups[sid]
        # Sort each setup's members HIGHEST Z first (descending result_m)
        # so the visual order matches a CAD section view top-to-bottom.
        members = sorted(members, key=lambda r: r["result_m"], reverse=True)
        # Use the first member's captured_at as the setup header timestamp
        first_ts = members[0]["captured_at_iso"][:19].replace("T", " ")
        out.write(f"Setup {first_ts}\n")
        labels = [m.get("setup_label") or "(unlabeled)" for m in members]
        max_w = max(len(lbl) for lbl in labels) + 1
        for m, lbl in zip(members, labels):
            out.write(f"  {lbl:<{max_w}} {m['result_imperial']}\n")
        if i < len(sids) - 1:
            out.write("\n\f\n")


# Standard AutoCAD attribute-tag mapping for the preset labels. User can
# extend via custom labels; those land in `custom_labels_json`.
_STANDARD_LABEL_TO_TAG = {
    "bottom-of-beam":      "BOT_BEAM",
    "bottom-of-purlin":    "BOT_PURLIN",
    "bottom-of-subpurlin": "BOT_SUBPURLIN",
    "bottom-of-foil":      "BOT_FOIL",
    "bottom-of-deck":      "BOT_DECK",
}


def to_attribs(rows: Iterable[dict], out) -> None:
    """One CSV row per setup, columns matching AutoCAD block-attribute
    tags. Pipe entries are split into BOT_PIPE (imperial) and BOT_PIPE_SIZE
    (the size suffix). Custom (non-preset) labels round-trip via the
    `custom_labels_json` column."""
    groups = _group_by_setup(rows)
    sids = sorted(groups)  # oldest first for export readability

    fieldnames = [
        "setup_id", "captured_at_iso", "site_name",
        "latitude", "longitude",
        "BOT_BEAM", "BOT_PURLIN", "BOT_SUBPURLIN", "BOT_FOIL", "BOT_DECK",
        "BOT_PIPE", "BOT_PIPE_SIZE",
        "custom_labels_json",
    ]
    writer = csv.DictWriter(out, fieldnames=fieldnames)
    writer.writeheader()

    for sid in sids:
        members = groups[sid]
        first = members[0]
        row: dict = {
            "setup_id": sid,
            "captured_at_iso": first["captured_at_iso"],
            "site_name": first.get("site_name") or "",
            "latitude": first.get("latitude"),
            "longitude": first.get("longitude"),
            "BOT_BEAM": "", "BOT_PURLIN": "", "BOT_SUBPURLIN": "",
            "BOT_FOIL": "", "BOT_DECK": "",
            "BOT_PIPE": "", "BOT_PIPE_SIZE": "",
        }
        custom: dict[str, str] = {}
        for m in members:
            label = m.get("setup_label") or ""
            if not label:
                continue
            tag = _STANDARD_LABEL_TO_TAG.get(label)
            if tag is not None:
                row[tag] = m["result_imperial"]
                continue
            if label.startswith("bottom-of-pipe(") and label.endswith(")"):
                row["BOT_PIPE"] = m["result_imperial"]
                row["BOT_PIPE_SIZE"] = label[len("bottom-of-pipe("):-1]
                continue
            # Custom label
            custom[label] = m["result_imperial"]
        row["custom_labels_json"] = json.dumps(custom) if custom else ""
        writer.writerow(row)


FORMATS = {
    "csv": to_csv,
    "json": to_json,
    "md": to_markdown,
    "mleader": to_mleader,
    "attribs": to_attribs,
}


def export_main() -> None:
    from . import __version__
    parser = argparse.ArgumentParser(
        description="Export measurements from the local SQLite store.",
        epilog="Default excludes soft-deleted rows AND draft setups. "
               "Use --include-deleted / --include-drafts to widen.",
    )
    parser.add_argument("--version", "-V", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("--format", choices=sorted(FORMATS), default="csv")
    parser.add_argument("--since", metavar="DATE",
                        help="ISO date (YYYY-MM-DD) or timestamp; lower bound (inclusive)")
    parser.add_argument("--until", metavar="DATE",
                        help="ISO date or timestamp; upper bound (inclusive)")
    parser.add_argument("--site", metavar="NAME", help="match this site name exactly")
    parser.add_argument("--device", metavar="ADDR", help="match this BLE device address exactly")
    parser.add_argument("--setup", type=int, metavar="ID", help="match this setup id only")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="cap number of rows (newest first)")
    parser.add_argument("--include-deleted", action="store_true",
                        help="include soft-deleted measurements")
    parser.add_argument("--include-drafts", action="store_true",
                        help="include rows from draft (unconfirmed) setups")
    parser.add_argument("--all-setups", action="store_true",
                        help="for mleader format: emit ALL setups (default "
                             "is just the most recent matching one)")
    parser.add_argument("-o", "--output", metavar="PATH",
                        help="write to PATH instead of stdout")
    args = parser.parse_args()

    store = Store()
    try:
        since_ms = int(_parse_date(args.since).timestamp() * 1000) if args.since else None
        until_ms = int(_parse_date(args.until).timestamp() * 1000) if args.until else None
        rows = store.query(
            since_ms=since_ms, until_ms=until_ms,
            site=args.site, device_address=args.device,
            setup_id=args.setup, limit=args.limit,
            include_deleted=args.include_deleted,
            include_drafts=args.include_drafts,
        )
        dicts = [_row_to_dict(r) for r in rows]
        dicts.reverse()  # oldest-first reads better in exports
        # MLEADER default: limit to the single most recent setup unless
        # the user explicitly asked for all or pinned a specific one.
        if args.format == "mleader" and not args.all_setups and args.setup is None:
            setup_ids = [d["setup_id"] for d in dicts if d.get("setup_id")]
            if setup_ids:
                latest = max(setup_ids)
                dicts = [d for d in dicts if d.get("setup_id") == latest]
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
