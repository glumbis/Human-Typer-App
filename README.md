# Human Typer

Paste text, click into any app or website field, press **F8**, and Human Typer types it there with
human-like timing using real OS-level keystrokes (Unicode safe: æøå etc.). The app never needs focus while typing.

## Install & run (Windows)

```
pip install -r requirements.txt
python main.py
```

## Hotkeys

| Key | Action |
|-----|--------|
| F8  | Start typing (when idle) / stop (while typing) |
| F9  | Pause / resume |

F8 and F9 are swallowed system-wide while the app runs, so the target app never receives them. Esc is deliberately
not used. The GUI also has Start (3-second countdown so you can click into the target), Pause/Resume and Stop buttons.
Starting with F8 waits ~0.25 s so the key is released first.

## Revisions: `[first][revised]`

Two bracket groups with nothing between `]` and `[`. The first pass types `first` and skips `revised`. Later the typer
goes back (Left arrow N times), backspaces `first`, waits ~4 s as if thinking, types `revised` (typos possible), and
returns to the end (Right arrow N times).

```
I [realy][really] like it          ->  I really like it
Meet at [noon][3 pm]               ->  Meet at 3 pm
Hello[][, world]!                  ->  Hello, world!     (empty first: insert later)
Keep this[ - delete me][]          ->  Keep this         (empty revised: delete later)
```

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

* Autocorrect, autocomplete and auto-closing brackets/quotes (Word, Google Docs, code editors) can desync the arrow-key
  counting used by revisions. Test with a short sample first, or turn those features off.
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
