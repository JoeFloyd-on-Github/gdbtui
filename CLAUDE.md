# CLAUDE.md: gdbtui

Read this file before changing anything. For why things are the way they are (past bugs, rejected ideas, gdb quirks), read [DEVELOPMENT_HISTORY.md](DEVELOPMENT_HISTORY.md) too, especially before touching breakpoints, signals, stepping or system-call recording.

## What this project is

`gdbtui.py` is a single-file, Visual Studio–style debugger front end for GDB that runs in a text terminal. It exists for one situation:

- The program to debug runs **inside a container that is text-only** (`docker exec`, `kubectl exec` and similar).
- The container has **gdb**, but **gdbserver cannot be used** and **no network port can be forwarded**. Web front ends such as gdb-frontend are therefore ruled out.
- **No mounts or file sharing** are possible. The only way in is to **paste text into the terminal**, so the tool must be a single file that is recreated by pasting `install_gdbtui.txt`.
- gdb's built-in TUI (`gdb -tui`) is too bare. The goal is to offer **as many Visual Studio debugger features as possible**.

## Hard requirements (do not break these)

1. **Python 3.6 compatible.** No walrus operator, no f-string `=` specifier, no positional-only parameters, no `subprocess.run(capture_output=...)`, no `text=` argument, no `dataclasses`, no `str.isascii()`, no `dict | dict`, and so on. `tests/check_static.sh` enforces this with `ast.parse(feature_version=(3, 6))` and `vermin -t=3.6-`.
2. **Standard library only.** Nothing from pip, ever. Only `curses`, `select`, `subprocess`, `json`, `ctypes` and the like.
3. **One deployable file.** Everything the user needs is in `gdbtui.py`. Keep it as one file and don't split it into modules.
4. **The paste installer must match.** After **any** change to `gdbtui.py`, run `python3 make_installer.py` to regenerate `install_gdbtui.txt` (zlib + base64 with a sha256 check). The static checks and a unit test fail if the two are out of sync. Commit both files together.
5. **No function keys.** The host terminal already uses them. **Every global shortcut is a Ctrl+ key.** Single letters are fine only inside a focused panel, such as `d` to delete in Breakpoints.
6. **Ctrl keys that must stay unused:**
   - Ctrl+S: XON/XOFF can freeze the terminal.
   - Ctrl+P: the first half of Docker's detach sequence.
   - Ctrl+V: paste in Windows Terminal.
   - Ctrl+H, Ctrl+I, Ctrl+M and Ctrl+[ are indistinguishable from Backspace, Tab, Enter and Esc.

   Ctrl+B clashes with tmux, so keys are rebindable through `"keys"` in `~/.gdbtui.json`.
7. **Must work in restricted containers:**
   - ASLR may not be disableable (`personality()` is denied), so detect this and don't fail.
   - ptrace may be limited.
   - There may be no `/proc` niceties, and strace may be missing.
   - The program may have no debug info or be stripped.

   Degrade gracefully, and always tell the user what happened and what to do.
8. **Don't commit test or development material into the deployment path.** `testapp/` and `tests/` are development only. The installer carries only `gdbtui.py`.

## Shortcut map (keep in sync with the `ACTIONS` list, the help text, the module docstring and README)

| Key | Action | | Key | Action |
|---|---|---|---|---|
| Ctrl+R | Run / Continue | | Ctrl+W | Add Watch |
| Ctrl+N | Step Over | | Ctrl+D | Source / Disassembly (at cursor line) |
| Ctrl+T | Step Into | | Ctrl+A | Show Next Statement |
| Ctrl+U | Step Out | | Ctrl+O | Open file (starts in the binary's folder) |
| Ctrl+Y | Run To Cursor | | Ctrl+F | Find (Enter repeats) |
| Ctrl+\ | Set Next Statement | | Ctrl+G | Go to line / function / `file:line` / `*addr` |
| Ctrl+C | Break All | | Ctrl+] | Next tab in focused panel (next file in Source) |
| Ctrl+K | Stop (kill, or detach if attached) | | Ctrl+Z | Zoom panel |
| Ctrl+B | Toggle breakpoint (source or disassembly) | | Ctrl+X | Searchable command menu (every command) |
| Ctrl+E | QuickWatch | | Ctrl+_ (Ctrl+/) | Help |
| Ctrl+L | Redraw | | Ctrl+Q | Quit (confirms if a program is live) |

Tab and Shift+Tab move focus between the Source, right and bottom panels. Every feature must be reachable from the Ctrl+X menu, even if it also has a key.

## Required feature set (all implemented; keep them working)

- **Execution:**
  - run, continue, step over/into/out, run to cursor, set next statement, break all, stop, restart;
  - program arguments, environment and working directory;
  - attach (`-p PID`) and core dumps (`-c core`);
  - the program's stdin/stdout on its own pty, shown in the Output tab, where typing sends to stdin.
- **Breakpoints:**
  - line, function, conditional, hit count, logpoints (dprintf), data watchpoints, catch throw/catch, signal handling;
  - enable/disable/delete, save/load.
  - **On binaries without debug info:** function breakpoints, guessing the enclosing function for a source line.
  - **In disassembly,** stored as `*'func'+off` so they survive PIE relocation and ASLR.
  - **Recovery dialog** for "Cannot insert breakpoint N" (offers delete or disable, then continue).
- **Source and disassembly:**
  - multiple open files as **tabs** (Ctrl+], Files tab, Ctrl+O popup, LRU limit `max_open_files`, default 50);
  - **auto-open** the program's own source files from debug info (`--open-sources off|program|all`, setting `auto_open`; `all` includes external and system sources);
  - automatic disassembly when there is no source;
  - mixed source and assembly; syntax highlighting; find and go to; source path substitution.
- **Data:**
  - Locals and Watch as expandable trees (gdb varobjs). STL containers are readable: `std::map` shown as `[key] = value`, access-specifier levels flattened, char arrays shown as strings.
  - QuickWatch; Registers (changed values in red, editable); Memory (hex view, changes in red); Call Stack (switch frame); Threads (**updated live while running**, showing each thread's location in user code after a pause); Modules; Breakpoints.
  - Editing a value is done with `e`, which opens a prompt. **Not inline typing**: the user tried it and asked to keep the prompt (see the history).
- **Signals:** send any signal to the running program from the menu (`queue-signal NAME` + `-exec-continue`, never the CLI `signal`).
- **System calls (strace correlation):**
  - live recording through gdb `catch syscall`, with arguments decoded per architecture and paths and buffers read;
  - real `strace` runs, strace attach, and loading a strace log;
  - `-k` stacks mapped to source lines;
  - a Syscalls tab with filter, summary, details, break on call, and save.
- **Sessions:** breakpoints, watches, open files and the active file are saved per program in `~/.gdbtui.json` and restored automatically. Raw `*0x…` breakpoints are never saved.
- **UI:**
  - panels can be resized and zoomed; mouse support is optional;
  - a GDB Console tab (CLI commands; lines starting with `-` are raw MI);
  - a help popup; settings stored in `~/.gdbtui.json` (`tabsize`, `syntax`, `mouse`, `bottom_ratio`, `right_ratio`, `keys`, `disable_aslr`, `auto_open`, `max_open_files`, `strace_options`, `sessions`).

When adding a feature, prefer one that a Visual Studio user would expect. Map it to an unused Ctrl key only if it is used constantly. Otherwise put it in the Ctrl+X menu and, where it fits, give it a letter key inside the relevant panel.

## Architecture of `gdbtui.py` (≈6500 lines)

From top to bottom:

1. **Constants and config:**
   - the module docstring, which holds the key summary;
   - `CONFIG_PATH = ~/.gdbtui.json`;
   - the `ACTIONS` table (action name, default key, label), which is the source of truth for keys.
2. **gdb/MI layer:**
   - **`parse_mi_line`, `_parse_cstring`:** tolerant parsing, including gdb's non-standard bare values such as dprintf's `script={"..."}`.
   - **`GdbProcess`:** runs `gdb --interpreter=mi2`, sends token-numbered commands, and calls each command's callback and error callback.
   - CLI commands go through `-interpreter-exec console`, with output captured per token.
3. **Models and helpers:** `SourceFile` (with highlighting), `VarNode`, `TextBuffer`, `LineEdit` (history; the `fresh` flag means typing replaces a prefilled value).
4. **Views:**
   - `View` → `ListView` → `VarTreeView`, `RegistersView`, `StackView`, `ThreadsView`, `FilesView`, `BreakpointsView`, `ModulesView`, `SyscallsView`;
   - also `MemoryView`, the console views (`GdbConsoleView`, `OutputView`) and `SourceView` (tabs, cursor state per tab, disassembly mode).
5. **Popups:** `InputPopup`, `ListPopup` (ranks prefix matches first), `FilePopup`, `TextPopup`, `QuickWatchPopup`.
6. **System calls:**
   - `StraceParser`, an incremental parser that handles `<unfinished>`/`<resumed>` and `-k` lines;
   - the `SYSCALL_ARGS` and register tables;
   - `SyscallMixin`, which drives live recording and strace.
7. **`App(SyscallMixin)`:**
   - the main loop: `select()` over stdin, gdb's stdout and the inferior's pty master; curses in raw mode with `get_wch`;
   - MI async record handling (`*stopped`, `*running`, `=thread-*`, `=breakpoint-*`);
   - action handlers, menus and the session save/restore.
8. **`main()`:**
   - argparse options: `-p -c -g -x -ex -d --cd --nx --mouse --tabsize --open-sources --syscalls --syscall-filter --no-tty --no-session --log --args`.

**Rules for working in it:**

- **Async mode:** use `mi-async on`, falling back to `target-async`. Never send a command that blocks the MI channel while the program runs.
- **Communication:** all gdb communication is asynchronous and uses callbacks. Don't add blocking reads.
- **Debug log:** `--log FILE` records the full MI conversation, which is the first thing to read when something misbehaves.

## Repository layout

| Path | Purpose |
|---|---|
| `gdbtui.py` | The product. |
| `install_gdbtui.txt` | Generated paste installer. **Never edit it by hand.** |
| `make_installer.py` | Regenerates the installer. |
| `README.md` | User documentation. Update it when keys, options or features change. |
| `testapp/src/` | Multi-file C++ demo: `main.cpp`, `shapes.*`, `mathutil.*`, `shm_channel.*` (POSIX shm ring between threads), `net_channel.*` (127.0.0.1 TCP client/server threads). It keeps running and handles SIGUSR1 (statistics), SIGHUP (reset) and SIGINT/SIGTERM (clean stop). Options: `--seconds N`, `--quick`, `read`, `crash`. |
| `testapp/build_variants.sh` | Builds 11 variants into `testapp/build/` (git-ignored): debug, `-g3`, `-O2`, nodebug, nodebug `-O2`, nopie, movedsrc, stripped, debug-stripped, splitdebug, static. Compiled with `g++ src/*.cpp -Isrc -pthread -lrt`. |
| `tests/` | The test suite (see below). `tests/TESTING.md` explains it in detail. |
| `DEVELOPMENT_HISTORY.md` | Decisions, bugs and lessons. |

## Testing requirements

Run the tests before every commit that touches `gdbtui.py`:

```bash
sh tests/run_all.sh          # everything (~1–2 min): static + unit + end-to-end
```

```bash
sh tests/run_all.sh quick    # static checks + unit tests only (seconds; no gdb/tmux)
```

- **Static checks (`tests/check_static.sh`):** `py_compile`, the 3.6 grammar check, `pyflakes`, `vermin -t=3.6-` and the installer round trip. pyflakes and vermin are optional (`pip install pyflakes vermin`), but install them when possible; pyflakes has caught real bugs.
- **Unit tests (`tests/test_units.py`):** the MI parser, the strace parser, system-call formatting and register tables, helpers, ELF PIE/load-base detection (uses the built binaries) and the installer checksum.
- **End-to-end tests (`tests/test_tui.py`):**
  - Each test drives the real gdbtui, gdb and test app inside a detached **tmux** session sized 170×45, with a temporary `HOME`.
  - Keys are sent with `tmux send-keys`, and the screen is read with `capture-pane`.
  - **Wait for a regular expression on screen; never sleep for a fixed time.**
  - To find lines, search for source text, not line numbers.
  - `GDBTUI_TEST_KEEP=1` leaves the tmux session open for inspection.
- **Coverage of changes:**
  - Every bug fix gets a test that would have caught it, where feasible.
  - Every new user-visible feature gets at least one end-to-end test.
  - Parsers and formatters get unit tests.
- **Verify by hand as well:** start gdbtui in tmux, send keys, read the screen. Automated tests are the manual checks written down.
- **Environment:** this was developed on gdb 15.1, Ubuntu 24.04 and x86-64. Python 3.6, older gdb versions and other architectures are untested (see `tests/TESTING.md`). Don't use gdb features newer than about gdb 8 without a fallback. Existing fallbacks cover a missing `-data-disassemble -a`, `queue-signal`, `-file-list-shared-libraries` and `starti`.

## Workflow conventions

- Work on a branch and open a pull request to `main` on GitHub (`JoeFloyd-on-Github/gdbtui`). Don't push to `main` directly.
- In a commit that changes `gdbtui.py`, also include the regenerated `install_gdbtui.txt`, plus the README updates if the change is user-visible.
- Keep README's key table, the `ACTIONS` list, the help popup and the module docstring consistent.
- Messages shown to the user should say **what happened and what to do next**. Restricted-container failures are the normal case, not an edge case.
- Ask before changing an established interaction. The user values the current Visual Studio–like behaviour and has reverted a change before.
