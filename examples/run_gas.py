#!/usr/bin/python3
"""Weekly gas prices: Claude fetches Way.com -> validated JSON -> compare_prices.py -> email.

    run_gas.py             # full run: writes ~/reports JSON + CSV, emails EMAIL_TO
    run_gas.py --dry-run   # fetch and build the email against a scratch CSV copy; send nothing

Way.com blocks plain scripted requests (Cloudflare 403), so `claude -p` fetches the
station detail pages with its WebFetch tool (its only allowed tool) and returns the
raw numbers. The script validates them before anything is written or emailed.
Pump price = strikeOffPrice when present, else price (never the cashback price).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import send_email  # noqa: E402

EMAIL_TO = ["you@example.com"]
CLAUDE = os.path.join(HOME, ".local", "bin", "claude")
JSON_PATH = os.path.join(HOME, "reports", "gas_prices_today.json")
CSV_PATH = os.path.join(HOME, "reports", "gas_prices.csv")
COMPARE = os.path.join(HERE, "compare_prices.py")
STATIONS = [  # Way.com station id, name, address (must match compare_prices.py)
    ("0000001", "Station in town", "123 Main St, Your Town, ST 00000"),
    ("0000002", "Station a short drive away", "456 Other Rd, Your Town, ST 00000"),
    ("0000003", "Third station", "789 Highway Dr, Next Town, ST 00000"),
]
URL = "https://www.way.com/way-ev-consumer/v2/public/gas/{}/details"
MIN_PRICE, MAX_PRICE = 2.0, 12.0

PROMPT = """Use the WebFetch tool to fetch each URL below. Each page lists fuel types; find the "Regular" fuel
type and copy its "price" and "strikeOffPrice" values EXACTLY as they appear (strikeOffPrice may be absent).
Do not guess or estimate. If a page can't be fetched or has no Regular entry, use null for both values.
Reply with ONLY this JSON, no prose:
{"stations": [{"id": "<id>", "price": <number or null>, "strikeOffPrice": <number or null>}]}

""" + "\n".join(f"- id {sid}: {URL.format(sid)}" for sid, _, _ in STATIONS)


def fetch_prices():
    r = subprocess.run([CLAUDE, "-p", PROMPT, "--allowedTools", "WebFetch", "--output-format", "text"],
                       capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL, cwd=HOME)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p exit {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        raise RuntimeError(f"claude -p returned no JSON: {r.stdout[-300:]}")
    got = {str(s.get("id")): s for s in json.loads(m.group(0))["stations"]}
    out, problems = {}, []
    for sid, name, _ in STATIONS:
        s = got.get(sid) or {}
        pump = s.get("strikeOffPrice") if s.get("strikeOffPrice") is not None else s.get("price")
        try:
            pump = float(pump)
        except (TypeError, ValueError):
            problems.append(f"{name}: no price")
            continue
        if not MIN_PRICE <= pump <= MAX_PRICE:
            problems.append(f"{name}: implausible price {pump}")
            continue
        out[sid] = round(pump, 2)
    return out, problems


def write_json(prices, today):
    data = {"date": today, "stations": [
        {"name": name, "address": addr,
         "regular_price": f"${prices[sid]:.2f}" if sid in prices else "not found",
         "source": "Way.com", "price_age": "not stated",
         "crosscheck_source": "none", "crosscheck_price": "none"}
        for sid, name, addr in STATIONS]}
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    subject = f"Gas Prices — {datetime.now():%a %b %-d}"
    try:
        prices, problems = fetch_prices()
    except Exception as e:
        prices, problems = {}, [f"fetch failed: {type(e).__name__}: {e}"]
    print(f"prices: {prices} | problems: {problems or 'none'}")

    if not prices:
        body = f"No gas prices were collected today ({today}). " + "; ".join(problems)
    else:
        data = write_json(prices, today)
        work = tempfile.mkdtemp() if args.dry_run else None
        json_path = os.path.join(work, "gas.json") if work else JSON_PATH
        csv_path = os.path.join(work, "gas.csv") if work else CSV_PATH
        if work and os.path.exists(CSV_PATH):
            shutil.copy(CSV_PATH, csv_path)
        json.dump(data, open(json_path, "w"), indent=2)
        r = subprocess.run(["/usr/bin/python3", COMPARE, json_path, "--csv-path", csv_path],
                           capture_output=True, text=True, timeout=120)
        body = r.stdout if r.returncode == 0 else f"Gas price script failed on {today}:\n{r.stderr[-1000:]}"
        if problems:
            body += "\n\nNote: " + "; ".join(problems)
    if args.dry_run:
        print(subject)
        print(body)
        return 0 if prices and not problems else 1
    pw = send_email.app_password()
    for to in EMAIL_TO:
        send_email.smtp_send(send_email.build(to, subject, body, None), pw)
        print(f"sent to {to}: {subject}")
    return 0 if prices and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
