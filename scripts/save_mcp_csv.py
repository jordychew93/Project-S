"""Save an Alpha Vantage result fetched through the MCP connector into data/.

    python scripts/save_mcp_csv.py <asset_class> <symbol> <result_file>

`result_file` may be raw CSV, a {"result": "<csv>"} JSON object, or the saved-preview JSON the
connector writes for large results ({"sample_data": "<csv>", ...}). New rows are merged into the
existing cache by date (so a file holding only the latest few rows is enough); keeps the newest 200.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def extract_csv(text):
    text = text.strip()
    if text.startswith("{"):
        obj = json.loads(text)
        text = obj.get("sample_data") or obj.get("result") or ""
    return text.replace("\r\n", "\n").strip()


def main():
    asset_class, symbol, src = sys.argv[1:4]
    lines = extract_csv(open(src).read()).split("\n")
    if not lines or not lines[0].startswith("timestamp"):
        sys.exit(f"{symbol}: not an Alpha Vantage CSV: {lines[0][:120] if lines else ''}")
    dest = os.path.join(ROOT, "data", f"{asset_class}_{symbol}.csv")
    rows = {}
    if os.path.exists(dest):
        old = open(dest).read().strip().split("\n")
        if old[0] == lines[0]:
            rows.update((r[:10], r) for r in old[1:] if r)
    rows.update((r[:10], r) for r in lines[1:] if r)
    merged = [rows[d] for d in sorted(rows, reverse=True)][:200]
    with open(dest, "w") as f:
        f.write("\n".join([lines[0]] + merged) + "\n")
    print(f"{dest}: {len(merged)} bars, latest {merged[0][:10]}")


if __name__ == "__main__":
    main()
