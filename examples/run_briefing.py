#!/usr/bin/python3
"""Morning briefing: weather, sun and my calendar, emailed. No AI involved.

    run_briefing.py                 # build and send to RECIPIENTS
    run_briefing.py --to a@x.com    # send only to the given address(es)
    run_briefing.py --dry-run       # build, print, send nothing

Weather and sun come from morning_briefing_data.py (NWS API + calculated sun
times). The calendar comes from icalBuddy (read-only). Run it via launchd
through /usr/bin/osascript so macOS uses osascript's Calendar permission.
"""
import argparse
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import morning_briefing_data as data  # noqa: E402
import send_email  # noqa: E402

# ---- Settings (edit these) ----
RECIPIENTS = ["you@example.com"]
ICALBUDDY = "/opt/homebrew/bin/icalBuddy"
EVT, SEP = "@@EVT|", "@@SEP@@"  # icalBuddy -ps takes "<delim><separator><delim>"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def fetch_events():
    """Return ({date: [line, ...]}, error_or_None) for today and tomorrow."""
    cmd = [ICALBUDDY, "-nc", "-nrd", "-npn", "-b", EVT, "-ps", f"|{SEP}|",
           "-po", "datetime,title,location", "-iep", "datetime,title,location",
           "-df", "%Y-%m-%d", "-tf", "%I:%M %p", "eventsToday+1"]  # icalBuddy has no %-I
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        return {}, f"icalBuddy didn't run ({type(e).__name__})"
    out = (r.stdout or "") + (r.stderr or "")
    if "No calendars" in out:
        return {}, "Calendar access is missing for this job (macOS returned \"No calendars\")."
    if r.returncode != 0:
        return {}, f"The calendar couldn't be read today (icalBuddy exit {r.returncode})."
    events = {}
    for chunk in out.split(EVT)[1:]:
        parts = [p.strip() for p in chunk.replace("\n", " ").split(SEP) if p.strip()]
        when = next((p for p in parts if DATE_RE.match(p)), None)
        rest = [p for p in parts if p is not when]
        if not when or not rest:
            continue
        title, location = rest[0], (rest[1] if len(rest) > 1 else "")
        dates = DATE_RE.findall(when)
        times = [t.lstrip("0") for t in re.findall(r"\d{1,2}:\d{2} [AP]M", when)]
        start = date.fromisoformat(dates[0])
        line = f"{times[0]}–{times[1]}" if len(times) >= 2 else (times[0] if times else "All day")
        text = f"- {line} — {title}" + (f" ({location})" if location else "")
        if len(dates) >= 2 and dates[1] != dates[0] and not times:  # multi-day all-day event
            end = date.fromisoformat(dates[1])
            d = start
            while d <= end:
                events.setdefault(d, []).append(text)
                d += timedelta(days=1)
        else:
            events.setdefault(start, []).append(text)
    for d in events:  # list duplicate entries once, keep order
        events[d] = list(dict.fromkeys(events[d]))
    return events, None


CAL_STATUS = "calendar: not read"


def calendar_block(today):
    global CAL_STATUS
    events, err = fetch_events()
    tomorrow = today + timedelta(days=1)
    CAL_STATUS = (f"calendar: ERROR {err}" if err else
                  f"calendar: {len(events.get(today, []))} events today, {len(events.get(tomorrow, []))} tomorrow")
    lines = ["### My Calendar", "", "_Events from my calendar._", ""]
    if err:
        return lines + [err]
    for label, d in (("Today", today), ("Tomorrow", today + timedelta(days=1))):
        lines += [f"**{label} ({d:%a %b %-d})**", ""]
        lines += events.get(d) or ["- No events"]
        lines.append("")
    return lines


def build(today, tz):
    md = [f"## Good morning — {today:%A, %B %-d, %Y}", ""]
    md += data.weather_block(tz, today) + [""]
    md += data.sun_block(tz, today) + [""]
    md += calendar_block(today)
    return f"Morning Briefing — {today:%a %b %-d}", "\n".join(md).rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", action="append")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    tz = ZoneInfo(data.TIMEZONE)
    today = datetime.now(tz).date()
    subject, md = build(today, tz)
    print(CAL_STATUS)
    if args.dry_run:
        print(subject)
        print(md)
        return 0
    html_body = send_email.markdown_to_html(md)
    password = send_email.app_password()
    failed = []
    for to in (args.to or RECIPIENTS):
        msg = send_email.build(to, subject, md, html_body)
        try:
            send_email.smtp_send(msg, password)
            print(f"sent to {to}: {subject}")
        except Exception as e:
            failed.append(f"{to} ({type(e).__name__}: {e})")
    if failed:
        print("FAILED for " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
