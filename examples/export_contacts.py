#!/usr/bin/env python3
"""export_contacts — dump all your contacts to CSV or JSON.

python examples/export_contacts.py                 # -> contacts.csv
python examples/export_contacts.py -o friends.json --format json
"""

from __future__ import annotations

import argparse
import csv
import json

from _common import add_auth_args, all_contacts, load


def main() -> None:
    p = add_auth_args(argparse.ArgumentParser(description=__doc__))
    p.add_argument("-o", "--output", default="contacts.csv", help="output file")
    p.add_argument("--format", choices=["csv", "json"], default="csv")
    args = p.parse_args()
    api = load(args)
    try:
        rows = []
        for mid, w in all_contacts(api).items():
            c = w.get("contact", w) if isinstance(w, dict) else {}
            rows.append(
                {
                    "mid": mid,
                    "displayName": c.get("displayName", ""),
                    "displayNameOverridden": c.get("displayNameOverridden", ""),
                    "statusMessage": c.get("statusMessage", ""),
                    "official": bool(c.get("capableBuddy")),
                }
            )
        rows.sort(key=lambda r: (r["displayNameOverridden"] or r["displayName"]).lower())
        if args.format == "json":
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(rows, fh, ensure_ascii=False, indent=2)
        else:
            with open(args.output, "w", encoding="utf-8-sig", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["mid"])
                wr.writeheader()
                wr.writerows(rows)
        print(f"exported {len(rows)} contacts -> {args.output}")
    finally:
        api.close()


if __name__ == "__main__":
    main()
