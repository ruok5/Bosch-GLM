"""Attach free-form notes to existing stored measurements."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .format import format_imperial
from .store import Store


def notes_main() -> None:
    parser = argparse.ArgumentParser(
        description="Attach a note to a stored measurement.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("set", help="set or replace the note on a measurement")
    add.add_argument("--meas-id", type=int, required=True)
    add.add_argument("--device", help="device BLE address (omit if there's only one)")
    add.add_argument("text", help='the note text (use "-" to read from stdin)')

    show = sub.add_parser("show", help="print the note on a measurement")
    show.add_argument("--meas-id", type=int, required=True)
    show.add_argument("--device")

    list_p = sub.add_parser("list", help="list measurements that have notes")
    list_p.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()
    store = Store()
    try:
        if args.cmd == "set":
            text = sys.stdin.read().rstrip("\n") if args.text == "-" else args.text
            device = args.device or _only_device(store)
            ok = store.set_note(device, args.meas_id, text)
            if not ok:
                print(f"no measurement with meas_id={args.meas_id} on device {device}",
                      file=sys.stderr)
                sys.exit(1)
            print(f"note set on meas_id {args.meas_id} ({device})")
        elif args.cmd == "show":
            device = args.device or _only_device(store)
            row = store.conn.execute(
                "SELECT result_m, captured_at, notes FROM measurements "
                "WHERE device_address=? AND meas_id=?",
                (device, args.meas_id),
            ).fetchone()
            if row is None:
                print(f"no measurement with meas_id={args.meas_id}", file=sys.stderr)
                sys.exit(1)
            ts = datetime.fromtimestamp(row["captured_at"] / 1000).isoformat(timespec="seconds")
            print(f"#{args.meas_id}  {ts}  {row['result_m']:.4f} m  ({format_imperial(row['result_m'])})")
            print(f"  note: {row['notes'] or '(none)'}")
        elif args.cmd == "list":
            rows = store.conn.execute(
                "SELECT device_address, meas_id, result_m, captured_at, notes "
                "FROM measurements WHERE notes IS NOT NULL AND notes != '' "
                "ORDER BY captured_at DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            if not rows:
                print("(no notes yet)")
                return
            for r in rows:
                ts = datetime.fromtimestamp(r["captured_at"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                print(f"#{r['meas_id']:>4}  {ts}  {r['result_m']:.4f}m  {r['notes']}")
    finally:
        store.close()


def _only_device(store: Store) -> str:
    """Return the single distinct device address in the store, or error out
    if there are multiple — caller must then pass --device explicitly."""
    rows = store.conn.execute(
        "SELECT DISTINCT device_address FROM measurements"
    ).fetchall()
    if not rows:
        print("no measurements in store yet", file=sys.stderr)
        sys.exit(1)
    if len(rows) > 1:
        addrs = [r["device_address"] for r in rows]
        print(f"multiple devices in store; pass --device. Known: {addrs}", file=sys.stderr)
        sys.exit(1)
    return rows[0]["device_address"]


if __name__ == "__main__":
    notes_main()
