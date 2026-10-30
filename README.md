# Human Typer

Paste text, click into any app or website field, press **F8**, and Human Typer types it there with
human-like timing using real OS-level keystrokes (Unicode safe: æøå etc.). The app never needs focus while typing.

## Download

### Windows

Get the latest build from the [GitHub Releases page](https://github.com/glumbis/Human-Typer-App/releases):

- **`HumanTyper-Setup-x.y.z.exe`**: installer. Per-user (no admin needed), installs to
  `%LOCALAPPDATA%\Programs\Human Typer`, adds a Start Menu entry (optional desktop shortcut) and an uninstaller.
- **`HumanTyper.exe`**: portable single file. Download and double-click; nothing is installed (starts a little slower
  because it unpacks itself on launch).

The apps are not code-signed, so Windows SmartScreen may show "Windows protected your PC". Click **More info**, then
**Run anyway**.

### macOS (11 Big Sur or newer)

From the same Releases page (or the [website](https://glumbis.github.io/Human-Typer-App/), which walks you through it):

- **`HumanTyper-x.y.z-mac-arm64.dmg`**: Apple Silicon (M1/M2/M3/M4).
- **`HumanTyper-x.y.z-mac-x86_64.dmg`**: Intel Macs.

Not sure which? Apple menu → **About This Mac**: *Chip: Apple M…* = arm64, *Processor: … Intel …* = x86_64.

**A · Install**

1. Download the dmg and wait for it to finish.
2. Finder → **Downloads** → double-click the dmg. A small window opens.
3. Drag the **Human Typer** icon onto the **Applications** folder icon (click **Replace** if asked).
4. Close the window and eject "Human Typer" (⏏ in Finder's sidebar).

**B · Let macOS open it (first time only).** The app is only ad-hoc signed (no paid Apple Developer ID), so
Gatekeeper blocks the first launch.

5. Finder → **Applications** → double-click **Human Typer**.
6. macOS says *"Human Typer" could not be verified*: click **Done**. **Never click "Move to Trash".**
7. Apple menu → **System Settings** → **Privacy & Security**.
8. Scroll down to **Security**: next to *"Human Typer" was blocked…* click **Open Anyway**.
9. Enter your password / Touch ID, then **Open Anyway** / **Open**.
   (macOS 12 and older: System Preferences → Security & Privacy → General → Open Anyway.)

**C · Permissions** (without these it can't press keys for you)

10. On first launch the **SETUP** window opens (later: the **[ SETUP ]** button next to *HOTKEYS* in the title
    line). It has one row per permission, **1) Accessibility** (to press keys) and **2) Input Monitoring**
    (for fn+F8 / fn+F9), each with a live ✗ / ✔ status.
11. Press **[ GRANT ]** on a row: macOS shows its own prompt and opens the right Settings page with Human
    Typer already in the list. **Turn on the switch next to Human Typer.** Repeat for the second row.
12. When both show ✔ the hotkeys switch on immediately (title line: **HOTKEYS: ON**), the window says
    *All set!* and closes. No restart needed; if macOS insists on one, the window offers **[ RESTART HUMAN TYPER ]**.
13. (By hand instead: System Settings → Privacy & Security → Accessibility and → Input Monitoring, turn
    Human Typer on; not listed? **+** → Applications → Human Typer.)

**D · Use it**

14. Paste your text (or use **✦ AI WRITE**).
15. Click into the place to type into (Google Docs, Pages, a web form…).
16. Press **fn+F8** to start/stop and **fn+F9** to pause. Mac laptop F-keys are media keys unless you hold
    **fn**; to use plain F8/F9 enable System Settings → Keyboard → Keyboard Shortcuts… → Function Keys →
    *"Use F1, F2, etc. keys as standard function keys"*.

**Troubleshooting**

- *Nothing gets typed*: Accessibility is off: open **[ SETUP ]** (steps 10–12).
- *fn+F8 does nothing / HOTKEYS: OFF*: Input Monitoring is off: open **[ SETUP ]**. The ▶ START button in the floating stats
  window works too: clicking it briefly switches to Human Typer, then it hands the focus back to the app you
  were typing in and starts.
- *Switch is ON but nothing works (after updating from 1.4.3 or older)*: those versions looked like a new app to
  macOS after every update. In Accessibility and Input Monitoring remove Human Typer with **−**, add it again
  with **+** and turn it on. Needed once: newer versions keep the permission across updates.
- *"Damaged and can't be opened"* or no *Open Anyway* button: run this in Terminal, then open the app again:

  ```
  xattr -dr com.apple.quarantine /Applications/HumanTyper.app
  ```

- *Quits unexpectedly*: click **Report…** in the crash window and open an issue with that text.

**Google Docs / browsers.** Use the verified-clipboard locate mode (the default `auto` falls back to it): Google Docs has
no accessible text, so revisions find `first` through a Cmd+C round trip. Keep the browser tab focused while typing.

**AI writer.** The bundled llama.cpp engine uses Metal on Apple Silicon (GPU) and the CPU on Intel Macs. Models are
looked up in `~/.lmstudio/models`, `~/.cache/lm-studio/models` and `~/Library/Application Support/HumanTyper/models`.
Settings live in `~/Library/Application Support/HumanTyper`.

## Run from source

```
pip install -r requirements.txt
python main.py
```

## Build from source

Requires Python 3.10+ and [Inno Setup 6](https://jrsoftware.org/isdl.php) (for the installer). Then, in PowerShell:

```
.\build.ps1
```

This installs the requirements plus PyInstaller, builds `dist\HumanTyper.exe` (portable, onefile) and
`dist\HumanTyper\` (onedir, the installer payload), and compiles `dist\HumanTyper-Setup-<version>.exe`. The version
comes from `humantyper/__init__.py`. To sanity-check a build without opening the GUI or typing anything:
`dist\HumanTyper.exe --selftest report.txt` (writes a report and exits 0 on success).

### Build on macOS

Requires Python 3.10+ and Xcode command line tools. A Mac app can only be built on a Mac; releases are built by
GitHub Actions (`.github/workflows/macos.yml`, runs when a release is published, or manually:
`gh workflow run macos.yml -f tag=vX.Y.Z`). Locally:

```
bash build_mac.sh
```

This fetches the pinned llama.cpp engine for your CPU, builds `dist/HumanTyper.app`, ad-hoc signs it, runs
`--selftest` inside the bundle and creates `dist/HumanTyper-<version>-mac-<arch>.dmg`.

## Hotkeys

| Key | Action |
|-----|--------|
| F8  | Start typing (when idle) / stop (while typing) |
| F9  | Pause / resume |

F8 and F9 are swallowed system-wide while the app runs, so the target app never receives them. Esc is deliberately
not used. (On Mac use fn+F8 / fn+F9 on laptop keyboards.) The GUI also has Start (3-second countdown so you can click into the target), Pause/Resume and Stop buttons.
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

## AI write (local model)

The **AI WRITE** window generates text from a prompt with a model that runs **entirely on this PC** (no cloud, no
account) and sends the result to the paste box.

* **Engine**: the official [llama.cpp](https://github.com/ggml-org/llama.cpp) `llama-server` (Vulkan build, release
  `b11910`, SHA256-pinned) is bundled in the installer/exe and bound to `127.0.0.1` only. It tries the GPU first and
  falls back to CPU automatically (remembered in `%APPDATA%\HumanTyper\ai.json`). It starts on first use, stays loaded
  between generations, unloads after 10 idle minutes and always dies with the app.
* **Models** (`.gguf`): picked up automatically from LM Studio (`%USERPROFILE%\.lmstudio\models`,
  `%USERPROFILE%\.cache\lm-studio\models`) and `%LOCALAPPDATA%\HumanTyper\models`. The model manager can download
  Gemma 4 E2B, Qwen3 1.7B, Llama 3.2 1B and **Borealis 4B** (from the National Library of Norway, the best choice
  for Bokmål and Nynorsk; [NB-License](https://ai.nb.no/license/) + Gemma Terms of Use) from Hugging Face
  (resumable, size/SHA256 verified) and delete the ones it downloaded itself (your LM Studio files are never
  touched). Models are not bundled with the app.
* **Options**: length (any number of words; the slider covers 50-800), language (auto / English / Bokmål /
  Nynorsk), tone (neutral / casual / formal / student), and the number of `[draft][final]` revision pairs.
  The output is editable; **USE -> PASTE BOX** replaces the paste box, **COPY** copies it.
* **Running from source**: fetch the engine once with `.\build.ps1 -FetchEngine` (or `python tools/fetch_engine.py`).
  It is extracted to `packaging/vendor/llama/` (git-ignored). `.\build.ps1` does this automatically.

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
