#!/usr/bin/env python3
"""
ollama_code.py — A Claude Code TUI clone powered by local Ollama models.
Supports fake tool use (create/edit/read files, run shell commands) via XML tags.
Color: #DE7356 accent on black background.
"""

import os
import sys
import re
import json
import shutil
import subprocess
import threading
import time
import textwrap
import random
from pathlib import Path

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)

# ──────────────────────────────────────────────
# ANSI helpers and variables
# ──────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

def fg(hex_color: str) -> str:
    """Return ANSI 24-bit foreground escape for a #rrggbb color."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"\033[38;2;{r};{g};{b}m"

ACCENT  = fg("#DE7356")   # coral-red — primary accent
WHITE   = fg("#e8e8e8")   # body text
DIMTEXT = fg("#888888")   # dim/secondary text
GREEN   = fg("#88cc88")   # tool success
YELLOW  = fg("#cccc88")   # tool warning / thinking
BLUE    = fg("#8888fe")   # tool info

def accent(s): return f"{ACCENT}{s}{RESET}"
def bold_accent(s): return f"{BOLD}{ACCENT}{s}{RESET}"
def white(s): return f"{WHITE}{s}{RESET}"
def dim(s): return f"{DIM}{DIMTEXT}{s}{RESET}"
def green(s): return f"{GREEN}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"

verbs = ["Accomplishing","Analyzing","Beaming","Bootstrapping","Brewing","Calculating","Cascading","Cerebrating","Channelling","Clauding","Composing","Computing","Considering","Cooking","Crafting","Creating","Crystallizing","Crunching","Determining","Doing","Effecting","Enchanting","Evaluating","Fermenting","Forming","Frosting","Galloping","Generating","Germinating","Gitifying","Harmonizing","Hashing","Hatching","Hibernating","Hyperspacing","Ideating","Imagining","Improvising","Incubating","Inferring","Infusing","Ionizing","Jitterbugging","Levitating","Manifesting","Metamorphosing","Misting","Moonwalking","Nebulizing","Nesting","Noodling","Nucleating","Orbiting","Orchestrating","Osmosing","Philosophising","Photosynthesizing","Pollinating","Pondering","Pouncing","Precipitating","Processing","Proofing","Puzzling","Quantumizing","Roosting","Ruminating","Scampering","Scheming","Scurrying","Seasoning","Shimmying","Simmering","Sketching","Slithering","Smooshing","Spinning","Sprouting","Stewing","Sublimating","Swirling","Swooping","Synthesizing","Tempering","Thinking","Thundering","Tinkering","Twisting","Unravelling","Vibing","Waddling","Wandering","Warping","Whirlpooling","Working","Zesting"]

# ──────────────────────────────────────────────
# Terminal width helper
# ──────────────────────────────────────────────
def term_width() -> int:
    return shutil.get_terminal_size((100, 24)).columns

# ──────────────────────────────────────────────
# Ollama API
# ──────────────────────────────────────────────
OLLAMA_BASE = "http://localhost:11434"

def ollama_list_models() -> list[str]:
    try:
        r = requests.get(f"{OLLAMA_BASE}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception as e:
        print(f"{accent('Error')} reaching Ollama: {e}")
        print(dim("Is Ollama running? Try: ollama serve"))
        sys.exit(1)

def ollama_chat_stream(model: str, messages: list[dict]):
    """Stream chat completion from Ollama. Yields text chunks."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"temperature": 0.7},
    }
    try:
        with requests.post(
            f"{OLLAMA_BASE}/api/chat",
            json=payload,
            stream=True,
            timeout=120,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    chunk = data.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if data.get("done"):
                        break
                except json.JSONDecodeError:
                    continue
    except requests.exceptions.ConnectionError:
        yield "\n[ERROR] Lost connection to Ollama.\n"

# ──────────────────────────────────────────────
# Pixel art Claude logo (Unicode block chars)
# ──────────────────────────────────────────────
CLAUDE_LOGO = [
    "     ▐▛███▜▌     ",
    "    ▝▜█████▛▘    ",
    "      ▘▘ ▝▝      ",
]

# ASCII Moth (top-right corner)
MOTH = "   ~  ~    \n  .----.\n / ·  · \\ \n |      | \n ~`~``~`~  \n   Moth"

# ──────────────────────────────────────────────
# TUI drawing
# ──────────────────────────────────────────────
def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def draw_welcome(model: str, cwd: str, recent: list[str]):
    """Draw the opening welcome screen that mimics Claude Code."""
    w = term_width()
    clear_screen()

    # "Launching..." line
    print(f"{white('Launching Claude Code with ')}{bold_accent(model)}{white('...')}")

    # Top border: ╭─── Claude Code v2.1.96 ─...─╮
    title = " Claude Code v2.1.96 "
    left_dashes = "─" * 3
    right_dashes = "─" * max(0, w - len(title) - len(left_dashes) - 2)
    print(f"{ACCENT}╭{left_dashes}{title}{right_dashes}╮{RESET}")

    # Content area — split into left (40%) and right (60%)
    inner_w = w - 2  # subtract the two border chars
    left_w  = inner_w // 2 - 1
    right_w = inner_w - left_w - 1  # -1 for the │ separator

    tips = [
        "Run /init to create a CLAUDE.md file with instructions for Claude",
        f"Note: You have launched claude in your home directory. For the be…",
    ]

    right_lines = [
        bold_accent("Tips for getting started"),
        *[white(textwrap.shorten(t, width=right_w - 1)) for t in tips],
        accent("─" * (right_w - 1)),
        bold_accent("Recent activity"),
    ]
    if recent:
        right_lines += [white(r) for r in recent[:3]]
    else:
        right_lines.append(white("No recent activity"))

    # Left column lines
    def center_ansi(s: str, width: int) -> str:
        visible_len = len(re.sub(r'\033\[[^m]*m', '', s))
        pad = max(0, (width - visible_len) // 2)
        return " " * pad + s

    model_line = textwrap.shorten(f"{model} · API Usage Billing", width=left_w - 2)
    left_lines = [
        "",
        center_ansi(white("Welcome back!"), left_w),
        "",
        *[center_ansi(ACCENT + l.strip() + RESET, left_w) for l in CLAUDE_LOGO],
        "",
        center_ansi(white(model_line), left_w),
        center_ansi(white(textwrap.shorten(cwd, width=left_w - 2)), left_w),
        "",
    ]

    max_rows = max(len(left_lines), len(right_lines))

    def pad_strip(s: str, width: int) -> str:
        """Strip ANSI, pad to width, but keep the original string with codes."""
        visible = re.sub(r'\033\[[^m]*m', '', s)
        pad = max(0, width - len(visible))
        return s + " " * pad

    for i in range(max_rows):
        lline = left_lines[i] if i < len(left_lines) else ""
        rline = right_lines[i] if i < len(right_lines) else ""
        lpart = pad_strip(lline, left_w)
        print(f"{ACCENT}│{RESET}{lpart}{ACCENT}│{RESET}{rline}")

    # Bottom border
    print(f"{ACCENT}╰{'─' * inner_w}╯{RESET}")

    # Moth art — right aligned
    moth_lines = MOTH.split("\n")
    max_moth_w = max(len(ml) for ml in moth_lines)
    print()
    for ml in moth_lines:
        pad_left = " " * max(0, w - max_moth_w - 2)
        pad_right = " " * (max_moth_w - len(ml))
        print(pad_left + accent(ml + pad_right))

def draw_separator(w: int | None = None):
    w = shutil.get_terminal_size((100, 24)).columns
    print(f"\r{accent('─' * w)}")

def draw_status_bar(model: str | None):
    w = shutil.get_terminal_size((100, 24)).columns
    left  = "  ? for shortcuts"
    right = "○ low · /effort  "
    mid   = " " * max(0, w - len(left) - len(right) - 2)
    print(f"{dim(left)}{mid}{dim(right)}")

def draw_input_prompt() -> str:
    draw_separator()
    try:
        user_input = input(f"{bold_accent('> ')}")
    except (EOFError, KeyboardInterrupt):
        print()
        return "/exit"
    draw_separator()
    return user_input.strip()

# ──────────────────────────────────────────────
# Tool execution engine
# ──────────────────────────────────────────────
TOOL_PATTERN = re.compile(
    r"<tool_call>\s*<name>(.*?)</name>(.*?)</tool_call>",
    re.DOTALL,
)
PARAM_PATTERN = re.compile(r"<(\w+)>(.*?)</\1>", re.DOTALL)

def parse_tools(text: str) -> list[dict]:
    """Extract all <tool_call> blocks from model output."""
    tools = []
    for m in TOOL_PATTERN.finditer(text):
        name   = m.group(1).strip()
        params = {k: v.strip() for k, v in PARAM_PATTERN.findall(m.group(2))}
        tools.append({"name": name, "params": params})
    return tools

def safe_path(cwd: Path, rel: str) -> Path | None:
    """Return resolved path only if it stays within cwd. None if escape attempted."""
    try:
        resolved = (cwd / rel).resolve()
        resolved.relative_to(cwd.resolve())  # raises ValueError if outside
        return resolved
    except ValueError:
        return None
    
ALLOWED_COMMANDS = {
    "python", "py", "pip", "node", "npm",
    "dir", "echo", "type", "find", "findstr",
    "cd", "mkdir", "rmdir", "del", "copy", "move",
    "git", "where", "whoami",
}

def is_command_allowed(cmd: str) -> bool:
    """Check if the base command is in the allowlist."""
    base = cmd.strip().split()[0].lower()
    # Strip .exe if present
    base = base.removesuffix(".exe")
    return base in ALLOWED_COMMANDS

PYTHON_BLOCKED_IMPORTS = {
    "subprocess", "os", "sys", "shutil", "socket", "requests",
    "urllib", "http", "ftplib", "smtplib", "telnetlib",
    "ctypes", "winreg", "winsound", "msilib",
    "importlib", "pkgutil", "runpy",
    "multiprocessing", "threading", "concurrent",
    "pty", "tty", "termios", "atexit",
    "code", "codeop", "compileall", "py_compile",
}

def scan_python_file(path: Path) -> tuple[bool, str]:
    """
    Scan a .py file for dangerous patterns before execution.
    Returns (safe, reason).
    """
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Could not read file: {e}"

    lines = source.splitlines()

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Block dangerous imports
        for mod in PYTHON_BLOCKED_IMPORTS:
            if re.search(rf'\bimport\s+{mod}\b', stripped):
                return False, f"line {i}: import of '{mod}' is not allowed"
            if re.search(rf'\bfrom\s+{mod}\b', stripped):
                return False, f"line {i}: import of '{mod}' is not allowed"

        # Block exec() and eval()
        if re.search(r'\bexec\s*\(', stripped):
            return False, f"line {i}: exec() is not allowed"
        if re.search(r'\beval\s*\(', stripped):
            return False, f"line {i}: eval() is not allowed"

        # Block __import__()
        if re.search(r'\b__import__\s*\(', stripped):
            return False, f"line {i}: __import__() is not allowed"

        # Block open() with write modes
        if re.search(r'\bopen\s*\(.*["\'][wa+]["\']', stripped):
            return False, f"line {i}: open() in write mode is not allowed"

        # Block network builtins
        if re.search(r'\b(socket|connect|bind|listen|send|recv)\s*\(', stripped):
            return False, f"line {i}: network call is not allowed"

    return True, ""

JS_BLOCKED_PATTERNS = [
    (r'\brequire\s*\(\s*["\']child_process["\']',   "child_process is not allowed"),
    (r'\brequire\s*\(\s*["\']fs["\']',              "fs module is not allowed"),
    (r'\brequire\s*\(\s*["\']net["\']',             "net module is not allowed"),
    (r'\brequire\s*\(\s*["\']http["\']',            "http module is not allowed"),
    (r'\brequire\s*\(\s*["\']https["\']',           "https module is not allowed"),
    (r'\brequire\s*\(\s*["\']os["\']',              "os module is not allowed"),
    (r'\brequire\s*\(\s*["\']path["\']',            "path module is not allowed"),
    (r'\brequire\s*\(\s*["\']crypto["\']',          "crypto module is not allowed"),
    (r'\brequire\s*\(\s*["\']cluster["\']',         "cluster module is not allowed"),
    (r'\brequire\s*\(\s*["\']worker_threads["\']',  "worker_threads is not allowed"),
    (r'\bimport\s+.*\s+from\s+["\']fs["\']',        "fs module is not allowed"),
    (r'\bimport\s+.*\s+from\s+["\']child_process["\']', "child_process is not allowed"),
    (r'\bimport\s+.*\s+from\s+["\']net["\']',       "net module is not allowed"),
    (r'\bimport\s+.*\s+from\s+["\']http["\']',      "http module is not allowed"),
    (r'\bimport\s+.*\s+from\s+["\']https["\']',     "https module is not allowed"),
    (r'\beval\s*\(',                                "eval() is not allowed"),
    (r'\bFunction\s*\(',                            "Function() constructor is not allowed"),
    (r'\bsetTimeout\s*\(',                          "setTimeout is not allowed"),
    (r'\bsetInterval\s*\(',                         "setInterval is not allowed"),
    (r'\bprocess\.exit\s*\(',                       "process.exit() is not allowed"),
    (r'\bprocess\.env\b',                           "process.env access is not allowed"),
    (r'\bprocess\.binding\s*\(',                    "process.binding() is not allowed"),
    (r'\b__dirname\b',                              "__dirname is not allowed"),
    (r'\b__filename\b',                             "__filename is not allowed"),
    (r'\bfetch\s*\(',                               "fetch() network call is not allowed"),
    (r'\bXMLHttpRequest\b',                         "XMLHttpRequest is not allowed"),
    (r'\bnew\s+WebSocket\s*\(',                     "WebSocket is not allowed"),
]

def scan_js_file(path: Path) -> tuple[bool, str]:
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        return False, f"Could not read file: {e}"

    lines = source.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip comments
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for pattern, reason in JS_BLOCKED_PATTERNS:
            if re.search(pattern, stripped):
                return False, f"line {i}: {reason}"

    return True, ""

def is_command_allowed(cmd: str) -> tuple[bool, str]:
    """
    Returns (allowed, reason).
    Validates both the base command and its arguments.
    """
    parts = cmd.strip().split()
    if not parts:
        return False, "Empty command"
    
    base = parts[0].lower().removesuffix(".exe")
    args = parts[1:] if len(parts) > 1 else []

    # ── python / py ──────────────────────────────
    if base in ("python", "py"):
        if not args:
            return False, "Specify a script to run"
        sub = args[0].lower()
        # Block -m (module execution), -c (inline code)
        if sub in ("-m", "-c"):
            return False, f"python {sub} is not allowed (arbitrary execution)"
        # Must be a .py file in cwd
        if not sub.endswith(".py"):
            return False, "Only .py files can be executed"
        return True, ""

    # ── pip ──────────────────────────────────────
    if base == "pip":
        if not args:
            return False, "Specify a pip subcommand"
        sub = args[0].lower()
        # Only allow show/list/freeze — no install/uninstall
        if sub in ("show", "list", "freeze"):
            return True, ""
        return False, f"pip {sub} is not allowed (use pip manually for installs)"

    # ── node ─────────────────────────────────────
    if base == "node":
        if not args:
            return False, "Specify a .js file to run"
        sub = args[0].lower()
        if sub.startswith("-"):
            return False, f"node {sub} flag is not allowed"
        if not sub.endswith(".js"):
            return False, "Only .js files can be executed"
        return True, ""

    # ── npm ──────────────────────────────────────
    if base == "npm":
        if not args:
            return False, "Specify an npm subcommand"
        sub = args[0].lower()
        # Block install/ci/publish/run (run can execute arbitrary scripts)
        BLOCKED_NPM = {"install", "i", "ci", "publish", "run", "exec", "x", "uninstall"}
        if sub in BLOCKED_NPM:
            return False, f"npm {sub} is not allowed"
        if sub in ("list", "ls", "outdated", "audit"):
            return True, ""
        return False, f"npm {sub} is not allowed"

    # ── git ──────────────────────────────────────
    if base == "git":
        if not args:
            return False, "Specify a git subcommand"
        sub = args[0].lower()
        # Block hooks-triggering or remote-pushing commands
        BLOCKED_GIT = {"push", "clone", "fetch", "pull", "remote", "submodule", "hook"}
        if sub in BLOCKED_GIT:
            return False, f"git {sub} is not allowed"
        ALLOWED_GIT = {"status", "log", "diff", "show", "branch", "add", "commit", "stash"}
        if sub in ALLOWED_GIT:
            return True, ""
        return False, f"git {sub} is not allowed"

    # ── safe read-only builtins ───────────────────
    if base in ("dir", "echo", "where", "find", "findstr", "type"):
        return True, ""

    # ── mkdir (scoped to cwd by subprocess cwd= param) ──
    if base == "mkdir":
        return True, ""

    return False, f"'{base}' is not an allowed command"

def execute_tool(tool: dict, cwd: Path) -> str:
    name   = tool["name"]
    params = tool["params"]

    try:
        if name == "create_file":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            content = params.get("content", "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return green(f"✓ Created: {path}")

        elif name == "replace_text_in_file":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            old     = params.get("old_str", "")
            new     = params.get("new_str", "")
            if not path.exists():
                return accent(f"✗ File not found: {path}")
            text = path.read_text(encoding="utf-8")
            if old not in text:
                return accent(f"✗ String not found in {path.name}")
            path.write_text(text.replace(old, new, 1), encoding="utf-8")
            return green(f"✓ Replaced {old} with {new} in {path}")

        elif name == "read_file":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")
            content = path.read_text(encoding="utf-8")
            lines   = content.splitlines()
            preview = "\n".join(lines[:50])
            note    = f"\n{dim(f'... ({len(lines)} lines total)')}" if len(lines) > 50 else ""
            return f"{dim('─── ' + path.name + ' ───')}\n{white(preview)}{note}"

        elif name == "list_files":
            target = safe_path(cwd, params.get("path", "."))
            if target is None:
                return accent("✗ Access denied: path escapes working directory")

            if not target.exists():
                return accent(f"✗ Path not found: {target}")
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
            lines   = []
            for e in entries[:40]:
                icon = "📄 " if e.is_file() else "📁 "
                lines.append(f"  {icon}{e.name}")
            return "\n".join(lines) or dim("(empty directory)")

        elif name == "run_command":
            cmd = params.get("command", "")
            allowed, reason = is_command_allowed(cmd)
            if not allowed:
                return accent(f"✗ Command blocked: {reason}")
            # If executing a python file, scan it first
            parts = cmd.strip().split()
            if parts[0].lower().removesuffix(".exe") in ("python", "py"):
                script_path = safe_path(cwd, parts[1])
                if script_path is None:
                    return accent("✗ Access denied: script is outside working directory")
                if not script_path.exists():
                    return accent(f"✗ Script not found: {parts[1]}")
                safe, reason = scan_python_file(script_path)
                if not safe:
                    return accent(f"✗ Script blocked: {reason}")
            # If executing a JS file, scan it first
            if parts[0].lower().removesuffix(".exe") == "node":
                script_path = safe_path(cwd, parts[1])
                if script_path is None:
                    return accent("✗ Access denied: script is outside working directory")
                if not script_path.exists():
                    return accent(f"✗ Script not found: {parts[1]}")
                safe, reason = scan_js_file(script_path)
                if not safe:
                    return accent(f"✗ Script blocked: {reason}")
            timeout = int(params.get("timeout", 15))
            result = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=timeout,
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            parts = []
            if out:
                parts.append(white(out))
            if err:
                parts.append(accent(f"[stderr] {err}"))
            parts.append(dim(f"exit code: {result.returncode}"))
            return "\n".join(parts)

        elif name == "delete_file":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")
            path.unlink()
            return green(f"✓ Deleted: {path}")

        elif name == "insert_content_after_number":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            content = params.get("content", "")
            arg = params.get("argument", "").strip()
            if not arg.isdigit():
                return accent("✗ argument must be a line number (integer)")

            line_no = int(arg)
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if line_no < 0 or line_no > len(lines):
                return accent(f"✗ line number out of range (0..{len(lines)})")

            insert = content
            if insert and not insert.endswith("\n"):
                insert += "\n"

            idx = line_no  # append AFTER line_no => insert at index == line_no
            lines[idx:idx] = [insert]
            path.write_text("".join(lines), encoding="utf-8")
            return green(f"✓ Inserted after line {line_no} in {path}")

        elif name == "insert_content_after_line":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            content = params.get("content", "")
            needle = params.get("argument", "")
            text = path.read_text(encoding="utf-8")
            pos = text.find(needle)
            if pos == -1:
                return accent("✗ argument text not found in file")

            insert = content
            if insert and not insert.endswith("\n"):
                insert += "\n"

            insert_at = pos + len(needle)
            new_text = text[:insert_at] + insert + text[insert_at:]
            path.write_text(new_text, encoding="utf-8")
            return green(f"✓ Inserted after matching text in {path}")

        elif name == "delete_lines":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            arg = params.get("argument", "").strip()
            if not arg.isdigit():
                return accent("✗ argument must be a line number (integer)")

            line_no = int(arg)
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if line_no < 1 or line_no > len(lines):
                return accent(f"✗ line number out of range (1..{len(lines)})")
            del lines[line_no - 1]
            path.write_text("".join(lines), encoding="utf-8")
            return green(f"✓ Deleted line {line_no} in {path}")

        elif name == "delete_one_line_number":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            arg = params.get("content", "").strip()
            if not arg.isdigit():
                return accent("✗ content must be a line number (integer)")

            line_no = int(arg)
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if line_no < 1 or line_no > len(lines):
                return accent(f"✗ line number out of range (1..{len(lines)})")
            del lines[line_no - 1]
            path.write_text("".join(lines), encoding="utf-8")
            return green(f"✓ Deleted line {line_no} in {path}")

        elif name == "delete_one_line_content":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            needle = params.get("content", "").strip()
            if not needle:
                return accent("✗ content must not be empty")

            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            found = -1
            for i, line in enumerate(lines):
                if needle in line.strip():
                    found = i
                    break
            if found == -1:
                return accent("✗ No line matching that content found")
            del lines[found]
            path.write_text("".join(lines), encoding="utf-8")
            return green(f"✓ Deleted line {found + 1} matching '{needle[:40]}' in {path}")

        elif name == "delete_line_range_number":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            arg1 = params.get("argument1", "").strip()
            arg2 = params.get("argument2", "").strip()
            if not arg1.isdigit() or not arg2.isdigit():
                return accent("✗ argument1 and argument2 must be line numbers (integers)")

            start = int(arg1)
            end = int(arg2)
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if start < 1 or end > len(lines) or start > end:
                return accent(f"✗ line range out of bounds (1..{len(lines)}), start must be <= end")
            del lines[start - 1:end]
            path.write_text("".join(lines), encoding="utf-8")
            return green(f"✓ Deleted lines {start}-{end} in {path}")

        elif name == "replace_line":
            path = safe_path(cwd, params.get("path", ""))
            if path is None:
                return accent("✗ Access denied: path escapes working directory")
            if not path.exists():
                return accent(f"✗ File not found: {path}")

            content = params.get("content", "")
            arg = params.get("argument", "").strip()
            if not arg.isdigit():
                return accent("✗ argument must be a line number (integer)")

            line_no = int(arg)
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            if line_no < 1 or line_no > len(lines):
                return accent(f"✗ line number out of range (1..{len(lines)})")

            replacement = content
            if replacement and not replacement.endswith("\n"):
                replacement += "\n"
            lines[line_no - 1] = replacement
            path.write_text("".join(lines), encoding="utf-8")
            return green(f"✓ Replaced line {line_no} in {path}")

        else:
            return accent(f"✗ Unknown tool: {name}")

    except subprocess.TimeoutExpired:
        return accent("✗ Command timed out")
    except PermissionError as e:
        return accent(f"✗ Permission denied: {e}")
    except Exception as e:
        return accent(f"✗ Tool error: {e}")

def feed_tool_result(tool_name: str, result: str, history: list) -> None:
    """Inject tool result into history as a user message so model can continue."""
    clean = re.sub(r'\033\[[^m]*m', '', result)
    history.append({
        "role": "user",
        "content": f"<tool_result>\n<name>{tool_name}</name>\n<output>{clean}</output>\n</tool_result>\nNow use this result to answer the user's original question. Do NOT call the same tool again."
    })

def print_tool_call(tool: dict):
    name   = tool["name"]
    params = tool["params"]
    print(f"\n  {yellow('⚙')} {bold_accent(name)}", end="")
    for k, v in params.items():
        if k == "content":
            lines = v.splitlines()
            preview = lines[0][:60] + ("…" if len(lines[0]) > 60 else "")
            print(f"\n    {dim(k + ':')} {white(preview)}", end="")
        else:
            print(f"  {dim(k + ':')} {white(v[:60])}", end="")
    print()

# ──────────────────────────────────────────────
# System prompt
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """
You are an expert coding assistant inside a terminal IDE called OllamaCode.

IMPORTANT: To use a tool, you MUST wrap it in <tool_call>...</tool_call> tags EXACTLY like this:

<tool_call>
<name>list_files</name>
<path>.</path>
</tool_call>

WRONG (NEVER do this):
<name>list_files</name>
<path>.</path>

WRONG (NEVER do this):
```xml
<name>list_files</name>
```

The outer <tool_call> and inner </tool_call> tags are REQUIRED. Without them, the tool will NOT execute.

You have the following tools at your disposal. Use them to interact with the filesystem and run commands. Always prefer tools over direct code blocks output when possible.

<tool_call>
<name>create_file</name>
<path>FILE_PATH</path>
<content>FILE_CONTENTS</content>
</tool_call>

<tool_call>
<name>replace_text_in_file</name>
<path>FILE_PATH</path>
<old_str>EXACT_TEXT_TO_REPLACE</old_str>
<new_str>NEW_TEXT</new_str>
</tool_call>

<tool_call>
<name>read_file</name>
<path>FILE_PATH</path>
</tool_call>

<tool_call>
<name>list_files</name>
<path>DIRECTORY_PATH</path>
</tool_call>

<tool_call>
<name>run_command</name>
<command>COMMAND</command>
<timeout>15</timeout>
</tool_call>

<tool_call>
<name>delete_file</name>
<path>FILE_PATH</path>
</tool_call>

<tool_call>
<name>insert_content_after_number</name>
<path>FILE_PATH</path>
<content>CONTENT_TO_INSERT</content>
<argument>INSERT_AFTER_LINE_NUMBER</argument>
</tool_call>

<tool_call>
<name>insert_content_after_line</name>
<path>FILE_PATH</path>
<content>CONTENT_TO_INSERT</content>
<argument>INSERT_AFTER_TEXT</argument>
</tool_call>

<tool_call>
<name>delete_one_line_number</name>
<path>FILE_PATH</path>
<content>LINE_NUMBER_TO_DELETE</content>
</tool_call>

<tool_call>
<name>delete_one_line_content</name>
<path>FILE_PATH</path>
<content>CONTENT_TO_DELETE</content>
</tool_call>

<tool_call>
<name>delete_line_range_number</name>
<path>FILE_PATH</path>
<argument1>START_LINE_NUMBER</argument1>
<argument2>END_LINE_NUMBER</argument2>
</tool_call>

<tool_call>
<name>delete_lines</name>
<path>FILE_PATH</path>
<argument>LINE_NUMBER_TO_DELETE</argument>
</tool_call>

<tool_call>
<name>replace_line</name>
<path>FILE_PATH</path>
<content>NEW_LINE_CONTENT</content>
<argument>LINE_NUMBER_TO_REPLACE</argument>
</tool_call>


Rules:
- FILE_PATH must be the actual filename the user mentioned, never a placeholder.
- NEVER use tool_call just for greetings or questions — just reply in plain text if the use is greeting.
- NEVER invent file paths.
- For current directory, use <path>.</path>
- Be concise. This is a terminal interface, not a chat app.
- Use Windows cmd syntax for run_command (dir, cd, mkdir, &, etc.), not Unix/macOS.
- If info is missing, ask. Don't assume.
- Always prefer tools over direct code output when possible. For example, to show a file's contents, use the read_file tool instead of printing the contents directly.
- After executing a tool, wait for the user's next input before doing anything else, even if you have more tools to call. This allows the user to control the pace and see results step by step.
"""

# ──────────────────────────────────────────────
# Spinner
# ──────────────────────────────────────────────
class Spinner:
    FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

    def __init__(self, label="Thinking"):
        self.label   = label
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            print(f"\r  {accent(frame)} {dim(self.label)}...", end="", flush=True)
            time.sleep(0.08)
            i += 1

    def start(self): self._thread.start()
    def stop(self):
        self._stop.set()
        self._thread.join()
        print("\r" + " " * 40 + "\r", end="", flush=True)

# ──────────────────────────────────────────────
# Built-in slash commands
# ──────────────────────────────────────────────
def handle_slash(cmd: str, cwd: Path, history: list, model: str) -> bool:
    """Handle /commands. Returns True if handled."""
    parts = cmd.split(None, 1)
    name  = parts[0].lower()
    arg   = parts[1] if len(parts) > 1 else ""

    if name in ("exit", "quit", "q"):
        print(f"\n{bold_accent('Goodbye!')} {dim('Session ended.')}\n")
        sys.exit(0)

    elif name == "clear":
        history.clear()
        print(dim("  Context cleared."))

    elif name == "help":
        cmds = [
            ("/clear",          "Clear conversation context"),
            ("/ls [path]",      "List files in directory"),
            ("/cat <path>",     "Print a file"),
            ("/cd <path>",      "Change working directory"),
            ("/pwd",            "Show current directory"),
            ("/model",          "Show current model"),
            ("/history",        "Show conversation turns"),
            ("/exit",           "Quit OllamaCode"),
        ]
        print(f"\n  {bold_accent('Slash commands:')}")
        for c, d in cmds:
            print(f"  {accent(c.ljust(20))} {dim(d)}")
        print()

    elif name == "ls":
        target = (cwd / arg) if arg else cwd
        result = execute_tool({"name": "list_files", "params": {"path": str(target)}}, cwd)
        print(result)

    elif name == "cat":
        if not arg:
            print(accent("  Usage: /cat <path>"))
        else:
            result = execute_tool({"name": "read_file", "params": {"path": arg}}, cwd)
            print(result)

    elif name == "pwd":
        print(f"  {white(str(cwd))}")

    elif name == "model":
        print(f"  {dim('Model:')} {bold_accent(model)}")

    elif name == "history":
        for i, msg in enumerate(history):
            role = msg["role"]
            preview = msg["content"][:80].replace("\n", "↵")
            print(f"  {dim(str(i).rjust(2)+'.')} {accent(role.ljust(10))} {dim(preview)}")

    elif name == "cd":
        if not arg:
            print(accent("  Usage: /cd <path>"))
        else:
            new = (cwd / arg).resolve()
            if new.is_dir():
                os.chdir(new)
                cwd_container = [new]  # we can't return from here easily, use side effect
                print(green(f"  → {new}"))
                # We update via os.chdir; caller re-reads Path.cwd()
            else:
                print(accent(f"  ✗ Not a directory: {new}"))

    elif name == "init":
        path = cwd / "CLAUDE.md"
        content = f"# Project Instructions\n\nModel: {model}\nDirectory: {cwd}\n\nAdd your project-specific instructions here.\n"
        path.write_text(content)
        print(green(f"  ✓ Created {path}"))

    else:
        return False  # not handled
    return True

# ──────────────────────────────────────────────
# Model picker
# ──────────────────────────────────────────────
def pick_model() -> str:
    models = ollama_list_models()
    if not models:
        print(accent("No Ollama models found. Pull one first: ollama pull qwen2.5-coder"))
        sys.exit(1)

    clear_screen()
    w = term_width()
    print(bold_accent("  OllamaCode — Model Selection"))
    print(accent("─" * w))
    print()
    for i, m in enumerate(models, 1):
        print(f"  {dim(str(i).rjust(2)+'.')} {white(m)}")
    print()
    print(dim("  Enter number or model name (default: 1): "), end="", flush=True)

    try:
        choice = input().strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(0)

    if not choice:
        return models[0]
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(models):
            return models[idx]
        print(accent("Invalid number, using first model."))
        return models[0]
    if choice in models:
        return choice
    # fuzzy: find first model containing the typed string
    for m in models:
        if choice.lower() in m.lower():
            return m
    print(accent(f"Model '{choice}' not found, using {models[0]}"))
    return models[0]

# ──────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────
def main():
    model   = pick_model()
    cwd     = Path.cwd()
    history: list[dict] = []
    recent:  list[str]  = []

    # Inject CLAUDE.md into context if present
    claude_md = cwd / "CLAUDE.md"
    if claude_md.exists():
        md_content = claude_md.read_text(encoding="utf-8")
        history.append({"role": "user",      "content": f"Project instructions:\n{md_content}"})
        history.append({"role": "assistant",  "content": "Understood. I've read the project instructions."})

    draw_welcome(model, str(cwd), recent)
    draw_separator()
    print()

    while True:
        # Re-read cwd in case /cd changed it
        cwd = Path.cwd()

        user_input = draw_input_prompt()

        if not user_input:
            continue

        # Slash commands
        if user_input.startswith("/"):
            handled = handle_slash(user_input[1:], cwd, history, model)
            if not handled:
                print(dim(f"  Unknown command: {user_input}  (type /help for list)"))
            continue

        # Record and send to model
        history.append({"role": "user", "content": user_input})
        recent.insert(0, user_input[:60])
        recent = recent[:5]

        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

        index = random.randint(0, len(verbs) - 1)
        spinner = Spinner(verbs[index])
        spinner.start()

        first_chunk = True

        first_chunk = True
        full_response = ""
        hold_buffer = ""
        in_tool = False
        pending_tool_results = []  # collect (tool_name, result) during stream

        try:
            for chunk in ollama_chat_stream(model, messages):
                if first_chunk:
                    spinner.stop()
                    first_chunk = False
                    print(f"\n  {bold_accent('●')} {dim(model)}\n")

                full_response += chunk
                hold_buffer += chunk
                hold_buffer = re.sub(r'```[\w_]*\n?', '', hold_buffer)

                # Start holding back when we see an opening tool tag
                if not in_tool and "<tool" in hold_buffer:
                    in_tool = True

                if in_tool:
                    # Stream the raw XML dimmed as it arrives
                    print(dim(chunk), end="", flush=True)

                    # Check if we now have a complete tool block
                    while True:
                        m = TOOL_PATTERN.search(hold_buffer)
                        if not m:
                            break
                        # Erase the streamed XML lines we just printed
                        xml_lines = hold_buffer[:m.end()].count("\n") + 1
                        print(f"\033[{xml_lines}A\033[J", end="", flush=True)
                        # Print any text before the tool block
                        before = hold_buffer[:m.start()]
                        if before.strip():
                            print(white(before), end="", flush=True)
                        # Execute the tool
                        tool = {
                            "name": m.group(1).strip(),
                            "params": {k: v.strip() for k, v in PARAM_PATTERN.findall(m.group(2))},
                        }
                        # Confirm before creating/appending/deleting
                        if tool["name"] in ("create_file", "append_to_file_after_number", "append_to_file_after_line", "delete_file"):
                            path_preview = tool["params"].get("path", "?")
                            print(f"\n  {yellow('?')} {dim('Run')} {bold_accent(tool['name'])} {dim('on')} {white(path_preview)}{dim('?')} {dim('[y/N]')} ", end="", flush=True)
                            try:
                                confirm = input().strip().lower()
                            except (EOFError, KeyboardInterrupt):
                                confirm = "n"
                            if confirm != "y":
                                print(dim("  Skipped.\n"))
                                hold_buffer = hold_buffer[m.end():]
                                in_tool = "<tool" in hold_buffer
                                continue
                        print(accent("\n  ─── Tool Execution ───"))
                        print_tool_call(tool)
                        result = execute_tool(tool, cwd)
                        print(f"  {result}\n")
                        pending_tool_results.append((tool["name"], result))
                        # Consume this tool block from the buffer
                        hold_buffer = hold_buffer[m.end():]
                        in_tool = "<tool" in hold_buffer
                else:
                    # No tool detected — stream immediately
                    print(white(hold_buffer), end="", flush=True)
                    hold_buffer = ""

        except KeyboardInterrupt:
            if first_chunk:
                spinner.stop()
            print(f"\n{dim('  Interrupted.')}")

        if first_chunk:
            spinner.stop()

        # Flush any remaining non-tool text
        if hold_buffer.strip():
            print(white(hold_buffer), end="", flush=True)

        print("\n")
        history.append({"role": "assistant", "content": full_response})
        for tool_name, result in pending_tool_results:
            feed_tool_result(tool_name, result, history)
        pending_tool_results = []

        while (history
               and history[-1]["role"] == "user"
               and "<tool_result>" in history[-1]["content"]):
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

            index = random.randint(0, len(verbs) - 1)
            spinner = Spinner(verbs[index])
            spinner.start()

            full_response = ""
            hold_buffer = ""
            in_tool = False
            first_chunk = True
            pending_tool_results = []

            try:
                for chunk in ollama_chat_stream(model, messages):
                    if first_chunk:
                        spinner.stop()
                        first_chunk = False
                        print(f"\n  {bold_accent('●')} {dim(model)}\n")
                    full_response += chunk
                    hold_buffer += chunk
                    if not in_tool and "<tool" in hold_buffer:
                        in_tool = True
                    if in_tool:
                        print(dim(chunk), end="", flush=True)
                        while True:
                            m = TOOL_PATTERN.search(hold_buffer)
                            if not m:
                                break
                            xml_lines = hold_buffer[:m.end()].count("\n") + 1
                            print(f"\033[{xml_lines}A\033[J", end="", flush=True)
                            before = hold_buffer[:m.start()]
                            if before.strip():
                                print(white(before), end="", flush=True)
                            tool = {
                                "name": m.group(1).strip(),
                                "params": {k: v.strip() for k, v in PARAM_PATTERN.findall(m.group(2))},
                            }
                            if tool["name"] in ("create_file", "append_to_file_after_number", "append_to_file_after_line", "delete_file"):
                                path_preview = tool["params"].get("path", "?")
                                print(f"\n  {yellow('?')} {dim('Run')} {bold_accent(tool['name'])} {dim('on')} {white(path_preview)}{dim('?')} {dim('[y/N]')} ", end="", flush=True)
                                try:
                                    confirm = input().strip().lower()
                                except (EOFError, KeyboardInterrupt):
                                    confirm = "n"
                                if confirm != "y":
                                    print(dim("  Skipped.\n"))
                                    hold_buffer = hold_buffer[m.end():]
                                    in_tool = "<tool" in hold_buffer
                                    continue
                            print(accent("\n  ─── Tool Execution ───"))
                            print_tool_call(tool)
                            result = execute_tool(tool, cwd)
                            print(f"  {result}\n")
                            pending_tool_results.append((tool["name"], result))
                            hold_buffer = hold_buffer[m.end():]
                            in_tool = "<tool" in hold_buffer
                    else:
                        print(white(hold_buffer), end="", flush=True)
                        hold_buffer = ""
            except KeyboardInterrupt:
                if first_chunk:
                    spinner.stop()
                print(f"\n{dim('  Interrupted.')}")
                break

            if first_chunk:
                spinner.stop()
            if hold_buffer.strip():
                print(white(hold_buffer), end="", flush=True)
            print()
            history.append({"role": "assistant", "content": full_response})
            for tool_name, result in pending_tool_results:
                feed_tool_result(tool_name, result, history)
            pending_tool_results = []

        draw_status_bar(model)

# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────
if __name__ == "__main__":
    # Windows: enable ANSI escape codes
    if os.name == "nt":
        os.system("color")  # activates VT100 on Windows 10+
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)

    main()
