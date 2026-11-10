#!/usr/bin/python3
"""Daily job search: prefetch -> Claude ranks the shortlist -> record -> email the log.

    run_jobsearch.py            # full run, emails the log to EMAIL_TO
    run_jobsearch.py --no-email # everything except the email (for testing)

1. job_board_prefetch.py sweeps ~/job-search/boards.txt and writes today's
   UNRANKED log plus prefetch/<date>.jobs.json / .skips.json (the safety net).
2. `claude -p` (my Claude plan) gets profile.md + the candidates and returns
   keep/skip decisions as JSON. It is given no tools.
3. The decisions are checked (every candidate accounted for) and recorded with
   jobsearch.py record, which rewrites today's log ranked.
4. Today's log is emailed. If Claude fails, the unranked log is emailed anyway
   and the run exits non-zero so run_job.sh alerts me.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
JS = os.path.join(HOME, "job-search")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bin"))
import send_email  # noqa: E402

EMAIL_TO = ["you.careers@example.com"]
CLAUDE = os.path.join(HOME, ".local", "bin", "claude")
CLAUDE_TIMEOUT = 600

PROMPT = """You are screening job postings for a job seeker. Use ONLY the profile and candidates below.
Do not use any tools. Reply with ONLY a JSON object, no prose, in exactly this shape:
{"decisions": [{"url": "<candidate url>", "decision": "keep" | "skip", "note": "<one short line>"}]}

Rules:
- One decision for EVERY candidate url, copied exactly.
- "keep" = fits the profile's target titles and passes every hard disqualifier. The note is a one-line fit note;
  add "eligibility unverified" if the snippets don't settle California eligibility or the schedule.
- "skip" = fails a rule. The note says which rule, e.g. "pre-sales / not a target title", "CA not eligible",
  "requires regular travel", "Eastern-time hours", "US citizenship required".
- Never invent facts that aren't in the candidate text.

=== PROFILE (profile.md) ===
{profile}

=== CANDIDATES ===
{candidates}
"""


def run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def ask_claude(profile, candidates):
    prompt = PROMPT.replace("{profile}", profile).replace("{candidates}", json.dumps(candidates, indent=1))
    r = subprocess.run([CLAUDE, "-p", prompt, "--output-format", "text"], capture_output=True,
                       text=True, timeout=CLAUDE_TIMEOUT, stdin=subprocess.DEVNULL, cwd=JS)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p exit {r.returncode}: {(r.stderr or r.stdout)[-300:]}")
    m = re.search(r"\{.*\}", r.stdout, re.S)
    if not m:
        raise RuntimeError(f"claude -p returned no JSON: {r.stdout[-300:]}")
    return json.loads(m.group(0))["decisions"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-email", action="store_true")
    args = ap.parse_args()
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    jobs_path = os.path.join(JS, "prefetch", f"{today}.jobs.json")
    skips_path = os.path.join(JS, "prefetch", f"{today}.skips.json")
    log_path = os.path.join(JS, "logs", f"{today}.md")

    # 1. Prefetch (writes the unranked log: the safety net)
    pf = run(["/usr/bin/python3", os.path.join(HERE, "job_board_prefetch.py")], 900)
    print((pf.stdout or "").splitlines()[1] if pf.stdout else "prefetch: no output")
    if pf.returncode != 0 or not os.path.exists(jobs_path):
        raise SystemExit(f"prefetch failed (exit {pf.returncode}): {(pf.stderr or pf.stdout)[-400:]}")
    candidates = json.load(open(jobs_path))
    skips = json.load(open(skips_path))
    print(f"candidates for Claude: {len(candidates)}")

    # 2-3. Claude decides; check and record
    error = None
    if candidates:
        try:
            by_url = {d.get("url"): d for d in ask_claude(open(os.path.join(JS, "profile.md")).read(), candidates)}
            keep = []
            for c in candidates:
                d = by_url.get(c["url"])
                if not d:  # Claude missed it: keep it, clearly marked
                    keep.append({**c, "why": "UNRANKED (Claude gave no decision) · " + c.get("why", "")})
                elif d.get("decision") == "keep":
                    keep.append({**c, "why": d.get("note") or "fits the profile"})
                else:
                    skips.append({"company": c["company"], "title": c["title"],
                                  "reason": d.get("note") or "skipped by Claude"})
            json.dump(keep, open(jobs_path, "w"), indent=1)
            json.dump(skips, open(skips_path, "w"), indent=1)
            rec = run(["/usr/bin/python3", os.path.join(JS, "jobsearch.py"), "record", "--date", today,
                       "--input", jobs_path, "--skipped", skips_path], 120)
            if rec.returncode != 0:
                raise RuntimeError(f"record failed: {(rec.stderr or rec.stdout)[-300:]}")
            print(f"ranked: {len(keep)} kept, {len(candidates) - len(keep)} skipped by Claude")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            print(f"Claude ranking FAILED, emailing the unranked log instead: {error}")
    else:
        rec = run(["/usr/bin/python3", os.path.join(JS, "jobsearch.py"), "record", "--date", today,
                   "--input", jobs_path, "--skipped", skips_path], 120)
        print("no candidates today; log recorded")

    # 4. Email today's log
    if not args.no_email:
        md = open(log_path).read()
        subject = f"Job Search — {datetime.now():%a %b %-d}" + (" (unranked)" if error else "")
        html_body = send_email.markdown_to_html(md)
        pw = send_email.app_password()
        for to in EMAIL_TO:
            send_email.smtp_send(send_email.build(to, subject, md, html_body), pw)
            print(f"sent to {to}: {subject}")
    return 1 if error else 0


if __name__ == "__main__":
    sys.exit(main())
