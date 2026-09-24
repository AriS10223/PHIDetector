"""UserPromptSubmit hook: HIPAA Safe Harbor identifier guard.

Claude Code cannot let a UserPromptSubmit hook rewrite the prompt (verified against the
2.1.281 binary: there is no updatedPrompt-style field). The only way to keep an identifier
off the wire is exit 2, which erases the prompt. So "redact" here means:

  1. mask every regex hit with *** ,
  2. put the masked prompt on the clipboard (pbcopy on macOS),
  3. block the original and show the masked copy, ready to paste and resend.

Laya (server.py) returns a category + probability, not character spans, so its hits cannot
be masked and remain a plain block.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.request

MASK = "***"

PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "ssn": r"\b\d{3}[- ]\d{2}[- ]\d{4}\b",
    "phone_or_fax": r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    "account_or_card_number": r"\b(?:\d[ -]*?){13,16}\b",
    "url": r"(?i)\bhttps?://\S+",
    "ip_address": r"\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b",
    "vehicle_vin": r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b",
    # Dates need a year so "10/1" deadlines and "3.14.159" versions are untouched. Any date is masked:
    # regex can't tell a birthday from a release date, and HIPAA counts every date tied to a person.
    "date": (
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b"
        r"|(?i:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b)"
        r"|(?i:\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b)"
    ),
}

# Order matters: a URL can contain an email or IP, so mask the wider pattern first. Once a span
# is *** the narrower patterns can no longer match inside it, so no overlap bookkeeping is needed.
REDACT_ORDER = ["url", "email", "vehicle_vin", "account_or_card_number", "ssn", "phone_or_fax", "ip_address", "date"]


def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def redact(text):
    """Return (masked_text, {label: count}) for every regex identifier in text."""
    counts = {}
    for label in REDACT_ORDER:
        text, n = re.subn(PATTERNS[label], MASK, text)
        if n:
            counts[label] = n
    return text, counts


def copy_to_clipboard(text):
    """Best effort. Returns True if the masked prompt landed on the clipboard."""
    cmd = shutil.which("pbcopy") or shutil.which("xclip") or shutil.which("wl-copy")
    if not cmd:
        return False
    args = [cmd, "-selection", "clipboard"] if cmd.endswith("xclip") else [cmd]
    try:
        subprocess.run(args, input=text.encode("utf-8"), check=True, timeout=3)
        return True
    except Exception:
        return False


def laya_check(prompt):
    """Second layer for what regex can't see: names, addresses, dates, MRN/plan/device numbers.
    On-device only (127.0.0.1). Fails OPEN: a server hiccup must never block every prompt."""
    timeout = 10  # measured worst case ~6-7s for a max-chunk (4 x ~2.9k chars) prompt; hook timeout is 15s
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            "http://127.0.0.1:8420/check",
            data=json.dumps({"text": prompt}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
        prompt = data.get("prompt") or ""
    except Exception:
        block("Blocked: PHI check could not read the prompt (failing closed).")

    masked, counts = redact(prompt)
    if counts:
        summary = ", ".join(f"{n} {label}" for label, n in counts.items())
        on_clipboard = copy_to_clipboard(masked)
        hint = "Redacted copy is on your clipboard: paste and resend." if on_clipboard \
            else "Copy the redacted version below and resend."
        # Laya sees the masked copy: anything it still flags (a name, an address) has no span to
        # mask and would hard-block the resend. Say so now instead of bouncing the user twice.
        r = laya_check(masked)
        still = ""
        if r and r.get("blocked"):
            still = (f"\nLaya would still flag a possible '{r['category']}' (p={r['phi_prob']:.2f}) "
                     f"in the redacted copy. Edit that part before resending.")
        block(
            f"Blocked: redacted {summary} (HIPAA Safe Harbor). {hint}{still}\n"
            f"--- redacted prompt ---\n{masked}"
        )

    r = laya_check(prompt)
    if r and r.get("blocked"):
        block(f"Blocked: Laya flagged a possible '{r['category']}' Safe Harbor identifier "
              f"(p={r['phi_prob']:.2f}). Laya gives no span, so this cannot be auto-redacted.")

    sys.exit(0)


if __name__ == "__main__":
    main()
