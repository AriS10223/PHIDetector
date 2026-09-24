import sys, json, re, os, subprocess, urllib.request

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
    if os.environ.get("PHI_MASK_NO_CLIPBOARD"):
        return False
    try:
        subprocess.run(["clip.exe"], input=text.encode("utf-16-le"), timeout=5, check=True)
        return True
    except Exception:
        return False

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
        block(
            "Blocked: HIPAA Safe Harbor identifier(s) detected. Claude did not see this prompt.\n"
            + ("Masked version copied to clipboard. Paste and send it:\n\n" if copied else "Send this masked version instead:\n\n")
            + masked
        )

    # <-- after the team checkpoint, the Laya client block goes here -->

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
