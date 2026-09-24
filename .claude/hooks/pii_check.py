import sys, json, re, os, shutil, subprocess, urllib.request

sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # Windows default is cp1252; block messages echo the prompt

def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)

def emit(event, **fields):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, **fields}}))
    sys.exit(0)

try:
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    event = data.get("hook_event_name") or "UserPromptSubmit"
except Exception:
    block("Blocked: PHI check could not read the prompt (failing closed).")

# Order matters: when two matches overlap, the earlier-starting then longer one wins,
# so a URL containing an email or IP becomes a single token.
PATTERNS = {
    "URL": r"(?i)\bhttps?://\S+",
    "EMAIL": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "SSN": r"\b\d{3}[- ]\d{2}[- ]\d{4}\b",
    "VIN": r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b",
    "CARD": r"\b(?:\d[ -]*?){13,16}\b",
    "IP": r"\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b",
    "PHONE": r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    # Dates need a year so "10/1" deadlines and "3.14.159" versions are untouched. Any date is masked:
    # regex can't tell a birthday from a release date, and HIPAA counts every date tied to a person.
    "DATE": (
        r"\b(?:\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|\d{4}-\d{2}-\d{2})\b"
        r"|(?i:\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b)"
        r"|(?i:\b\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4}\b)"
    ),
}
TOKEN_RE = re.compile(r"\[\[PHI_([A-Z]+)_(\d+)\]\]")

# --- token map: %LOCALAPPDATA%\phi-mask\<session_id>.json (outside the OneDrive-synced project) ---

STORE_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share"), "phi-mask")
SESSION = re.sub(r"[^A-Za-z0-9-]", "_", data.get("session_id") or "default")
MAP_PATH = os.path.join(STORE_DIR, SESSION + ".json")
LOCK_PATH = MAP_PATH + ".lock"

class MapLock:
    # Parallel tool calls fire PostToolUse hooks concurrently; serialize read-modify-write.
    def __enter__(self):
        os.makedirs(STORE_DIR, exist_ok=True)
        self.f = open(LOCK_PATH, "a+b")
        try:
            import msvcrt
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_LOCK, 1)
        except ImportError:
            pass
        return self
    def __exit__(self, *exc):
        try:
            import msvcrt
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except (ImportError, OSError):
            pass
        self.f.close()

def load_map():
    try:
        with open(MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"tokens": {}, "counters": {}}

def save_map(m):
    tmp = MAP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f)
    os.replace(tmp, MAP_PATH)

# --- mask / unmask ---

def find_spans(text):
    spans = []
    for label, pattern in PATTERNS.items():
        for m in re.finditer(pattern, text):
            spans.append((m.start(), m.end(), label))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept, end = [], -1
    for s in spans:
        if s[0] >= end:
            kept.append(s)
            end = s[1]
    return kept

def mask(text, m):
    """Replace known values and new regex matches with tokens. Mutates m; returns new text."""
    by_value = {v: t for t, v in m["tokens"].items()}
    for value in sorted(by_value, key=len, reverse=True):
        text = text.replace(value, by_value[value])
    out, pos = [], 0
    for start, end, label in find_spans(text):
        value = text[start:end]
        token = by_value.get(value)
        if token is None:
            n = m["counters"].get(label, 0) + 1
            m["counters"][label] = n
            token = f"[[PHI_{label}_{n}]]"
            m["tokens"][token] = value
            by_value[value] = token
        out += [text[pos:start], token]
        pos = end
    out.append(text[pos:])
    return "".join(out)

def unmask(text, m):
    return TOKEN_RE.sub(lambda t: m["tokens"].get(t.group(0), t.group(0)), text)

def walk(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [walk(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: walk(v, fn) for k, v in obj.items()}
    return obj

def copy_to_clipboard(text):
    """Best effort. Returns True if the masked prompt landed on the clipboard."""
    if os.environ.get("PHI_MASK_NO_CLIPBOARD"):
        return False
    if os.name == "nt":
        args, payload = ["clip.exe"], text.encode("utf-16-le")
    else:
        cmd = shutil.which("pbcopy") or shutil.which("xclip") or shutil.which("wl-copy")
        if not cmd:
            return False
        args = [cmd, "-selection", "clipboard"] if cmd.endswith("xclip") else [cmd]
        payload = text.encode("utf-8")
    try:
        subprocess.run(args, input=payload, timeout=5, check=True)
        return True
    except Exception:
        return False

def laya_check(text):
    """Second layer for what regex can't see: names, addresses, MRN/plan/device numbers.
    On-device only (127.0.0.1). Fails OPEN: a server hiccup must never block every prompt.
    Tokens are stripped first: they hold no PHI, but Laya scores them as ID numbers (p~0.96)."""
    timeout = 10  # measured worst case ~6-7s for a max-chunk prompt; hook timeout is 15s
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            "http://127.0.0.1:8420/check",
            data=json.dumps({"text": TOKEN_RE.sub("", text)}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None

# --- event handlers ---

# Edit validates old_string against the real file before PreToolUse can unmask it, so a
# token-bearing Edit always fails with "string not found". Write is unmasked correctly.
WRITE_NOT_EDIT = (
    "To change a file whose contents contain these tokens, use the Write tool with the full new "
    "file content (tokens included), not Edit: Edit cannot match tokens against the file."
)

def on_prompt():
    prompt = data.get("prompt") or ""
    if find_spans(prompt):
        with MapLock():
            m = load_map()
            masked = mask(prompt, m)
            save_map(m)
        copied = copy_to_clipboard(masked)
        # Laya has no spans, so anything it still flags in the masked copy (a name, an address)
        # would hard-block the resend. Say so now instead of bouncing the user twice.
        r = laya_check(masked)
        still = ""
        if r and r.get("blocked"):
            still = (f"Laya would still flag a possible '{r['category']}' (p={r['phi_prob']:.2f}) "
                     "in the masked copy. Edit that part before resending.\n")
        block(
            "Blocked: HIPAA Safe Harbor identifier(s) detected. Claude did not see this prompt.\n"
            + still
            + ("Masked version copied to clipboard. Paste and send it:\n\n" if copied else "Send this masked version instead:\n\n")
            + masked
        )

    r = laya_check(prompt)
    if r and r.get("blocked"):
        block(f"Blocked: Laya flagged a possible '{r['category']}' Safe Harbor identifier "
              f"(p={r['phi_prob']:.2f}). Laya gives no span, so this cannot be auto-masked.")

    if TOKEN_RE.search(prompt):
        emit("UserPromptSubmit", additionalContext=(
            "Tokens like [[PHI_EMAIL_n]] (n is a number) are masked placeholders for protected health information. "
            "Treat them as opaque values and reproduce them exactly (same brackets and spelling) wherever the "
            "real value is needed, including in file contents and commands; hooks substitute the real values "
            "automatically. Never try to guess or reconstruct the underlying value. " + WRITE_NOT_EDIT
        ))
    sys.exit(0)

def on_pre_tool():
    tool_input = data.get("tool_input")
    if not TOKEN_RE.search(json.dumps(tool_input, ensure_ascii=False)):
        sys.exit(0)
    m = load_map()
    emit("PreToolUse", updatedInput=walk(tool_input, lambda s: unmask(s, m)))

def on_post_tool():
    response = data.get("tool_response")
    with MapLock():
        m = load_map()
        before = len(m["tokens"])
        masked = walk(response, lambda s: mask(s, m))
        if len(m["tokens"]) != before:
            save_map(m)
    if masked != response:
        emit("PostToolUse", updatedToolOutput=masked, additionalContext=(
            "This output had PHI replaced by [[PHI_...]] tokens; reproduce them exactly where needed. " + WRITE_NOT_EDIT
        ))
    sys.exit(0)

def on_display():
    delta = data.get("delta") or ""
    if not TOKEN_RE.search(delta):
        sys.exit(0)
    emit("MessageDisplay", displayContent=unmask(delta, load_map()))

def on_session_end():
    for p in (MAP_PATH, LOCK_PATH):
        try:
            os.remove(p)
        except OSError:
            pass
    sys.exit(0)

HANDLERS = {
    "UserPromptSubmit": on_prompt,
    "PreToolUse": on_pre_tool,
    "PostToolUse": on_post_tool,
    "MessageDisplay": on_display,
    "SessionEnd": on_session_end,
}

handler = HANDLERS.get(event)
if handler is None:
    sys.exit(0)
if event == "UserPromptSubmit":
    try:
        handler()
    except SystemExit:
        raise
    except Exception:
        block("Blocked: PHI check hit an internal error (failing closed).")
else:
    try:
        handler()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # never break tools or display over a masking hiccup
