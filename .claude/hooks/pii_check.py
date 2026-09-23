import sys, json, re, urllib.request

def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)

try:
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    prompt = data.get("prompt") or ""
except Exception:
    block("Blocked: PHI check could not read the prompt (failing closed).")

PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "ssn": r"\b\d{3}[- ]\d{2}[- ]\d{4}\b",
    "phone_or_fax": r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    "account_or_card_number": r"\b(?:\d[ -]*?){13,16}\b",
    "url": r"(?i)\bhttps?://\S+",
    "ip_address": r"\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b",
    "vehicle_vin": r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b",
}
for label, pattern in PATTERNS.items():
    if re.search(pattern, prompt):
        block(f"Blocked: possible {label} detected (HIPAA Safe Harbor identifier).")

# Local Laya server (server.py) for categories regex can't catch: names, addresses, dates, free-form IDs.
# Measured on the demo machine: ~0.5s for a short prompt, ~6.6s worst case (4 chunks, LAYA_THREADS=24).
# Must stay under the 15s hook timeout in settings.json.
LAYA_TIMEOUT = 10
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "http://127.0.0.1:8420/check",
        data=json.dumps({"text": prompt}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=LAYA_TIMEOUT) as resp:
        r = json.loads(resp.read())
    if r.get("blocked"):
        block(f"Blocked: Laya flagged a possible '{r['category']}' Safe Harbor identifier (p={r['phi_prob']:.2f}).")
except Exception:
    pass  # fail OPEN — a Laya/server hiccup must never block every prompt

sys.exit(0)
