# gdbtui

A Visual Studio style debugger UI for GDB that runs in a plain text terminal.

- **Single file:** `gdbtui.py`. Needs Python 3.6 or newer and only the standard library.
- **No gdbserver:** it starts `gdb` as a local child process and talks to it through gdb's machine interface (MI).
- **No function keys:** every global shortcut is a **Ctrl+** key.

## Getting it into a container (copy and paste)

1. Open `install_gdbtui.txt` and copy **all** of it. It's about 700 lines of compressed, base64-encoded text.
2. In the container's shell, `cd` to a writable directory such as `/tmp` and paste.
3. The pasted text writes `gdbtui.py` and checks a SHA-256 checksum. If the paste was damaged, it says so: paste it again.

If the container's Python is called something other than `python3`, change the first line of the pasted text, for example to `python3.6 - <<'GDBTUI_EOF'`.

After changing `gdbtui.py`, run this to rebuild `install_gdbtui.txt`:

```bash
python3 make_installer.py
```

## Running

```bash
python3 gdbtui.py ./myprog arg1 arg2
```
```bash
python3 gdbtui.py --args ./myprog -v "two words"
```
```bash
python3 gdbtui.py -p 1234
```
```bash
python3 gdbtui.py -c core.1234 ./myprog
```
```bash
python3 gdbtui.py --help
```

The first starts a program with arguments, the second uses gdb-style `--args`, the third attaches to a running process, and the fourth opens a core dump.

Useful options:

| Option | What it does |
|---|---|
| `--gdb PATH` | Use a different gdb |
| `-x FILE` | Source a gdb script at startup |
| `-ex CMD` | Run a gdb command at startup |
| `-d DIR` | Add a source directory |
| `--nx` | Don't read `~/.gdbinit` |
| `--mouse` | Turn on mouse support |
| `--open-sources off\|program\|all` | Which source files open as tabs when the program loads (see below) |
| `--syscalls` | Record system calls from the start (Syscalls tab) |
| `--syscall-filter "CALLS"` | Record only these system calls, such as `"openat write"` |
| `--no-session` | Don't save or restore breakpoints and watches |
| `--log FILE` | Record the raw MI traffic, for troubleshooting |

Attaching needs ptrace permission. In Docker that usually means the container was started with `--cap-add=SYS_PTRACE`, or that you're root in the container with a permissive ptrace setting.

## Binaries without debug information

Source-line breakpoints, Locals and source-level stepping all need a binary built with `-g`. Without it, gdb has no way to map a source line to an address.

When gdbtui loads a binary without debug information, it shows a warning and opens the source panel on the **disassembly of `main`**. If you toggle a breakpoint on a source line, it offers to break on the enclosing function instead.

These still work without debug information:

- **Breakpoints on instructions:** select an instruction and press Ctrl+B. Before the program starts, gdbtui records the breakpoint as `function+offset`, such as `*'main'+24`. The breakpoint then still hits in position-independent (PIE) programs, which load at a random address.
- **Browse Functions** (Ctrl+X): lists every function in the binary. Enter shows a function's disassembly, so you can set breakpoints in it.
- **Function breakpoints** (Ctrl+X, then "New Function Breakpoint"), and Ctrl+G with a function name.
- **Registers, Memory, Call Stack and Threads.**
- **Scrolling the disassembly:** moving past either end loads more instructions.

A **stripped** binary has no symbols at all, not even `main`. gdbtui then shows the disassembly at the program's entry point. Ctrl+N or Ctrl+T starts the program and stops at that entry point, after the dynamic loader has placed it in memory.

Containers usually don't allow gdb to turn off address space randomization (ASLR), which caused the warning "Error disabling address space randomization: Operation not permitted". gdbtui leaves ASLR on to avoid it. To have gdb try anyway, put `"disable_aslr": true` in `~/.gdbtui.json`.

## System calls (strace)

The **Syscalls** tab in the bottom panel lists the system calls your program makes. Each call shows its arguments, return value or error, and thread, and is linked to **the line in your code that made it**. Select a row and press Enter to jump to that line. Source lines that made system calls are annotated, for example `<< syscalls: fstat, write`.

Linux allows only one tracer per process. gdb and strace both use `ptrace`, so strace cannot attach to a program gdb is debugging. gdbtui offers two ways round this, and both fill the same tab. Rows are marked `g` (gdb) or `s` (strace).

Nothing is recorded until you start it, because recording slows the program down.

**Live recording, during your debug session.** Press `r` in the Syscalls tab, or use Ctrl+X → "Syscalls: Start / Stop Live Recording". Then run or continue the program. To record from the start, add `--syscalls`, or `--syscall-filter "openat read write"` to record only those calls:

```bash
python3 gdbtui.py --syscalls ./myprog
```

- gdb records each call through a syscall catchpoint. It reads the arguments, including file paths and the first bytes of data read or written, and the return value. It then continues the program automatically.
- You can record every call, only named calls (`openat read write`), or a group (`group:file`, `group:network`, gdb 8.3+).
- Stepping keeps working. When you step over a line, the calls it makes are recorded and the step still stops on the next line.
- Breakpoints, Break All (Ctrl+C) and Stop (Ctrl+K) work as usual.
- Every call pauses the program twice, so programs that make thousands of calls per second run noticeably slower. Record only the calls you care about to keep it fast.
- While gdb pauses one thread, the kernel interrupts and restarts some calls in the other threads. Those attempts show as dimmed `? ERESTART...` rows and are left out of the counts shown beside the source.

**The real strace, in a separate run.** Press `t` in the Syscalls tab, or use the Ctrl+X entries below. strace needs to be installed in the container.
- *Run Program Under strace* runs the program with the same arguments, outside the debugger. Its output appears in the Output tab, and you can type input there.
- *Attach strace to Another Process* traces a process that gdb is not debugging.
- *Load strace Log File* opens a log you made earlier with `strace -o FILE`.
- The default options are `-f -tt -T -s 64 -k`. You can change them before each run.
- With `-k`, strace records a stack trace for every call, and gdbtui maps it to the line in your code. If your strace has no `-k`, calls are listed but not linked to source lines.
- *Stop strace* ends the trace. In a run, it stops the program too; when attached, it detaches and the process keeps running.
- The log is kept in `/tmp`, and its path is shown in the list.

**Keys in the Syscalls tab:**

| Key | Action |
|---|---|
| `r` | Start or stop live recording |
| `t` | Run the program under strace |
| Enter | Go to the source line that made the call |
| `i` | Details: full arguments, time, and call stack |
| `f` | Filter by text in the name, arguments or location |
| `s` | Summary: count, errors and time per system call, like `strace -c` |
| `b` | Break whenever the program makes this system call |
| `w` | Save the list to a file |
| `x` | Clear the list |

To stop when the program makes particular system calls, without recording, use Ctrl+X → "Syscalls: Break When a System Call Is Made".

## Address randomization and breakpoints

gdb normally turns off address randomization (ASLR) for the program it debugs, so the program loads at the same address every run. Many containers forbid that. gdbtui checks at start-up: if turning ASLR off is allowed, it does so; otherwise it leaves ASLR on, and the program loads at a different address each run. To force either behaviour, set `"disable_aslr": true` or `false` in `~/.gdbtui.json`.

Breakpoints on source lines and functions are unaffected, because gdb recalculates them every run. Breakpoints set in the disassembly are stored as `function+offset` (for example `*'on_signal(int)'+15`), so they move with the program too. Only code with no symbol at all gets a breakpoint on a raw address, and that breakpoint is valid for one run only and isn't saved in the session.

If gdb reports **"Cannot insert breakpoint N. Cannot access memory at address 0x…"**, a breakpoint points at an address from an earlier run. gdbtui explains this and offers to delete or disable the breakpoint and continue. Breakpoints typed in the Console as `break *0x...` have the same limitation.

## Keys

Global shortcuts. Ctrl+X opens a searchable menu of every command.

| Key | Action | Visual Studio equivalent |
|---|---|---|
| Ctrl+R | Run / Continue | F5 |
| Ctrl+N | Step Over | F10 |
| Ctrl+T | Step Into | F11 |
| Ctrl+U | Step Out | Shift+F11 |
| Ctrl+Y | Run To Cursor | Ctrl+F10 |
| Ctrl+\ | Set Next Statement | Ctrl+Shift+F10 |
| Ctrl+C | Break All | Ctrl+Alt+Break |
| Ctrl+K | Stop Debugging (kill, or detach if attached) | Shift+F5 |
| Ctrl+B | Toggle Breakpoint | F9 |
| Ctrl+E | QuickWatch / Evaluate | Shift+F9 |
| Ctrl+W | Add Watch | |
| Ctrl+D | Toggle Source / Disassembly. From the source, shows the disassembly of the line under the cursor. | Ctrl+Alt+D |
| Ctrl+A | Show Next Statement | Alt+Num * |
| Ctrl+O | Open source file | |
| Ctrl+F | Find in file (Ctrl+F then Enter finds the next match) | Ctrl+F |
| Ctrl+G | Go to a line, function, `file:line` or `*address` | Ctrl+G |
| Ctrl+] | Next tab in the focused panel. In the Source panel: next open file. | Ctrl+Tab |
| Ctrl+Z | Zoom or unzoom the focused panel | |
| Ctrl+X | Command menu | |
| Ctrl+_ (usually typed Ctrl+/) | Help | |
| Ctrl+L | Redraw the screen | |
| Ctrl+Q | Quit (asks first if a program is being debugged) | |
| Tab / Shift+Tab | Move focus: Source, then the right panel, then the bottom panel | |

Some Ctrl keys are deliberately not used:

- **Ctrl+S** can freeze a terminal (XON/XOFF flow control).
- **Ctrl+P** is the first half of Docker's detach sequence (Ctrl+P, Ctrl+Q).
- **Ctrl+V** is paste in Windows Terminal.
- **Ctrl+H, Ctrl+I, Ctrl+M and Ctrl+[** arrive as Backspace, Tab, Enter and Esc.

If you run gdbtui inside tmux, Ctrl+B is tmux's prefix key. Rebind gdbtui's breakpoint key in `~/.gdbtui.json`, for example to Ctrl+A:

```json
{"keys": {"toggle_bp": "^A", "show_next": null}}
```

Action names are the first column of the `ACTIONS` list at the top of `gdbtui.py`. Other settings you can put in the same file are `tabsize`, `syntax`, `mouse`, `bottom_ratio` and `right_ratio`.

### Keys inside panels

| Panel | Keys |
|---|---|
| **Open File (Ctrl+O)** | Starts in the folder of the program being debugged. Enter opens a file or folder, Backspace on an empty filter goes up a folder, and you can type a path. Source files named in the debug info are listed too, marked `[debug info]`. |
| **Source / Disassembly** | Arrows, PgUp/PgDn and Home/End move the cursor. The word under the cursor is the default for QuickWatch, Watch, Data Breakpoint and Memory. |
| **Locals / Watch** | Enter or Right expands, Left collapses. `e` edits a value, `w` adds the item to Watch, `x` shows it in Memory. In Watch, `a` adds, `d` deletes and `r` edits the expression. |
| **Files** | Your program's source files from its debug info, with open files marked. Enter opens or switches to a file. `a` shows or hides system headers, `c` closes an open file. |
| **Call Stack** | Enter switches to that frame. Its locals and source are shown, and its line is marked in green. |
| **Threads** | Updates live while the program runs, showing each thread's state and CPU core. After a pause it shows where each thread is in **your** code, plus the library function it's waiting in. Enter switches to that thread (while paused). |
| **Breakpoints** | Enter goes to the location. Space enables or disables, `d` deletes, `c` sets a condition, `i` sets a hit count (ignore count), `n` adds a new one. |
| **Registers** | `a` toggles between general and all registers, `e` edits a value. Changed values are red. |
| **Memory** | Enter or `g` goes to an address or expression. Scroll with the arrows and PgUp/PgDn. Bytes that changed are red. |
| **Console** | Type any gdb command. Up/Down recall history, Enter on an empty line repeats the last command, PgUp/PgDn scroll. Lines starting with `-` are sent as raw MI commands. |
| **Output** | Shows the program's stdout and stderr through its own pseudo-terminal. Type a line and press Enter to send it to the program's stdin. |
| **Syscalls** | System calls from live recording or strace. Enter goes to the calling line; `i` details, `f` filter, `s` summary, `b` break on this call, `w` save, `x` clear. |

## Features

### Running the program

- Run and continue, step over, into and out, run to cursor, set next statement, break all, stop, restart, and start with a break at `main`.
- Attach to and detach from a process. The process picker lists everything under `/proc`.
- Open core dumps.
- Set the program's arguments, environment variables and working directory.

### Breakpoints

- Line, function and address breakpoints.
- Conditions and hit counts.
- Enable, disable, delete, and delete all.
- Pending breakpoints in shared libraries that haven't loaded yet.
- Logpoints, using gdb's `dprintf`.
- Data breakpoints (watchpoints) that trigger on write, read, or read/write.
- Breaking on C++ exceptions when they are thrown or caught.
- Signal handling (stop, pass or print).
- Saving breakpoints to a file and loading them back.

### Viewing data

- **Locals and Watch:** expandable trees that use gdb's STL pretty printers. A `std::map` is shown as `[key] = value`. Base classes appear inline, `public`/`private` grouping is hidden, `char` arrays are shown as strings, and changed values are red. You can edit values.
- **QuickWatch** popup with a full expandable tree.
- **Hexadecimal** display toggle.
- **Call stack** with argument values.
- **Threads, Registers, Memory, Modules** (shared libraries) and a **Disassembly** view with source lines mixed in. When a frame has no source, gdbtui switches to disassembly automatically.

### Interface and sessions

- **Source files open automatically.** When a debug build is loaded, every source file of your program that exists on disk opens as a tab. The file containing `main` is shown first. You choose how much opens:

  | Setting | Opens |
  |---|---|
  | `program` (default) | Your program's own source and header files |
  | `all` | Those, then external sources found on disk: system and library headers such as `/usr/include/c++/...`, and library sources. Library sources are checked again once the program has started. |
  | `off` | Nothing; files open when you step into them, or from Ctrl+O or the Files tab |

  Change it with Ctrl+X → "Settings: Open Source Files Automatically", which saves the choice. For a single run, use `--open-sources`, or set `"auto_open"` in `~/.gdbtui.json`. At most 50 files stay open, so `all` usually reaches the limit. Change the limit with `"max_open_files"` in the same file.
- **Several source files open as tabs** in the Source panel's title bar:
  - Each tab keeps its own cursor and scroll position.
  - Stepping or stopping in another file opens or switches to its tab. The tab holding the current statement is marked `>`.
  - Ctrl+] (with the Source panel focused) cycles through the open files.
  - Ctrl+O lists the open files first, then your program's source files from its debug info, then the binary's folder.
  - The **Files** tab in the right panel lists your program's source files; Enter opens one.
  - Ctrl+X has "Files: Switch to Open File", "Next / Previous Open File", "Close Current File" and "Close Other Files".
  - With mouse support on, click a tab to switch to it.
  - When the limit is reached (50 by default), opening another file closes the one used least recently.
- **Send a signal** (Ctrl+X → "Send Signal to Program / Process"):
  - Pick a signal from the list or type a name or number, then send it to the program being debugged, to the program running under strace, or to any other process.
  - While the program is being debugged, gdbtui pauses it for a moment and has gdb deliver the signal, so the program receives it and carries on. Otherwise gdb would intercept the signal itself.
  - If the program is already paused, you can choose to deliver the signal and continue, or queue it for when you continue.
  - SIGKILL and SIGSTOP are always sent directly.
- C/C++ syntax highlighting.
- Optional mouse support (`--mouse`, or Ctrl+X → "Toggle Mouse Support"):
  - Click a tab label in any panel to switch to it, including the open-file tabs in the Source panel.
  - Click a shortcut on the bottom line (`^N Over`, `^R Cont`, `^C Break` …) to run it, exactly as if you pressed the key. Double-clicking Step or Continue does it twice: the second one runs once the program has stopped.
  - Messages appear to the right of the shortcuts, so the shortcuts stay clickable. A message too long to fit covers the line for 8 seconds; click it to bring the shortcuts back.
  - Click the gutter to toggle a breakpoint, double-click a word to QuickWatch it, scroll with the wheel.
  - Hold Shift to select text with the mouse in most terminals.
- Panel zoom and resizable layout.
- Breakpoints, watches and open source files are saved per program in `~/.gdbtui.json` and restored the next time you debug it.
- Source path substitution, for binaries built in another directory.

## Files

| File | Purpose |
|---|---|
| `gdbtui.py` | The tool itself |
| `install_gdbtui.txt` | Paste-in installer, generated by the next file |
| `make_installer.py` | Rebuilds the installer from `gdbtui.py` |
| `testapp/src/*.cpp`, `*.h` | The multithreaded C++ test program, in several files |
| `testapp/build_variants.sh` | Builds the test program in several forms into `testapp/build/` |
| `tests/` | Automated tests and `TESTING.md` (how the project is tested). Run them with `sh tests/run_all.sh`. |

The project ships as two archives. Extract both into the same folder to get the full project.

| Archive | Contents |
|---|---|
| `gdbtui.tar.gz` (deployment) | `gdbtui.py`, `install_gdbtui.txt`, `make_installer.py`, `README.md` |
| `gdbtui-tests.tar.gz` (development only) | `tests/`, plus `testapp/`, the C++ test program and its build script |

Only `install_gdbtui.txt` (or `gdbtui.py`) goes into the container you debug in.

## Test builds

The test program in `testapp/src/` is split across several files so you can try the file tabs. It keeps running until you stop it, with two groups of threads passing data:

| File | What's in it |
|---|---|
| `main.cpp` | Start-up steps (vector, map, classes, `factorial`), launches the threads, prints statistics, handles signals |
| `shm_channel.cpp/.h` | Two producer threads send messages to a consumer thread through a ring buffer in **POSIX shared memory** (`shm_open` + `mmap`, visible as `/dev/shm/gdbtui_demo_<pid>`), with a process-shared mutex and condition variables |
| `net_channel.cpp/.h` | Two client threads send requests (`PING`, `SUM`, `FACT`) over **TCP on 127.0.0.1** to a server thread, which starts a handler thread per connection. The OS picks a free port. |
| `mathutil.cpp/.h` | `factorial` (recursive), `fibonacci`, `checksum` |
| `shapes.cpp/.h` | `Point`, `Shape`, `Rect`, `Circle` |
| `common.h` | Shared declarations: `g_running`, `g_stats`, logging |

The program reacts to signals, which you can send with Ctrl+X → "Send Signal":

| Signal | Effect |
|---|---|
| SIGUSR1 | Prints statistics now |
| SIGHUP | Resets the statistics |
| SIGINT, SIGTERM | Shuts down cleanly |

Program options:

| Option | Effect |
|---|---|
| `--seconds N` | Stops after N seconds |
| `--quick` | Runs only the start-up steps, then exits |
| `read` | Waits for a line on stdin first |
| `crash` | Segfaults |

Good places for breakpoints: `ShmRing::push`, `process_message`, `handle_request`, `net_client`.

Run this from the `testapp` folder:

```bash
sh build_variants.sh
```

It needs a C++ compiler. `strip` and `objcopy` (from binutils) are used if they're installed. `sh build_variants.sh clean` removes `testapp/build/`. To use a different compiler, set `CXX`, for example `CXX=clang++ sh build_variants.sh`.

| Binary | How it's built | What it tests |
|---|---|---|
| `demo_debug` | `-g -O0` | Everything. The best debugging experience. |
| `demo_debug_g3` | `-g3 -O0` | Debug information plus macro definitions |
| `demo_debug_O2` | `-g -O2` | Optimized code: `<optimized out>` values, jumpy stepping |
| `demo_nodebug` | `-O0` | No debug information: disassembly, function and instruction breakpoints |
| `demo_nodebug_O2` | `-O2` | No debug information, optimized |
| `demo_nopie` | `-g -no-pie` | Fixed load address |
| `demo_movedsrc` | `-g`, debug paths say `/build/src` | Source path substitution |
| `demo_stripped` | `strip --strip-all` | No symbols: entry-point start, assembly only |
| `demo_debug_stripped` | `strip --strip-debug` | Function symbols kept, debug information removed |
| `demo_splitdebug` | `objcopy --only-keep-debug` | Debug information in a separate `.debug` file (gnu-debuglink) |
| `demo_static` | `-g -static` | Static linking. Optional: needs static libraries. |

Run the program with an argument to exercise more features:

- `demo_debug read` waits for a line on stdin; type it in the Output tab.
- `demo_debug --seconds 10` stops by itself after 10 seconds.
- `--syscalls build/demo_debug` (a gdbtui option) shows the shared-memory and socket system calls in the Syscalls tab.
- `demo_debug crash` segfaults, which brings up the exception popup.
