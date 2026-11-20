"""Web search and quicklinks (a URL with an optional {query}, behind a keyword)."""

from __future__ import annotations

import urllib.parse

from . import Provider, action, close, entry_index, form, item, refresh, set_query, stable_entries, toast
from .. import util
from ..settings import ENGINES


def engine(settings) -> tuple[str, str]:
    key = settings.get("web.engine", "duckduckgo")
    name, url = ENGINES.get(key, ENGINES["duckduckgo"])
    if key == "custom":
        url = settings.get("web.custom", "") or ENGINES["duckduckgo"][1]
        name = urllib.parse.urlparse(url).hostname or "the web"
    return name, url


def fill(url: str, query: str) -> str:
    return url.replace("{query}", urllib.parse.quote_plus(query) if "?" in url.split("{query}")[0]
                       else urllib.parse.quote(query, safe="/"))


class Web(Provider):
    id = "web"
    name = "Web search"
    prefix = "?"
    in_global = False

    def search(self, q, scoped):
        q = q.strip()
        if not q:
            return []
        name, _ = engine(self.settings)
        out = [item("web", "search", f"Search {name} for “{q}”", icon="󰖟", accessory="Web",
                    recordable=False, score=950, _q=q)]
        # Something like a web address: open it. Only http(s); "file://" or a custom
        # scheme typed here would hand xdg-open whatever it points at.
        if "." in q and " " not in q and not q.startswith(".") and \
                ("://" not in q or q.lower().startswith(("http://", "https://"))):
            out.insert(0, item("web", "open", f"Open {q}", icon="󰌹", accessory="Web", recordable=False,
                               score=960, _url=q if "://" in q else "https://" + q))
        return out

    def fallback(self, q: str) -> dict:
        name, _ = engine(self.settings)
        return item("web", "search", f"Search {name} for “{q}”", icon="󰖟", accessory="Web",
                    recordable=False, _q=q)

    def actions(self, it):
        return [action("default", "Open in browser", "󰖟"), action("copy", "Copy URL", "󰆏", "ctrl+shift+c")]

    def run(self, it, aid, arg):
        url = it.get("_url") or fill(engine(self.settings)[1], it["_q"])
        if aid == "copy":
            util.copy(url)
            return [toast("Copied the URL")]
        util.open_target(url)
        return [close()]


class Quicklinks(Provider):
    id = "quicklinks"
    name = "Quicklinks"
    prefix = "ql"
    description = "Links with a keyword, like “gh owner/repo”"

    def _items(self):
        out = []
        for ql in stable_entries(self.settings, "quicklinks", "quicklinks", lambda e: str(e.get("name") or e.get("url"))):
            if not isinstance(ql, dict) or not ql.get("url"):
                continue
            name = str(ql.get("name") or ql["url"])
            kw = str(ql.get("keyword") or "")
            needs = "{query}" in ql["url"]
            out.append(item("quicklinks", ql["id"], name, subtitle=str(ql["url"]), icon="󰌷",
                            accessory=kw or "Quicklink", _keywords=[kw] if kw else [], _eid=ql["id"],
                            _url=str(ql["url"]), _kw=kw,
                            arg={"placeholder": "Search " + name} if needs else None))
        return out

    def search(self, q, scoped):
        items = self._items()
        out = []
        qs = q.strip()
        # "gh owner/repo": the keyword, a space, then the argument.
        head, _, rest = qs.partition(" ")
        for it in items:
            if it.get("_kw") and head.lower() == it["_kw"].lower() and rest and it.get("arg"):
                # An id of its own: the filled-in copy must never stand in for the quicklink itself.
                filled = dict(it, id=it["id"] + ":filled", title=f"{it['title']}: {rest}", score=985,
                              recordable=False, _filled=rest)
                filled.pop("arg", None)
                out.append(filled)
        for it in self.rank(qs, items):
            if it.get("_kw") and it["_kw"].lower() == qs.lower():
                it["score"] = 970
            out.append(it)
        if scoped:
            out.append(item("quicklinks", "new", "Create quicklink", subtitle="A URL with an optional {query}",
                            icon="󰐕", accessory="Quicklink", recordable=False, score=200, _new=True))
        return out

    def browse(self):
        return self.search("", True) + self._items()

    def resolve(self, item_id):
        for it in self._items():
            if it["id"] == item_id:
                return it
        return None

    def actions(self, it):
        if it.get("_new"):
            return [action("default", "Create quicklink", "󰐕")]
        return [action("default", "Open", "󰌷"), action("copy", "Copy URL", "󰆏", "ctrl+shift+c"),
                action("edit", "Edit quicklink", "󰏫", "ctrl+e"), action("delete", "Delete quicklink", "󰆴", "ctrl+d")]

    def run(self, it, aid, arg):
        if it.get("_new") or aid == "edit":
            links = self.settings.get("quicklinks", [])
            n = -1 if it.get("_new") else entry_index(links, it.get("_eid", ""))
            if not it.get("_new") and n < 0:
                return [toast("That quicklink is gone", True), refresh()]
            ql = {} if it.get("_new") else links[n]
            return [form(f"quicklink:{'new' if it.get('_new') else it['_eid']}",
                         "Create quicklink" if it.get("_new") else "Edit quicklink", [
                             {"key": "name", "label": "Name", "type": "text", "value": ql.get("name", "")},
                             {"key": "url", "label": "URL", "type": "text", "value": ql.get("url", ""),
                              "hint": "Put {query} where the search text goes"},
                             {"key": "keyword", "label": "Keyword", "type": "text", "value": ql.get("keyword", ""),
                              "hint": "Type the keyword, a space, then what to search"},
                         ], icon="󰌷")]
        if aid == "delete":
            links = list(self.settings.get("quicklinks", []))
            n = entry_index(links, it.get("_eid", ""))
            if n < 0:
                return [toast("That quicklink is gone", True), refresh()]
            links.pop(n)
            self.settings.set_many({"quicklinks": links})
            return [toast("Deleted " + it["title"]), refresh()]
        query = it.get("_filled") if it.get("_filled") is not None else (arg or "")
        url = fill(it["_url"], query) if "{query}" in it["_url"] else it["_url"]
        if aid == "copy":
            util.copy(url)
            return [toast("Copied the URL")]
        util.open_target(url)
        return [close()]

    def submit(self, form_id, values):
        which = form_id.split(":", 1)[1]
        url = str(values.get("url") or "").strip()
        if not url:
            return [toast("A quicklink needs a URL", True)]
        if "://" not in url:
            url = "https://" + url
        entry = {"name": str(values.get("name") or "").strip() or url, "keyword": str(values.get("keyword") or "").strip(),
                 "url": url}
        links = list(self.settings.get("quicklinks", []))
        if which == "new":
            import uuid
            links.append(dict(entry, id=uuid.uuid4().hex[:12]))
        else:
            n = entry_index(links, which)
            if n < 0:
                return [toast("That quicklink is gone", True)]
            links[n] = dict(entry, id=which)
        self.settings.set_many({"quicklinks": links})
        return [toast("Saved " + entry["name"]), set_query("ql ")]
