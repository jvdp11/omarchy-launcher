# Launcher

A Raycast-style launcher for the Omarchy shell: one search box for apps,
Omarchy commands, files, sums, world clocks, the clipboard, emoji, snippets,
quicklinks, 1Password, herdr spaces, projects, your other plugins, script
commands and *Ask Claude*. It follows the Omarchy theme, because it uses the
same colour tokens as the Omarchy menu.

**Alt + Space** opens it (bound in the dotfiles), or `omarchy-shell shell toggle jvdp.launcher`.

## Keys

| Key | Does |
|---|---|
| type | search everything that is on in the normal search |
| ↑ ↓, Ctrl+J/K, PgUp/PgDn | move |
| Enter | the result's main action (shown bottom right) |
| Tab or Ctrl+K | all actions of the result, with their shortcuts |
| Ctrl+P | add to / remove from favourites |
| Ctrl+L | set an alias: type it exactly and that result comes first |
| Ctrl+, | settings |
| Esc | back: actions → page → argument → clear the search → close |

Before you type, it shows your favourites and what you use most (frecency:
uses count for less as they get older). The search you used to find something
is remembered too, so typing it again puts that result first.

## What it finds

In the normal search: apps (from Omarchy's app library, launched with its
launch feedback), every Omarchy menu command (its `when` conditions checked),
plugin commands, script commands, quicklinks, snippets, herdr spaces,
projects, open windows, sums, world clocks, recent files, manual sections,
audio outputs, microphones, Bluetooth devices, Wi-Fi networks, themes, Azure
subscriptions and (once listed) 1Password items.

Behind a prefix (all can be changed under *Search sources*):

| Prefix | Searches |
|---|---|
| `/` | files under your folders (fd) |
| `=` | calculator; also works without `=`: `5 ft to cm`, `100 usd to eur`, `today + 3 weeks` |
| `tz` | world clock; also without: `time in tokyo`, `nyc time`, `15:00 in tokyo`, `3pm london in tokyo` |
| `cb` | clipboard history (Omarchy's): Enter pastes, Ctrl+C copies |
| `:` | emoji |
| `>` | shell command; Ctrl+O runs it and shows the output here |
| `?` | the web; a bare domain opens it |
| `ql`, `sn` | quicklinks, snippets (with *Create…*, and Edit/Delete on Tab) |
| `1p` | 1Password: Enter copies the password, Ctrl+U username, Ctrl+O one-time code |
| `ask` | Ask Claude, answering in the launcher from your Omarchy space |
| `kill` | processes, biggest first |
| `keys` | keyboard shortcuts (`hyprctl binds`) |
| `help` | the dotfiles manual's sections |
| `hs`, `pj`, `win`, `theme`, `az` | herdr spaces, projects, windows, themes, Azure |

Under every search sit *Search the web*, *Ask Claude* and *Search files*
(settings: *Under every search*).

Quicklinks take an argument after their keyword: `gh owner/repo`, `aur foo`,
`yt lofi`. Snippets fill in `{date}`, `{time}`, `{datetime}`, `{weekday}`,
`{uuid}` and `{clipboard}`, and paste into the window you came from.

### Your plugins

Shown only when the plugin is installed: Snag (download the clipboard link as
video, GIF or audio, or a link you type), Awake (`awake 2h`, 1h/4h/8h/∞, off),
Atmosphere (lights, snow, party, case and keyboard effects), Netwatch
(`block example.com`, unblock, the panel), Clock+ (today's agenda, *Join next
meeting*, refresh) and Weather+.

### 1Password

`op` lists titles, vaults and URLs only, kept in memory. Secrets are fetched
when you pick one, after the launcher has closed (1Password's authorization
dialog needs the keyboard). They are copied with `wl-copy --sensitive`, so
Omarchy's clipboard history skips them, and cleared after 30 seconds
(settings). A locked CLI shows *Unlock 1Password*; the launcher comes back
afterwards. Commands run as `setsid -w op`.

### Ask Claude

Runs `claude -p` in `~/Work/omarchy` (settings), so it knows the manual and
the rules there. It is read-only: Read, Grep, Glob and a few listing commands.
The answer streams in as Markdown, with images and HTML removed first. Ctrl+Enter
hands the question to the omarchy space in herdr instead, for things it should
*do*.

## Script commands

Executables in `~/.config/omarchy/launcher/commands/` (Tab → *Open script
commands folder*) with [Raycast's script-command header](https://github.com/raycast/script-commands),
so scripts from that collection work unchanged:

```bash
#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Public IP
# @raycast.mode compact          # silent | compact | fullOutput | inline
# @raycast.icon 🌐
# @raycast.argument1 { "type": "text", "placeholder": "Host", "optional": true }
curl -s https://ifconfig.me
```

`@launcher.` works as well, plus `@launcher.mode list` with `@launcher.prefix`:
the script becomes a search source that gets the query as `$1` and prints one
JSON object per line: `{"title", "subtitle", "icon", "accessory", "action"}`,
with action `{"open": url}`, `{"copy": text}`, `{"paste": text}` or
`{"run": [argv]}`. Examples are in `commands/`, among them `man` for manual pages.

## Settings

`Ctrl+,` or *Launcher settings*; stored in
`~/.config/omarchy/omarchy-launcher.json` (only what differs from the
defaults; editing it by hand works too).

- **Look:** position (upper third or centred), width, rows, second line on or off.
- **Search:** what shows before typing, remember the last search, the rows under every search.
- **Search sources:** each source on everywhere, only with its prefix, or off; the prefixes.
- **Web:** search engine (DuckDuckGo, Google, Kagi, Startpage, Bing, custom URL), quicklinks, snippets.
- **Files and projects:** folders, what to skip, hidden files, depth, recent files; project folders (globs) and what opens them.
- **Commands:** shell commands in a terminal or the background; live exchange rates.
- **1Password:** what Enter copies, when the clipboard is cleared, which account.
- **Claude:** model, and the folder it asks from.

Favourites, aliases and recent items: Tab on a result. *Clear launcher
history* forgets the recent list.

## Files

| Path | What |
|---|---|
| `Launcher.qml` | the overlay: search line, results, pages, keys |
| `ResultRow.qml`, `ActionsPopup.qml`, `DetailView.qml`, `FormView.qml`, `KeyHint.qml` | its parts |
| `bin/launcher` | the backend (`serve`), plus `query TEXT` to test a search in a terminal and `open [TEXT]` |
| `launcher/` | search, ranking, history, settings and one module per kind of source (`providers/`) |
| `commands/` | example script commands |
| `tests/` | `python3 -m unittest discover -s tests` |

State: `~/.local/state/omarchy-launcher/history.json`.

## Optional packages

- `libqalculate` (`qalc`) for units, currencies, dates and time zones in the
  calculator. Plain sums work without it.
- `fd` (in Omarchy) for the file search.

## Privacy and network

Nothing goes over the network unless you ask: web searches and quicklinks open
your browser; the calculator lets `qalc` refresh exchange rates once a day
(turn off under *Live exchange rates*); Ask Claude sends your question to
Claude only when you pick it.
