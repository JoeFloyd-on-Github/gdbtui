# How gdbtui is tested

This folder, together with `testapp/`, is development material only. None of it is needed inside the container you debug in. To deploy, paste `install_gdbtui.txt`, which carries only `gdbtui.py`.

## Running the tests

```bash
sh tests/run_all.sh
```

That runs everything: static checks, unit tests, then the end-to-end tests. It builds the test program first if needed, and takes about 1–2 minutes.

```bash
sh tests/run_all.sh quick
```

That runs only the static checks and unit tests. They need no gdb or tmux and take a few seconds.

To run one end-to-end test and leave its tmux session open so you can look at it:

```bash
GDBTUI_TEST_KEEP=1 python3 tests/test_tui.py TuiTests.test_file_tabs_and_files_tab
```

Then attach to the session with `tmux attach -t gdbtui-test-…`; the test output prints the exact name.

**Requirements:**
- Python 3.8 or newer to run the checks (gdbtui itself only needs 3.6).
- gdb, tmux, and a C++ compiler.
- strace, for one test; it is skipped if strace is missing.
- Optionally `pip install pyflakes vermin`, for two of the static checks; they are skipped if missing.

## The three layers

### 1. Static checks: `check_static.sh`

| Check | Why |
|---|---|
| `py_compile` | The file is valid Python |
| `ast.parse(..., feature_version=(3, 6))` | No syntax newer than Python 3.6, such as the walrus operator or positional-only parameters |
| `vermin -t=3.6-` | No standard-library functions newer than 3.6. For example, it would catch `subprocess.run(capture_output=...)` or `str.isascii()` |
| `pyflakes` | Undefined or unused names; this caught a real bug during development |
| Installer round trip | Pastes `install_gdbtui.txt` into a fresh shell and checks the result is byte-for-byte `gdbtui.py`, so a stale installer is caught |

### 2. Unit tests: `test_units.py` (32 tests, standard-library `unittest`)

These cover the parts that need neither gdb nor a terminal:

- **gdb/MI output parser:** results, nested tuples and lists, escaped strings, UTF-8 sent as octal escapes, malformed lines, and gdb's non-standard `script={"..."}` (a dprintf breakpoint once broke the Breakpoints list).
- **strace log parser:** threads (`-f`), timestamps, calls split into `<unfinished ...>` and `<... resumed>` parts, and `-k` stack lines with C++ names containing parentheses. The log is also fed in awkward chunks to test incremental reading.
- **System call display:** open flags, escaping of data buffers, errno names, kernel "restart" codes, and which registers hold the arguments on each architecture.
- **Helpers:** Ctrl-key names, C/C++ syntax highlighting (including block comments), guessing the enclosing function for a source line, recognising system paths, the line editor (history, replace-on-type), and output buffering.
- **ELF headers,** using the built test binaries: PIE versus non-PIE detection and the load base used to map strace addresses.
- **The installer** contains the checksum of the current `gdbtui.py`.

### 3. End-to-end tests: `test_tui.py` (20 tests)

These run the real `gdbtui.py`, the real gdb and the test program, the same way a person uses it:

1. Start gdbtui inside a detached **tmux** session of fixed size (170×45), with a **temporary HOME** so saved sessions and settings start clean.
2. Send keys with `tmux send-keys` (`C-r`, `C-x`, `Enter` …) or type text.
3. Read the screen as plain text with `tmux capture-pane -p`, and wait (with a timeout) until it matches a regular expression. Waiting for the screen instead of sleeping for fixed times is what keeps these tests stable.
4. Quit with Ctrl+Q and remove the session.

| Test | What it proves |
|---|---|
| `test_start_and_step` | Ctrl+N starts the program, stops at `main`, then steps forward |
| `test_function_breakpoint_locals_stack_step_out` | Function breakpoint, Locals value (`n = 5`), Call Stack with arguments, Step Out |
| `test_locals_tree_expands_stl_and_classes` | Run To Cursor; expanding a `std::map` shows `["alice"] = 30` |
| `test_file_tabs_and_files_tab` | Source files open as tabs; the Files tab opens a file; Ctrl+] switches files |
| `test_session_restores_breakpoints_and_tabs` | Breakpoints come back in the next session |
| `test_threads_live_and_after_break` | Threads are listed while running; after Break All each shows where it is in the user's code |
| `test_send_signal_to_running_program` | SIGUSR1 reaches the running program (it prints statistics) and the program keeps running |
| `test_program_input_via_output_tab` | Typing in the Output tab reaches the program's stdin |
| `test_crash_shows_exception_popup` | A segfault shows the exception popup |
| `test_live_syscall_recording_links_source` | `write` and `sendto` are recorded and linked to `main.cpp` / `net_channel.cpp` |
| `test_step_over_while_recording_lands_on_next_line` | Step Over still lands on the next line while system calls are recorded; this test caught a real bug |
| `test_strace_run_links_source_with_k` | A real strace run, with `-k` stacks mapped to source lines |
| `test_nodebug_opens_disassembly` | A binary without `-g` opens on the disassembly of `main` |
| `test_stripped_stops_at_entry_point` | A stripped binary stops at its entry point |
| `test_assembly_breakpoint_before_run_hits_after_relocation` | A breakpoint on an instruction, set before the program runs, still hits after the program is loaded at a random address |
| `test_disasm_breakpoint_survives_restart_with_aslr` | With address randomization forced on, a disassembly breakpoint still hits after a restart |
| `test_stale_address_breakpoint_recovery` | A breakpoint on a raw address from an earlier run gives the "Breakpoint Problem" dialog, and Delete-and-continue works |
| `test_mouse_clicks_shortcuts_and_tabs` | With `--mouse`, clicking `^N Over` on the bottom line steps, a double click steps twice, clicking the Threads tab label switches to it, and clicking Cont then Break runs and pauses the program. The clicks are xterm X10 mouse sequences sent with `tmux send-keys -H` |
| `test_help_and_menu_open` | Help (Ctrl+_) and the menu (Ctrl+X) open |
| `test_quit_cleans_up_processes` | Ctrl+Q asks first, and the debugged program does not survive |

Tests find source lines by searching for their text (for example `if (sig == SIGUSR1)`), so editing the test program doesn't break them.

## The test program: `testapp/`

`build_variants.sh` builds the C++ demo (in `testapp/src/`) in 11 forms. The end-to-end tests use these:

- `demo_debug`: full debug information.
- `demo_nodebug`: no debug information.
- `demo_stripped`: no symbols at all.
- `demo_nopie`: fixed load address, used by the ELF unit test.

The others (optimized, `-g3`, separate debug file, moved sources, static) were checked by hand.

## How this was developed

Every feature was first tried by hand, with the same tmux technique the end-to-end tests use: start gdbtui in tmux, send keys, read the screen. When something looked wrong, the gdb/MI conversation was recorded (`gdbtui.py --log FILE`) and read, or gdb was run directly with MI commands piped in. The automated tests are those manual checks written down, so they can be repeated after every change.

Bugs found this way include:
- **Parsing:** the dprintf `script={...}` record, strace stack lines with parentheses in C++ names, and a doubled comma when joining `<unfinished>`/`<resumed>` calls.
- **Signals:** gdb's own numbering, where "signal 10" is SIGBUS instead of SIGUSR1; and gdb's `signal` command blocking further requests.
- **Breakpoints:** stale raw-address breakpoints under address randomization.
- **Stepping:** Step Over skipping a line when the line's last call made a system call.

## What has *not* been tested

- **Python 3.6 itself.** No 3.6 interpreter could be installed in the development environment. Compatibility rests on the grammar check and vermin's analysis, and the code avoids newer features deliberately, but it hasn't actually run on 3.6. Running the quick checks inside the target container closes this gap:
  ```bash
  python3 tests/test_units.py
  ```
- **Other gdb versions.** Only gdb 15.1 (Ubuntu 24.04) was used. Fallbacks exist for older versions, for example where `-data-disassemble -a`, `queue-signal`, `-file-list-shared-libraries` or `starti` are missing, but they haven't been exercised.
- **Other architectures.** Only x86-64 was tested. The system-call register tables for i386, AArch64 and ARM are untested.
- **Real terminals.** tmux stands in for the terminal. Colours, mouse support and key handling in specific terminal emulators, and over `docker exec` and `kubectl exec`, were not tested automatically. Mouse support is only tested by injecting raw xterm X10 mouse sequences, not with a real mouse or the SGR (1006) encoding some terminals use.
- **Attaching to a process and core dumps.** Both were checked by hand during development, but have no automated test.
