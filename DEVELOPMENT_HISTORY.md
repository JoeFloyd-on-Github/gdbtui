# gdbtui development history

This file records how the project got to its current state: requests in the order they came, the decisions behind them, the bugs found and what they taught. It is meant to stop a future developer from repeating mistakes or undoing decisions on purpose without knowing it. [CLAUDE.md](CLAUDE.md) holds the current requirements.

## Origin

The user first built a gdb TUI in a local Claude desktop session. That code wasn't available afterwards, so the project was **recreated from scratch** in a Claude Code cloud session from a written specification:

- Python 3.6, standard library only.
- A text-only container that has gdb, with no gdbserver and no mounts. The tool gets in by pasting text.
- As many Visual Studio debugger features as possible.
- **No function keys; all shortcuts Ctrl+.**

A plan was presented and approved before most of the code was written. The user had also looked at the alternatives:

- **gdb's built-in TUI:** too bare.
- **gdb-frontend:** much more complete, but it needs a forwarded network port, which the container can't provide.

This confirmed the direction: everything stays inside one terminal.

## Timeline of requests and resulting changes

### 1. The first version

- **Driving gdb:** gdb/MI with `mi-async`; curses and a `select()` loop; the program on its own pty.
- **Panels:** Source; Locals, Watch and Registers on the right; Call Stack, Threads, Breakpoints, Output, Console and Memory at the bottom.
- **Other:** the Ctrl+X command menu, a help popup, and per-program session saving in `~/.gdbtui.json`.
- **Paste installer:** zlib + base64 with a sha256 check (`make_installer.py` builds `install_gdbtui.txt`), so a bad paste is detected instead of producing a corrupt script.
- **Downloads:** the user asked for downloadable bundles, so a zip or tar.gz was produced on request. Compiler flags for the demo were documented.

### 2. Binaries without debug info, ASLR, and quitting

**User report:** breakpoints couldn't be set on a binary without debug info; gdb warned that "address space randomization cannot be disabled: Operation not permitted"; and there was no way to quit.

- **Breakpoints:** on a binary without debug info, a source-line breakpoint stays *pending* forever. gdbtui now guesses the enclosing function (`guess_function`) and offers a function breakpoint instead.
- **ASLR:**
  - Many containers forbid `personality(ADDR_NO_RANDOMIZE)`.
  - The first fix simply turned `disable-randomization` off.
  - Later this became auto-detection (`aslr_can_be_disabled()`, which calls ctypes `personality` in a forked child), with a `disable_aslr` setting to override it.
- **Quitting:** Ctrl+Q was added, and it asks for confirmation when a program is running.

### 3. Opening files, disassembly, and test variants

- **Opening files:** Ctrl+O starts in the **folder of the binary**, not the current directory.
- **Disassembly:** shown automatically when no source is available. Breakpoints can be set on assembly lines.
- **Test variants:** the demo got `build_variants.sh`, which builds debug, nodebug, stripped, `-O2`, nopie, split debug, static and other variants. The stripped variant exposed a missing error callback: `info line main` fails there, and gdbtui then has to fall back to the entry point.

### 4. Inline editing of Locals: implemented, then reverted

- **Request:** edit a value in place in Locals by moving to it and typing. Enter applies the change and Esc reverts it.
- **Outcome:** it was implemented. The user then said **"Hold on. let's keep the old behavior."** and the previous `gdbtui.py` was restored.
- **Lesson:** editing stays as `e`, which opens a prompt. Don't reintroduce inline editing unless asked. More generally, confirm before changing an established interaction.

### 5. System call correlation (strace)

**Request:** collect strace output and correlate it with the program's system calls. The user chose **both** methods:

- **Live recording through gdb:**
  - `catch syscall`; at each stop gdbtui reads the argument registers for the architecture, reads path strings and buffers, picks the first *user* frame from `-stack-list-frames`, and continues automatically.
  - Kernel restart codes such as ERESTARTSYS are shown dimmed.
- **Real strace:**
  - run, attach, or load a log, with `-f -tt -T -s 64 -k`;
  - the incremental `StraceParser`;
  - `-k` addresses mapped to source lines with one batch `gdb -batch -ex "info line *ADDR"` run. Non-PIE binaries use the ELF load base, and caller frames use `addr-1`.

**Follow-up:** "I don't see any syscalls". Recording was off by default and the empty tab gave no hint. This added the `r` (record) and `t` (strace) keys in the tab, a clearer message when the tab is empty, and the `--syscalls` / `--syscall-filter` options. **Lesson:** an empty view should say how to fill it.

### 6. Multiple open files and a realistic test program

- The user ranked **several open files** as the most important missing feature, after comparing with gdb-frontend.
- **Source tabs:** state is kept per tab, Ctrl+] cycles through them, Files menu actions handle switching and closing, and a Files tab lists the program's sources.
- **The test program was rewritten** to span several files and keep running:
  - threads passing data through a **POSIX shared memory ring**;
  - a second set of threads passing data over **127.0.0.1 TCP** (the most portable "network").
- **Requested mid-task:** sending a signal to the process from the TUI.

### 7. Threads while running, and switching files

**User reports:**

- Threads weren't shown while the program was running. **Fix:** the Threads view updates live from `=thread-*` events and /proc state, and after a pause shows where each thread is in *user* code.
- "How do I switch source files?" This led to auto-opening sources, below.

**Auto-open:** with debug info, gdbtui opens the program's own source files. External and system sources are a user choice: `--open-sources off|program|all` and the `auto_open` setting. A path counts as a system path when it is under `/usr/`, `/lib/`, `/lib64/`, `/nix/store/` or `/gnu/store/`, or is relative (`./`, `../`). Tabs are limited by an LRU limit of `max_open_files`, default 50.

### 8. "Cannot insert breakpoint 7. Cannot access memory at address 0x558931c03d45"

- **Cause:** a breakpoint set in the disassembly was stored as a raw address. Under ASLR (which can't be disabled in the user's container) the PIE binary loads somewhere else on the next run, so the address is invalid.
- **Fix:**
  - Disassembly breakpoints are stored as `*'func'+offset` whenever a symbol is known, so gdb relocates them.
  - Raw `*0x…` breakpoints are never saved in the session.
  - `cmd_error` recognises "Cannot insert breakpoint N" and opens a dialog offering delete or disable, then continue (`fix_uninsertable`).
- **Lesson:** always assume ASLR is on. Never persist absolute code addresses.

### 9. Test suite, GitHub, and project files

- The test code was organised into `tests/` with `TESTING.md`. It is deliberately kept out of the deployment package.
- The user pushed the project to GitHub (`JoeFloyd-on-Github/gdbtui`) and connected GitHub to the cloud session.
- A `.gitignore` was added for `testapp/build/` and bytecode, through PR #1, which was merged.
- `CLAUDE.md` and this file were added afterwards.
- **Working setup:** development happens in Claude Code **cloud** sessions working on the GitHub repository. A Windows machine with Remote Control was tried, but its folder couldn't be served to cloud sessions, so the repository on GitHub is the shared source of truth.

### 10. Clickable shortcuts and tabs

- **Request:** click the debugging shortcuts to run them, and click panel tab labels to switch tabs.
- Tab clicks already worked with `--mouse`, which is off by default. The new part is the shortcut bar on the bottom line: each `^N Over`-style item records its screen range (`hint_hits`), and a click calls the same `act_*` method as the key.
- **Messages hid the shortcuts.** Every step or stop shows a message on the bottom line for 8 seconds, which covered the bar, so a second click did nothing. Messages are now drawn to the right of the shortcuts when they fit. A message that is too long still covers the line, and clicking it clears it.
- **Double clicks.** curses merges two fast clicks into one `BUTTON1_DOUBLE_CLICKED` event, which would lose the second step. Panels need double clicks, so the click timing (`mouseinterval`) stays. For Continue and the three Steps, a double click runs the action again once `stop_gen` shows the program stopped (`click_repeat`). A key press cancels it, and so does waiting 5 seconds.
- **Testing mouse input:** tmux passes raw bytes with `send-keys -H`. The tests send xterm X10 sequences (`ESC [ M` with button and 1-based coordinates + 32) for press and release.

### 11. Shortcuts at the top, status bar at the bottom

- **Request:** put the shortcuts at the top of the screen and the status bar on its own line at the bottom, so status updates never cover the shortcuts.
- The two bars swapped rows; the panels still use the rows in between, so the panel layout didn't change.
  - **Top line (`draw_hints`):** only the shortcuts. Clicks on row 0 run them.
  - **Bottom line (`draw_status`):** the program and its state, then the latest message, then flags and the current thread and frame. A message takes priority over the frame part when both don't fit. Clicking the line clears the message.
- This replaces step 10's arrangement, where messages shared the shortcut line and sat to its right.
- **Tests:** `Tui.status()` now reads the last screen line, and the shortcut clicks target row 0.

## gdb quirks and technical lessons

**Signals**

- gdb numbers signals its own way: `signal 10` is **SIGBUS** in gdb, not SIGUSR1. Always pass signal **names**.
- The CLI `signal` command blocked further MI commands. Use `queue-signal NAME` followed by `-exec-continue`.
- Show the exception popup only for crash signals (SEGV, BUS, FPE, ILL, ABRT, SYS), not for every signal stop.

**Breakpoints**

- `dprintf` breakpoints produce `script={"..."}`, which isn't valid MI. The tuple parser must accept bare values, or the whole Breakpoints list, and the session autosave, breaks. This once saved an empty breakpoint list.
- Function names containing `(`, `:` or spaces must be quoted inside `*'func'+off`.

**Stepping**

- Stepping while system calls are recorded: if a line's last call made a syscall, the catchpoint stops inside it. The resume logic uses `-stack-info-depth`, then `-exec-finish --thread --frame`, and re-issues the step. **After the finish, if the line has already changed, the step is complete.** Without that check Step Over skipped the next line. The end-to-end tests found this.

**Recording control**

- Stopping recording while the program runs: removing the catchpoint needs an interrupt, which raced with the program. The catchpoint is now removed lazily (`sys_stale_catch`).

**Parsing strace**

- strace `-k` frames contain C++ names with parentheses, so the regular expressions have to allow them.
- When joining `<unfinished ...>` and `<... resumed>` parts, watch for doubled commas.
- Don't link the last parsed record before its stack lines have arrived.

**Mapping addresses to source**

- A return address maps to the *next* line, so use `addr-1` for caller frames.
- Frames with neither a library nor a file belong to the program itself, not libc.

**Files and paths**

- Core files whose paths need quoting failed through MI. Use the CLI `core-file` command.

**Display**

- Tab characters were shown as `?` in curses. Expand them to spaces.
- After the program exits, clear the disassembly so stale code isn't shown.
- Popups need a width that fits their titles.
- `std::map` children come in pairs, so show them as `[key] = value`. Flatten varobjs' `public`/`private`/`protected` levels.
- When a prompt is prefilled, the first key typed should *replace* the text (the `fresh` flag in `LineEdit`).
- Menu labels must not collide under fuzzy matching. "call stack" used to match "frame up".

**Testing technique**

- In tmux tests, wait for a regular expression on screen instead of sleeping.
- Check regular expressions against real screen captures. Spacing mistakes, such as two spaces in the status bar, and lists scrolling past rows caused false failures.
- Run every end-to-end test with a temporary HOME, so that saved sessions don't leak between tests.

## Known gaps and ideas not yet done

- **Untested environments:** Python 3.6 itself, gdb older than 15.1, non-x86-64 system-call tables, real terminal emulators over `docker exec`/`kubectl exec`, and automated tests for attaching and core dumps.
- **Candidates proposed but not chosen yet.** When gdbtui was compared with gdb-frontend, multiple files as tabs was picked first. The others offered were:
  - reverse debugging (gdb `record`, step backwards);
  - a Visual Studio-style **Autos** window;
  - visualising data structures (follow pointers through lists and trees, arrays as tables);
  - inline values next to the current line while stepping.

  Ask the user before starting any of them.
