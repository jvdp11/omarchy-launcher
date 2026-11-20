"""Answers: ask Claude about the setup, and the dotfiles manual's sections."""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time

from . import Provider, action, close, detail, item, toast
from .. import util

SYSTEM_PROMPT = (
    "You answer questions about the user's Omarchy desktop, dotfiles and setup from a launcher popup. "
    "Be short and concrete: the command, file or setting they need, then one or two lines of why. "
    "Markdown without images or HTML. Never change files."
)
TOOLS = ["Read", "Grep", "Glob", "Bash(omarchy-plugin-map:*)", "Bash(herdr-spaces list)", "Bash(omarchy commands:*)"]
# The only built-in tools that exist for these calls at all; TOOLS above narrows Bash further.
BUILTIN_TOOLS = "Read,Grep,Glob,Bash"


FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# A link reference definition, also inside a list item or a quote: "[x]: url", "- [x]: url".
REFERENCE = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+|>\s*)*\[[^\]\n]*\]:\s*\S")


def _escape(text: str) -> str:
    # "![" never starts an image: a zero-width space after the "!" makes it a plain
    # link at most, which the overlay doesn't follow. Matching image syntax with a
    # pattern instead was bypassed by brackets inside the alt text.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("![", "!\u200b[")


def sanitize_markdown(text: str) -> str:
    """Markdown that is safe to render: no images, no raw HTML.

    Qt's Markdown renderer loads images (remote ones too) and passes HTML
    through, so both go before display. Link targets stay as text; the
    overlay doesn't follow links. Inside code (fenced or `inline`) Markdown
    shows text literally, so nothing is escaped there.
    """
    out = []
    fence = ""                      # the fence that opened the current code block
    for line in text.split("\n"):
        m = FENCE.match(line)
        if m and (not fence or (m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence)
                                and not line[m.end():].strip())):
            fence = "" if fence else m.group(1)
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        if REFERENCE.match(line):
            continue
        parts = line.split("`")
        for i in range(0, len(parts), 2):
            if i == len(parts) - 1 and len(parts) % 2 == 0:
                break
            parts[i] = _escape(parts[i])
        if len(parts) % 2 == 0:   # an unclosed ` : the rest is plain text after all
            parts[-1] = _escape(parts[-1])
        out.append("`".join(parts))
    return "\n".join(out)


class Claude(Provider):
    id = "claude"
    name = "Ask Claude"
    prefix = "ask"
    in_global = False
    description = "Answers from the Omarchy space: your manual, rules and setup"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def available(self):
        return util.have("claude")

    def search(self, q, scoped):
        q = q.strip()
        if not q:
            return [item("claude", "hint", "Ask Claude about your setup", subtitle="Type a question, then Enter",
                         icon="󰚩", accessory="Claude", recordable=False, score=1, _hint=True)]
        return [self.fallback(q, 990)]

    def fallback(self, q: str, score: int = 0) -> dict:
        return item("claude", "ask", f"Ask Claude: {q}", subtitle="Answers about your setup (read-only)",
                    icon="󰚩", accessory="Claude", recordable=False, score=score, _q=q)

    def actions(self, it):
        if it.get("_hint"):
            return []
        acts = [action("default", "Ask", "󰚩")]
        space = self.settings.get("claude.space", "")
        if space and util.have("herdr"):
            acts.append(action("space", f"Ask in the {space} space (herdr)", "", "ctrl+enter"))
        return acts

    def run(self, it, aid, arg):
        if it.get("_hint"):
            return []
        q = it["_q"]
        if aid == "space":
            space = self.settings.get("claude.space", "")
            util.copy(q)
            from .work import focus_herdr_window
            util.run_ok(["herdr", "workspace", "focus", _herdr_ws(space)], timeout=3)
            focus_herdr_window()
            return [toast(f"Question copied: paste it in the {space} space"), close()]
        # The conversation lives in its own window (ChatWindow.qml), which sends
        # the question back as {"op": "chat"} so follow-ups can resume it.
        return [{"type": "chat", "q": q}, close()]

    def chat(self, turn: str, q: str, session: str = "") -> None:
        """Answer one turn of the chat window; a session id continues that conversation."""
        threading.Thread(target=self._ask, args=(q, {"op": "chat", "turn": turn}, session), daemon=True).start()

    def cancel(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                util.kill_group(self._proc)     # claude and the tools it started

    def _ask(self, q: str, tag: dict, session: str = "") -> None:
        self.cancel()
        folder = util.expand(self.settings.get("claude.folder", "~") or "~")
        # The question goes in on stdin: not in argv, where `ps` shows it and a
        # question starting with "--" would read as an option. Read-only for real:
        # only these tools, no MCP servers, and only the user's own settings, not
        # the hooks or permissions of whatever folder it answers from.
        argv = ["claude", "-p", "--model", self.settings.get("claude.model", "sonnet"),
                "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--append-system-prompt", SYSTEM_PROMPT, "--tools", BUILTIN_TOOLS,
                "--strict-mcp-config", "--setting-sources", "user", "--allowedTools", *TOOLS]
        if session:
            argv += ["--resume", session]

        def emit(fields: dict) -> None:
            self.ctx.emit({**tag, **fields})

        emit({"status": "Thinking…"})
        try:
            proc = subprocess.Popen(argv, cwd=str(folder) if folder.is_dir() else str(util.HOME),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, errors="replace", start_new_session=True)
        except OSError as e:
            emit({"text": f"Could not start claude: {e}", "done": True, "status": ""})
            return
        with self._lock:
            self._proc = proc
        try:
            proc.stdin.write(q)
            proc.stdin.close()
        except OSError:
            pass
        # stderr is read alongside, so a chatty claude can't fill its pipe and stall.
        errors: list[str] = []
        drain = threading.Thread(target=lambda: errors.extend(proc.stderr.readlines()[-20:]), daemon=True)
        drain.start()
        answer = ""
        final = ""
        last_emit = 0.0
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if ev.get("session_id") and ev["session_id"] != session:
                session = ev["session_id"]
                emit({"session": session})
            if kind == "stream_event":
                inner = ev.get("event") or {}
                delta = inner.get("delta") or {}
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    answer += delta.get("text", "")
                    # At most ten updates a second: each one re-sanitizes the whole answer.
                    now = time.monotonic()
                    if now - last_emit > 0.1:
                        last_emit = now
                        emit({"text": sanitize_markdown(answer), "raw": answer, "status": ""})
                elif inner.get("type") == "content_block_start" and \
                        (inner.get("content_block") or {}).get("type") == "tool_use":
                    tool = (inner.get("content_block") or {}).get("name", "")
                    emit({"status": f"Looking at your setup ({tool})…"})
                elif inner.get("type") == "message_start" and answer:
                    # A new turn after tool use: start the answer over.
                    answer += "\n\n"
            elif kind == "result":
                final = str(ev.get("result") or "")
        proc.wait()
        drain.join(timeout=2)
        if proc.returncode != 0 and not (final or answer):
            err = "".join(errors).strip().splitlines()
            final = "*Claude did not answer.* " + (err[-1] if err else "Is it logged in? Try `claude` in a terminal.")
        emit({"text": sanitize_markdown(final or answer), "raw": final or answer, "done": True, "status": ""})


def _herdr_ws(label: str) -> str:
    raw = util.run(["herdr", "workspace", "list"], timeout=3)
    try:
        for w in json.loads(raw)["result"]["workspaces"]:
            if w.get("label") == label:
                return str(w.get("workspace_id"))
    except (ValueError, KeyError, TypeError):
        pass
    return ""


def parse_sections(path) -> list[dict]:
    """## and ### headings outside code fences, with their text."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    heads = []
    fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{2,4})\s+(.+?)\s*#*\s*$", line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip()))
    out = []
    parent = ""
    for n, (i, level, title) in enumerate(heads):
        end = len(lines)
        for j, lv, _ in heads[n + 1:]:
            if lv <= level:
                end = j
                break
        if level == 2:
            parent = title
        body = "\n".join(lines[i:end]).strip()
        out.append({"title": title, "parent": parent if level > 2 else "", "line": i + 1, "body": body})
    return out


class Manual(Provider):
    id = "manual"
    name = "Manual"
    prefix = "help"
    description = "The sections of your own Markdown manual (Settings › Files and projects)"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.items: list[dict] = []
        self._mtimes: tuple = ()

    def docs(self) -> list:
        return [util.expand(f) for f in self.settings.get("manual.files", []) if str(f).strip()]

    def available(self):
        return any(p.exists() for p in self.docs())

    def refresh(self):
        docs = self.docs()
        mtimes = tuple((str(p), p.stat().st_mtime if p.exists() else 0) for p in docs)
        if mtimes == self._mtimes:
            return
        items = []
        for doc in docs:
            for s in parse_sections(doc):
                where = "Manual" if doc == docs[0] else doc.parent.name
                sub = f"{where} › {s['parent']}" if s["parent"] else where
                # The path and line make the id unique: two "### Install" sections, or two README.md files.
                items.append(item("manual", f"{doc}:{s['line']}:{s['title']}", s["title"], subtitle=sub, icon="󰈙",
                                  accessory="Manual", _keywords=[s["body"][:400]], _body=s["body"],
                                  _doc=str(doc), _line=s["line"]))
        self.items = items
        self._mtimes = mtimes

    def search(self, q, scoped):
        found = self.rank(q, self.items, minimum=1 if scoped else 600)
        for it in found:
            # Body matches are weak: a word somewhere in a section.
            if not scoped:
                it["score"] -= 150
        return found

    def browse(self):
        return self.items

    def resolve(self, item_id):
        for it in self.items:
            if it["id"] == item_id:
                return it
        return None

    def doc_actions(self) -> list:
        # dotfiles-help is a reader of its own some setups have; offered only there.
        acts = [action("help", "Open in Dotfiles Help", "󰋖", "ctrl+enter")] if util.have("dotfiles-help") else []
        return acts + [action("edit", "Edit the manual", "󰏫", "ctrl+e")]

    def actions(self, it):
        return [action("default", "Read", "󰈙")] + self.doc_actions()

    def run(self, it, aid, arg):
        if aid == "help":
            util.terminal(["dotfiles-help", it["title"]])
            return [close()]
        if aid == "edit":
            util.editor(it["_doc"])
            return [close()]
        return [detail(it["title"], sanitize_markdown(it["_body"]), fmt="markdown", icon="󰈙",
                       actions=self.doc_actions())]
