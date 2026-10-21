"""Answers: ask Claude about the setup, and the dotfiles manual's sections."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading

from . import Provider, action, close, detail, item, toast
from .. import util

DOTFILES = util.expand(os.environ.get("DOTFILES") or "~/.dotfiles")
DOCS = [DOTFILES / "README.md", DOTFILES / "packages.d/README.md"]

SYSTEM_PROMPT = (
    "You answer questions about the user's Omarchy desktop, ~/.dotfiles and setup from a launcher popup. "
    "Be short and concrete: the command, file or setting they need, then one or two lines of why. "
    "Markdown without images or HTML. Never change files."
)
TOOLS = ["Read", "Grep", "Glob", "Bash(omarchy-plugin-map:*)", "Bash(herdr-spaces list)", "Bash(omarchy commands:*)"]


def sanitize_markdown(text: str) -> str:
    """Markdown that is safe to render: no images, no raw HTML.

    Qt's Markdown renderer loads images (remote ones too) and passes HTML
    through, so both go before display. Link targets stay as text; the
    overlay doesn't follow links.
    """
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)          # ![alt](src) → alt
    text = re.sub(r"!\[([^\]]*)\]\[[^\]]*\]", r"\1", text)          # ![alt][ref] → alt
    text = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", "", text, flags=re.M)  # [ref]: url
    # HTML outside code: escaped. Inside code (fenced or `inline`) Markdown
    # shows text literally, so escaping there would show "&lt;".
    out = []
    fence = False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
            out.append(line)
            continue
        if fence:
            out.append(line)
            continue
        parts = line.split("`")
        for i in range(0, len(parts), 2):
            if i == len(parts) - 1 and len(parts) % 2 == 0:
                break
            parts[i] = parts[i].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if len(parts) % 2 == 0:   # an unclosed ` : the rest is plain text after all
            parts[-1] = parts[-1].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
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
        return item("claude", "ask", f"Ask Claude: {q}", subtitle="Answers from your Omarchy space (read-only)",
                    icon="󰚩", accessory="Claude", recordable=False, score=score, _q=q)

    def actions(self, it):
        if it.get("_hint"):
            return []
        return [action("default", "Ask", "󰚩"), action("space", "Ask in the omarchy space (herdr)", "", "ctrl+enter")]

    def run(self, it, aid, arg):
        if it.get("_hint"):
            return []
        q = it["_q"]
        if aid == "space":
            util.copy(q)
            from .work import focus_herdr_window
            util.run_ok(["herdr", "workspace", "focus", _omarchy_ws()], timeout=3)
            focus_herdr_window()
            return [toast("Question copied: paste it in the omarchy space"), close()]
        stream = self.ctx.new_stream()
        threading.Thread(target=self._ask, args=(q, stream), daemon=True).start()
        return [detail(q, "", fmt="markdown", stream=stream, icon="󰚩",
                       actions=[action("copy", "Copy answer", "󰆏", "ctrl+c")])]

    def cancel(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def _ask(self, q: str, stream: str) -> None:
        self.cancel()
        folder = util.expand(self.settings.get("claude.folder", "~/Work/omarchy"))
        argv = ["claude", "-p", q, "--model", self.settings.get("claude.model", "sonnet"),
                "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                "--append-system-prompt", SYSTEM_PROMPT, "--allowedTools", *TOOLS]
        emit = self.ctx.emit
        emit({"op": "stream", "stream": stream, "status": "Thinking…"})
        try:
            proc = subprocess.Popen(argv, cwd=str(folder) if folder.is_dir() else str(util.HOME),
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
        except OSError as e:
            emit({"op": "stream", "stream": stream, "text": f"Could not start claude: {e}", "done": True})
            return
        with self._lock:
            self._proc = proc
        answer = ""
        final = ""
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "stream_event":
                inner = ev.get("event") or {}
                delta = inner.get("delta") or {}
                if inner.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                    answer += delta.get("text", "")
                    emit({"op": "stream", "stream": stream, "text": sanitize_markdown(answer), "status": ""})
                elif inner.get("type") == "content_block_start" and \
                        (inner.get("content_block") or {}).get("type") == "tool_use":
                    tool = (inner.get("content_block") or {}).get("name", "")
                    emit({"op": "stream", "stream": stream, "status": f"Looking at your setup ({tool})…"})
                elif inner.get("type") == "message_start" and answer:
                    # A new turn after tool use: start the answer over.
                    answer += "\n\n"
            elif kind == "result":
                final = str(ev.get("result") or "")
        proc.wait()
        if proc.returncode != 0 and not (final or answer):
            err = (proc.stderr.read() if proc.stderr else "").strip().splitlines()
            final = "*Claude did not answer.* " + (err[-1] if err else "Is it logged in? Try `claude` in a terminal.")
        emit({"op": "stream", "stream": stream, "text": sanitize_markdown(final or answer), "done": True,
              "status": ""})


def _omarchy_ws() -> str:
    raw = util.run(["herdr", "workspace", "list"], timeout=3)
    try:
        for w in json.loads(raw)["result"]["workspaces"]:
            if w.get("label") == "omarchy":
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
    name = "Dotfiles manual"
    prefix = "help"
    description = "The sections of ~/.dotfiles/README.md (what dotfiles-help shows)"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.items: list[dict] = []
        self._mtimes: tuple = ()

    def available(self):
        return DOCS[0].exists()

    def refresh(self):
        mtimes = tuple(p.stat().st_mtime if p.exists() else 0 for p in DOCS)
        if mtimes == self._mtimes:
            return
        items = []
        for doc in DOCS:
            for s in parse_sections(doc):
                where = "Manual" if doc == DOCS[0] else doc.parent.name
                sub = f"{where} › {s['parent']}" if s["parent"] else where
                items.append(item("manual", f"{doc.name}:{s['title']}", s["title"], subtitle=sub, icon="󰈙",
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

    def actions(self, it):
        return [action("default", "Read", "󰈙"), action("help", "Open in Dotfiles Help", "󰋖", "ctrl+enter"),
                action("edit", "Edit the manual", "󰏫", "ctrl+e")]

    def run(self, it, aid, arg):
        if aid == "help":
            util.terminal(["dotfiles-help", it["title"]])
            return [close()]
        if aid == "edit":
            util.editor(it["_doc"])
            return [close()]
        return [detail(it["title"], sanitize_markdown(it["_body"]), fmt="markdown", icon="󰈙",
                       actions=[action("help", "Open in Dotfiles Help", "󰋖", "ctrl+enter"),
                                action("edit", "Edit the manual", "󰏫", "ctrl+e")])]
