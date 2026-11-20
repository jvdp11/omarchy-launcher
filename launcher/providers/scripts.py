"""Script commands: any executable in ~/.config/omarchy/launcher/commands/.

A script describes itself in comments near the top:

  # @launcher.schemaVersion 1
  # @launcher.title Public IP
  # @launcher.mode fullOutput        silent | compact | fullOutput | inline | list
  # @launcher.icon 🌐
  # @launcher.argument1 { "type": "text", "placeholder": "Query", "optional": true }
  # @launcher.needsConfirmation true

(Headers written as @raycast.* are read the same way, so existing
script-command collections work unchanged.)

A list script is a search source: with `@launcher.prefix man` it gets the
query as $1 and prints one JSON object per line, {"title", "subtitle", "icon",
"accessory", "action"}, where action is {"open": url}, {"copy": text},
{"paste": text} or {"run": [argv]}.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import secrets
import time

from . import Provider, action, close, detail, form, item, toast
from .. import util

HEADER = re.compile(r"^\s*(?:#|//|--|;)\s*@(?:raycast|launcher)\.(\w+)\s+(.*?)\s*$")
MAX_OUTPUT = 200_000


def parse_header(path: str) -> dict | None:
    """The @launcher. fields of a script, or None if it has none."""
    meta: dict = {"arguments": []}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f):
                if n > 60:
                    break
                m = HEADER.match(line)
                if not m:
                    continue
                key, value = m.groups()
                am = re.fullmatch(r"argument(\d)", key)
                if am:
                    try:
                        arg = json.loads(value)
                    except ValueError:
                        continue
                    if isinstance(arg, dict):
                        meta["arguments"].append((int(am.group(1)), arg))
                else:
                    meta[key] = value
    except OSError:
        return None
    if not meta.get("title"):
        return None
    meta["arguments"] = [a for _, a in sorted(meta["arguments"], key=lambda x: x[0])][:3]
    return meta


def needs_confirmation(meta: dict) -> bool:
    """@launcher.needsConfirmation (or Raycast's spelling): true, yes or 1, in any case."""
    return str(meta.get("needsConfirmation", "")).strip().lower() in ("true", "yes", "1")


def script_dirs(settings) -> list[str]:
    dirs = [str(util.USER_DIR / "commands"), str(util.PLUGIN_DIR / "commands")]
    return [d for d in dirs if os.path.isdir(d)]


class Scripts(Provider):
    id = "scripts"
    name = "Script commands"
    description = "Your scripts in ~/.config/omarchy/launcher/commands"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.items: list[dict] = []
        self.lists: list[dict] = []
        self.pending_args: dict[str, list[str]] = {}
        self.pending_confirmations: dict[str, tuple] = {}
        self.confirmed_lists: dict[str, float] = {}

    def refresh(self):
        items, lists = [], []
        for d in script_dirs(self.settings):
            for name in sorted(os.listdir(d)):
                path = os.path.join(d, name)
                if not os.path.isfile(path) or not os.access(path, os.X_OK):
                    continue
                meta = parse_header(path)
                if not meta:
                    continue
                icon = meta.get("icon", "")
                if icon.startswith(("http://", "https://")) or "/" in icon or "." in icon:
                    icon = ""   # no remote or file icons; a glyph or emoji only
                mode = meta.get("mode", "silent")
                it = item("scripts", path, meta["title"], subtitle=meta.get("description") or meta.get("packageName") or "Script",
                          icon=icon or "󰆍", accessory=meta.get("packageName") or "Script",
                          _keywords=[meta.get("packageName", ""), name], _path=path, _mode=mode,
                          _args=meta["arguments"], _confirm=needs_confirmation(meta),
                          _prefix=meta.get("prefix", ""), _dir=d)
                if meta["arguments"]:
                    it["arg"] = {"placeholder": meta["arguments"][0].get("placeholder") or "Argument"}
                (lists if mode == "list" else items).append(it)
        self.items = items
        self.lists = lists

    def search(self, q, scoped):
        return self.rank(q, self.items + [dict(l, arg=None) for l in self.lists])

    def browse(self):
        return self.items + self.lists

    def resolve(self, item_id):
        for it in self.items + self.lists:
            if it["id"] == item_id:
                return it
        return None

    # List scripts act as sources of their own, behind their prefix.
    def list_prefixes(self) -> dict[str, dict]:
        return {l["_prefix"]: l for l in self.lists if l["_prefix"]}

    def list_search(self, script: dict, q: str) -> list[dict]:
        if script.get("_confirm") and self.confirmed_lists.get(script["_path"], 0) < time.monotonic():
            return [dict(script, arg=None, score=1000,
                         subtitle="Confirm this script before searching it")]
        try:
            _, stdout, _ = util.communicate([script["_path"], q], timeout=5, cwd=script["_dir"])
        except (OSError, subprocess.SubprocessError):
            return []
        items = []
        for n, line in enumerate(stdout[:MAX_OUTPUT].splitlines()[:200]):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not row.get("title"):
                continue
            act = row.get("action") if isinstance(row.get("action"), dict) else {}
            items.append(item("scripts", f"{script['_path']}#{n}", str(row["title"])[:300],
                              subtitle=str(row.get("subtitle") or "")[:300], icon=str(row.get("icon") or script["icon"])[:8],
                              accessory=str(row.get("accessory") or "")[:40], recordable=False, score=500 - n,
                              _row_action=act))
        return items

    def actions(self, it):
        if "_row_action" in it:
            return [action("default", "Open", "󰏌"), action("copy", "Copy title", "󰆏", "ctrl+c")]
        if it.get("_mode") == "list":
            return [action("default", "Search", "󰍉")]
        return [action("default", "Run", "󰘳"), action("edit", "Edit script", "󰏫", "ctrl+e"),
                action("folder", "Open commands folder", "󰉋", "ctrl+o")]

    def run(self, it, aid, arg):
        if "_row_action" in it:
            return self._row(it, aid)
        if aid == "edit":
            util.editor(it["_path"])
            return [close()]
        if aid == "folder":
            util.open_target(it["_dir"])
            return [close()]
        # Read the script's header again now: it may have changed (or gained a
        # confirmation) since the list was built.
        meta = parse_header(it["_path"]) if os.access(it["_path"], os.X_OK) else None
        if meta is None:
            return [toast(f"{it['title']} is gone or no longer a launcher script", True)]
        it = dict(it, _confirm=needs_confirmation(meta), _args=meta["arguments"],
                  _mode=meta.get("mode", it.get("_mode", "silent")))
        if it.get("_mode") == "list":
            if it.get("_confirm"):
                return self._confirmation(it, [])
            return self._execute(it, [])
        # Collect the arguments one prompt at a time.
        args = self.pending_args.get(it["id"], [])
        if it["_args"]:
            if arg is not None:
                args = args + [arg]
            spec = it["_args"]
            while len(args) < len(spec) and spec[len(args)].get("optional") and arg is None:
                args.append("")
            if len(args) < len(spec):
                self.pending_args[it["id"]] = args
                nxt = spec[len(args)]
                return [{"type": "arg", "item": it["id"], "action": aid, "title": it["title"],
                         "placeholder": nxt.get("placeholder") or f"Argument {len(args) + 1}"}]
            if any(not a and not spec[i].get("optional") for i, a in enumerate(args)):
                self.pending_args.pop(it["id"], None)
                return [toast(f"{it['title']} needs {spec[[i for i, a in enumerate(args) if not a][0]].get('placeholder', 'an argument')}", True)]
        self.pending_args.pop(it["id"], None)
        if it.get("_confirm"):
            return self._confirmation(it, args)
        return self._execute(it, args)

    def _confirmation(self, it, args):
        now = time.monotonic()
        self.pending_confirmations = {k: v for k, v in self.pending_confirmations.items() if v[2] > now}
        if len(self.pending_confirmations) >= 32:
            self.pending_confirmations.pop(next(iter(self.pending_confirmations)))
        token = secrets.token_urlsafe(24)
        self.pending_confirmations[token] = (dict(it), list(args), now + 300)
        note = "Allow searches through this list script for five minutes." if it.get("_mode") == "list" else "Run this script with the arguments shown below."
        return [form("confirm-script:" + token, "Run " + it["title"] + "?", [
            {"type": "info", "label": note},
            {"type": "info", "label": it["_path"]},
            {"type": "info", "label": "Arguments: " + repr(args)},
        ], submit="Run", icon=it.get("icon", ""))]

    def submit(self, form_id, values):
        token = form_id.partition(":")[2]
        pending = self.pending_confirmations.pop(token, None)
        if pending is None or pending[2] < time.monotonic():
            return [toast("Confirmation expired; select the script again", True)]
        return self._execute(pending[0], pending[1])

    def _execute(self, it, args):
        if it.get("_mode") == "list":
            self.confirmed_lists[it["_path"]] = time.monotonic() + 300
            return [{"type": "query", "q": (it["_prefix"] + " ") if it["_prefix"] else ""}]
        argv = [it["_path"]] + args
        mode = it.get("_mode", "silent")
        if mode == "silent":
            util.spawn(argv, cwd=it["_dir"])
            return [close()]
        try:
            code, stdout, stderr = util.communicate(argv, timeout=30, cwd=it["_dir"])
        except subprocess.TimeoutExpired:
            return [toast(f"{it['title']} took longer than 30 seconds", True)]
        except OSError as e:
            return [toast(f"{it['title']}: {e.strerror}", True)]
        text = (stdout + (("\n" + stderr) if code != 0 else ""))[:MAX_OUTPUT].rstrip()
        if mode == "fullOutput":
            return [detail(it["title"], _strip_ansi(text) or "(no output)", icon=it["icon"],
                           actions=[action("copy", "Copy output", "󰆏", "ctrl+c")])]
        last = (_strip_ansi(text).splitlines() or ["Done"])[-1]
        return [toast(last, code != 0)] + ([] if code != 0 else [close()])

    def _row(self, it, aid):
        act = it["_row_action"]
        if aid == "copy":
            util.copy(it["title"])
            return [toast("Copied")]
        if isinstance(act.get("open"), str) and re.match(r"^(https?|file)://|^/", act["open"]):
            util.open_target(act["open"])
        elif isinstance(act.get("copy"), str):
            util.copy(act["copy"])
        elif isinstance(act.get("paste"), str):
            util.paste(act["paste"])
        elif isinstance(act.get("run"), list) and all(isinstance(a, str) for a in act["run"]) and act["run"]:
            util.spawn(act["run"])
        else:
            return [toast("This row has nothing to do", True)]
        return [close()]


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
