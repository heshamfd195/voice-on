#!/usr/bin/env python3
"""voice-on: Claude Code reads its replies out loud.

Hooks (hooks/hooks.json runs these):
  speak.py                 Stop hook: speak the last reply, in the background.
  speak.py --stop          UserPromptSubmit hook: a new prompt stops the speech.

By hand (the voice skill runs these):
  speak.py --on [--all]    voice on for this chat (or for every chat)
  speak.py --off [--all]   voice off for this chat (or for every chat)
  speak.py --status        what is on, and which engine and voice
  speak.py --repeat        read this chat's last reply again
  speak.py --halt          stop speaking now
  speak.py --say "text"    speak a test sentence with the current settings
  speak.py --voices        list the voices of the current engine
  speak.py --setup kokoro  install the Kokoro neural voice (about 120 MB)
  speak.py --clean FILE    print what would be said for a text file or transcript

Add --session ID to name the chat; the skill passes it. Without it the
CLAUDE_CODE_SESSION_ID environment variable is used.

Speech only plays where the "on" file lists the chat id (or "*" for every chat).
Settings and state live in ~/.claude/voice-on (set VOICE_ON_HOME to move them).
Python standard library only; the Kokoro engine runs in its own venv.
"""
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the plugin
DATA = os.path.expanduser(os.environ.get("VOICE_ON_HOME") or "~/.claude/voice-on")
ON_FILE = os.path.join(DATA, "on")
PID_FILE = os.path.join(DATA, "speaking.pid")
CONFIG_FILE = os.path.join(DATA, "config.json")
LOG_FILE = os.path.join(DATA, "voice.log")
LAST_DIR = os.path.join(DATA, "last")
VENV_PYTHON = os.path.join(DATA, "venv", "bin", "python")
MODELS_DIR = os.path.join(DATA, "models")

DEFAULTS = {
    "engine": "auto",
    "say": {"voice": "", "rate": 190},
    "espeak": {"voice": "en-us", "rate": 175},
    "kokoro": {"voice": "af_heart", "speed": 1.1, "lang": "en-us"},
}

KOKORO_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = {"kokoro-v1.0.int8.onnx": 92361271, "voices-v1.0.bin": 28214398}

SESSION = ""  # the chat id, set in main()


# ---------- settings and state ----------

def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    try:
        with open(CONFIG_FILE) as f:
            user = json.load(f)
    except (OSError, ValueError):
        user = {}
    for key, value in user.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def write_default_config():
    if not os.path.exists(CONFIG_FILE):
        os.makedirs(DATA, exist_ok=True)
        with open(CONFIG_FILE, "w") as f:
            json.dump(DEFAULTS, f, indent=2)
            f.write("\n")


def log(msg):
    try:
        os.makedirs(DATA, exist_ok=True)
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 200000:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def read_on():
    try:
        with open(ON_FILE) as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return None


def write_on(ids):
    if ids:
        os.makedirs(DATA, exist_ok=True)
        with open(ON_FILE, "w") as f:
            f.write("\n".join(sorted(ids)) + "\n")
    elif os.path.exists(ON_FILE):
        os.remove(ON_FILE)


def is_on(session_id):
    ids = read_on()
    return ids is not None and ("*" in ids or session_id in ids)


def stop_speaking():
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return False
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    try:
        os.killpg(pid, signal.SIGTERM)  # the speaker and its engine and player
        return True
    except OSError:
        return False


# ---------- the reply ----------

def last_reply(transcript_path):
    """Text of the final assistant reply: trailing text blocks after the last tool call."""
    try:
        with open(transcript_path) as f:
            lines = f.readlines()
    except OSError:
        return ""
    parts = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if kind == "user":
            break
        if kind != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        blocks = content or []
        if any(b.get("type") == "tool_use" for b in blocks):
            break  # text before a tool call is narration, not the reply
        parts.extend(reversed([b.get("text", "") for b in blocks if b.get("type") == "text"]))
    return "\n\n".join(reversed(parts)).strip()


# ---------- turning markdown into speech ----------

# Each repeat ends in its own slash: "(?:/?x+)+" backtracks for minutes on a long
# hyphenated name with no slash in it.
PATH_RE = re.compile(r"(?:~|\.{1,2})?/?(?:[\w.@+-]+/)+([\w.@+-]+)")
URL_RE = re.compile(r"https?://\S+")
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def shorten_path(m):
    whole, name = m.group(0), m.group(1)
    if "/" not in whole:
        return whole
    # short pieces are words, not a path: and/or, f/m/x, CI/CD, I/O
    if all(len(piece) <= 3 for piece in whole.split("/")):
        return whole
    return name


def clean_inline(s):
    s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)            # images
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)             # links -> their text
    s = re.sub(r"<(https?://[^>]+)>", "a link", s)
    s = URL_RE.sub("a link", s)
    s = re.sub(r"\b(link|URL|url)\s+a link", r"\1", s)
    s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)                    # html tags
    s = s.replace("`", "")
    s = PATH_RE.sub(shorten_path, s)                           # paths -> file name
    s = re.sub(r"\b([\w-]+\.\w{1,5}):(\d+)\b", r"\1 line \2", s)
    s = re.sub(r"(\*\*|__|~~)", "", s)
    s = re.sub(r"(?<![\w*])\*(?!\s)([^*\n]+?)\*(?!\w)", r"\1", s)
    s = re.sub(r"(?<!\w)_(?!\s)([^_\n]+?)_(?!\w)", r"\1", s)
    s = s.replace("→", " to ").replace("←", " from ").replace("⇒", " so ")
    s = s.replace(" — ", ", ").replace("—", ", ").replace(" – ", ", ")
    s = s.replace("&", " and ").replace("≈", "about ").replace("×", " times ")
    s = EMOJI_RE.sub("", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    return s.strip()


# tables

MAX_ROWS = 12  # a longer table: the first rows, then how many more are on screen
MARKS = {"✓": "yes", "✔": "yes", "✅": "yes", "☑": "yes", "✗": "no", "✘": "no",
         "❌": "no", "—": "", "–": "", "-": "", "n/a": "none", "N/A": "none"}


def table_cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", line)]
    return [MARKS[c] if c in MARKS else clean_inline(c).rstrip(".") for c in cells]


def speak_table(lines):
    """A markdown table as sentences: one per row, each cell named by its column."""
    rows, header = [], None
    for i, line in enumerate(lines):
        if re.fullmatch(r"\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?", line.strip()):
            if i == 1 and rows:
                header = rows.pop()
            continue
        rows.append(table_cells(line))
    rows = [r for r in rows if any(r)]
    if not rows:
        return []
    out = ["A table with %d row%s." % (len(rows), "" if len(rows) == 1 else "s")]
    for row in rows[:MAX_ROWS]:
        label, rest = row[0], row[1:]
        if header and len(header) == len(row):
            parts = []
            for name, value in zip(header[1:], rest):
                if not value:
                    continue
                parts.append(value if len(row) == 2 or not name else "%s %s" % (name, value))
        else:
            parts = [v for v in rest if v]
        if label and parts:
            out.append("%s: %s." % (label, ", ".join(parts)))
        elif label or parts:
            out.append("%s." % (label or ", ".join(parts)))
    if len(rows) > MAX_ROWS:
        more = len(rows) - MAX_ROWS
        out.append("And %d more row%s on screen." % (more, "" if more == 1 else "s"))
    return out


# drawings, diagrams and code

BOX_CHARS = "─━│┃┌┐└┘├┤┬┴┼╭╮╯╰═║╔╗╚╝╠╣╦╩╬╒╓╕╖╘╙╛╜╞╟╡╢╤╥╧╨╪╫▔▁░▒▓█"
ARROW_RIGHT = re.compile(r"\s*(?:[─━=\-]*[▶►▸>→⟶]+|[─━=\-]{1,}>)\s*")
ARROW_LEFT = re.compile(r"\s*(?:[◀◄◂<←⟵]+[─━=\-]*|<[─━=\-]{1,})\s*")
ARROW_DOWN = re.compile(r"^[\s│┃|▼▽↓v^▲△↑+]*$")
DRAWING_LANGS = {"", "text", "txt", "ascii", "diagram", "drawing", "plain", "plaintext", "art"}
CODE_NAMES = {"ts": "TypeScript", "tsx": "TypeScript", "typescript": "TypeScript",
              "js": "JavaScript", "jsx": "JavaScript", "javascript": "JavaScript",
              "py": "Python", "python": "Python", "json": "JSON", "yaml": "YAML",
              "yml": "YAML", "sql": "SQL", "html": "HTML", "css": "CSS", "java": "Java",
              "go": "Go", "rust": "Rust", "ruby": "Ruby", "swift": "Swift", "kotlin": "Kotlin",
              "c": "C", "cpp": "C++", "csharp": "C#", "php": "PHP",
              "dockerfile": "Dockerfile", "diff": "a diff"}
SHELL_LANGS = {"bash", "sh", "zsh", "shell", "console", "terminal", "powershell", "fish"}


def is_drawing(lang, lines):
    if lang not in DRAWING_LANGS:
        return False
    body = "\n".join(lines)
    if any(ch in body for ch in BOX_CHARS + "▶►◀◄▼▲→←↓↑"):
        return True
    # ASCII boxes and arrows: +----+, |  x  |, -->, ==>
    return bool(re.search(r"\+-{3,}\+|-{2,}>|={2,}>|<-{2,}", body))


def drawing_line(line):
    """The words on one row of a drawing, with its arrows said as words."""
    line = ARROW_RIGHT.sub(" to ", line)
    line = ARROW_LEFT.sub(" from ", line)
    line = re.sub("[%s]" % re.escape(BOX_CHARS), "  ", line)
    line = re.sub(r"\+-{2,}\+?|-{3,}|={3,}|(?<!\w)[|+](?!\w)", "  ", line)
    parts = [p.strip(" .:-|+") for p in re.split(r"\s{2,}", line)]
    parts = [p for p in parts if re.search(r"\w", p)]
    text = ", ".join(parts)
    text = re.sub(r"(,\s*)?\bto\b(\s*,)?", " to ", text)
    text = re.sub(r"(,\s*)?\bfrom\b(\s*,)?", " from ", text)
    text = re.sub(r"^\s*(to|from)\s+|\s+(to|from)\s*$", "", text)
    return re.sub(r"\s{2,}", " ", clean_inline(text)).strip(" ,")


def speak_drawing(lines):
    out, then = [], False
    for line in lines:
        if not line.strip():
            continue
        if ARROW_DOWN.match(line) and re.search(r"[▼▽↓v]", line):
            then = True  # an arrow pointing down: what follows comes next
            continue
        said = drawing_line(line)
        if not said:
            continue
        if out and said == out[-1].rstrip("."):
            continue
        out.append(("Then " if then and out else "") + said + ".")
        then = False
    if not out:
        return ["There's a drawing on screen."]
    if len(out) > 10:
        out = out[:10] + ["And more of the drawing on screen."]
    return ["A drawing."] + out


MERMAID_NODE = re.compile(
    r"(\w+)\s*(\(\[|\[\[|\[\(|\(\(|\[/|\[\\\\|\{\{|\[|\(|\{|>)\s*\"?(.*?)\"?\s*"
    r"(\]\)|\]\]|\)\]|\)\)|/\]|\\\\\]|\}\}|\]|\)|\})")
MERMAID_EDGE = re.compile(
    r"\s*(?:<?-{2,}>?|<?={2,}>?|-\.+->?|--[xo]|~~~|-->>|->>|-\)|--\))\s*(?:\|\s*\"?([^|]*?)\"?\s*\|)?\s*")
MERMAID_TEXT_EDGE = re.compile(r"\s*--\s+([^->]+?)\s+-->\s*")
MERMAID_KINDS = {"flowchart": "A flowchart", "graph": "A flowchart",
                 "sequencediagram": "A sequence diagram", "classdiagram": "A class diagram",
                 "statediagram": "A state diagram", "statediagram-v2": "A state diagram",
                 "erdiagram": "An entity diagram", "gantt": "A timeline", "pie": "A pie chart",
                 "mindmap": "A mind map", "timeline": "A timeline", "journey": "A journey map"}
MERMAID_SKIP = ("style ", "classdef ", "class ", "click ", "linkstyle ", "direction ",
                "participant ", "actor ", "title ", "autonumber", "activate ", "deactivate ")


def speak_mermaid(lines):
    lines = [l.strip() for l in lines if l.strip() and not l.strip().startswith("%%")]
    if not lines:
        return []
    kind = lines[0].split()[0].lower()
    title = MERMAID_KINDS.get(kind, "A diagram")
    labels, out = {}, []
    for line in lines[1:]:
        for m in MERMAID_NODE.finditer(line):
            labels[m.group(1)] = clean_inline(m.group(3).replace("<br>", " ").replace("<br/>", " "))

    def name(node):
        return labels.get(node, node)

    for line in lines[1:]:
        low = line.lower()
        if low == "end" or low.startswith(MERMAID_SKIP):
            continue
        if low.startswith("subgraph"):
            out.append("In %s:" % clean_inline(line[8:].strip(' "[]')))
            continue
        if kind == "sequencediagram":
            m = re.match(r"(\w[\w ]*?)\s*(-{1,2}>>|-{1,2}>|-{1,2}x|-{1,2}\))\s*([\w ]+?)\s*:\s*(.*)", line)
            if m:
                out.append("%s to %s: %s." % (m.group(1), m.group(3), clean_inline(m.group(4))))
            elif low.startswith(("note ", "loop ", "alt ", "else", "opt ")):
                out.append(clean_inline(re.sub(r"^note (over|left of|right of) [^:]*:\s*", "", line,
                                               flags=re.I)) + ".")
            continue
        line = MERMAID_NODE.sub(lambda m: m.group(1), line)
        line = MERMAID_TEXT_EDGE.sub(lambda m: " -->|%s| " % m.group(1), line)
        pieces = MERMAID_EDGE.split(line)
        nodes, edge_labels = [n.strip() for n in pieces[0::2]], pieces[1::2]
        if len(nodes) < 2:
            if nodes and nodes[0] and kind in ("flowchart", "graph"):
                continue  # a lone node definition: its label shows up in the edges
            if nodes and nodes[0]:
                out.append(clean_inline(nodes[0]) + ".")
            continue
        for i in range(len(nodes) - 1):
            a = " and ".join(name(x.strip()) for x in nodes[i].split("&"))
            b = " and ".join(name(x.strip()) for x in nodes[i + 1].split("&"))
            label = (edge_labels[i] or "").strip() if i < len(edge_labels) else ""
            said = "%s to %s%s" % (a, b, (", " + clean_inline(label)) if label else "")
            out.append(said if re.search(r"[.!?]$", said) else said + ".")
    if len(out) > 14:
        out = out[:14] + ["And more of the diagram on screen."]
    return [title + "."] + out if out else ["There's %s on screen." % title.lower()]


def speak_block(lang, lines, said):
    """What to say for a fenced block: a diagram described, a drawing read, code named once."""
    lang = lang.strip().lower().split()[0] if lang and lang.strip() else ""
    if lang == "mermaid":
        return speak_mermaid(lines)
    if is_drawing(lang, lines):
        return speak_drawing(lines)
    if lang in SHELL_LANGS:
        key, line = "shell", "There's a command on screen."
    elif lang in CODE_NAMES:
        key = CODE_NAMES[lang]
        line = ("There's %s on screen." if key.startswith("a ") else "There's %s code on screen.") % key
    else:
        key, line = "code", "There's code on screen."
    if key in said:
        return []
    said.add(key)
    return [line]


def clean_for_speech(text):
    """Markdown as it should sound: tables and diagrams described, code named, symbols dropped."""
    out, said_code = [], set()
    in_code, fence, lang, block, table = False, "", "", [], []
    for raw in text.splitlines() + [""]:
        line = raw.strip()
        if in_code:
            if line.startswith(fence):
                out.extend(speak_block(lang, block, said_code))
                in_code, block = False, []
            else:
                block.append(raw)
            continue
        if line.startswith("```") or line.startswith("~~~"):
            if table:
                out.extend(speak_table(table))
                table = []
            fence, lang, in_code = line[:3], line[3:].strip("`~ "), True
            continue
        if line.startswith("|"):
            table.append(line)
            continue
        if table:
            out.extend(speak_table(table))
            table = []
        if re.fullmatch(r"[-*_=]{3,}", line):                  # horizontal rule
            continue
        line = re.sub(r"^#{1,6}\s+", "", line)                 # headings
        line = re.sub(r"^(>\s*)+", "", line)                   # quotes
        line = re.sub(r"^[-*+•]\s+(\[[ xX]\]\s+)?", "", line)  # bullets, task boxes
        line = re.sub(r"^(\d+)[.)]\s+", r"\1. ", line)         # numbered lists
        out.append(clean_inline(line))
    if in_code and block:  # a fence left open
        out.extend(speak_block(lang, block, said_code))
    final = [l + "." if l and not re.search(r"[.!?:;,]$", l) else l for l in out]  # a pause per line
    return re.sub(r"\n{3,}", "\n\n", "\n".join(final)).strip()


# ---------- engines ----------

def system_engine():
    if shutil.which("say"):
        return "say"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return None


def kokoro_installed():
    return os.path.exists(VENV_PYTHON) and all(
        os.path.exists(os.path.join(MODELS_DIR, name)) for name in KOKORO_FILES)


def pick_engine(cfg):
    """The engine to use: the configured one, or for "auto" Kokoro if installed, else the system voice."""
    engine = cfg.get("engine") or "auto"
    if engine == "auto":
        return "kokoro" if kokoro_installed() else system_engine()
    return engine


def audio_player():
    """A command that plays a wav file and waits until it ends."""
    for cmd in (["afplay"], ["paplay"], ["pw-play"], ["aplay", "-q"],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
        if shutil.which(cmd[0]):
            return cmd
    return None


def speak_with_say(text, cfg):
    opts = cfg.get("say", {})
    cmd = ["say", "-r", str(opts.get("rate", 190)), "-f", "-"]
    if opts.get("voice"):
        cmd[1:1] = ["-v", opts["voice"]]
    subprocess.run(cmd, input=text.encode(), check=True)


def speak_with_espeak(text, cfg):
    opts = cfg.get("espeak", {})
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng is not installed")
    cmd = [exe, "-s", str(opts.get("rate", 175)), "--stdin"]
    if opts.get("voice"):
        cmd[1:1] = ["-v", opts["voice"]]
    subprocess.run(cmd, input=text.encode(), check=True)


def speak_with_kokoro(text, cfg):
    """Kokoro runs in its own venv (made by --setup kokoro) and plays the audio itself."""
    if not kokoro_installed():
        raise RuntimeError("kokoro is not installed; run: speak.py --setup kokoro")
    player = audio_player()
    if not player:
        raise RuntimeError("no audio player found (afplay, paplay, pw-play, aplay or ffplay)")
    opts = dict(cfg.get("kokoro", {}), models=MODELS_DIR,
                audio=os.path.join(DATA, "audio"), player=player)
    script = os.path.join(ROOT, "scripts", "engines", "kokoro.py")
    subprocess.run([VENV_PYTHON, script, json.dumps(opts)], input=text.encode(), check=True)


ENGINES = {"say": speak_with_say, "espeak": speak_with_espeak, "kokoro": speak_with_kokoro}
HALTED = []


def speak_now(text, cfg):
    engine = pick_engine(cfg)
    fallback = system_engine()
    if engine in ENGINES and engine != fallback:
        try:
            ENGINES[engine](text, cfg)
            return
        except Exception as e:
            if HALTED:
                return  # stopped on purpose, not a failure
            log("engine %s failed, using the system voice: %s" % (engine, e))
    if not fallback:
        raise RuntimeError("no voice found: install espeak-ng, or run --setup kokoro")
    ENGINES[fallback](text, cfg)


def speak_in_background(text, sid=""):
    """Start a detached speaker so the hook returns at once."""
    stop_speaking()
    os.makedirs(DATA, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     dir=DATA, prefix=".reply-") as f:
        f.write(text)
        path = f.name
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--speak-file", path, sid],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True, close_fds=True)
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))


def run_speaker(path):
    # Stopped: the engine in our process group gets the same signal and tidies up.
    # Exiting here at once would leave it to be killed hard, so note it and wait.
    signal.signal(signal.SIGTERM, lambda *_: HALTED.append(1))
    try:
        with open(path) as f:
            text = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        speak_now(text, load_config())
    except Exception as e:
        if not HALTED:  # a stopped voice exits with an error; that is not a failure
            log("speaking failed: %s" % e)
    finally:
        try:
            with open(PID_FILE) as f:
                if f.read().strip() == str(os.getpid()):
                    os.remove(PID_FILE)
        except OSError:
            pass


# ---------- hooks ----------

def read_hook_input():
    try:
        return json.load(sys.stdin)
    except ValueError:
        return {}


def last_paths(session_id):
    base = os.path.join(LAST_DIR, re.sub(r"[^\w-]", "_", session_id or "unknown"))
    return base + ".txt", base + ".skip"


def save_last(session_id, text):
    os.makedirs(LAST_DIR, exist_ok=True)
    week_ago = time.time() - 7 * 86400
    for name in os.listdir(LAST_DIR):  # forget old chats
        path = os.path.join(LAST_DIR, name)
        if os.path.getmtime(path) < week_ago:
            os.remove(path)
    with open(last_paths(session_id)[0], "w") as f:
        f.write(text)


def hook_stop():
    data = read_hook_input()
    sid = data.get("session_id", "")
    if read_on() is None:
        return  # voice is off everywhere: touch nothing
    skip = last_paths(sid)[1]
    if os.path.exists(skip):  # this turn was a repeat: let the old reply keep playing
        os.remove(skip)
        log("stop hook: repeat turn for %s, left alone" % sid[:8])
        return
    text = data.get("last_assistant_message") or ""
    source = "message"
    if not text:
        source = "transcript"
        time.sleep(0.3)  # let the transcript catch up with the final reply
        text = last_reply(data.get("transcript_path", ""))
    if text:
        save_last(sid, text)
    if not is_on(sid):
        return
    spoken = clean_for_speech(text)
    if spoken:
        log("stop hook: speaking %d chars from %s for %s" % (len(spoken), source, sid[:8]))
        speak_in_background(spoken, sid)


def hook_prompt():
    data = read_hook_input()
    if read_on() is not None and stop_speaking():
        log("prompt hook: stopped speech for %s" % data.get("session_id", "?")[:8])


# ---------- commands ----------

def repeat(sid):
    last, skip = last_paths(sid)
    try:
        with open(last) as f:
            text = f.read()
    except OSError:
        text = ""
    spoken = clean_for_speech(text)
    if not spoken:
        print("Nothing to repeat yet in this chat. Replies are kept once voice is on somewhere.")
        return
    open(skip, "w").close()  # the reply to the repeat command is not spoken or saved
    speak_in_background(spoken, sid)
    print("Reading the last reply again (%d words)." % len(spoken.split()))


def toggle(on, every, sid):
    ids = read_on() or set()
    key = "*" if every else sid
    if not key:
        print("No chat id found; use 'all' to switch every chat.")
        return
    if on:
        ids.add(key)
        write_default_config()
    else:
        stop_speaking()
        ids = set() if every else ids - {sid}
    write_on(ids)
    status(sid)


def status(sid):
    ids = read_on()
    if ids is None:
        print("Voice is off everywhere.")
    elif "*" in ids:
        print("Voice is on for every chat.")
    elif sid in ids:
        others = len(ids) - 1
        print("Voice is on for this chat" + (" and %d other(s)." % others if others else "."))
    else:
        print("Voice is off here, on in %d other chat(s)." % len(ids))
    cfg = load_config()
    engine = pick_engine(cfg)
    if not engine:
        print("No voice found: install espeak-ng, or run --setup kokoro.")
        return
    opts = cfg.get(engine, {})
    shown = ", ".join("%s %s" % kv for kv in sorted(opts.items()) if kv[1] != "")
    print("Engine: %s%s" % (engine, " (" + shown + ")" if shown else ""))
    print("Settings: %s" % CONFIG_FILE)


def list_voices():
    engine = pick_engine(load_config())
    if engine == "say":
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        english = [l for l in out.splitlines() if re.search(r"\sen[_-]", l)]
        print("\n".join(english or out.splitlines()))
    elif engine == "espeak":
        exe = shutil.which("espeak-ng") or shutil.which("espeak")
        print(subprocess.run([exe, "--voices=en"], capture_output=True, text=True).stdout)
    elif engine == "kokoro":
        code = ("import sys; from kokoro_onnx import Kokoro; "
                "k = Kokoro(sys.argv[1], sys.argv[2]); print(' '.join(sorted(k.get_voices())))")
        subprocess.run([VENV_PYTHON, "-c", code, os.path.join(MODELS_DIR, "kokoro-v1.0.int8.onnx"),
                        os.path.join(MODELS_DIR, "voices-v1.0.bin")])
        print("English: af_* and am_* are American (female, male), bf_* and bm_* British.")
    else:
        print("No voice found: install espeak-ng, or run --setup kokoro.")


def python_for_kokoro():
    """A Python from 3.10 to 3.13 (what kokoro-onnx supports)."""
    names = ["python3.12", "python3.11", "python3.13", "python3.10", "python3"]
    for exe in [shutil.which(n) for n in names] + [sys.executable]:
        if not exe:
            continue
        try:
            out = subprocess.run([exe, "-c", "import sys; print(sys.version_info[:2] >= (3, 10) "
                                  "and sys.version_info[:2] < (3, 14))"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            continue
        if out == "True":
            return exe
    return None


def download(url, dest, size):
    import urllib.request
    part = dest + ".part"
    with urllib.request.urlopen(url, timeout=60) as r, open(part, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    if os.path.getsize(part) != size:
        os.remove(part)
        raise RuntimeError("%s did not download completely" % os.path.basename(dest))
    os.replace(part, dest)


def setup_kokoro():
    """Make the venv, install kokoro-onnx, download the model. Safe to run again."""
    if not audio_player():
        print("No audio player found. Install one of: paplay (pulseaudio-utils), aplay (alsa-utils) or ffplay.")
        return 1
    os.makedirs(MODELS_DIR, exist_ok=True)
    if not os.path.exists(VENV_PYTHON):
        venv = os.path.join(DATA, "venv")
        uv = shutil.which("uv")
        python = python_for_kokoro()
        print("Making a Python environment in %s ..." % venv, flush=True)
        if python:
            subprocess.run([python, "-m", "venv", venv], check=True)
        elif uv:
            subprocess.run([uv, "venv", "--python", "3.12", "--seed", venv], check=True)
        else:
            print("Kokoro needs Python 3.10 to 3.13 (or uv). Install one, then run this again.")
            return 1
    print("Installing kokoro-onnx (a few minutes the first time) ...", flush=True)
    subprocess.run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run([VENV_PYTHON, "-m", "pip", "install", "--quiet", "kokoro-onnx>=0.4"], check=True)
    for name, size in KOKORO_FILES.items():
        dest = os.path.join(MODELS_DIR, name)
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            continue
        print("Downloading %s (%d MB) ..." % (name, size // 1000000), flush=True)
        download(KOKORO_RELEASE + name, dest, size)
    cfg = load_config()
    if cfg.get("engine") not in ("auto", "kokoro"):
        print("Note: config.json sets engine to %r; set it to \"kokoro\" or \"auto\" to use Kokoro."
              % cfg.get("engine"))
    print("Kokoro is installed. Testing it now.", flush=True)
    speak_with_kokoro("Kokoro is ready.", cfg)
    return 0


def session_from(argv):
    if "--session" in argv:
        i = argv.index("--session")
        value = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
        if value and "${" not in value and not value.startswith("$"):
            return value
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID", "")


def main(argv):
    argv = list(argv)
    sid = session_from(argv)
    arg = argv[1] if len(argv) > 1 else ""
    every = "--all" in argv or "all" in argv[2:]
    if arg == "":
        hook_stop()
    elif arg == "--stop":
        hook_prompt()
    elif arg == "--speak-file":
        run_speaker(argv[2])
    elif arg == "--on":
        toggle(True, every, sid)
    elif arg == "--off":
        toggle(False, every, sid)
    elif arg == "--status":
        status(sid)
    elif arg == "--repeat":
        repeat(sid)
    elif arg == "--halt":
        print("Stopped." if stop_speaking() else "Nothing was playing.")
    elif arg == "--say":
        speak_now(clean_for_speech(" ".join(argv[2:]) or "Voice mode is working."), load_config())
    elif arg == "--voices":
        list_voices()
    elif arg == "--setup":
        if argv[2:3] != ["kokoro"]:
            print("Usage: speak.py --setup kokoro")
            return 1
        return setup_kokoro()
    elif arg == "--clean":
        path = argv[2]
        print(clean_for_speech(last_reply(path) if path.endswith(".jsonl") else open(path).read()))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    hook = len(sys.argv) == 1 or sys.argv[1] in ("--stop", "--speak-file")
    try:
        code = main(sys.argv)
    except Exception as e:
        log("error: %r" % e)
        if not hook:  # a hook must never break Claude; a command should say what went wrong
            print("Error: %s" % e)
            sys.exit(1)
        code = 0
    sys.exit(0 if hook else (code or 0))
