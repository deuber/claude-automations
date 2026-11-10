#!/usr/bin/python3
"""Send an email from you.automations@gmail.com (all my automation emails).

    send_email.py --to a@x.com [--to b@x.com] --subject "Morning Briefing — Tue Nov 10" \
        (--markdown FILE | --text FILE)        # "-" reads stdin

--markdown sends a styled HTML email with the Markdown as the plain-text fallback.
--text sends plain text. Each recipient gets their own copy (no shared To/Cc).

The Gmail app password is read from macOS Keychain at send time
(service "mac-automations-smtp"); it is never stored in a file or printed.
Exit codes: 0 all sent, 1 a send failed, 2 usage/setup error.
"""
import argparse
import html
import re
import smtplib
import ssl
import subprocess
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

ACCOUNT = "you.automations@gmail.com"
FROM_NAME = "My Automations"
KEYCHAIN_SERVICE = "mac-automations-smtp"
SMTP_HOST, SMTP_PORT = "smtp.gmail.com", 465

STYLE = {
    "h1": "font-size:22px;line-height:1.3;margin:0 0 6px;color:#1f2328",
    "h2": "font-size:18px;margin:4px 0 10px;color:#1f2328",
    "h3": ("font-size:15px;text-transform:uppercase;letter-spacing:.04em;margin:24px 0 8px;"
           "padding-bottom:6px;border-bottom:1px solid #e3e4e8;color:#2f6fed"),
    "p": "margin:8px 0",
    "ul": "margin:8px 0;padding-left:20px",
    "li": "margin:5px 0",
    "a": "color:#2f6fed;text-decoration:none;font-weight:600",
    "em": "color:#6b6f76",
}


def _inline(text):
    t = html.escape(text, quote=False)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
               lambda m: f'<a style="{STYLE["a"]}" href="{html.escape(m.group(2))}">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w/])_(.+?)_(?![\w/])", rf'<em style="{STYLE["em"]}">\1</em>', t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    return t


def markdown_to_html(md):
    """Small Markdown subset: #/##/### headings, '- ' bullets, paragraphs (single
    newlines become line breaks), **bold**, _italic_, `code`, [text](url)."""
    out, para, items = [], [], []

    def flush():
        if para:
            out.append(f'<p style="{STYLE["p"]}">' + "<br>".join(_inline(x) for x in para) + "</p>")
            para.clear()
        if items:
            out.append(f'<ul style="{STYLE["ul"]}">' + "".join(
                f'<li style="{STYLE["li"]}">{_inline(x)}</li>' for x in items) + "</ul>")
            items.clear()

    for line in md.splitlines():
        s = line.rstrip()
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            flush()
            tag = f"h{len(m.group(1))}"
            out.append(f'<{tag} style="{STYLE[tag]}">{_inline(m.group(2))}</{tag}>')
        elif re.match(r"^\s*[-*]\s+", s):
            if para:
                flush()
            items.append(re.sub(r"^\s*[-*]\s+", "", s))
        elif not s.strip():
            flush()
        else:
            if items:
                flush()
            para.append(s.strip())
    flush()
    body = "\n".join(out)
    return ('<!doctype html><html><body style="margin:0;padding:0;background:#f4f5f7">'
            '<div style="max-width:640px;margin:0 auto;padding:24px 22px;background:#ffffff;'
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;"
            f'font-size:15px;line-height:1.55;color:#1f2328">{body}</div></body></html>')


def app_password():
    r = subprocess.run(["/usr/bin/security", "find-generic-password", "-a", ACCOUNT,
                        "-s", KEYCHAIN_SERVICE, "-w"], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit(f"send_email: app password not found in Keychain "
                         f"(service {KEYCHAIN_SERVICE}, account {ACCOUNT})")
    return r.stdout.strip().replace(" ", "")


def build(to, subject, text, html_body):
    msg = EmailMessage()
    msg["From"] = formataddr((FROM_NAME, ACCOUNT))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="my.automations")
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def smtp_send(msg, password, tries=2):
    """Send one message through Gmail, retrying once after 10 s; raises on failure."""
    for attempt in range(1, tries + 1):
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=30) as s:
                s.login(ACCOUNT, password)
                s.send_message(msg)
            return
        except Exception:
            if attempt == tries:
                raise
            time.sleep(10)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", action="append", required=True)
    ap.add_argument("--subject", required=True)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--markdown")
    src.add_argument("--text")
    ap.add_argument("--dry-run", action="store_true", help="build the message and print it; don't send")
    args = ap.parse_args()

    path = args.markdown or args.text
    body = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    html_body = markdown_to_html(body) if args.markdown else None
    msgs = [build(to, args.subject, body, html_body) for to in args.to]
    if args.dry_run:
        print(msgs[0].as_string()[:4000])
        return 0

    password = app_password()
    failed = []
    for msg in msgs:
        try:
            smtp_send(msg, password)
            print(f"sent to {msg['To']}: {args.subject}")
        except Exception as e:
            failed.append(f"{msg['To']} ({type(e).__name__}: {e})")
    if failed:
        print("send_email: FAILED for " + "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
