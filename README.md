# Human Typer

Paste text, click into any app or website field, press **F8**, and Human Typer types it there with
human-like timing using real OS-level keystrokes (Unicode safe: æøå etc.). The app never needs focus while typing.

## Download (Windows)

Get the latest build from the [GitHub Releases page](https://github.com/glumbis/Human-Typer-App/releases):

- **`HumanTyper-Setup-x.y.z.exe`**: installer. Per-user (no admin needed), installs to
  `%LOCALAPPDATA%\Programs\Human Typer`, adds a Start Menu entry (optional desktop shortcut) and an uninstaller.
- **`HumanTyper.exe`**: portable single file. Download and double-click; nothing is installed (starts a little slower
  because it unpacks itself on launch).

The apps are not code-signed, so Windows SmartScreen may show "Windows protected your PC". Click **More info**, then
**Run anyway**.

## Run from source

```
pip install -r requirements.txt
python main.py
```

## Build from source

Requires Python 3.10+ and [Inno Setup 6](https://jrsoftware.org/isdl.php) (for the installer). Then, in PowerShell:

```
.uild.ps1
```

This installs the requirements plus PyInstaller, builds `dist\HumanTyper.exe` (portable, onefile) and
`dist\HumanTyper\` (onedir, the installer payload), and compiles `dist\HumanTyper-Setup-<version>.exe`. The version
comes from `humantyper/__init__.py`. To sanity-check a build without opening the GUI or typing anything:
`dist\HumanTyper.exe --selftest report.txt` (writes a report and exits 0 on success).

## Hotkeys

| Key | Action |
|-----|--------|
| F8  | Start typing (when idle) / stop (while typing) |
| F9  | Pause / resume |

F8 and F9 are swallowed system-wide while the app runs, so the target app never receives them. Esc is deliberately
not used. The GUI also has Start (3-second countdown so you can click into the target), Pause/Resume and Stop buttons.
Starting with F8 waits ~0.25 s so the key is released first.

### Stats overlay

A small always-on-top window (draggable by its title strip, remembers its position) shows state, progress, live
WPM, elapsed/ETA, typed chars, typos fixed and revisions, with START / PAUSE / STOP buttons, `[⌂]` (bring the main
window to front) and `[x]` (hide). It never takes keyboard focus, so its START begins after ~0.3 s with no
countdown. Turn it off or on with command `8) OVERLAY` in the main window (saved in settings).

## Revisions: `[first][revised]`

Two bracket groups with nothing between `]` and `[`. The first pass types `first` and skips `revised`. Later the typer
goes back to just after `first`, backspaces it, waits ~4 s as if thinking, types `revised` (typos possible), and returns
to where it left off.

```
I [realy][really] like it          ->  I really like it
Meet at [noon][3 pm]               ->  Meet at 3 pm
Hello[][, world]!                  ->  Hello, world!     (empty first: insert later)
Keep this[ - delete me][]          ->  Keep this         (empty revised: delete later)
```

### How the typer finds `first` again

Counting arrow presses breaks when the target app autocorrects, autocompletes, auto-closes brackets or converts quotes,
so the location is verified in layers (setting **Locate mode**, default `auto`):

1. **UI Automation (exact)**: reads the focused control's text and caret through Windows UI Automation and places the
   caret directly after `first` (no key counting). Works in Notepad, Word, most native/WPF/Chromium text fields.
2. **Clipboard verified**: no round trip to the start of the document. From the caret (end of the typed text) it
   extends a selection backwards with Ctrl+Shift+Left word jumps, only as far as `first` should be plus a small margin,
   and copies it. It finds `first` in that text (closest to where it should be; surrounding text breaks ties between
   duplicates). If the text drifted beyond the margin it keeps extending the same selection (and finally Ctrl+Shift+Home)
   and copies again. Then one Left collapses the selection to its start and a short run of Right presses lands just
   after `first`. On the first revision of a run (and whenever `first` occurs twice in the copied text) it also selects
   `first` once more and copies it to confirm the caret really is there; nothing is deleted unless that check passes.
   **Use this for Google Docs** (canvas, no UI Automation text). Your clipboard is saved first and restored afterwards
   (all memory-backed formats such as text, HTML, files and images; only GDI-handle formats are not kept).
3. **Counting** (last resort in `auto`, or mode `count`): the old Left-N-times method; unverified. It is logged as
   `count (unverified)` when used.

Mode `clipboard` never falls back to counting: if the location cannot be verified the revision is skipped (the original
`first` text stays). Empty `first` (`[][text]`) is located by the 16 characters typed before it.
After the new text is typed the caret goes back to where typing left off (UI Automation: set directly; clipboard: Right
arrow N times over the unchanged text, tapped quickly at about 25-55 ms per key).

Rules: a lone `[x]` (not directly followed by `[...]`) is typed literally with brackets. `\[` and `\]` give literal
brackets (also inside groups). **No nesting**: the first unescaped `]` closes a group. Newlines may appear inside groups
(each counts as one key press). The final text always equals the input with each `[a][b]` replaced by `b`.

## Settings

* **Speed** 10-200 WPM (per-session variation of about 8 %, common words and bigrams faster, capitals/symbols/digits slower,
  pauses after spaces, punctuation and newlines, rare "thinking" pauses, capped fatigue).
* **Typo rate** 0-10 % (0 = off). Neighbour-key (QWERTY incl. æøå) and swapped-letter typos, noticed after 0-3 chars, then
  fixed with backspaces. No typos on newlines.
* **Revise when**: "Random, while typing" (each revision fires 60-400 chars after its `first`, at a space/punctuation) or
  "All at the end".
* **Always on top**. Settings and the last text are saved to `%APPDATA%\HumanTyper\settings.json`.

Newline presses Enter; a Tab character presses Tab.

## Caveats

* Autocorrect, autocomplete and auto-closing brackets/quotes are handled by the verified locators above, but not by
  `count` mode. If the same phrase also appears nearby and autocorrect changed the text right before it, the wrong copy
  could in theory be chosen; use distinctive `first` text. Test with a short sample first.
* Tab moves focus in browsers; avoid tabs in text for web forms. Enter may submit single-line fields or send chat messages.
* Keep the target field focused and do not type or click while it runs.
* Some elevated (administrator) windows ignore keystrokes from a non-elevated process; run Human Typer as admin for those.

## Tests

```
pip install pytest
python -m pytest
```

## Optional: build an .exe

```
pip install pyinstaller
pyinstaller --noconsole --onefile --collect-all customtkinter --name HumanTyper main.py
```
The exe appears in `dist/`.
