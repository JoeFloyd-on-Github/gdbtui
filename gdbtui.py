#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gdbtui.py - a Visual Studio style debugger front end for GDB, in a text terminal.

Requirements : Python 3.6 or newer (standard library only) and gdb on the PATH.
Usage        : python3 gdbtui.py [options] [program [program args...]]
               python3 gdbtui.py -p PID              attach to a running process
               python3 gdbtui.py -c core ./program   open a core dump
Help         : press Ctrl+_ (usually typed as Ctrl+/ ) or Ctrl+X for the command menu.

All global shortcuts are Ctrl+letter keys (no function keys are used):
  Ctrl+R Run/Continue    Ctrl+N Step Over      Ctrl+T Step Into     Ctrl+U Step Out
  Ctrl+Y Run To Cursor   Ctrl+\\ Set Next Stmt  Ctrl+C Break All     Ctrl+K Stop
  Ctrl+B Breakpoint      Ctrl+E QuickWatch     Ctrl+W Add Watch     Ctrl+D Disassembly
  Ctrl+A Show Next Stmt  Ctrl+O Open File      Ctrl+F Find          Ctrl+G Go To
  Ctrl+] Next Tab        Ctrl+Z Zoom Panel     Ctrl+X Command Menu  Ctrl+L Redraw
  Tab / Shift+Tab move focus between the Source, right and bottom panels.
"""

import argparse
import codecs
import curses
import errno
import json
import locale
import os
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import time
import traceback

try:
    import termios
except ImportError:  # pragma: no cover
    termios = None
try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

VERSION = '1.0.0'
CONFIG_PATH = os.path.expanduser('~/.gdbtui.json')
# Shortcut-bar commands that a double click runs twice (curses merges two fast clicks).
REPEATABLE_CLICK_ACTIONS = ('continue', 'step_over', 'step_into', 'step_out')
MAX_BUFFER_LINES = 5000

# --------------------------------------------------------------------------
# Actions and default key bindings (all Ctrl+ keys)
# --------------------------------------------------------------------------
# (action, label, default key letter or None for menu-only actions)
ACTIONS = [
    ('continue', 'Run / Continue', 'R'),
    ('step_over', 'Step Over', 'N'),
    ('step_into', 'Step Into', 'T'),
    ('step_out', 'Step Out', 'U'),
    ('run_to_cursor', 'Run To Cursor', 'Y'),
    ('set_next', 'Set Next Statement (jump to cursor)', '\\'),
    ('break_all', 'Break All (pause program)', 'C'),
    ('stop', 'Stop Debugging (kill / detach)', 'K'),
    ('toggle_bp', 'Toggle Breakpoint at cursor', 'B'),
    ('quickwatch', 'QuickWatch / Evaluate Expression...', 'E'),
    ('add_watch', 'Add Watch...', 'W'),
    ('toggle_disasm', 'Toggle Source / Disassembly', 'D'),
    ('show_next', 'Show Next Statement (current line)', 'A'),
    ('open_file', 'Open Source File...', 'O'),
    ('find', 'Find in File...', 'F'),
    ('goto', 'Go To Line / Function / Address...', 'G'),
    ('next_tab', 'Next Tab in Focused Panel (Source panel: next open file)', ']'),
    ('zoom', 'Zoom / Unzoom Focused Panel', 'Z'),
    ('menu', 'Command Menu', 'X'),
    ('help', 'Help', '_'),
    ('redraw', 'Redraw Screen', 'L'),
    ('quit', 'Quit gdbtui', 'Q'),
    ('switch_file', 'Files: Switch to Open File...', None),
    ('next_file', 'Files: Next Open File', None),
    ('prev_file', 'Files: Previous Open File', None),
    ('close_file', 'Files: Close Current File', None),
    ('close_other_files', 'Files: Close Other Files', None),
    ('send_signal', 'Send Signal to Program / Process...', None),
    ('start', 'Start Debugging and Break at main', None),
    ('restart', 'Restart', None),
    ('attach', 'Attach to Process...', None),
    ('detach', 'Detach from Process', None),
    ('load_core', 'Open Core Dump...', None),
    ('load_program', 'Load Program...', None),
    ('set_args', 'Set Program Arguments...', None),
    ('set_env', 'Set Environment Variable...', None),
    ('set_cwd', 'Set Working Directory...', None),
    ('func_bp', 'New Function Breakpoint...', None),
    ('location_bp', 'New Breakpoint at Location (file:line, *addr)...', None),
    ('cond_bp', 'Breakpoint Condition at cursor...', None),
    ('hit_bp', 'Breakpoint Hit Count (ignore count) at cursor...', None),
    ('enable_bp', 'Enable / Disable Breakpoint at cursor', None),
    ('logpoint', 'Insert Tracepoint / Logpoint (dprintf) at cursor...', None),
    ('data_bp', 'New Data Breakpoint (watchpoint)...', None),
    ('catch_throw', 'Break When C++ Exception Thrown', None),
    ('catch_catch', 'Break When C++ Exception Caught', None),
    ('handle_signal', 'Signal Handling (stop / pass / print)...', None),
    ('enable_all_bp', 'Enable All Breakpoints', None),
    ('disable_all_bp', 'Disable All Breakpoints', None),
    ('delete_all_bp', 'Delete All Breakpoints', None),
    ('save_bp', 'Save Breakpoints to File...', None),
    ('load_bp', 'Load Breakpoints from File...', None),
    ('delete_all_watch', 'Delete All Watches', None),
    ('frame_up', 'Frame Up (go to caller)', None),
    ('frame_down', 'Frame Down (go to callee)', None),
    ('mem_goto', 'Memory: Go To Address / Expression...', None),
    ('toggle_hex', 'Toggle Hexadecimal Display', None),
    ('toggle_mouse', 'Toggle Mouse Support', None),
    ('subst_path', 'Source Path Substitution (from -> to)...', None),
    ('add_dir', 'Add Source Directory...', None),
    ('gdb_command', 'Run GDB Command...', None),
    ('browse_functions', 'Browse Functions (go to / set breakpoints)...', None),
    ('show_locals', 'Show Locals', None),
    ('show_watch', 'Show Watch', None),
    ('show_registers', 'Show Registers', None),
    ('show_memory', 'Show Memory', None),
    ('show_console', 'Show GDB Console', None),
    ('show_output', 'Show Program Output', None),
    ('show_stack', 'Show Call Stack', None),
    ('show_breakpoints', 'Show Breakpoints', None),
    ('show_threads', 'Show Threads', None),
    ('show_modules', 'Show Modules (shared libraries)', None),
    ('show_files', 'Show Files (program source files)', None),
    ('auto_open_setting', 'Settings: Open Source Files Automatically (program / all / off)...', None),
    ('show_syscalls', 'Show Syscalls', None),
    ('syscall_record', 'Syscalls: Start / Stop Live Recording (gdb)...', None),
    ('strace_run', 'Syscalls: Run Program Under strace (separate run)...', None),
    ('strace_attach', 'Syscalls: Attach strace to Another Process...', None),
    ('strace_load', 'Syscalls: Load strace Log File...', None),
    ('strace_stop', 'Syscalls: Stop strace', None),
    ('syscall_break', 'Syscalls: Break When a System Call Is Made...', None),
    ('syscall_summary', 'Syscalls: Summary (counts, errors, time)', None),
    ('grow_bottom', 'Layout: Bottom Panel Taller', None),
    ('shrink_bottom', 'Layout: Bottom Panel Shorter', None),
    ('grow_right', 'Layout: Right Panel Wider', None),
    ('shrink_right', 'Layout: Right Panel Narrower', None),
    ('focus_next', 'Focus Next Panel (Tab)', None),
    ('save_session', 'Save Session (breakpoints and watches)', None),
]
ACTION_LABELS = dict((a, l) for a, l, _k in ACTIONS)

KEY_ENTERS = (10, 13, curses.KEY_ENTER)
KEY_BACKS = (8, 127, curses.KEY_BACKSPACE)
KEY_ESC = 27


def ctrl_code(letter):
    """'R' -> 18 ; '\\' -> 28 ; ']' -> 29 ; '_' -> 31"""
    if letter is None:
        return None
    letter = letter.strip()
    if letter.startswith('^') and len(letter) == 2:
        letter = letter[1]
    if letter.lower().startswith('ctrl+') or letter.lower().startswith('ctrl-'):
        letter = letter[5:]
    if len(letter) != 1:
        return None
    return ord(letter.upper()) & 0x1f


def key_name(code):
    if code is None:
        return ''
    if 1 <= code <= 26:
        return 'Ctrl+' + chr(code + 64)
    return {28: 'Ctrl+\\', 29: 'Ctrl+]', 30: 'Ctrl+^', 31: 'Ctrl+_'}.get(code, str(code))


def short_key(code):
    return key_name(code).replace('Ctrl+', '^')


# --------------------------------------------------------------------------
# GDB/MI output parser
# --------------------------------------------------------------------------
class MIParseError(Exception):
    pass


_ESCAPES = {'n': 10, 't': 9, 'r': 13, '"': 34, '\\': 92, 'a': 7, 'b': 8,
            'f': 12, 'v': 11, 'e': 27, "'": 39}


def _parse_cstring(s, i):
    """Parse a C string starting at s[i] == '"'. Returns (str, next_index)."""
    i += 1
    out = bytearray()
    n = len(s)
    while i < n:
        c = s[i]
        if c == '"':
            return out.decode('utf-8', 'replace'), i + 1
        if c == '\\':
            i += 1
            if i >= n:
                break
            c = s[i]
            if c in '01234567':
                j = i
                while j < n and j < i + 3 and s[j] in '01234567':
                    j += 1
                out.append(int(s[i:j], 8) & 0xff)
                i = j
                continue
            if c == 'x':
                j = i + 1
                while j < n and j < i + 3 and s[j] in '0123456789abcdefABCDEF':
                    j += 1
                if j > i + 1:
                    out.append(int(s[i + 1:j], 16) & 0xff)
                    i = j
                    continue
            if c in _ESCAPES:
                out.append(_ESCAPES[c])
            else:
                out.extend(c.encode('utf-8', 'replace'))
            i += 1
            continue
        out.extend(c.encode('utf-8', 'replace'))
        i += 1
    raise MIParseError('unterminated string')


def _dict_add(d, k, v):
    if k in d:
        old = d[k]
        if isinstance(old, list) and getattr(old, '_multi', False):
            old.append(v)
        else:
            lst = _MultiList([old, v])
            d[k] = lst
    else:
        d[k] = v


class _MultiList(list):
    _multi = True


def _parse_value(s, i):
    c = s[i]
    if c == '"':
        return _parse_cstring(s, i)
    if c == '{':
        i += 1
        d = {}
        if s[i] == '}':
            return d, i + 1
        while True:
            if s[i] in '"{[':
                # non-standard: bare value inside a tuple, e.g. script={"printf ..."}
                v, i = _parse_value(s, i)
                _dict_add(d, '_values', v)
            else:
                k, v, i = _parse_result(s, i)
                _dict_add(d, k, v)
            if s[i] == ',':
                i += 1
                continue
            if s[i] == '}':
                return d, i + 1
            raise MIParseError('bad tuple at %d' % i)
    if c == '[':
        i += 1
        lst = []
        if s[i] == ']':
            return lst, i + 1
        while True:
            if s[i] in '"{[':
                v, i = _parse_value(s, i)
            else:
                _k, v, i = _parse_result(s, i)
            lst.append(v)
            if s[i] == ',':
                i += 1
                continue
            if s[i] == ']':
                return lst, i + 1
            raise MIParseError('bad list at %d' % i)
    raise MIParseError('unexpected %r at %d' % (c, i))


def _parse_result(s, i):
    j = s.index('=', i)
    k = s[i:j]
    v, i = _parse_value(s, j + 1)
    return k, v, i


_MI_PREFIX = re.compile(r'^(\d*)([\^*+=~@&])')


def parse_mi_line(line):
    """Parse one line of GDB/MI output into a dict."""
    if line.startswith('(gdb)'):
        return {'type': 'prompt', 'raw': line}
    m = _MI_PREFIX.match(line)
    if not m:
        return {'type': 'other', 'text': line, 'raw': line}
    tok = int(m.group(1)) if m.group(1) else None
    kind = m.group(2)
    rest = line[m.end():]
    if kind in '~@&':
        text = rest
        if rest.startswith('"'):
            try:
                text, _ = _parse_cstring(rest, 0)
            except MIParseError:
                text = rest
        typ = {'~': 'console', '@': 'target', '&': 'log'}[kind]
        return {'type': typ, 'text': text, 'raw': line, 'token': tok}
    typ = {'^': 'result', '*': 'exec', '+': 'status', '=': 'notify'}[kind]
    j = rest.find(',')
    cls = rest if j < 0 else rest[:j]
    results = {}
    if j >= 0:
        i = j + 1
        n = len(rest)
        try:
            while i < n:
                k, v, i = _parse_result(rest, i)
                _dict_add(results, k, v)
                if i < n and rest[i] == ',':
                    i += 1
                else:
                    break
        except (MIParseError, ValueError, IndexError):
            results['_parse_error'] = True
    return {'type': typ, 'token': tok, 'class': cls, 'results': results, 'raw': line}


def mi_quote(s):
    return '"' + (s.replace('\\', '\\\\').replace('"', '\\"')
                  .replace('\n', '\\n').replace('\t', '\\t')) + '"'


def cli(cmd):
    return '-interpreter-exec console ' + mi_quote(cmd)


def parse_int(s):
    if s is None:
        return None
    try:
        return int(str(s).split()[0], 0)
    except (ValueError, IndexError):
        m = re.search(r'0x[0-9a-fA-F]+', str(s))
        if m:
            return int(m.group(), 16)
        return None


def as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


# --------------------------------------------------------------------------
# GDB process
# --------------------------------------------------------------------------
class GdbProcess(object):
    def __init__(self, path, extra_args, log_path=None):
        cmd = [path, '--interpreter=mi2', '-q'] + list(extra_args)
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, bufsize=0,
                                     start_new_session=True)
        self.fd = self.proc.stdout.fileno()
        self.wfd = self.proc.stdin.fileno()
        os.set_blocking(self.fd, False)
        self.buf = b''
        self.token = 0
        self.pending = {}   # token -> (ok_cb, err_cb, quiet, capture_list)
        self.order = []     # outstanding tokens in send order
        self.alive = True
        self.log = open(log_path, 'a') if log_path else None

    def send(self, cmd, cb=None, errcb=None, quiet=False, capture=False):
        if not self.alive:
            return None
        self.token += 1
        t = self.token
        self.pending[t] = (cb, errcb, quiet, [] if capture else None)
        self.order.append(t)
        data = ('%d%s\n' % (t, cmd)).encode('utf-8', 'replace')
        if self.log:
            self.log.write('>> %d%s\n' % (t, cmd))
            self.log.flush()
        try:
            while data:
                n = os.write(self.wfd, data)
                data = data[n:]
        except OSError:
            self.alive = False
        return t

    def read(self):
        recs = []
        while True:
            try:
                data = os.read(self.fd, 65536)
            except (OSError, IOError) as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                    break
                self.alive = False
                break
            if not data:
                self.alive = False
                break
            self.buf += data
            if len(data) < 65536:
                break
        if b'\n' in self.buf:
            parts = self.buf.split(b'\n')
            self.buf = parts.pop()
            for p in parts:
                line = p.decode('utf-8', 'replace').rstrip('\r')
                if not line:
                    continue
                if self.log:
                    self.log.write('<< %s\n' % line)
                recs.append(parse_mi_line(line))
            if self.log:
                self.log.flush()
        return recs

    def close(self):
        if self.alive:
            try:
                self.send('-gdb-exit')
            except Exception:
                pass
        deadline = time.time() + 1.5
        while time.time() < deadline and self.proc.poll() is None:
            time.sleep(0.05)
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.alive = False


# --------------------------------------------------------------------------
# Syntax highlighting (C / C++ and friends)
# --------------------------------------------------------------------------
C_KEYWORDS = set('''alignas alignof asm auto break case catch class const constexpr
const_cast continue co_await co_return co_yield decltype default delete do dynamic_cast
else enum explicit export extern false final for friend goto if inline mutable namespace
new noexcept nullptr operator override private protected public register reinterpret_cast
return sizeof static static_assert static_cast struct switch template this thread_local
throw true try typedef typeid typename union using virtual volatile while NULL self
def import from elif pass lambda yield fn let mut impl match pub use mod func var type
package go defer'''.split())
C_TYPES = set('''void bool char short int long float double signed unsigned wchar_t
char8_t char16_t char32_t size_t ssize_t off_t pid_t int8_t int16_t int32_t int64_t
uint8_t uint16_t uint32_t uint64_t intptr_t uintptr_t ptrdiff_t std string vector map
set list unique_ptr shared_ptr FILE u8 u16 u32 u64 i8 i16 i32 i64 usize isize str'''.split())
_HL_RE = re.compile(r'(?P<lc>//.*)|(?P<bc>/\*)|'
                    r'(?P<str>"(?:[^"\\]|\\.)*"?|\'(?:[^\'\\]|\\.)*\'?)|'
                    r'(?P<num>\b\d[\w.]*)|(?P<id>[A-Za-z_]\w*)')
_PP_RE = re.compile(r'^\s*#\s*\w+')


def highlight(line, in_comment):
    """Return ([(text, style)], in_comment_at_end)."""
    segs = []
    i = 0
    n = len(line)
    if not in_comment:
        m = _PP_RE.match(line)
        if m:
            segs.append((m.group(), 'pre'))
            i = m.end()
    while i < n:
        if in_comment:
            j = line.find('*/', i)
            if j < 0:
                segs.append((line[i:], 'com'))
                return segs, True
            segs.append((line[i:j + 2], 'com'))
            i = j + 2
            in_comment = False
            continue
        m = _HL_RE.search(line, i)
        if not m:
            segs.append((line[i:], None))
            break
        if m.start() > i:
            segs.append((line[i:m.start()], None))
        kind = m.lastgroup
        t = m.group()
        if kind == 'bc':
            j = line.find('*/', m.end())
            if j < 0:
                segs.append((line[m.start():], 'com'))
                return segs, True
            segs.append((line[m.start():j + 2], 'com'))
            i = j + 2
            continue
        if kind == 'lc':
            segs.append((t, 'com'))
        elif kind == 'str':
            segs.append((t, 'str'))
        elif kind == 'num':
            segs.append((t, 'num'))
        else:
            segs.append((t, 'kw' if t in C_KEYWORDS else ('type' if t in C_TYPES else None)))
        i = m.end()
    return segs, in_comment


class SourceFile(object):
    def __init__(self, path, tabsize):
        self.path = path
        with open(path, 'rb') as f:
            data = f.read()
        text = data.decode('utf-8', 'replace')
        self.lines = [l.expandtabs(tabsize) for l in text.splitlines()] or ['']
        self.mtime = os.path.getmtime(path)
        self._states = None

    def state(self, idx):
        if self._states is None:
            st = []
            inc = False
            for l in self.lines:
                st.append(inc)
                if inc or '/*' in l:
                    _s, inc = highlight(l, inc)
            self._states = st
        return self._states[idx] if idx < len(self._states) else False


# --------------------------------------------------------------------------
# Variable objects (Locals / Watch / QuickWatch trees)
# --------------------------------------------------------------------------
class VarNode(object):
    def __init__(self, name, expr=None):
        self.name = name
        self.expr = expr
        self.vobj = None
        self.value = ''
        self.type = ''
        self.numchild = 0
        self.has_more = False
        self.expanded = False
        self.children = None
        self.changed = False
        self.error = None
        self.access = False
        self.loading = False
        self.in_scope = True
        self.parent = None
        self.dead = False
        self.displayhint = None
        self.hidden = []
        self.path = None
        self.strval = None

    def expandable(self):
        return bool(self.vobj) and (self.numchild > 0 or self.has_more or bool(self.children))


class TextBuffer(object):
    def __init__(self):
        self.lines = []
        self.tail = ''
        self.tail_attr = None

    def write(self, text, attr=None):
        if not text:
            return
        text = self.tail + text
        parts = text.split('\n')
        self.tail = parts.pop()
        self.tail_attr = attr
        for p in parts:
            self.lines.append((p, attr))
        if len(self.lines) > MAX_BUFFER_LINES:
            del self.lines[:len(self.lines) - MAX_BUFFER_LINES]

    def all(self):
        if self.tail:
            return self.lines + [(self.tail, self.tail_attr)]
        return self.lines

    def clear(self):
        self.lines = []
        self.tail = ''


# --------------------------------------------------------------------------
# Line editor
# --------------------------------------------------------------------------
class LineEdit(object):
    def __init__(self, text='', history=None):
        self.text = text
        self.pos = len(text)
        self.hist = history if history is not None else []
        self.hpos = None
        self.scroll = 0
        self.fresh = False   # True: pre-filled text is 'selected'; typing replaces it

    def set(self, text):
        self.text = text
        self.pos = len(text)

    def key(self, k):
        """Returns 'enter', 'cancel', True (consumed) or False."""
        fresh = self.fresh
        self.fresh = False
        if isinstance(k, str):
            if fresh:
                self.text = ''
                self.pos = 0
            self.text = self.text[:self.pos] + k + self.text[self.pos:]
            self.pos += len(k)
            return True
        if k in KEY_ENTERS:
            if self.text and (not self.hist or self.hist[-1] != self.text):
                self.hist.append(self.text)
                del self.hist[:-200]
            self.hpos = None
            return 'enter'
        if k == KEY_ESC:
            return 'cancel'
        if k in KEY_BACKS:
            if fresh:
                self.set('')
                return True
            if self.pos > 0:
                self.text = self.text[:self.pos - 1] + self.text[self.pos:]
                self.pos -= 1
            return True
        if k == curses.KEY_DC or k == 4:
            self.text = self.text[:self.pos] + self.text[self.pos + 1:]
            return True
        if k == curses.KEY_LEFT:
            self.pos = max(0, self.pos - 1)
            return True
        if k == curses.KEY_RIGHT:
            self.pos = min(len(self.text), self.pos + 1)
            return True
        if k in (curses.KEY_HOME, 1):
            self.pos = 0
            return True
        if k in (curses.KEY_END, 5):
            self.pos = len(self.text)
            return True
        if k == 21:  # Ctrl+U
            self.text = self.text[self.pos:]
            self.pos = 0
            return True
        if k == 11:  # Ctrl+K
            self.text = self.text[:self.pos]
            return True
        if k == 23:  # Ctrl+W delete word
            j = self.pos
            while j > 0 and self.text[j - 1] == ' ':
                j -= 1
            while j > 0 and self.text[j - 1] != ' ':
                j -= 1
            self.text = self.text[:j] + self.text[self.pos:]
            self.pos = j
            return True
        if k == curses.KEY_UP:
            if self.hist:
                if self.hpos is None:
                    self.hpos = len(self.hist)
                self.hpos = max(0, self.hpos - 1)
                self.set(self.hist[self.hpos])
            return True
        if k == curses.KEY_DOWN:
            if self.hpos is not None:
                self.hpos += 1
                if self.hpos >= len(self.hist):
                    self.hpos = None
                    self.set('')
                else:
                    self.set(self.hist[self.hpos])
            return True
        return False

    def draw(self, app, y, x, w, attr=0):
        """Draw and return the screen column of the cursor."""
        if w <= 1:
            return x
        if self.pos < self.scroll:
            self.scroll = self.pos
        if self.pos >= self.scroll + w:
            self.scroll = self.pos - w + 1
        if self.fresh and self.text:
            vis = self.text[self.scroll:self.scroll + w]
            app.put(y, x, ' ' * w, attr, w)
            app.put(y, x, vis, attr | curses.A_REVERSE, w)
        else:
            app.put(y, x, self.text[self.scroll:self.scroll + w].ljust(w), attr, w)
        return x + self.pos - self.scroll


# --------------------------------------------------------------------------
# Views (tabs)
# --------------------------------------------------------------------------
class View(object):
    title = ''
    arrows_switch_tabs = True

    def draw(self, app, y, x, h, w, focused):
        pass

    def key(self, app, k):
        return False

    def click(self, app, row, col, double=False):
        pass

    def scroll(self, app, n):
        pass

    def on_show(self, app):
        pass


class ListView(View):
    empty_text = ''

    def __init__(self):
        self.sel = 0
        self.top = 0
        self.h = 1

    def rows(self, app):
        return []

    def render(self, app, row, w):
        return [(str(row), None)]

    def activate(self, app, row):
        pass

    def row_key(self, app, k, row):
        return False

    def draw(self, app, y, x, h, w, focused):
        rows = self.rows(app)
        self.h = h
        n = len(rows)
        if n == 0:
            self.sel = 0
            self.top = 0
            if self.empty_text:
                app.put(y, x + 1, self.empty_text, app.A['dim'], w - 1)
            return
        self.sel = max(0, min(self.sel, n - 1))
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + h:
            self.top = self.sel - h + 1
        self.top = max(0, min(self.top, max(0, n - h)))
        for i in range(h):
            idx = self.top + i
            if idx >= n:
                break
            segs = self.render(app, rows[idx], w)
            sel = (idx == self.sel)
            base = curses.A_REVERSE if (sel and focused) else 0
            if base:
                app.put(y + i, x, ' ' * w, base, w)
            app.draw_segs(y + i, x, w, segs, base)

    def key(self, app, k):
        rows = self.rows(app)
        n = len(rows)
        if k == curses.KEY_UP:
            self.sel = max(0, self.sel - 1)
        elif k == curses.KEY_DOWN:
            self.sel = min(max(0, n - 1), self.sel + 1)
        elif k == curses.KEY_PPAGE:
            self.sel = max(0, self.sel - max(1, self.h - 1))
        elif k == curses.KEY_NPAGE:
            self.sel = min(max(0, n - 1), self.sel + max(1, self.h - 1))
        elif k == curses.KEY_HOME:
            self.sel = 0
        elif k == curses.KEY_END:
            self.sel = max(0, n - 1)
        elif k in KEY_ENTERS:
            if n:
                self.activate(app, rows[min(self.sel, n - 1)])
        else:
            return self.row_key(app, k, rows[min(self.sel, n - 1)] if n else None)
        return True

    def click(self, app, row, col, double=False):
        rows = self.rows(app)
        idx = self.top + row
        if 0 <= idx < len(rows):
            self.sel = idx
            if double:
                self.activate(app, rows[idx])

    def scroll(self, app, n):
        rows = self.rows(app)
        self.sel = max(0, min(len(rows) - 1, self.sel + n))


class VarTreeView(ListView):
    arrows_switch_tabs = False

    def __init__(self, title, get_roots, is_watch=False, empty_text=''):
        ListView.__init__(self)
        self.title = title
        self.get_roots = get_roots
        self.is_watch = is_watch
        self.empty_text = empty_text

    def rows(self, app):
        out = []

        def walk(nodes, depth):
            for n in nodes:
                if n.access:
                    if n.children is None:
                        if not n.loading:
                            app.fetch_children(n)
                        out.append(('loading', depth))
                    else:
                        walk(n.children, depth)
                    continue
                out.append((n, depth))
                if n.expanded:
                    if n.children is None:
                        out.append(('loading', depth + 1))
                    else:
                        walk(n.children, depth + 1)
                        if n.has_more:
                            out.append(('more', depth + 1))
        walk(self.get_roots(app), 0)
        if self.is_watch:
            out.append(('add', 0))
        return out

    def render(self, app, row, w):
        node, depth = row
        ind = '  ' * depth
        if node == 'loading':
            return [(ind + '    loading...', 'dim')]
        if node == 'more':
            return [(ind + '    ... (more items not shown)', 'dim')]
        if node == 'add':
            return [('    <add watch: press Enter>', 'dim')]
        if node.expandable():
            mark = '[-] ' if node.expanded else '[+] '
        else:
            mark = '    '
        typew = 0
        if w >= 56:
            typew = min(28, w // 4)
        name = ind + mark + node.name
        if node.error and not node.vobj:
            val, vattr = node.error, 'err'
        elif not node.in_scope:
            val, vattr = '<out of scope>', 'dim'
        else:
            val = node.value
            vattr = 'changed' if node.changed else None
        namew = min(max(12, w // 3), len(name) + 1)
        segs = [(name.ljust(namew)[:max(1, w - typew - 4)], 'name'), ('= ', 'dim')]
        valw = w - typew - len(segs[0][0]) - 2
        val = val.replace('\n', ' ')
        if typew:
            segs.append((val[:max(0, valw - 1)].ljust(max(0, valw)), vattr))
            segs.append((node.type[:typew], 'dim'))
        else:
            segs.append((val, vattr))
        return segs

    def _parent_index(self, rows, idx):
        depth = rows[idx][1]
        for j in range(idx - 1, -1, -1):
            if rows[j][1] < depth and isinstance(rows[j][0], VarNode):
                return j
        return None

    def key(self, app, k):
        rows = self.rows(app)
        if not rows:
            return ListView.key(self, app, k)
        self.sel = max(0, min(self.sel, len(rows) - 1))
        node, _depth = rows[self.sel]
        if node == 'add':
            if k in KEY_ENTERS or k in ('a', 'i', curses.KEY_IC):
                app.act_add_watch(prompt_default='')
                return True
            return ListView.key(self, app, k)
        if not isinstance(node, VarNode):
            return ListView.key(self, app, k)
        if k in (curses.KEY_RIGHT, '+'):
            if node.expandable() and not node.expanded:
                app.expand(node)
            elif node.expanded and self.sel + 1 < len(rows):
                self.sel += 1
            return True
        if k in (curses.KEY_LEFT, '-'):
            if node.expanded:
                node.expanded = False
            else:
                p = self._parent_index(rows, self.sel)
                if p is not None:
                    self.sel = p
            return True
        if k in KEY_ENTERS or k == ' ':
            if node.expandable():
                if node.expanded:
                    node.expanded = False
                else:
                    app.expand(node)
            elif self.is_watch and node.parent is None:
                app.edit_watch(node)
            return True
        if k == 'e':
            app.edit_value(node)
            return True
        if k == 'w' and not self.is_watch:
            app.watch_node(node)
            return True
        if k == 'x':
            app.mem_show_node(node)
            return True
        if self.is_watch and node.parent is None:
            if k in ('d', curses.KEY_DC) or k in KEY_BACKS:
                app.remove_watch(node)
                return True
            if k == 'r':
                app.edit_watch(node)
                return True
        if self.is_watch and k in ('a', 'i', curses.KEY_IC):
            app.act_add_watch(prompt_default='')
            return True
        return ListView.key(self, app, k)


class RegistersView(ListView):
    title = 'Registers'
    empty_text = 'Registers are shown when the program is paused.'

    def __init__(self):
        ListView.__init__(self)
        self.show_all = False

    def rows(self, app):
        regs = app.registers
        if not self.show_all:
            regs = [r for r in regs if not r[1].startswith('{')]
        return regs

    def render(self, app, row, w):
        name, value, changed = row
        return [(name.ljust(10), 'name'), (value, 'changed' if changed else None)]

    def row_key(self, app, k, row):
        if k == 'a':
            self.show_all = not self.show_all
            app.msg('Showing %s registers' % ('all' if self.show_all else 'general'))
            return True
        if k == 'e' and row:
            v = app.prompt('Set $%s =' % row[0], row[1])
            if v is not None and v != '':
                app.gdb.send('-data-evaluate-expression %s' % mi_quote('$%s = %s' % (row[0], v)),
                             lambda r: app.after_state_change(), errcb=app.cmd_error)
            return True
        return False

    def on_show(self, app):
        app.refresh_registers()


class MemoryView(View):
    title = 'Memory'

    def __init__(self):
        self.h = 1
        self.bpr = 16

    def draw(self, app, y, x, h, w, focused):
        self.h = h
        if app.mem_addr is None:
            app.put(y, x + 1, "Press Enter (or 'g') to enter an address or expression, e.g. &buf or ptr", app.A['dim'], w - 1)
            if app.mem_error:
                app.put(y + 1, x + 1, app.mem_error, app.A['err'], w - 1)
            return
        aw = 18 if app.mem_addr > 0xffffffff else 10
        bpr = 16
        while bpr > 4 and aw + 2 + bpr * 3 + 1 + bpr > w:
            bpr //= 2
        self.bpr = bpr
        app.put(y, x, ('%s %s' % ('Address'.ljust(aw), app.mem_label or ''))[:w], app.A['dim'], w)
        for row in range(h - 1):
            base = app.mem_addr + row * bpr
            line_y = y + 1 + row
            cx = x
            app.put(line_y, cx, ('0x%0*x' % (aw - 2, base)), app.A['lineno'])
            cx += aw + 1
            asc = []
            for b in range(bpr):
                a = base + b
                off = a - app.mem_addr
                v = app.mem_bytes[off] if 0 <= off < len(app.mem_bytes) else None
                if v is None:
                    app.put(line_y, cx, '??', app.A['dim'])
                    asc.append(('.', 'dim'))
                else:
                    old = app.mem_prev.get(a)
                    attr = app.A['changed'] if (old is not None and old != v) else 0
                    app.put(line_y, cx, '%02x' % v, attr)
                    asc.append((chr(v) if 32 <= v < 127 else '.', 'changed' if attr else None))
                cx += 3
                if b == bpr // 2 - 1 and bpr >= 8:
                    pass
            cx += 1
            for ch, a in asc:
                app.put(line_y, cx, ch, app.A.get(a, 0) if a else 0)
                cx += 1
        if app.mem_error:
            app.put(y + h - 1, x + 1, app.mem_error, app.A['err'], w - 1)

    def key(self, app, k):
        if app.mem_addr is None:
            if k in KEY_ENTERS or k == 'g':
                app.act_mem_goto()
                return True
            return False
        rows = max(1, self.h - 1)
        if k == curses.KEY_UP:
            app.mem_move(-self.bpr)
        elif k == curses.KEY_DOWN:
            app.mem_move(self.bpr)
        elif k == curses.KEY_PPAGE:
            app.mem_move(-self.bpr * rows)
        elif k == curses.KEY_NPAGE:
            app.mem_move(self.bpr * rows)
        elif k in KEY_ENTERS or k == 'g':
            app.act_mem_goto()
        elif k == 'r':
            app.refresh_memory()
        else:
            return False
        return True

    def scroll(self, app, n):
        if app.mem_addr is not None:
            app.mem_move(self.bpr * n)

    def on_show(self, app):
        if app.mem_addr is not None:
            app.refresh_memory()


class ConsoleView(View):
    arrows_switch_tabs = False

    def __init__(self, title, buf_attr, prompt):
        self.title = title
        self.buf_attr = buf_attr
        self.prompt_text = prompt
        self.edit = LineEdit('')
        self.scrollback = 0
        self.h = 1

    def buffer(self, app):
        return getattr(app, self.buf_attr)

    def draw(self, app, y, x, h, w, focused):
        self.h = h
        lines = self.buffer(app).all()
        out_h = max(0, h - 1)
        maxscroll = max(0, len(lines) - out_h)
        self.scrollback = max(0, min(self.scrollback, maxscroll))
        end = len(lines) - self.scrollback
        start = max(0, end - out_h)
        for i, (t, a) in enumerate(lines[start:end]):
            app.put(y + i, x, t, app.A.get(a, 0) if a else 0, w)
        if self.scrollback:
            tag = ' [scrolled: %d lines up, PgDn to return] ' % self.scrollback
            app.put(y, x + max(0, w - len(tag)), tag, app.A['warn'], w)
        p = self.prompt_text
        app.put(y + h - 1, x, p, app.A['prompt'], w)
        cx = self.edit.draw(app, y + h - 1, x + len(p), w - len(p), 0)
        if focused:
            app.cursor_req = (y + h - 1, cx)

    def key(self, app, k):
        if k == curses.KEY_PPAGE:
            self.scrollback += max(1, self.h - 2)
            return True
        if k == curses.KEY_NPAGE:
            self.scrollback = max(0, self.scrollback - max(1, self.h - 2))
            return True
        r = self.edit.key(k)
        if r == 'enter':
            text = self.edit.text
            self.edit.set('')
            self.scrollback = 0
            self.submit(app, text)
            return True
        if r == 'cancel':
            self.edit.set('')
            return True
        return bool(r)

    def scroll(self, app, n):
        self.scrollback = max(0, self.scrollback - n)

    def submit(self, app, text):
        pass


class GdbConsoleView(ConsoleView):
    def __init__(self):
        ConsoleView.__init__(self, 'Console', 'console', '(gdb) ')
        self.last = ''

    def submit(self, app, text):
        if not text.strip():
            text = self.last
            if not text:
                return
        self.last = text
        app.run_console_command(text)


class OutputView(ConsoleView):
    def __init__(self):
        ConsoleView.__init__(self, 'Output', 'output', 'stdin> ')

    def submit(self, app, text):
        app.send_program_input(text + '\n')


class StackView(ListView):
    title = 'Call Stack'
    empty_text = 'No call stack (program not paused).'

    def rows(self, app):
        return [] if app.state == 'running' else app.stack

    def draw(self, app, y, x, h, w, focused):
        self.empty_text = ('The program is running. Call stacks can be read only while it is paused: '
                           'Ctrl+C = Break All.' if app.state == 'running' else 'No call stack (program not paused).')
        ListView.draw(self, app, y, x, h, w, focused)

    def render(self, app, f, w):
        lvl = f.get('level', '?')
        cur = str(lvl) == str(app.frame.get('level', '0'))
        args = app.stack_args.get(str(lvl))
        return [(('> ' if cur else '  '), 'exec' if cur else None),
                ('#%-3s ' % lvl, 'lineno'), (frame_label(f, args), 'bold' if cur else None)]

    def activate(self, app, f):
        app.select_frame(int(f.get('level', 0)))


class ThreadsView(ListView):
    title = 'Threads'
    empty_text = 'No threads (the program is not running).'

    def on_show(self, app):
        app.refresh_threads()

    def rows(self, app):
        return app.threads

    def render(self, app, t, w):
        cur = str(t.get('id')) == str(app.thread_id)
        f = t.get('frame') or {}
        name = t.get('name') or ''
        segs = [('* ' if cur else '  ', 'exec' if cur else None),
                ('%-4s' % t.get('id', ''), 'lineno'),
                ('%-24s ' % (t.get('target-id', '') + (' "%s"' % name if name else '')), 'name'),
                ('%-8s ' % t.get('state', ''), 'ok' if t.get('state') == 'running' else 'warn'),
                ('core %-3s ' % t['core'] if t.get('core') else '', 'dim')]
        if t.get('user_where') and t.get('state') != 'running':
            segs.append(('in ' + t['user_where'] + '  ', 'name'))
            segs.append(('(' + (f.get('func') or '??') + ')', 'dim'))
        elif f:
            segs.append((frame_label(f), None))
        elif t.get('state') == 'running':
            segs.append(('(running - Ctrl+C to see where)', 'dim'))
        return segs

    def activate(self, app, t):
        if app.state == 'running':
            app.msg('Threads can be selected while the program is paused (Ctrl+C = Break All)', 'warn')
            return
        app.select_thread(t.get('id'))


class FilesView(ListView):
    """The program's source files (from its debug info) plus the open files: Enter opens / switches."""
    title = 'Files'

    def __init__(self):
        ListView.__init__(self)
        self.show_system = False

    def rows(self, app):
        sv = app.src_view
        open_paths = [t['file'].path for t in sv.tabs]
        rows = [('open', pth) for pth in open_paths]
        seen = set(open_paths)
        mine, system = [], []
        for f in app.source_files:
            full = f.get('fullname') or f.get('file')
            if not full:
                continue
            real = os.path.realpath(full) if os.path.isfile(full) else full
            if real in seen:
                continue
            seen.add(real)
            if os.path.isfile(full) and not is_system_path(full):
                mine.append(('src', real))
            elif self.show_system:
                system.append(('sys', real))
        mine.sort(key=lambda r: (os.path.basename(r[1]).lower(), r[1]))
        system.sort(key=lambda r: (os.path.basename(r[1]).lower(), r[1]))
        return rows + mine + system

    def draw(self, app, y, x, h, w, focused):
        if app.no_debug:
            self.empty_text = 'No source files: the program has no debug information (built without -g).'
        elif not app.have_program:
            self.empty_text = 'No program loaded.'
        else:
            self.empty_text = 'No source files found in the debug information (Ctrl+O browses folders).'
        ListView.draw(self, app, y, x, h, w, focused)

    def render(self, app, row, w):
        kind, path = row
        sv = app.src_view
        active = sv.file is not None and sv.file.path == path
        mark = {'open': '> ' if active else '* ', 'src': '  ', 'sys': '  '}[kind]
        tag = {'open': 'open', 'src': '', 'sys': 'system'}[kind]
        segs = [(mark, 'exec' if active else 'ok'), (os.path.basename(path), 'name' if kind != 'sys' else 'dim')]
        if tag:
            segs.append(('  [%s]' % tag, 'dim'))
        segs.append(('  ' + os.path.dirname(path), 'dim'))
        return segs

    def activate(self, app, row):
        if app.open_source(row[1]):
            app.focus = 'source'

    def row_key(self, app, k, row):
        if k == 'a':
            self.show_system = not self.show_system
            app.msg('System headers %s' % ('shown' if self.show_system else 'hidden'))
            return True
        if k in ('c', curses.KEY_DC) and row and row[0] == 'open':
            sv = app.src_view
            for i, t in enumerate(sv.tabs):
                if t['file'].path == row[1]:
                    sv.close_tab(i)
                    app.msg('Closed %s' % os.path.basename(row[1]))
                    break
            return True
        return False

    def on_show(self, app):
        app.refresh_source_files()


class BreakpointsView(ListView):
    title = 'Breakpoints'
    empty_text = "No breakpoints. Ctrl+B toggles one at the cursor, 'n' adds one here."

    def rows(self, app):
        return app.breakpoints

    def render(self, app, b, w):
        child = '.' in str(b.get('number', ''))
        en = b.get('enabled', 'y') == 'y'
        typ = b.get('type', 'breakpoint' if child else '')
        mark = '[x]' if en else '[ ]'
        loc = bp_location_text(b)
        segs = [('   ' if child else '', None), (mark + ' ', 'bp' if en else 'bpdis'),
                ('%-5s ' % b.get('number', ''), 'lineno')]
        if not child and typ not in ('breakpoint', ''):
            segs.append(('[%s] ' % typ, 'dim'))
        segs.append((loc, None))
        if b.get('cond'):
            segs.append(('  if ' + b['cond'], 'warn'))
        if b.get('ignore'):
            segs.append(('  ignore %s' % b['ignore'], 'dim'))
        if not child:
            segs.append(('  hits %s' % b.get('times', '0'), 'dim'))
        if b.get('disp') == 'del':
            segs.append(('  (temporary)', 'dim'))
        return segs

    def activate(self, app, b):
        app.goto_bp(b)

    def row_key(self, app, k, b):
        if k in ('n', 'a', curses.KEY_IC):
            app.act_location_bp()
            return True
        if b is None:
            return False
        num = str(b.get('number', ''))
        if k == ' ':
            app.cli_then_refresh('%s %s' % ('disable' if b.get('enabled') == 'y' else 'enable', num))
            return True
        if k in ('d', curses.KEY_DC) or k in KEY_BACKS:
            app.cli_then_refresh('delete %s' % num.split('.')[0])
            return True
        if k == 'c':
            app.edit_bp_condition(num.split('.')[0], b.get('cond', ''))
            return True
        if k == 'i':
            app.edit_bp_ignore(num.split('.')[0])
            return True
        return False


class ModulesView(ListView):
    title = 'Modules'
    empty_text = 'Modules (shared libraries) are listed once the program is running.'

    def rows(self, app):
        return app.modules

    def render(self, app, m, w):
        if isinstance(m, str):
            return [(m, None)]
        rng = as_list(m.get('ranges'))
        lo = rng[0].get('from', '') if rng and isinstance(rng[0], dict) else m.get('from', '')
        loaded = m.get('symbols-loaded', '')
        return [('%-18s ' % lo, 'lineno'), ('%-4s' % ('Yes' if loaded == '1' else 'No'), 'dim'),
                (m.get('target-name', m.get('host-name', '')), None)]

    def on_show(self, app):
        app.refresh_modules()


_FUNC_DEF_RE = re.compile(r'^(?:[A-Za-z_][\w:<>,\*&\s]*?[\s\*&])?(~?[A-Za-z_][\w]*(?:::~?[A-Za-z_]\w*)*)\s*\(')
_NOT_FUNCS = set('if for while switch return sizeof catch else do case'.split())


def guess_function(lines, idx):
    """Best-effort: name of the C/C++ function whose body contains line idx."""
    for i in range(min(idx, len(lines) - 1), -1, -1):
        text = lines[i]
        if not text or text[0] in ' \t#/*':
            continue
        if text.startswith('}') and i != idx:
            return None
        m = _FUNC_DEF_RE.match(text)
        if m and m.group(1) not in _NOT_FUNCS and ';' not in text.split(')')[-1]:
            return m.group(1)
    return None


# --------------------------------------------------------------------------
# System calls: argument formatting, strace log parsing, ELF helpers
# --------------------------------------------------------------------------
SYSCALL_ARGS = {
    'read': 'fd,obuf,n', 'write': 'fd,ibuf,n', 'pread64': 'fd,obuf,n,n', 'pwrite64': 'fd,ibuf,n,n',
    'open': 'path,oflags,mode', 'openat': 'dirfd,path,oflags,mode', 'creat': 'path,mode', 'close': 'fd',
    'stat': 'path,ptr', 'lstat': 'path,ptr', 'fstat': 'fd,ptr', 'newfstatat': 'dirfd,path,ptr,hex',
    'statx': 'dirfd,path,hex,hex,ptr', 'access': 'path,int', 'faccessat': 'dirfd,path,int',
    'faccessat2': 'dirfd,path,int,hex', 'lseek': 'fd,int,int', 'mmap': 'ptr,n,hex,hex,fd,hex',
    'munmap': 'ptr,n', 'mprotect': 'ptr,n,hex', 'brk': 'ptr', 'ioctl': 'fd,hex,ptr', 'readv': 'fd,ptr,n',
    'writev': 'fd,ptr,n', 'pipe': 'ptr', 'pipe2': 'ptr,hex', 'dup': 'fd', 'dup2': 'fd,fd', 'dup3': 'fd,fd,hex',
    'socket': 'int,int,int', 'connect': 'fd,ptr,n', 'accept': 'fd,ptr,ptr', 'accept4': 'fd,ptr,ptr,hex',
    'bind': 'fd,ptr,n', 'listen': 'fd,int', 'sendto': 'fd,ibuf,n,hex,ptr,n',
    'recvfrom': 'fd,obuf,n,hex,ptr,ptr', 'sendmsg': 'fd,ptr,hex', 'recvmsg': 'fd,ptr,hex', 'shutdown': 'fd,int',
    'execve': 'path,ptr,ptr', 'exit': 'int', 'exit_group': 'int', 'kill': 'int,int', 'tgkill': 'int,int,int',
    'getpid': '', 'gettid': '', 'getppid': '', 'getuid': '', 'geteuid': '', 'sched_yield': '',
    'unlink': 'path', 'unlinkat': 'dirfd,path,hex', 'mkdir': 'path,mode', 'mkdirat': 'dirfd,path,mode',
    'rmdir': 'path', 'chdir': 'path', 'rename': 'path,path', 'renameat2': 'dirfd,path,dirfd,path,hex',
    'readlink': 'path,obuf,n', 'readlinkat': 'dirfd,path,obuf,n', 'getcwd': 'obuf,n', 'chmod': 'path,mode',
    'fchmod': 'fd,mode', 'nanosleep': 'ptr,ptr', 'clock_nanosleep': 'int,int,ptr,ptr',
    'futex': 'ptr,int,int,ptr,ptr,int', 'clone': 'hex,ptr,ptr,ptr,hex', 'clone3': 'ptr,n',
    'wait4': 'int,ptr,hex,ptr', 'poll': 'ptr,n,int', 'ppoll': 'ptr,n,ptr,ptr,n', 'select': 'n,ptr,ptr,ptr,ptr',
    'epoll_wait': 'fd,ptr,n,int', 'epoll_pwait': 'fd,ptr,n,int,ptr,n', 'rt_sigaction': 'int,ptr,ptr,n',
    'rt_sigprocmask': 'int,ptr,ptr,n', 'getrandom': 'obuf,n,hex', 'set_robust_list': 'ptr,n',
    'rseq': 'ptr,n,hex,hex', 'prlimit64': 'int,int,ptr,ptr', 'arch_prctl': 'hex,ptr', 'set_tid_address': 'ptr',
    'madvise': 'ptr,n,int', 'fcntl': 'fd,int,hex', 'getdents64': 'fd,ptr,n', 'ftruncate': 'fd,n',
    'fsync': 'fd', 'truncate': 'path,n', 'mremap': 'ptr,n,n,hex,ptr',
}
SYSCALL_HEX_RET = ('mmap', 'brk', 'mremap', 'shmat')
KERNEL_RESTART = {512: ('ERESTARTSYS', 'To be restarted if SA_RESTART is set'),
                  513: ('ERESTARTNOINTR', 'To be restarted'),
                  514: ('ERESTARTNOHAND', 'To be restarted if no handler'),
                  516: ('ERESTART_RESTARTBLOCK', 'Interrupted by signal')}
OPEN_FLAGS = [(0o100, 'O_CREAT'), (0o200, 'O_EXCL'), (0o400, 'O_NOCTTY'), (0o1000, 'O_TRUNC'),
              (0o2000, 'O_APPEND'), (0o4000, 'O_NONBLOCK'), (0o10000, 'O_DSYNC'), (0o200000, 'O_DIRECTORY'),
              (0o400000, 'O_NOFOLLOW'), (0o2000000, 'O_CLOEXEC'), (0o10000000, 'O_PATH')]
# registers holding the syscall arguments / return value, by gdb architecture name
SYSCALL_REGS = [
    ('x86-64', ['$rdi', '$rsi', '$rdx', '$r10', '$r8', '$r9'], '$rax', 64),
    ('aarch64', ['$x0', '$x1', '$x2', '$x3', '$x4', '$x5'], '$x0', 64),
    ('i386', ['$ebx', '$ecx', '$edx', '$esi', '$edi', '$ebp'], '$eax', 32),
    ('arm', ['$r0', '$r1', '$r2', '$r3', '$r4', '$r5'], '$r0', 32),
]
PATH_SYSCALLS_RE = None


def syscall_regs(arch):
    for key, args, ret, bits in SYSCALL_REGS:
        if key in (arch or ''):
            return args, ret, bits
    return SYSCALL_REGS[0][1], SYSCALL_REGS[0][2], SYSCALL_REGS[0][3]


def to_signed(v, bits=64):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def fmt_open_flags(v):
    acc = {0: 'O_RDONLY', 1: 'O_WRONLY', 2: 'O_RDWR'}.get(v & 3, str(v & 3))
    parts = [acc]
    rest = v & ~3
    for bit, name in OPEN_FLAGS:
        if rest & bit:
            parts.append(name)
            rest &= ~bit
    if rest:
        parts.append('0x%x' % rest)
    return '|'.join(parts)


def escape_bytes(data, limit=32):
    out = []
    for b in data[:limit]:
        if b == 10:
            out.append('\\n')
        elif b == 9:
            out.append('\\t')
        elif b == 13:
            out.append('\\r')
        elif b == 34:
            out.append('\\"')
        elif b == 92:
            out.append('\\\\')
        elif 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append('\\x%02x' % b)
    return '"' + ''.join(out) + '"' + ('...' if len(data) > limit else '')


def fmt_syscall_ret(name, v, bits=64):
    sv = to_signed(v, bits)
    if -sv in KERNEL_RESTART:
        # kernel-internal "restart this call" codes (seen because the debugger paused the thread)
        return '? %s (%s)' % KERNEL_RESTART[-sv], False
    if -4095 <= sv < 0:
        e = -sv
        return '-1 %s (%s)' % (errno.errorcode.get(e, 'E%d' % e), os.strerror(e)), True
    if name in SYSCALL_HEX_RET:
        return '0x%x' % (v & ((1 << bits) - 1)), False
    return str(sv), False


def elf_load_base(path):
    """Virtual address the first PT_LOAD segment maps file offset 0 to (0 for PIE / shared objects)."""
    try:
        with open(path, 'rb') as f:
            hdr = f.read(64)
            if hdr[:4] != b'\x7fELF':
                return 0
            is64 = hdr[4] == 2
            e = '<' if hdr[5] == 1 else '>'
            if is64:
                phoff = struct.unpack(e + 'Q', hdr[32:40])[0]
                phentsize, phnum = struct.unpack(e + 'HH', hdr[54:58])
            else:
                phoff = struct.unpack(e + 'I', hdr[28:32])[0]
                phentsize, phnum = struct.unpack(e + 'HH', hdr[42:46])
            f.seek(phoff)
            ph = f.read(phentsize * phnum)
        for i in range(phnum):
            p = ph[i * phentsize:(i + 1) * phentsize]
            if struct.unpack(e + 'I', p[:4])[0] != 1:
                continue
            if is64:
                off, vaddr = struct.unpack(e + 'QQ', p[8:24])
            else:
                off, vaddr = struct.unpack(e + 'II', p[4:12])
            return vaddr - off
    except (IOError, OSError, struct.error, IndexError):
        pass
    return 0


_ST_PREFIX = re.compile(r'^(?:(\d+)\s+)?(?:(\d{2}:\d{2}:\d{2}(?:\.\d+)?|\d{9,}\.\d+)\s+)?(.*)$')
_ST_CALL = re.compile(r'^(\w+)\((.*)\)\s+=\s+(.+?)(?:\s+<(\d+\.\d+)>)?$')
_ST_UNFINISHED = re.compile(r'^(\w+)\((.*?)\s*<unfinished \.\.\.>$')
_ST_RESUMED = re.compile(r'^<\.\.\. (\w+) resumed>\s?(.*)$')
_ST_STACK = re.compile(r'^\s*>\s+(\S.*?)\s+\[(0x[0-9a-fA-F]+)\]\s*$')


def strace_time(ts):
    if not ts:
        return None
    if ':' in ts:
        h, m, sec = ts.split(':')
        return int(h) * 3600 + int(m) * 60 + float(sec)
    return float(ts)


class StraceParser(object):
    """Incremental parser for strace -o output (with or without -f, -tt, -T, -k)."""

    def __init__(self):
        self.tail = ''
        self.pending = {}
        self.last = None
        self.t0 = None

    def feed(self, text, final=False):
        text = self.tail + text
        lines = text.split('\n')
        self.tail = '' if final else lines.pop()
        out = []
        for line in lines:
            line = line.rstrip('\r')
            if line.strip():
                rec = self.parse_line(line)
                if rec is not None:
                    out.append(rec)
        return out

    def parse_line(self, line):
        m = _ST_STACK.match(line)
        if m:
            if self.last is not None:
                text, off = m.group(1), int(m.group(2), 16)
                i = text.find('(')
                if i > 0 and text.endswith(')'):
                    module, symoff = text[:i], text[i + 1:-1]
                else:
                    module, symoff = text, ''
                self.last.setdefault('stack', []).append('%s(%s)' % (os.path.basename(module), symoff))
                self.last.setdefault('sites', []).append((module, symoff, off))
            return None
        m = _ST_PREFIX.match(line)
        pid, ts, body = m.group(1), m.group(2), m.group(3)
        t = strace_time(ts)
        if t is not None and self.t0 is None:
            self.t0 = t
        rel = (t - self.t0) if t is not None else None
        rec = {'src': 'strace', 'tid': pid or '', 't': rel}
        r = _ST_RESUMED.match(body)
        if r:
            prev = self.pending.pop(pid, None)
            name = r.group(1)
            head = (prev['args'] if prev else '').rstrip()
            rest = r.group(2)
            if head and not rest.startswith(')'):
                head += ' ' if head.endswith(',') else ', '
            full = '%s(%s%s' % (name, head, rest)
            c = _ST_CALL.match(full)
            target = prev if prev is not None else rec
            target['name'] = name
            if c:
                target['args'], target['ret'], target['dur'] = c.group(2), c.group(3), c.group(4)
            else:
                target['args'] = full[len(name) + 1:]
                target['ret'] = '?'
            target['err'] = (target.get('ret') or '').startswith('-1')
            self.last = target
            return None if prev is not None else target
        u = _ST_UNFINISHED.match(body)
        if u:
            rec.update({'name': u.group(1), 'args': u.group(2), 'ret': '...', 'err': False})
            self.pending[pid] = rec
            self.last = rec
            return rec
        if body.startswith('---') or body.startswith('+++'):
            rec.update({'note': body.strip('-+ ').strip()})
            self.last = None
            return rec
        c = _ST_CALL.match(body)
        if c:
            rec.update({'name': c.group(1), 'args': c.group(2), 'ret': c.group(3), 'dur': c.group(4)})
            rec['err'] = rec['ret'].startswith('-1')
            self.last = rec
            return rec
        rec['note'] = body
        self.last = None
        return rec


class SyscallsView(ListView):
    title = 'Syscalls'
    empty_text = ''

    def draw(self, app, y, x, h, w, focused):
        if app.sysrec:
            self.empty_text = ('Recording system calls%s - they appear here while the program runs '
                               '(Ctrl+R to run / continue).   r = stop recording' %
                               (' (%s)' % app.sysrec['filter'] if app.sysrec.get('filter') else ''))
        elif app.sys_filter and app.syscalls:
            self.empty_text = 'No calls match the filter "%s" (f to change it).' % app.sys_filter
        else:
            self.empty_text = ('No system calls recorded.   r = start live recording (this debug session)   '
                               't = run the program under strace   (both also in Ctrl+X)')
        ListView.draw(self, app, y, x, h, w, focused)

    def rows(self, app):
        f = app.sys_filter
        if not f:
            return app.syscalls
        return [r for r in app.syscalls if f in app.syscall_text(r).lower()]

    def render(self, app, r, w):
        segs = [('%6d ' % r.get('n', 0), 'lineno'), ('g ' if r['src'] == 'gdb' else 's ', 'dim')]
        t = r.get('t')
        segs.append(('%9.4f ' % t if t is not None else '          ', 'dim'))
        if r.get('tid'):
            segs.append(('[%s] ' % r['tid'], 'dim'))
        if r.get('note'):
            segs.append((r['note'], 'warn'))
            return segs
        where = (r.get('where') or {}).get('text', '')
        call = '%s(%s)' % (r.get('name', '?'), r.get('args', ''))
        ret = r.get('ret')
        used = sum(len(t[0]) for t in segs)
        wherew = min(len(where) + 2, max(0, (w - used) // 3)) if where else 0
        room = max(10, w - used - wherew)
        retxt = ' = %s' % ret if ret is not None else ' = ...'
        if len(call) + len(retxt) > room:
            call = call[:max(5, room - len(retxt) - 3)] + '...'
        dim = 'dim' if r.get('restart') else None
        segs.append((r.get('name', '?'), dim or 'kw'))
        segs.append((call[len(r.get('name', '?')):], dim))
        segs.append((retxt, dim or ('err' if r.get('err') else 'ok')))
        if wherew:
            pad = max(1, w - used - len(call) - len(retxt) - wherew + 2)
            segs.append((' ' * pad + where[:wherew - 2], 'name'))
        return segs

    def activate(self, app, r):
        app.goto_syscall(r)

    def row_key(self, app, k, r):
        if k == 'r':
            app.act_syscall_record()
            return True
        if k == 't':
            app.act_strace_run()
            return True
        if k == 'f':
            f = app.prompt('Filter system calls (text in name, arguments or location; empty = all)',
                           app.sys_filter, 'sysfilter')
            if f is not None:
                app.sys_filter = f.strip().lower()
                self.sel = 0
            return True
        if k == 'x':
            app.clear_syscalls()
            return True
        if k == 's':
            app.act_syscall_summary()
            return True
        if k == 'w':
            app.save_syscalls()
            return True
        if r is None:
            return False
        if k == 'i':
            app.syscall_details(r)
            return True
        if k == 'b' and r.get('name'):
            app.cli_then_refresh('catch syscall %s' % r['name'])
            app.msg('The program will stop at every %s() call (see Breakpoints)' % r['name'])
            return True
        return False

    def scroll(self, app, n):
        ListView.scroll(self, app, n)


SYSTEM_SOURCE_PREFIXES = ('/usr/', '/lib/', '/lib64/', '/nix/store/', '/gnu/store/')


def is_system_path(path):
    """Compiler / OS / library sources (external to the user's program)."""
    return path.startswith(SYSTEM_SOURCE_PREFIXES) or path.startswith(('./', '../'))


def frame_label(f, args=None):
    func = f.get('func') or '??'
    if args is not None:
        parts = []
        for a in as_list(args):
            if isinstance(a, dict):
                if 'value' in a:
                    parts.append('%s=%s' % (a.get('name', '?'), a.get('value')))
                else:
                    parts.append('%s' % a.get('name', '?'))
        s = '%s(%s)' % (func, ', '.join(parts))
    else:
        s = func + '()'
    if f.get('file'):
        s += ' at %s:%s' % (f.get('file'), f.get('line', '?'))
    else:
        if f.get('addr'):
            s = '%s in %s' % (f.get('addr'), s)
        if f.get('from'):
            s += ' from %s' % f['from']
    return s


def bp_location_text(b):
    typ = b.get('type', '')
    if 'watchpoint' in typ:
        return 'watch ' + b.get('exp', b.get('what', ''))
    if typ == 'catchpoint':
        return 'catch ' + b.get('what', b.get('catch-type', ''))
    if b.get('pending'):
        return '%s (pending)' % b.get('pending')
    if b.get('file') and b.get('line'):
        s = '%s:%s' % (b.get('file'), b.get('line'))
        if b.get('func'):
            s = '%s  in %s' % (s, b['func'])
        return s
    if b.get('addr') == '<MULTIPLE>':
        return '%s (multiple locations)' % b.get('original-location', '')
    return b.get('original-location') or b.get('what') or b.get('addr', '')


class SourceView(View):
    """Source / disassembly editor-like view."""
    title = 'Source'

    MAX_TABS = 50

    def __init__(self):
        self.file = None
        self.cur = 0
        self.col = 0
        self.top = 0
        self.hscroll = 0
        self.h = 1
        self.tabs = []           # open files: {'file', 'cur', 'col', 'top', 'hscroll', 'used'}
        self.active_tab = -1
        self.use_seq = 0
        self.dis_rows = []
        self.dis_cur = 0
        self.dis_top = 0
        self.center_pending = False
        self.gutter_w = 0

    # ---- open files (tabs); the active tab's position lives in self.cur/col/top/hscroll
    def _save_tab(self):
        if 0 <= self.active_tab < len(self.tabs):
            self.tabs[self.active_tab].update(file=self.file, cur=self.cur, col=self.col, top=self.top,
                                              hscroll=self.hscroll)

    def _load_tab(self, i):
        self.active_tab = i
        t = self.tabs[i]
        self.file = t['file']
        self.cur, self.col, self.top, self.hscroll = t['cur'], t['col'], t['top'], t['hscroll']
        self.use_seq += 1
        t['used'] = self.use_seq

    def set_file(self, f):
        """Show file f: switch to its tab, or open a new tab next to the current one."""
        if self.file is not None and f.path == self.file.path:
            self.file = f                       # same file (possibly re-read from disk): keep position
            if 0 <= self.active_tab < len(self.tabs):
                self.tabs[self.active_tab]['file'] = f
            return
        self._save_tab()
        for i, t in enumerate(self.tabs):
            if t['file'].path == f.path:
                t['file'] = f
                self._load_tab(i)
                return
        tab = {'file': f, 'cur': 0, 'col': 0, 'top': 0, 'hscroll': 0, 'used': 0}
        self.tabs.insert(self.active_tab + 1, tab)
        self._load_tab(self.active_tab + 1)
        while len(self.tabs) > self.MAX_TABS:          # close the least recently used tab
            victim = min((t for t in self.tabs if t is not tab), key=lambda t: t['used'])
            i = self.tabs.index(victim)
            self.tabs.pop(i)
            if i < self.active_tab:
                self.active_tab -= 1

    def add_tab(self, f):
        """Open f as a tab at the end without switching to it (or activate it if nothing is open)."""
        if any(t['file'].path == f.path for t in self.tabs) or len(self.tabs) >= self.MAX_TABS:
            return False
        if not self.tabs:
            self.set_file(f)
            return True
        self.tabs.append({'file': f, 'cur': 0, 'col': 0, 'top': 0, 'hscroll': 0, 'used': 0})
        return True

    def activate_tab(self, i):
        if 0 <= i < len(self.tabs) and i != self.active_tab:
            self._save_tab()
            self._load_tab(i)

    def cycle_tab(self, delta):
        if len(self.tabs) > 1:
            self.activate_tab((self.active_tab + delta) % len(self.tabs))

    def close_tab(self, i=None):
        if not self.tabs:
            return
        self._save_tab()
        i = self.active_tab if i is None else i
        self.tabs.pop(i)
        if not self.tabs:
            self.active_tab = -1
            self.file = None
            return
        if i < self.active_tab or self.active_tab >= len(self.tabs):
            self.active_tab = max(0, self.active_tab - 1)
        self.active_tab = min(self.active_tab, len(self.tabs) - 1)
        self._load_tab(self.active_tab)

    def close_other_tabs(self):
        if 0 <= self.active_tab < len(self.tabs):
            self._save_tab()
            self.tabs = [self.tabs[self.active_tab]]
            self._load_tab(0)

    def tab_labels(self):
        names = [os.path.basename(t['file'].path) for t in self.tabs]
        out = []
        for t, n in zip(self.tabs, names):
            if names.count(n) > 1:      # same file name in two folders: add the folder
                n = os.path.join(os.path.basename(os.path.dirname(t['file'].path)), n)
            out.append(n)
        return out

    def goto_line(self, line, center=True):
        if not self.file:
            return
        self.cur = max(0, min(line, len(self.file.lines) - 1))
        if center:
            self.center_pending = True

    def set_dis(self, rows):
        self.dis_rows = rows
        self.dis_cur = 0
        self.dis_top = 0

    def goto_addr(self, addr, center=True):
        for i, r in enumerate(self.dis_rows):
            if r.get('addr') == addr:
                self.dis_cur = i
                if center:
                    self.center_pending = True
                return True
        return False

    def selected_dis(self):
        if not self.dis_rows:
            return None
        i = max(0, min(self.dis_cur, len(self.dis_rows) - 1))
        for j in list(range(i, len(self.dis_rows))) + list(range(i - 1, -1, -1)):
            if self.dis_rows[j].get('addr') is not None:
                return self.dis_rows[j]
        return None

    def word_at_cursor(self):
        if not self.file:
            return ''
        line = self.file.lines[self.cur] if self.cur < len(self.file.lines) else ''
        best = ''
        for m in re.finditer(r'[A-Za-z_]\w*(?:(?:\.|->)[A-Za-z_]\w*|\[[^\]]*\])*', line):
            if m.start() <= self.col <= m.end():
                return m.group()
            if not best and m.start() > self.col:
                best = m.group()
        return best

    def draw(self, app, y, x, h, w, focused):
        self.h = h
        if app.disasm:
            self.draw_dis(app, y, x, h, w, focused)
            return
        f = self.file
        if f is None:
            msg = ['No source file loaded.', '',
                   'Ctrl+O open a source file    Ctrl+X command menu',
                   'Ctrl+R run the program       Ctrl+_ help (Ctrl+/)']
            for i, m in enumerate(msg):
                app.put(y + 1 + i, x + 2, m, app.A['dim'], w - 2)
            return
        n = len(f.lines)
        nd = max(4, len(str(n)))
        gw = 3 + nd + 1
        self.gutter_w = gw
        tw = max(1, w - gw)
        self.cur = max(0, min(self.cur, n - 1))
        if self.center_pending:
            self.top = max(0, self.cur - h // 3)
            self.center_pending = False
        if self.cur < self.top:
            self.top = self.cur
        if self.cur >= self.top + h:
            self.top = self.cur - h + 1
        line_len = len(f.lines[self.cur])
        self.col = max(0, min(self.col, line_len))
        if self.col < self.hscroll:
            self.hscroll = self.col
        if self.col >= self.hscroll + tw:
            self.hscroll = self.col - tw + 1
        bpl = app.bp_lines_for(f.path)
        sysl = app.sys_lines.get(f.path)
        exec_line = app.exec_line if app.exec_path == f.path else None
        A = app.A
        for row in range(h):
            idx = self.top + row
            if idx >= n:
                break
            ln = idx + 1
            yy = y + row
            bp = bpl.get(ln)
            if bp:
                app.put(yy, x, bp[0], A['bp'] if bp[1] else A['bpdis'])
            if exec_line == ln:
                app.put(yy, x + 1, '=>', A['arrow'] if app.exec_top else A['arrow2'])
            num_attr = A['lineno']
            if idx == self.cur:
                num_attr = A['curlineno'] if focused else A['lineno'] | curses.A_BOLD
            app.put(yy, x + 3, str(ln).rjust(nd), num_attr)
            text = f.lines[idx]
            if exec_line == ln:
                attr = A['exec'] if app.exec_top else A['exec2']
                app.put(yy, x + gw, text[self.hscroll:].ljust(tw), attr, tw)
                continue
            if idx == self.cur and focused:
                app.put(yy, x + gw, ' ' * tw, A['curline'], tw)
                base = A['curline']
            else:
                base = 0
            if app.syntax:
                segs, _ = highlight(text, f.state(idx))
            else:
                segs = [(text, None)]
            app.draw_segs(yy, x + gw, tw, segs, base, skip=self.hscroll, keep_base_color=bool(base))
            if sysl and sysl.get(ln):
                note = '  << ' + app.syscall_line_note(sysl[ln])
                nx = len(text.rstrip()) - self.hscroll
                if 0 <= nx < tw - 8:
                    app.put(yy, x + gw + nx, note, A['name'] | (base and curses.A_REVERSE), tw - nx)
        if focused:
            app.cursor_req = (y + self.cur - self.top, x + gw + self.col - self.hscroll)

    def draw_dis(self, app, y, x, h, w, focused):
        rows = self.dis_rows
        A = app.A
        if not rows:
            app.put(y + 1, x + 2, app.dis_error or 'No disassembly available (program not paused?).', A['dim'], w - 2)
            return
        n = len(rows)
        self.dis_cur = max(0, min(self.dis_cur, n - 1))
        if self.center_pending:
            self.dis_top = max(0, self.dis_cur - h // 3)
            self.center_pending = False
        if self.dis_cur < self.dis_top:
            self.dis_top = self.dis_cur
        if self.dis_cur >= self.dis_top + h:
            self.dis_top = self.dis_cur - h + 1
        self.gutter_w = 3
        for row in range(h):
            idx = self.dis_top + row
            if idx >= n:
                break
            r = rows[idx]
            yy = y + row
            addr = r.get('addr')
            if addr is not None:
                bp = app.bp_addrs.get(addr)
                if bp:
                    app.put(yy, x, bp[0], A['bp'] if bp[1] else A['bpdis'])
                if addr == app.exec_addr:
                    app.put(yy, x + 1, '=>', A['arrow'] if app.exec_top else A['arrow2'])
            text = r.get('text', '')
            if addr is not None and addr == app.exec_addr:
                attr = A['exec'] if app.exec_top else A['exec2']
            elif idx == self.dis_cur and focused:
                attr = A['curline']
            elif addr is None:
                attr = A['com']
            else:
                attr = 0
            app.put(yy, x + 3, text.ljust(w - 3), attr, w - 3)

    def key(self, app, k):
        if app.disasm:
            n = len(self.dis_rows)
            if k in (curses.KEY_UP, curses.KEY_PPAGE) and self.dis_cur == 0 and n:
                app.extend_disasm(-1)
            if k in (curses.KEY_DOWN, curses.KEY_NPAGE) and self.dis_cur >= n - 1 and n:
                app.extend_disasm(1)
            if k == curses.KEY_UP:
                self.dis_cur = max(0, self.dis_cur - 1)
            elif k == curses.KEY_DOWN:
                self.dis_cur = min(max(0, n - 1), self.dis_cur + 1)
            elif k == curses.KEY_PPAGE:
                self.dis_cur = max(0, self.dis_cur - (self.h - 1))
            elif k == curses.KEY_NPAGE:
                self.dis_cur = min(max(0, n - 1), self.dis_cur + (self.h - 1))
            elif k == curses.KEY_HOME:
                self.dis_cur = 0
            elif k == curses.KEY_END:
                self.dis_cur = max(0, n - 1)
            else:
                return False
            return True
        if not self.file:
            return False
        n = len(self.file.lines)
        if k == curses.KEY_UP:
            self.cur = max(0, self.cur - 1)
        elif k == curses.KEY_DOWN:
            self.cur = min(n - 1, self.cur + 1)
        elif k == curses.KEY_LEFT:
            if self.col > 0:
                self.col = min(self.col, len(self.file.lines[self.cur])) - 1
            elif self.cur > 0:
                self.cur -= 1
                self.col = len(self.file.lines[self.cur])
        elif k == curses.KEY_RIGHT:
            if self.col < len(self.file.lines[self.cur]):
                self.col += 1
            elif self.cur < n - 1:
                self.cur += 1
                self.col = 0
        elif k == curses.KEY_PPAGE:
            self.cur = max(0, self.cur - (self.h - 1))
            self.top = max(0, self.top - (self.h - 1))
        elif k == curses.KEY_NPAGE:
            self.cur = min(n - 1, self.cur + (self.h - 1))
            self.top = min(max(0, n - self.h), self.top + (self.h - 1))
        elif k == curses.KEY_HOME:
            line = self.file.lines[self.cur]
            ind = len(line) - len(line.lstrip())
            self.col = 0 if self.col == ind else ind
        elif k == curses.KEY_END:
            self.col = len(self.file.lines[self.cur])
        elif k in (getattr(curses, 'KEY_SHOME', -1), 535, 536):   # ctrl-home variants
            self.cur = 0
        elif k in (getattr(curses, 'KEY_SEND', -1), 530, 531):
            self.cur = n - 1
        else:
            return False
        return True

    def click(self, app, row, col, double=False):
        if app.disasm:
            idx = self.dis_top + row
            if 0 <= idx < len(self.dis_rows):
                self.dis_cur = idx
                if col < 3:
                    app.act_toggle_bp()
            return
        if not self.file:
            return
        idx = self.top + row
        if 0 <= idx < len(self.file.lines):
            self.cur = idx
            self.col = max(0, col - self.gutter_w + self.hscroll)
            if col < self.gutter_w:
                app.act_toggle_bp()
            elif double:
                app.act_quickwatch()

    def scroll(self, app, n):
        if app.disasm:
            self.dis_cur = max(0, min(len(self.dis_rows) - 1, self.dis_cur + n))
            return
        if self.file:
            total = len(self.file.lines)
            self.top = max(0, min(max(0, total - self.h), self.top + n))
            self.cur = max(self.top, min(self.top + self.h - 1, self.cur))


# --------------------------------------------------------------------------
# Popups (modal dialogs)
# --------------------------------------------------------------------------
class Popup(object):
    def draw(self, app):
        pass

    def key(self, app, k):
        return None


class InputPopup(Popup):
    def __init__(self, title, initial='', history=None, hint=''):
        self.title = title
        self.edit = LineEdit(initial or '', history)
        self.edit.fresh = bool(initial)
        self.hint = hint

    def draw(self, app):
        w = min(app.W - 4, max(60, len(self.title) + 6, len(self.hint) + 6))
        h = 5 if self.hint else 4
        y = max(0, (app.H - h) // 3)
        x = max(0, (app.W - w) // 2)
        app.box(y, x, h, w, self.title)
        cx = self.edit.draw(app, y + 1, x + 2, w - 4, app.A['field'])
        if self.hint:
            app.put(y + 2, x + 2, self.hint, app.A['dim'], w - 4)
        app.put(y + h - 2, x + 2, 'Enter = OK    Esc = Cancel', app.A['dim'], w - 4)
        app.cursor_req = (y + 1, cx)

    def key(self, app, k):
        if k in (KEY_ESC, 3):
            return ('done', None)
        r = self.edit.key(k)
        if r == 'enter':
            return ('done', self.edit.text)
        if r == 'cancel':
            return ('done', None)
        return None


class ListPopup(Popup):
    def __init__(self, title, items, allow_free=False, hint=''):
        self.title = title
        self.items = items          # [(label, value, right_hint)]
        self.filtered = list(items)
        self.edit = LineEdit('')
        self.sel = 0
        self.top = 0
        self.allow_free = allow_free
        self.hint = hint
        self.lh = 1

    def refilter(self):
        words = self.edit.text.lower().split()
        self.filtered = [it for it in self.items
                         if all(wd in (it[0] + ' ' + (it[2] or '')).lower() for wd in words)]
        self.sel = 0
        self.top = 0

    def draw(self, app):
        longest = max([len(it[0]) + len(it[2] or '') + 4 for it in self.items] + [40, len(self.title) + 2])
        w = min(app.W - 4, max(50, longest + 6))
        h = min(app.H - 2, max(8, len(self.items) + 5))
        y = max(0, (app.H - h) // 2)
        x = max(0, (app.W - w) // 2)
        app.box(y, x, h, w, self.title)
        app.put(y + 1, x + 2, 'Filter: ', app.A['bold'])
        cx = self.edit.draw(app, y + 1, x + 10, w - 12, app.A['field'])
        lh = h - 4
        self.lh = lh
        n = len(self.filtered)
        self.sel = max(0, min(self.sel, n - 1))
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + lh:
            self.top = self.sel - lh + 1
        for i in range(lh):
            idx = self.top + i
            if idx >= n:
                break
            label, _v, right = self.filtered[idx]
            attr = app.A['popsel'] if idx == self.sel else app.A['popup']
            line = ' ' + label
            right = right or ''
            avail = w - 4
            if len(right) > avail // 2:
                right = '...' + right[-(avail // 2 - 3):]
            if right:
                line = line[:max(0, avail - len(right) - 2)].ljust(avail - len(right)) + right
            app.put(y + 2 + i, x + 2, line.ljust(avail), attr, avail)
        foot = self.hint or 'Type to filter   Up/Down select   Enter = OK   Esc = Cancel'
        if n == 0:
            app.put(y + 2, x + 3, '(no matches)' + ('  - Enter uses typed text' if self.allow_free else ''),
                    app.A['dim'], w - 6)
        app.put(y + h - 2, x + 2, foot, app.A['dim'], w - 4)
        app.cursor_req = (y + 1, cx)

    def key(self, app, k):
        if k in (KEY_ESC, 3):
            return ('done', None)
        n = len(self.filtered)
        if k == curses.KEY_UP:
            self.sel = max(0, self.sel - 1)
            return None
        if k == curses.KEY_DOWN:
            self.sel = min(max(0, n - 1), self.sel + 1)
            return None
        if k == curses.KEY_PPAGE:
            self.sel = max(0, self.sel - self.lh)
            return None
        if k == curses.KEY_NPAGE:
            self.sel = min(max(0, n - 1), self.sel + self.lh)
            return None
        if k in KEY_ENTERS:
            if self.allow_free and self.edit.text and (n == 0 or self.edit.text.startswith(('/', '.', '~'))):
                return ('done', ('free', self.edit.text))
            if n:
                return ('done', self.filtered[self.sel][1])
            return None
        old = self.edit.text
        self.edit.key(k)
        if self.edit.text != old:
            self.refilter()
        return None


SOURCE_EXTS = ('.c', '.cc', '.cpp', '.cxx', '.c++', '.h', '.hh', '.hpp', '.hxx', '.h++', '.inl', '.ipp',
               '.tcc', '.tpp', '.s', '.S', '.asm', '.f', '.f90', '.rs', '.go', '.m', '.mm', '.cu', '.d',
               '.y', '.l', '.java', '.py')


class FilePopup(ListPopup):
    """File browser that starts in the program's directory, plus the files listed in the debug info."""

    def __init__(self, start_dir, debug_files, open_files=()):
        self.open_files = list(open_files)
        ListPopup.__init__(self, '', [], allow_free=True,
                           hint='Enter = open / enter folder   Backspace (empty filter) = up   '
                                'type a path   Esc = cancel')
        self.debug_files = debug_files
        self.load_dir(start_dir)

    def load_dir(self, d):
        self.dir = os.path.realpath(d)
        self.title = 'Open Source File - ' + self.dir
        items = [(os.path.basename(f), ('file', f), '[open] ' + os.path.dirname(f)) for f in self.open_files]
        # the program's own source files (from its debug info) come right after the open files
        shown = set(self.open_files)
        own = sorted(set(f for f in self.debug_files if os.path.isfile(f) and not is_system_path(f)
                         and os.path.realpath(f) not in shown),
                     key=lambda f: os.path.basename(f).lower())
        for f in own:
            items.append((os.path.basename(f), ('file', f), '[source] ' + os.path.dirname(f)))
        self.own_files = set(own)
        n_open = len(items)
        items.append(('../', ('dir', os.path.dirname(self.dir)), 'parent folder'))
        try:
            names = sorted(os.listdir(self.dir), key=lambda n: n.lower())
        except OSError as e:
            names = []
            items.append(('(cannot list folder: %s)' % e.strerror, ('dir', self.dir), ''))
        dirs = []
        files = []
        for n in names:
            if n.startswith('.'):
                continue
            pth = os.path.join(self.dir, n)
            if os.path.isdir(pth):
                dirs.append((n + '/', ('dir', pth), 'folder'))
            else:
                files.append((n, ('file', pth), 'source' if n.endswith(SOURCE_EXTS) else ''))
        files.sort(key=lambda i: (not i[0].endswith(SOURCE_EXTS), i[0].lower()))
        items.extend(dirs)
        items.extend(files)
        dbg = sorted(set(self.debug_files) - self.own_files,
                     key=lambda f: (not os.path.isfile(f), is_system_path(f), os.path.basename(f).lower()))
        for f in dbg:
            items.append((os.path.basename(f), ('file', f), '[debug info] ' + os.path.dirname(f)))
        self.items = items
        self.edit.set('')
        self.refilter()
        if own or self.open_files:
            self.sel = len(self.open_files)            # first program source file that isn't open yet
        elif len(items) > n_open + 1:
            self.sel = n_open + 1 + len(dirs) if files else n_open + 1

    def key(self, app, k):
        if k in KEY_BACKS and not self.edit.text:
            self.load_dir(os.path.dirname(self.dir))
            return None
        res = ListPopup.key(self, app, k)
        if not res or res[1] is None:
            return res
        kind, val = res[1]
        if kind == 'free':
            path = os.path.expanduser(val)
            if not os.path.isabs(path):
                path = os.path.join(self.dir, path)
            if os.path.isdir(path):
                self.load_dir(path)
                return None
            return ('done', path)
        if kind == 'dir':
            self.load_dir(val)
            return None
        return ('done', val)


def elf_is_pie(path):
    """True for position-independent (ET_DYN) ELF executables."""
    try:
        with open(path, 'rb') as f:
            hdr = f.read(18)
        if len(hdr) < 18 or hdr[:4] != b'\x7fELF':
            return False
        e_type = hdr[16] | (hdr[17] << 8) if hdr[5] == 1 else (hdr[16] << 8) | hdr[17]
        return e_type == 3
    except (IOError, OSError):
        return False


class TextPopup(Popup):
    def __init__(self, title, lines):
        self.title = title
        self.lines = [l if isinstance(l, tuple) else (l, None) for l in lines]
        self.top = 0
        self.lh = 1

    def draw(self, app):
        longest = max([len(l[0]) for l in self.lines] + [30])
        w = min(app.W - 2, longest + 6)
        h = min(app.H - 2, len(self.lines) + 4)
        y = max(0, (app.H - h) // 2)
        x = max(0, (app.W - w) // 2)
        app.box(y, x, h, w, self.title)
        lh = h - 3
        self.lh = lh
        self.top = max(0, min(self.top, len(self.lines) - lh))
        for i in range(lh):
            idx = self.top + i
            if idx >= len(self.lines):
                break
            t, a = self.lines[idx]
            app.put(y + 1 + i, x + 2, t, app.A.get(a, app.A['popup']) if a else app.A['popup'], w - 4)
        more = ''
        if len(self.lines) > lh:
            more = '  (Up/Down/PgUp/PgDn to scroll, %d%%)' % (100 * min(len(self.lines), self.top + lh) // len(self.lines))
        app.put(y + h - 2, x + 2, 'Esc / Enter = Close' + more, app.A['dim'], w - 4)

    def key(self, app, k):
        if k in (KEY_ESC, 3, 'q') or k in KEY_ENTERS:
            return ('done', None)
        if k == curses.KEY_UP:
            self.top = max(0, self.top - 1)
        elif k == curses.KEY_DOWN:
            self.top += 1
        elif k in (curses.KEY_PPAGE,):
            self.top = max(0, self.top - self.lh)
        elif k in (curses.KEY_NPAGE, ' '):
            self.top += self.lh
        elif k == curses.KEY_HOME:
            self.top = 0
        elif k == curses.KEY_END:
            self.top = len(self.lines)
        return None


class QuickWatchPopup(Popup):
    def __init__(self, app, expr):
        self.set_expr(app, expr)

    def set_expr(self, app, expr):
        self.expr = expr
        self.node = VarNode(expr, expr)
        self.view = VarTreeView('QuickWatch', lambda a: [self.node])
        app.create_var(self.node, '@')

    def draw(self, app):
        w = min(app.W - 4, max(70, app.W * 3 // 4))
        h = min(app.H - 2, max(12, app.H * 2 // 3))
        y = max(0, (app.H - h) // 2)
        x = max(0, (app.W - w) // 2)
        app.box(y, x, h, w, 'QuickWatch')
        app.put(y + 1, x + 2, 'Expression: ', app.A['bold'])
        app.put(y + 1, x + 14, self.expr, app.A['field'], w - 16)
        app.put(y + 2, x + 2, '-' * (w - 4), app.A['dim'], w - 4)
        self.view.draw(app, y + 3, x + 2, h - 5, w - 4, True)
        app.put(y + h - 2, x + 2,
                'Enter/Right expand  Left collapse  e edit value  w add watch  n new expression  Esc close',
                app.A['dim'], w - 4)

    def close(self, app):
        app.delete_var(self.node)

    def key(self, app, k):
        if k in (KEY_ESC, 3, 5):
            self.close(app)
            return ('done', None)
        if k == 'n' or k == 'r':
            e = app.prompt('QuickWatch expression', self.expr, 'expr')
            if e:
                self.close(app)
                self.set_expr(app, e)
            return None
        if k == 'w' or k == 23:
            rows = self.view.rows(app)
            if rows and isinstance(rows[min(self.view.sel, len(rows) - 1)][0], VarNode):
                app.watch_node(rows[min(self.view.sel, len(rows) - 1)][0])
            return None
        self.view.key(app, k)
        return None



# --------------------------------------------------------------------------
# System call tracing (live via gdb catchpoints, or the real strace)
# --------------------------------------------------------------------------
class SyscallMixin(object):
    MAX_SYSCALLS = 20000

    def init_syscalls(self):
        self.syscalls = []
        self.sys_seq = 0
        self.sys_filter = ''
        self.sys_lines = {}         # source path -> {line: {syscall name: count}}
        self.sysrec = None          # live recording state (gdb catch syscall)
        self.step_ctx = None        # a step/finish in progress while recording
        self.strace = None          # running / loaded strace state
        self.resolver = None        # background gdb resolving strace stack sites
        self.site_queue = []
        self.site_cache = {}
        self.strace_k = None
        self.sys_stale_catch = None

    # ---------------------------------------------------------------- records
    def add_syscall(self, rec):
        self.sys_seq += 1
        rec['n'] = self.sys_seq
        view = self.v_sys
        follow = view.sel >= len(view.rows(self)) - 1
        self.syscalls.append(rec)
        if len(self.syscalls) > self.MAX_SYSCALLS:
            drop = len(self.syscalls) - self.MAX_SYSCALLS
            del self.syscalls[:drop]
            view.sel = max(0, view.sel - drop)
        if follow:
            view.sel = len(view.rows(self)) - 1
        if rec.get('where'):
            self.note_syscall_line(rec)
        if not self.is_visible(self.v_sys) and not self.v_sys.title.endswith('*'):
            self.v_sys.title = 'Syscalls*'
        self.dirty = True

    def set_where(self, rec, where):
        rec['where'] = where
        self.note_syscall_line(rec)
        self.dirty = True

    def note_syscall_line(self, rec):
        w = rec.get('where') or {}
        if rec.get('restart'):
            return
        if w.get('path') and w.get('line') and rec.get('name'):
            d = self.sys_lines.setdefault(w['path'], {}).setdefault(w['line'], {})
            d[rec['name']] = d.get(rec['name'], 0) + 1

    def unnote_syscall_line(self, rec):
        w = rec.get('where') or {}
        d = self.sys_lines.get(w.get('path'), {}).get(w.get('line'))
        if d and d.get(rec.get('name'), 0) > 0:
            d[rec['name']] -= 1
            if not d[rec['name']]:
                del d[rec['name']]

    def syscall_line_note(self, d):
        parts = ['%s x%d' % (k, v) if v > 1 else k for k, v in sorted(d.items(), key=lambda kv: -kv[1])]
        return 'syscalls: ' + ', '.join(parts[:4]) + (' ...' if len(parts) > 4 else '')

    def syscall_text(self, r):
        if r.get('note'):
            return r['note']
        return '%s(%s) = %s  %s' % (r.get('name'), r.get('args', ''), r.get('ret'),
                                    (r.get('where') or {}).get('text', ''))

    def clear_syscalls(self):
        self.syscalls = []
        self.sys_lines = {}
        self.v_sys.sel = 0
        self.msg('System call list cleared')

    def act_show_syscalls(self):
        self.v_sys.title = 'Syscalls'
        self.show_view(self.v_sys)

    def goto_syscall(self, r):
        w = r.get('where') or {}
        if w.get('path') and w.get('line'):
            if self.open_source(w['path'], w['line']):
                self.focus = 'source'
            return
        if w.get('addr') is not None and r.get('src') == 'gdb' and self.state == 'stopped':
            self.goto_address(w['addr'])
            return
        if w.get('expr'):
            self.goto_function_disasm(w['expr'])
            return
        self.msg('No source location for this call%s' %
                 (' (strace -k stack traces give one)' if r.get('src') == 'strace' else ''), 'warn')

    def syscall_details(self, r):
        lines = []
        if r.get('note'):
            lines.append(r['note'])
        else:
            lines += [('%s(%s)' % (r.get('name'), r.get('args', '')), 'bold'),
                      '  = %s' % r.get('ret'), '']
            if r.get('dur'):
                lines.append('Time in call : %s s%s' % (r['dur'], '  (includes debugger overhead)'
                                                       if r.get('src') == 'gdb' else ''))
        lines.append('Recorded by  : %s' % ('gdb (live, this debug session)' if r.get('src') == 'gdb'
                                            else 'strace (separate run / log)'))
        if r.get('tid'):
            lines.append('Thread       : %s' % r['tid'])
        if r.get('t') is not None:
            lines.append('Time         : +%.6f s' % r['t'])
        w = r.get('where') or {}
        lines.append('Called from  : %s' % (w.get('text') or 'unknown'))
        if r.get('stack'):
            lines += ['', ('Call stack (innermost first):', 'warn')]
            lines += ['  %s' % f for f in r['stack']]
        lines += ['', 'Enter in the list jumps to the calling source line.']
        self.show_text('System Call #%s' % r.get('n'), lines)

    def act_syscall_summary(self):
        stats = {}
        for r in self.syscalls:
            if r.get('note') or not r.get('name'):
                continue
            st = stats.setdefault(r['name'], [0, 0, 0.0])
            st[0] += 1
            if r.get('err'):
                st[1] += 1
            try:
                st[2] += float(r.get('dur') or 0)
            except ValueError:
                pass
        if not stats:
            self.msg('No system calls recorded', 'warn')
            return
        total = sum(v[2] for v in stats.values()) or 1.0
        lines = [('%7s %7s %10s %6s  %s' % ('calls', 'errors', 'seconds', '% time', 'syscall'), 'bold')]
        for name, (n, e, t) in sorted(stats.items(), key=lambda kv: (-kv[1][2], -kv[1][0])):
            lines.append('%7d %7s %10.6f %6.1f  %s' % (n, e or '', t, 100.0 * t / total, name))
        lines.append('%7d %7d %10.6f %6s  total' % (sum(v[0] for v in stats.values()),
                                                   sum(v[1] for v in stats.values()),
                                                   sum(v[2] for v in stats.values()), '100.0'))
        self.show_text('System Call Summary', lines)

    def save_syscalls(self):
        default = os.path.join(self.program_dir(), 'syscalls.txt')
        p = self.prompt('Save system call list to file', default, 'syssave')
        if not p:
            return
        try:
            with open(os.path.expanduser(p), 'w') as f:
                for r in self.syscalls:
                    t = r.get('t')
                    f.write('%s%s%s\n' % ('%.6f ' % t if t is not None else '',
                                          '[%s] ' % r['tid'] if r.get('tid') else '', self.syscall_text(r)))
            self.msg('Saved %d entries to %s' % (len(self.syscalls), p))
        except (IOError, OSError) as e:
            self.msg('Cannot save: %s' % e, 'err')

    # ---------------------------------------------------------------- live recording (gdb)
    def act_syscall_record(self, preset=None):
        if self.sysrec:
            num = self.sysrec['catch']
            self.sysrec = None
            self.step_ctx = None

            if self.state == 'running':
                self.sys_stale_catch = num      # removed at the next stop, without interrupting the program
            else:
                self.gdb.send('-break-delete %s' % num, lambda r: self.refresh_breakpoints(), quiet=True)
            self.msg('Live system call recording stopped')
            return
        if not self.have_program and preset is None:
            self.msg('Load a program first', 'warn')
            return
        if preset is not None:
            f = preset
        else:
            f = self.prompt('Record system calls live - which calls? (empty = all)', '', 'sysrec',
                            hint='e.g.  openat read write close    or  group:file  group:network  (gdb 8.3+)')
        if f is None:
            return
        f = f.strip()

        def create():
            def ok(r):
                m = re.search(r'Catchpoint (\d+)', r.get('captured', ''))
                if not m:
                    self.msg('Could not create the syscall catchpoint', 'err')
                    return
                self.sysrec = {'catch': m.group(1), 'filter': f, 't0': time.time(), 'pending': {}}
                self.refresh_breakpoints()
                self.act_show_syscalls()
                self.msg('Recording system calls%s. Each call is linked to the source line that made it.'
                         % (' (%s)' % f if f else ''))
                if self.state == 'stopped' and resume:
                    self.exec_cmd('-exec-continue')
            self.gdb.send(cli('catch syscall %s' % f), ok, errcb=self.cmd_error, capture=True)
        resume = self.state == 'running'
        self.after_stop_or_now(create)

    def act_syscall_break(self):
        f = self.prompt('Break when the program makes these system calls', '', 'sysbreak',
                        hint='e.g.  openat   write   group:network   (empty = any system call)')
        if f is None:
            return
        self.cli_then_refresh('catch syscall %s' % f.strip())
        self.msg('Syscall catchpoint added (see Breakpoints tab)')

    def step_cmd(self, cmd):
        """Run a step command; while recording, remember enough to resume it after syscall stops."""
        if not self.sysrec or cmd in ('-exec-next-instruction', '-exec-step-instruction'):
            self.exec_cmd(cmd)
            return
        tid = self.thread_id
        level = parse_int(self.frame.get('level')) or 0

        start_line = (self.frame.get('fullname') or self.frame.get('file'), self.frame.get('line'))

        def ok(r):
            depth = parse_int(r['results'].get('depth'))
            self.step_ctx = {'cmd': cmd, 'tid': tid, 'depth': (depth or 0) - level, 'phase': 'step',
                             'line': start_line}
            self.exec_cmd('%s --thread %s' % (cmd, tid) if tid else cmd)
        self.gdb.send('-stack-info-depth', ok, errcb=lambda r: self.exec_cmd(cmd))

    def syscall_stop_hook(self, res, reason):
        """Called first for every *stopped. Returns True when the stop was consumed here."""
        stale = self.sys_stale_catch
        if stale:
            self.sys_stale_catch = None
            self.gdb.send('-break-delete %s' % stale, lambda r: self.refresh_breakpoints(), quiet=True)
            if reason in ('syscall-entry', 'syscall-return') and str(res.get('bkptno')) == str(stale) \
                    and not self.interrupting and not self.after_stop:
                self.gdb.send('-exec-continue', quiet=True)
                return True
        sr = self.sysrec
        if sr and reason in ('syscall-entry', 'syscall-return') and str(res.get('bkptno')) == str(sr['catch']):
            self.record_syscall_stop(res, reason)
            if self.interrupting or self.after_stop:
                return False          # the user asked to pause / stop: show this stop normally
            self.state = 'running'
            return True
        ctx = self.step_ctx
        if ctx and reason == 'function-finished' and ctx.get('phase') == 'finishing' \
                and ctx['cmd'] != '-exec-finish' and str(res.get('thread-id')) == str(ctx['tid']):
            # back in the stepping frame after a syscall interrupted the step
            f = res.get('frame') or {}
            here = (f.get('fullname') or f.get('file'), f.get('line'))
            if here[1] and here != ctx.get('line'):
                # the call was the last thing on the line: we are already at the start of the next
                # line, so the step is complete (stepping again would skip a line)
                self.step_ctx = None
                res['reason'] = 'end-stepping-range'
                return 'step-done'
            ctx['phase'] = 'step'          # still part-way through the line: carry on stepping
            self.state = 'running'
            self.exec_cmd('%s --thread %s' % (ctx['cmd'], ctx['tid']))
            return True
        self.step_ctx = None
        return False

    def record_syscall_stop(self, res, reason):
        sr = self.sysrec
        tid = res.get('thread-id', '')
        name = res.get('syscall-name') or 'syscall_%s' % res.get('syscall-number', '?')
        frame = res.get('frame') or {}
        if not sr.get('arch'):
            sr['arch'] = frame.get('arch', '')
        args_regs, ret_reg, bits = syscall_regs(sr['arch'])
        kinds = SYSCALL_ARGS.get(name)
        kinds = kinds.split(',') if kinds is not None else ['hex', 'hex', 'hex']
        kinds = [k for k in kinds if k]
        if reason == 'syscall-entry':
            rec = {'src': 'gdb', 'tid': tid, 't': time.time() - sr['t0'], 'name': name, 'args': '...',
                   'ret': None, 'start': time.time(), 'kinds': kinds, 'bits': bits}
            self.add_syscall(rec)
            sr['pending'][tid] = rec
            fmt = ' '.join(['%lu'] * len(args_regs))

            def got_args(r):
                vals = [int(v) for v in re.findall(r'\d+', r.get('captured', ''))][:len(args_regs)]
                rec['raw'] = vals
                self.format_syscall_args(rec)
                self.fetch_syscall_strings(rec, 'entry')
                self.fetch_syscall_where(rec)
                self.resume_after_syscall()
            self.gdb.send(cli('printf "%s\\n", %s' % (fmt, ', '.join(args_regs))), got_args,
                          errcb=lambda r: (self.fetch_syscall_where(rec), self.resume_after_syscall()),
                          capture=True)
        else:
            rec = sr['pending'].pop(tid, None)
            if rec is None:
                rec = {'src': 'gdb', 'tid': tid, 't': time.time() - sr['t0'], 'name': name, 'args': '?',
                       'kinds': kinds, 'bits': bits}
                self.add_syscall(rec)

            def got_ret(r):
                m = re.search(r'-?\d+', r.get('captured', ''))
                if m:
                    v = int(m.group())
                    rec['retv'] = v
                    rec['ret'], rec['err'] = fmt_syscall_ret(name, v, bits)
                    if rec['ret'].startswith('? ERESTART'):
                        rec['restart'] = True
                        self.unnote_syscall_line(rec)
                if rec.get('start'):
                    rec['dur'] = '%.6f' % (time.time() - rec['start'])
                self.fetch_syscall_strings(rec, 'return')
                self.resume_after_syscall()
                self.dirty = True
            self.gdb.send(cli('printf "%%lu\\n", %s' % ret_reg), got_ret,
                          errcb=lambda r: self.resume_after_syscall(), capture=True)

    def format_syscall_args(self, rec):
        vals = rec.get('raw') or []
        bits = rec.get('bits', 64)
        out = []
        for i, kind in enumerate(rec['kinds']):
            if i >= len(vals):
                break
            v = vals[i]
            if kind in ('fd', 'int'):
                sv = to_signed(v, 32)
                out.append(str(sv))
            elif kind == 'dirfd':
                sv = to_signed(v, 32)
                out.append('AT_FDCWD' if sv == -100 else str(sv))
            elif kind == 'n':
                out.append(str(v))
            elif kind == 'oflags':
                out.append(fmt_open_flags(v))
            elif kind == 'mode':
                out.append('0%o' % v)
            elif kind in ('ptr', 'hex', 'path', 'ibuf', 'obuf'):
                out.append('NULL' if v == 0 and kind != 'hex' else '0x%x' % (v & ((1 << bits) - 1)))
            else:
                out.append('0x%x' % v)
        rec['argv'] = out
        rec['args'] = ', '.join(out)
        self.dirty = True

    def fetch_syscall_strings(self, rec, phase):
        """Read path strings and I/O buffers from the stopped program (before it resumes)."""
        vals = rec.get('raw') or []
        kinds = rec['kinds']
        for i, kind in enumerate(kinds):
            if i >= len(vals) or not vals[i]:
                continue
            if kind == 'path' and phase == 'entry':
                self.read_syscall_string(rec, i, vals[i])
            elif kind in ('ibuf', 'obuf'):
                if kind == 'ibuf' and phase != 'entry':
                    continue
                if kind == 'obuf' and (phase != 'return' or (rec.get('retv') or 0) <= 0):
                    continue
                count = rec.get('retv') if kind == 'obuf' else (vals[i + 1] if i + 1 < len(vals) else 32)
                count = max(0, min(int(count or 0), 33))
                if count:
                    self.read_syscall_buffer(rec, i, vals[i], count)

    def read_syscall_string(self, rec, i, addr):
        def ok(r):
            m = re.search(r'"(.*)"', r['results'].get('value', ''))
            if m and i < len(rec.get('argv', [])):
                rec['argv'][i] = '"%s"' % m.group(1)
                rec['args'] = ', '.join(rec['argv'])
                self.dirty = True
        self.gdb.send('-data-evaluate-expression %s' % mi_quote('(const char *)0x%x' % addr), ok, quiet=True)

    def read_syscall_buffer(self, rec, i, addr, count):
        def ok(r):
            data = bytearray()
            for chunk in as_list(r['results'].get('memory')):
                if isinstance(chunk, dict):
                    hexs = chunk.get('contents', '')
                    data.extend(int(hexs[j:j + 2], 16) for j in range(0, len(hexs) - 1, 2))
            if data and i < len(rec.get('argv', [])):
                rec['argv'][i] = escape_bytes(bytes(data), 32)
                rec['args'] = ', '.join(rec['argv'])
                self.dirty = True
        self.gdb.send('-data-read-memory-bytes 0x%x %d' % (addr, count), ok, quiet=True)

    def fetch_syscall_where(self, rec):
        def ok(r):
            frames = [f for f in as_list(r['results'].get('stack')) if isinstance(f, dict)]
            rec['stack'] = [frame_label(f) for f in frames]
            self.set_where(rec, self.pick_user_frame(frames))
        self.gdb.send('-stack-list-frames 0 40', ok, quiet=True)

    def pick_user_frame(self, frames):
        prog = os.path.realpath(self.program) if self.program and os.path.exists(self.program) else None
        for f in frames:
            full = f.get('fullname')
            fname = f.get('file') or ''
            if full and f.get('line') and os.path.isfile(full) and not is_system_path(full) \
                    and not fname.startswith(('../', './')):
                path = os.path.realpath(full)
                return {'path': path, 'line': parse_int(f.get('line')), 'func': f.get('func'),
                        'addr': parse_int(f.get('addr')),
                        'text': '%s:%s %s' % (os.path.basename(path), f.get('line'), f.get('func') or '')}
        for f in frames:
            frm = f.get('from')
            # frames in the main executable without debug info have neither a file nor a library
            if (prog and frm and os.path.realpath(frm) == prog) or (not frm and not f.get('file')
                                                                    and f.get('func')):
                return {'func': f.get('func'), 'addr': parse_int(f.get('addr')),
                        'text': '%s %s' % (f.get('func') or '??', f.get('addr', ''))}
        f = frames[-1] if frames else {}
        for fr in frames:
            if fr.get('func') in ('start_thread', '__libc_start_main', '__libc_start_call_main', '_start',
                                  'clone', 'clone3', '__GI__exit', '_exit', 'exit'):
                f = fr
                break
        lib = os.path.basename(f.get('from') or f.get('file') or 'libc')
        return {'addr': parse_int(f.get('addr')),
                'text': '%s (%s, no caller in your code)' % (f.get('func') or '??', lib)}

    def resume_after_syscall(self):
        if self.interrupting or self.after_stop or not self.sysrec:
            return
        ctx = self.step_ctx
        if not ctx:
            self.gdb.send('-exec-continue', quiet=True)
            return
        tid = ctx['tid']

        def got_depth(r):
            d = parse_int(r['results'].get('depth')) or 0
            lvl = d - ctx['depth'] - (0 if ctx['cmd'] == '-exec-finish' else 1)
            if lvl < 0:
                ctx['phase'] = 'step'
                self.exec_cmd('%s --thread %s' % (ctx['cmd'], tid))
            else:
                ctx['phase'] = 'finishing' if ctx['cmd'] != '-exec-finish' else 'step'
                self.exec_cmd('-exec-finish --thread %s --frame %d' % (tid, lvl))
        self.gdb.send('-stack-info-depth --thread %s' % tid, got_depth,
                      errcb=lambda r: self.gdb.send('-exec-continue', quiet=True))

    # ---------------------------------------------------------------- strace
    def strace_path(self):
        p = shutil.which('strace')
        if not p:
            self.msg('strace is not installed in this container (try: apt-get install strace / yum install strace)',
                     'err')
        return p

    def strace_supports_k(self, path):
        if self.strace_k is None:
            try:
                devnull = open(os.devnull, 'w')
                self.strace_k = subprocess.call([path, '-k', '-o', os.devnull, 'true'],
                                                stdout=devnull, stderr=devnull) == 0
                devnull.close()
            except OSError:
                self.strace_k = False
        return self.strace_k

    def strace_options(self, path):
        default = '-f -tt -T -s 64' + (' -k' if self.strace_supports_k(path) else '')
        hint = '-f follow threads/children  -tt timestamps  -T time per call  -s string size' + \
               ('  -k stack trace (links calls to source)' if self.strace_k else
                '   (this strace has no -k: calls cannot be linked to source lines)')
        return self.prompt('strace options', self.config.get('strace_options', default), 'straceopts', hint=hint)

    def act_strace_run(self):
        st = self.strace_path()
        if not st:
            return
        if not (self.program and os.path.isfile(self.program)):
            self.msg('Load a program first', 'warn')
            return
        opts = self.strace_options(st)
        if opts is None:
            return

        def got_args(r):
            m = re.search(r'"(.*)"', r.get('captured', ''), re.S)
            args = m.group(1) if m else ''
            self.pending_popups.append(lambda: self.start_strace(st, opts, args=args))
        self.gdb.send(cli('show args'), got_args, quiet=True, capture=True,
                      errcb=lambda r: self.pending_popups.append(lambda: self.start_strace(st, opts, args='')))

    def act_strace_attach(self):
        st = self.strace_path()
        if not st:
            return
        pid = self.choose('Attach strace to process (not the one gdb is debugging)', self.process_items(),
                          allow_free=True, hint='Type to filter (or a PID)   Enter = attach   Esc = cancel')
        if isinstance(pid, tuple):
            pid = pid[1].strip()
        if not pid or not str(pid).isdigit():
            return
        if self.inferior_pid and str(pid) == str(self.inferior_pid):
            self.msg('That process is being debugged by gdb - a process can have only one tracer. '
                     'Use "Syscalls: Start / Stop Live Recording" instead.', 'err')
            return
        opts = self.strace_options(st)
        if opts is None:
            return
        self.start_strace(st, opts, pid=pid)

    def start_strace(self, st, opts, args='', pid=None):
        if self.strace and not self.strace.get('done'):
            self.act_strace_stop()
        log = os.path.join(tempfile.gettempdir(), 'gdbtui-strace-%d-%d.log' % (os.getpid(), int(time.time())))
        if pid:
            cmd = 'exec %s %s -o %s -p %s' % (shlex.quote(st), opts, shlex.quote(log), pid)
        else:
            cmd = 'exec %s %s -o %s -- %s %s' % (shlex.quote(st), opts, shlex.quote(log),
                                                shlex.quote(self.program), args)
        io = self.pty_slave if self.pty_slave is not None else subprocess.DEVNULL
        try:
            proc = subprocess.Popen(['/bin/sh', '-c', cmd], stdin=io, stdout=io, stderr=io,
                                    start_new_session=True, cwd=os.getcwd())
        except OSError as e:
            self.msg('Cannot start strace: %s' % e, 'err')
            return
        self.strace = {'proc': proc, 'log': log, 'pos': 0, 'parser': StraceParser(), 'pid': pid,
                       'done': False, 'started': time.time()}
        self.add_syscall({'src': 'strace', 'note': 'strace started: %s%s' % (
            'attached to pid %s' % pid if pid else os.path.basename(self.program) + (' ' + args if args else ''),
            '   (log: %s)' % log)})
        if not pid:
            self.output.write('---- program started under strace %s ----\n' % time.strftime('%H:%M:%S'), 'dim')
        self.act_show_syscalls()
        self.msg('strace running%s - its output goes to the Output tab' % (' on pid %s' % pid if pid else ''))

    def act_strace_stop(self):
        stt = self.strace
        if not stt or stt.get('done') or not stt.get('proc'):
            self.msg('strace is not running', 'warn')
            return
        try:
            if stt.get('pid'):
                os.kill(stt['proc'].pid, signal.SIGINT)            # detach from the attached process
            else:
                os.killpg(stt['proc'].pid, signal.SIGTERM)          # stop strace and the program it started
        except OSError:
            pass
        self.msg('Stopping strace...')

    def act_strace_load(self):
        p = self.prompt('Load strace log file (strace -o FILE; -f -tt -T -k recommended)',
                        self.program_dir() + os.sep, 'straceload')
        if not p:
            return
        p = os.path.expanduser(p)
        try:
            with open(p, 'r', errors='replace') as f:
                text = f.read()
        except (IOError, OSError) as e:
            self.msg('Cannot read %s: %s' % (p, e), 'err')
            return
        parser = StraceParser()
        self.add_syscall({'src': 'strace', 'note': 'loaded strace log %s' % p})
        n = 0
        for rec in parser.feed(text, final=True):
            self.add_strace_record(rec)
            n += 1
        self.queue_strace_sites()
        self.act_show_syscalls()
        self.msg('Loaded %d entries from %s' % (n, p))

    def add_strace_record(self, rec):
        self.add_syscall(rec)
        if rec.get('sites') is None:
            rec['sites'] = []

    def poll_strace(self):
        stt = self.strace
        if stt and not stt.get('done'):
            data = ''
            try:
                with open(stt['log'], 'r', errors='replace') as f:
                    f.seek(stt['pos'])
                    data = f.read(1 << 20)
                    stt['pos'] = f.tell()
            except (IOError, OSError):
                pass
            finished = stt['proc'].poll() is not None and not data
            if data or finished:
                for rec in stt['parser'].feed(data, final=finished):
                    self.add_strace_record(rec)
            if finished:
                stt['done'] = True
                code = stt['proc'].returncode
                how = 'stopped' if code is not None and code < 0 else 'finished (exit status %s)' % code
                self.add_syscall({'src': 'strace', 'note': 'strace %s  log: %s' % (how, stt['log'])})
                self.msg('strace finished. Log kept at %s' % stt['log'])
            if data or finished:
                self.queue_strace_sites()
        self.poll_resolver()

    # ---- linking strace -k stack frames to source lines (via a separate batch gdb)
    def queue_strace_sites(self):
        prog = os.path.realpath(self.program) if self.program and os.path.exists(self.program) else None
        if not prog:
            return
        base = self.prog_load_base()
        stt = self.strace
        incomplete = stt['parser'].last if stt and not stt.get('done') else None
        for rec in self.syscalls:
            if rec.get('src') != 'strace' or rec.get('where') or not rec.get('sites') or rec.get('site_done'):
                continue
            if rec is incomplete:
                continue        # strace may still be writing this call's stack lines
            cands = []
            for idx, (module, symoff, off) in enumerate(rec['sites']):
                try:
                    same = os.path.realpath(module) == prog
                except (OSError, ValueError):
                    same = False
                if same or os.path.basename(module) == os.path.basename(prog):
                    # caller frames hold return addresses: look up the call instruction (addr - 1)
                    cands.append((base + off - (1 if idx else 0), symoff))
                    if len(cands) >= 4:
                        break
            rec['site_done'] = True
            if not cands:
                module, symoff, off = rec['sites'][0]
                self.set_where(rec, {'text': '%s(%s)' % (os.path.basename(module), symoff)})
                continue
            rec['site'] = cands
            if all(a in self.site_cache for a, _s in cands):
                self.apply_site(rec)
            for a, _s in cands:
                if a not in self.site_cache and a not in self.site_queue:
                    self.site_queue.append(a)

    def prog_load_base(self):
        if getattr(self, '_base_for', None) != self.program:
            self._base_for = self.program
            self._base = elf_load_base(self.program)
        return self._base

    def apply_site(self, rec):
        cands = rec['site']
        resolved = [(a, sym, self.site_cache.get(a) or {}) for a, sym in cands]
        pick = None
        for a, sym, info in resolved:          # prefer the caller in the user's own code
            if info.get('path') and not is_system_path(info['path']):
                pick = (a, sym, info)
                break
        if pick is None:
            pick = next(((a, sym, info) for a, sym, info in resolved if info.get('path')), resolved[0])
        addr, symoff, info = pick
        if info and info.get('path'):
            self.set_where(rec, {'path': info['path'], 'line': info['line'],
                                 'text': '%s:%s %s' % (os.path.basename(info['path']), info['line'],
                                                       symoff.split('+')[0])})
        else:
            fn = symoff.split('+')[0]
            self.set_where(rec, {'expr': fn if fn else None, 'text': symoff or '0x%x' % addr})

    def poll_resolver(self):
        rv = self.resolver
        if rv and rv['proc'].poll() is not None:
            try:
                with open(rv['out'], 'r', errors='replace') as f:
                    text = f.read()
                os.unlink(rv['out'])
            except (IOError, OSError):
                text = ''
            chunks = text.split('@@GDBTUI@@')
            for addr, chunk in zip(rv['addrs'], chunks):
                m = re.search(r'Line (\d+) of "([^"]+)"', chunk)
                if m:
                    path = self.resolve_path(m.group(2)) or m.group(2)
                    self.site_cache[addr] = {'path': path, 'line': int(m.group(1))}
                else:
                    self.site_cache[addr] = {}
            for addr in rv['addrs']:
                self.site_cache.setdefault(addr, {})
            self.resolver = None
            for rec in self.syscalls:
                if rec.get('site') and not rec.get('where'):
                    if all(a in self.site_cache for a, _s in rec['site']):
                        self.apply_site(rec)
            self.dirty = True
        if self.resolver is None and self.site_queue and self.program:
            batch = self.site_queue[:400]
            del self.site_queue[:400]
            out = os.path.join(tempfile.gettempdir(), 'gdbtui-resolve-%d.txt' % os.getpid())
            cmd = [self.opts.gdb, '-nx', '-q', '-batch', self.program]
            for d in self.opts.directory or []:
                cmd += ['-ex', 'directory %s' % d]
            for a in batch:
                cmd += ['-ex', 'info line *0x%x' % a, '-ex', 'echo @@GDBTUI@@\\n']
            try:
                fh = open(out, 'w')
                proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT,
                                        start_new_session=True)
                fh.close()
                self.resolver = {'proc': proc, 'out': out, 'addrs': batch}
            except OSError:
                for a in batch:
                    self.site_cache[a] = {}

# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------
class App(SyscallMixin):
    def __init__(self, scr, opts, config):
        self.scr = scr
        self.opts = opts
        self.config = config
        self.H, self.W = scr.getmaxyx()
        self.quitting = False
        self.dirty = True
        self.cursor_req = None
        self._cursor_vis = None
        self.popups = []
        self.pending_popups = []
        self.message = ('', None, 0)
        self.message_dismissed = False
        self.hint_hits = []
        self.click_repeat = None    # (action, stop_gen, time): second click of a double click on Step/Continue
        self.ascii_only = 'utf' not in (locale.getpreferredencoding(False) or '').lower()
        self.tabsize = int(config.get('tabsize', opts.tabsize or 4))
        self.syntax = bool(config.get('syntax', True))
        self.hist = {}

        # debugger state
        self.state = 'idle'          # idle | running | stopped | exited
        self.stop_reason = ''
        self.program = None
        self.have_program = False
        self.attached = False
        self.core = False
        self.inferior_pid = None
        self.thread_id = None
        self.frame = {}
        self.stop_gen = 0
        self.after_stop = []
        self.interrupting = False
        self.exec_path = None
        self.exec_line = None
        self.exec_addr = None
        self.exec_top = True
        self.disasm = False
        self.dis_error = ''
        self.hex = False

        self.breakpoints = []
        self.bp_map = {}             # realpath -> {line: (char, enabled)}
        self.bp_base = {}            # basename -> {line: (char, enabled)}
        self.bp_addrs = {}           # int addr -> (char, enabled)
        self.locals = []
        self.locals_sig = None
        self.watches = []
        self.vobj_map = {}
        self.stack = []
        self.stack_args = {}
        self.threads = []
        self.registers = []
        self.reg_names = None
        self.reg_prev = {}
        self.modules = []
        self.mem_addr = None
        self.mem_label = ''
        self.mem_bytes = []
        self.mem_prev = {}
        self.mem_error = ''
        self.source_files = []
        self.sources = {}
        self.session_ready = False
        self.char_arrays = set()

        self.console = TextBuffer()
        self.output = TextBuffer()
        self.pty_master = None
        self.pty_slave = None
        self.out_file = None
        self.out_file_pos = 0
        self.out_decoder = codecs.getincrementaldecoder('utf-8')('replace')
        self.ansi_re = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]')

        # views
        self.src_view = SourceView()
        self.src_view.MAX_TABS = max(4, int(config.get('max_open_files', 50)))
        self.auto_open = opts.open_sources or config.get('auto_open', 'program')
        if self.auto_open not in ('off', 'program', 'all'):
            self.auto_open = 'program'
        self.auto_open_ext_done = False
        self.v_locals = VarTreeView('Locals', lambda a: a.locals, False,
                                    'Locals are shown when the program is paused.')
        self.v_watch = VarTreeView('Watch', lambda a: a.watches, True)
        self.v_regs = RegistersView()
        self.v_mem = MemoryView()
        self.v_console = GdbConsoleView()
        self.v_output = OutputView()
        self.v_sys = SyscallsView()
        self.v_stack = StackView()
        self.v_bps = BreakpointsView()
        self.v_threads = ThreadsView()
        self.v_modules = ModulesView()
        self.v_files = FilesView()
        self.panes = {
            'source': [self.src_view],
            'right': [self.v_locals, self.v_watch, self.v_regs, self.v_mem, self.v_files],
            'bottom': [self.v_console, self.v_output, self.v_sys, self.v_stack, self.v_bps, self.v_threads, self.v_modules],
        }
        self.active = {'source': 0, 'right': 0, 'bottom': 0}
        self.focus = 'source'
        self.zoom = None
        self.bottom_ratio = float(config.get('bottom_ratio', 0.30))
        self.right_ratio = float(config.get('right_ratio', 0.38))
        self.rects = {}
        self.tab_hits = []
        self.mouse = bool(opts.mouse or config.get('mouse', False))

        # key bindings
        self.keymap = {}
        self.action_keys = {}
        overrides = config.get('keys', {}) if isinstance(config.get('keys'), dict) else {}
        for act, _label, default in ACTIONS:
            k = overrides.get(act, default)
            code = ctrl_code(k) if k else None
            if code:
                self.keymap[code] = act
                self.action_keys[act] = code
        if 30 not in self.keymap:
            self.keymap[30] = 'help'

        self.init_syscalls()
        self.init_colors()
        self.gdb = GdbProcess(opts.gdb, self.gdb_extra_args(), opts.log)

    # ------------------------------------------------------------------ setup
    def gdb_extra_args(self):
        args = []
        if self.opts.nx:
            args.append('-nx')
        return args

    def init_colors(self):
        A = {}
        b = curses.A_BOLD
        A['popup'] = 0
        A['bold'] = b
        A['dim'] = curses.A_DIM
        A['name'] = 0
        A['field'] = curses.A_UNDERLINE
        A['popsel'] = curses.A_REVERSE
        A['curline'] = curses.A_REVERSE
        A['curlineno'] = curses.A_REVERSE
        A['status'] = curses.A_REVERSE | b
        A['hdr'] = curses.A_REVERSE
        A['hdr_focus'] = curses.A_REVERSE | b
        A['tab'] = b | curses.A_UNDERLINE
        A['tab_focus'] = b
        A['exec'] = curses.A_REVERSE | b
        A['exec2'] = curses.A_REVERSE
        A['arrow'] = b
        A['arrow2'] = 0
        A['bp'] = b
        A['bpdis'] = curses.A_DIM
        A['lineno'] = curses.A_DIM
        for k in ('kw', 'type', 'str', 'num', 'com', 'pre'):
            A[k] = 0
        A['changed'] = b
        A['err'] = b
        A['warn'] = b
        A['ok'] = b
        A['prompt'] = b
        A['cmd'] = b
        A['hint'] = curses.A_REVERSE
        A['hintkey'] = curses.A_REVERSE | b
        if curses.has_colors():
            try:
                curses.start_color()
                try:
                    curses.use_default_colors()
                    bg = -1
                except curses.error:
                    bg = curses.COLOR_BLACK
                C = curses
                spec = [
                    ('status', C.COLOR_WHITE, C.COLOR_BLUE, b),
                    ('hdr', C.COLOR_BLACK, C.COLOR_WHITE, 0),
                    ('hdr_focus', C.COLOR_BLACK, C.COLOR_CYAN, 0),
                    ('tab', C.COLOR_WHITE, C.COLOR_BLUE, b),
                    ('tab_focus', C.COLOR_WHITE, C.COLOR_BLUE, b),
                    ('exec', C.COLOR_BLACK, C.COLOR_YELLOW, 0),
                    ('exec2', C.COLOR_BLACK, C.COLOR_GREEN, 0),
                    ('arrow', C.COLOR_YELLOW, bg, b),
                    ('arrow2', C.COLOR_GREEN, bg, b),
                    ('bp', C.COLOR_RED, bg, b),
                    ('bpdis', C.COLOR_RED, bg, 0),
                    ('lineno', C.COLOR_CYAN, bg, 0),
                    ('curlineno', C.COLOR_BLACK, C.COLOR_CYAN, 0),
                    ('kw', C.COLOR_BLUE, bg, b),
                    ('type', C.COLOR_CYAN, bg, 0),
                    ('str', C.COLOR_MAGENTA, bg, 0),
                    ('num', C.COLOR_GREEN, bg, b),
                    ('com', C.COLOR_GREEN, bg, 0),
                    ('pre', C.COLOR_MAGENTA, bg, b),
                    ('changed', C.COLOR_RED, bg, b),
                    ('err', C.COLOR_RED, bg, b),
                    ('warn', C.COLOR_YELLOW, bg, b),
                    ('ok', C.COLOR_GREEN, bg, b),
                    ('prompt', C.COLOR_GREEN, bg, b),
                    ('cmd', C.COLOR_YELLOW, bg, 0),
                    ('name', C.COLOR_CYAN, bg, 0),
                    ('popup', C.COLOR_BLACK, C.COLOR_WHITE, 0),
                    ('popsel', C.COLOR_WHITE, C.COLOR_BLUE, b),
                    ('field', C.COLOR_WHITE, C.COLOR_BLACK, b),
                    ('curline', C.COLOR_BLACK, C.COLOR_WHITE, 0),
                    ('hint', C.COLOR_BLACK, C.COLOR_WHITE, 0),
                    ('hintkey', C.COLOR_BLUE, C.COLOR_WHITE, b),
                ]
                for i, (name, fg, bgc, extra) in enumerate(spec, 1):
                    if i >= curses.COLOR_PAIRS:
                        break
                    curses.init_pair(i, fg, bgc)
                    A[name] = curses.color_pair(i) | extra
                A['dim'] = curses.A_DIM
            except curses.error:
                pass
        self.A = A
        self.ctl_table = dict((i, '?') for i in list(range(0, 32)) + [127])
        self.ctl_table[9] = ' '

    def setup_inferior_tty(self):
        if self.opts.no_tty:
            return
        try:
            m, s = os.openpty()
            name = os.ttyname(s)
            if termios:
                attrs = termios.tcgetattr(s)
                attrs[3] &= ~(termios.ECHO | termios.ECHONL)
                termios.tcsetattr(s, termios.TCSANOW, attrs)
            if fcntl and termios and hasattr(termios, 'TIOCSWINSZ'):
                try:
                    fcntl.ioctl(s, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 160, 0, 0))
                except Exception:
                    pass
            os.set_blocking(m, False)
            self.pty_master = m
            self.pty_slave = s
            self.gdb.send('-inferior-tty-set ' + name, errcb=lambda r: self.console_write(
                'warning: could not set inferior tty: %s\n' % r['results'].get('msg'), 'warn'))
        except Exception as e:
            path = '/tmp/gdbtui-%d.out' % os.getpid()
            try:
                open(path, 'w').close()
                self.out_file = path
                self.gdb.send('-inferior-tty-set ' + path, quiet=True)
                self.console_write('note: no pseudo-terminal available (%s); program output goes to %s\n'
                                   % (e, path), 'warn')
            except Exception:
                pass

    def start(self):
        g = self.gdb
        for c in ('-gdb-set mi-async on', '-gdb-set target-async on', '-gdb-set confirm off',
                  '-gdb-set pagination off', '-gdb-set width 0', '-gdb-set height 0',
                  '-gdb-set print object on', '-gdb-set breakpoint pending on',
                  '-gdb-set disable-randomization %s' % ('on' if self.aslr_can_be_disabled() else 'off'),
                  '-enable-pretty-printing'):
            g.send(c, quiet=True)
        g.send('-gdb-version')
        self.setup_inferior_tty()
        o = self.opts
        if o.cd:
            g.send(cli('cd %s' % o.cd), errcb=self.cmd_error)
        if o.program:
            self.load_program(o.program, o.args, from_cmdline=True)
        elif o.args:
            self.msg('Program arguments given without a program', 'err')
        for d in o.directory or []:
            g.send(cli('directory %s' % d), quiet=True)
        if o.pid:
            self.attach(o.pid)
        if o.core:
            self.open_core(o.core)
        for x in o.command or []:
            g.send(cli('source %s' % x), errcb=self.cmd_error)
        for ex in o.eval_command or []:
            g.send(cli(ex), errcb=self.cmd_error)
        if self.mouse:
            self.set_mouse(True)
        if o.syscalls or o.syscall_filter:
            self.act_syscall_record(preset=o.syscall_filter or '')
        if not (o.program or o.pid or o.core):
            self.msg('No program loaded. Ctrl+X -> "Load Program", or type "file PROG" in the Console.', 'warn')

    # ------------------------------------------------------------------ messages
    def msg(self, text, kind='ok'):
        self.message = (text, kind, time.time())
        self.message_dismissed = False
        self.dirty = True

    def error(self, text):
        self.msg(text, 'err')
        self.console_write('error: %s\n' % text, 'err')

    def aslr_can_be_disabled(self):
        """gdb normally turns address randomization off for the program (stable addresses between runs).
        Containers often forbid that (gdb then warns 'Operation not permitted'); test it in a child process."""
        setting = self.config.get('disable_aslr')
        if setting is not None:
            return bool(setting)
        try:
            import ctypes
            pid = os.fork()
            if pid == 0:
                ok = 1
                try:
                    libc = ctypes.CDLL(None, use_errno=True)
                    cur = libc.personality(0xffffffff)
                    ok = 0 if cur != -1 and libc.personality(cur | 0x0040000) != -1 else 1
                except Exception:
                    ok = 1
                os._exit(ok)
            _p, status = os.waitpid(pid, 0)
            return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0
        except Exception:
            return False

    def cmd_error(self, r):
        text = r['results'].get('msg', 'command failed')
        bad = re.findall(r'Cannot insert breakpoint (-?\d+)', text)
        if bad:
            self.console_write('error: %s\n' % text.strip(), 'err')
            self.pending_popups.append(lambda: self.fix_uninsertable(bad, text))
            return
        self.error(text)

    def fix_uninsertable(self, nums, text):
        descr = []
        for n in nums:
            b = self.find_bp(n) or {}
            descr.append('  %s: %s' % (n, bp_location_text(b) or '?'))
        m = re.search(r'address (0x[0-9a-fA-F]+)', text)
        lines = ['gdb could not insert breakpoint%s %s%s.' % ('s' if len(nums) > 1 else '', ', '.join(nums),
                                                          ' at ' + m.group(1) if m else ''), ''] + descr + [
            '',
            'Usually the breakpoint was set on a memory address in an earlier run of the program.',
            'The program now loads at a different address (address randomization), so that',
            'address is no longer valid. Breakpoints on source lines or functions do not have',
            'this problem.']
        self.show_text('Breakpoint Problem', lines)
        choice = self.choose('What should happen to breakpoint%s %s?' % ('s' if len(nums) > 1 else '',
                                                                       ', '.join(nums)), [
            ('Delete and continue the program', 'delete', ''),
            ('Disable and continue the program', 'disable', ''),
            ('Leave it (the program stays paused)', None, '')])
        if not choice:
            return
        for n in nums:
            self.gdb.send(cli('%s %s' % (choice, n)), quiet=True)
        self.refresh_breakpoints()
        if self.inferior_pid:
            self.exec_cmd('-exec-continue')
        else:
            self.run_program()

    def console_write(self, text, attr=None):
        self.console.write(text, attr)
        self.dirty = True

    # ------------------------------------------------------------------ drawing helpers
    def put(self, y, x, text, attr=0, maxw=None):
        if y < 0 or y >= self.H or x < 0 or x >= self.W:
            return
        lim = self.W - x
        if maxw is not None:
            lim = min(lim, maxw)
        if lim <= 0:
            return
        text = text[:lim].translate(self.ctl_table)
        if self.ascii_only:
            text = text.encode('ascii', 'replace').decode('ascii')
        try:
            self.scr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def draw_segs(self, y, x, w, segs, base=0, skip=0, keep_base_color=False):
        cx = 0
        for text, style in segs:
            if skip:
                if len(text) <= skip:
                    skip -= len(text)
                    continue
                text = text[skip:]
                skip = 0
            if cx >= w:
                break
            if keep_base_color:
                attr = base
            else:
                attr = (self.A.get(style, 0) if style else 0) | base
            self.put(y, x + cx, text, attr, w - cx)
            cx += len(text)
        return cx

    def box(self, y, x, h, w, title=''):
        attr = self.A['popup']
        for i in range(h):
            self.put(y + i, x, ' ' * w, attr, w)
        try:
            self.scr.attron(attr)
            self.scr.hline(y, x + 1, curses.ACS_HLINE, w - 2)
            self.scr.hline(y + h - 1, x + 1, curses.ACS_HLINE, w - 2)
            self.scr.vline(y + 1, x, curses.ACS_VLINE, h - 2)
            self.scr.vline(y + 1, x + w - 1, curses.ACS_VLINE, h - 2)
            self.scr.addch(y, x, curses.ACS_ULCORNER)
            self.scr.addch(y, x + w - 1, curses.ACS_URCORNER)
            self.scr.addch(y + h - 1, x, curses.ACS_LLCORNER)
            if y + h - 1 < self.H - 1 or x + w < self.W:
                self.scr.addch(y + h - 1, x + w - 1, curses.ACS_LRCORNER)
        except curses.error:
            pass
        finally:
            try:
                self.scr.attroff(attr)
            except curses.error:
                pass
        if title:
            self.put(y, x + 2, ' %s ' % title, attr | curses.A_BOLD, w - 4)

    # ------------------------------------------------------------------ layout & draw
    def layout(self):
        H, W = self.H, self.W
        avail = H - 2
        if self.zoom and self.zoom in self.panes:
            return {self.zoom: (1, 0, avail, W)}
        bh = int(avail * self.bottom_ratio)
        bh = max(4, min(avail - 5, bh))
        mh = avail - bh
        rw = int(W * self.right_ratio) if W >= 60 else 0
        rects = {'bottom': (1 + mh, 0, bh, W)}
        if rw:
            rects['source'] = (1, 0, mh, W - rw - 1)
            rects['right'] = (1, W - rw, mh, rw)
        else:
            rects['source'] = (1, 0, mh, W)
        return rects

    def pane_order(self):
        return [p for p in ('source', 'right', 'bottom') if p in self.layout()] or ['source']

    def draw(self):
        self.H, self.W = self.scr.getmaxyx()
        self.scr.erase()
        self.cursor_req = None
        self.tab_hits = []
        self.hint_hits = []
        if self.H < 10 or self.W < 40:
            self.put(0, 0, 'Terminal too small (%dx%d). Need 40x10.' % (self.W, self.H))
            self.scr.noutrefresh()
            curses.doupdate()
            self.dirty = False
            return
        self.draw_status()
        self.rects = self.layout()
        if self.focus not in self.rects:
            self.focus = list(self.rects.keys())[0]
        for pane, rect in self.rects.items():
            self.draw_pane(pane, rect)
        if 'right' in self.rects:
            y, x, h, _w = self.rects['right']
            try:
                self.scr.vline(y, x - 1, curses.ACS_VLINE, h)
            except curses.error:
                pass
        self.draw_hints()
        for p in self.popups:
            p.draw(self)
        req = self.cursor_req
        try:
            if req:
                if self._cursor_vis != 1:
                    curses.curs_set(1)
                    self._cursor_vis = 1
                self.scr.move(max(0, min(self.H - 1, req[0])), max(0, min(self.W - 1, req[1])))
            elif self._cursor_vis != 0:
                curses.curs_set(0)
                self._cursor_vis = 0
        except curses.error:
            pass
        self.scr.noutrefresh()
        curses.doupdate()
        self.dirty = False

    def draw_status(self):
        """Bottom line: program and state, then the latest message, then flags and the current frame.
        A message pushes the frame information off the line when both don't fit; after 8 seconds
        (or a click on the line) the message goes and the frame comes back."""
        y = self.H - 1
        st = {'idle': 'Not started', 'running': 'Running', 'stopped': 'Paused', 'exited': 'Exited'}[self.state]
        if self.core and self.state == 'stopped':
            st = 'Core dump'
        prog = os.path.basename(self.program) if self.program else '(no program)'
        if self.inferior_pid and self.state in ('running', 'stopped'):
            prog += ' [pid %s]' % self.inferior_pid
        left = ' gdbtui  %s  | %s' % (prog, st)
        if self.stop_reason and self.state in ('stopped', 'exited'):
            left += ': ' + self.stop_reason
        right = ''
        if self.state == 'stopped' and self.frame:
            right = 'Thread %s  %s ' % (self.thread_id or '?', frame_label(self.frame))
        flags = []
        if self.hex:
            flags.append('HEX')
        if self.mouse:
            flags.append('MOUSE')
        if self.sysrec:
            flags.append('SYSCALL REC')
        if self.strace and not self.strace.get('done'):
            flags.append('STRACE')
        if flags:
            right = '[%s] %s' % (' '.join(flags), right)
        attr = self.A['status']
        W = self.W - 1          # writing the bottom-right cell can scroll some terminals
        self.put(y, 0, ' ' * W, attr)
        self.put(y, 0, left, attr, W)
        x = len(left) + 2
        text, kind, ts = self.message
        if text and time.time() - ts < 8 and not self.message_dismissed and x < W - 4:
            mattr = {'err': self.A['err'], 'warn': self.A['warn'], 'ok': self.A['ok']}.get(kind, 0)
            if not curses.has_colors():
                mattr |= curses.A_REVERSE
            msg = ' ' + text + ' '
            self.put(y, x, msg, mattr, W - x)
            x += len(msg) + 2
        if right and x + len(right) <= W:
            self.put(y, W - len(right), right, attr)
        elif right and x < W:
            self.put(y, x, right, attr, W - x)     # narrow terminal: right after the state, cut off

    def draw_hints(self):
        """Top line: the debugging shortcuts. Nothing else is drawn here, so they are always
        visible, and with mouse support on, clicking one runs it."""
        y = 0
        self.put(y, 0, ' ' * self.W, self.A['hint'])
        acts = ['continue', 'step_over', 'step_into', 'step_out', 'toggle_bp', 'run_to_cursor',
                'quickwatch', 'add_watch', 'toggle_disasm', 'menu', 'help']
        labels = {'continue': 'Run' if self.state in ('idle', 'exited') else 'Cont', 'step_over': 'Over',
                  'step_into': 'Into', 'step_out': 'Out', 'toggle_bp': 'Bkpt', 'run_to_cursor': 'ToCursor',
                  'quickwatch': 'Eval', 'add_watch': 'Watch', 'toggle_disasm': 'Asm', 'menu': 'Menu',
                  'help': 'Help'}
        if self.state == 'running':
            acts = ['break_all', 'stop', 'toggle_bp', 'menu', 'help']
            labels.update({'break_all': 'Break', 'stop': 'Stop'})
        x = 1
        for a in acts:
            code = self.action_keys.get(a)
            if not code:
                continue
            k = short_key(code)
            if x + len(k) + len(labels[a]) + 2 >= self.W:
                break
            self.put(y, x, k, self.A['hintkey'])
            self.put(y, x + len(k), labels[a] + ' ', self.A['hint'])
            self.hint_hits.append((y, x, x + len(k) + len(labels[a]), a))
            x += len(k) + len(labels[a]) + 2

    def draw_pane(self, pane, rect):
        y, x, h, w = rect
        focused = (pane == self.focus)
        views = self.panes[pane]
        idx = self.active[pane]
        hattr = self.A['hdr_focus'] if focused else self.A['hdr']
        self.put(y, x, ' ' * w, hattr, w)
        if pane == 'source':
            sv = self.src_view
            if self.disasm:
                t = ' Disassembly'
                row = sv.selected_dis()
                func = (row or {}).get('func') or (self.frame.get('func') if self.state == 'stopped' else None)
                if func and func != '??':
                    t += ': ' + func
                elif sv.dis_rows:
                    t += ': (no symbol here)'
            elif len(sv.tabs) > 1:
                self.draw_file_tabs(y, x, w, hattr, focused)
                return self.draw_pane_body(pane, rect, focused)
            elif sv.file:
                t = ' %s  [line %d/%d, col %d]' % (sv.file.path, sv.cur + 1, len(sv.file.lines), sv.col + 1)
            else:
                t = ' Source'
            if self.zoom:
                t += '   [zoomed: Ctrl+Z to restore]'
            self.put(y, x, t, hattr | curses.A_BOLD, w)
        else:
            cx = x + 1
            for i, v in enumerate(views):
                label = ' %s ' % v.title
                if v is self.v_bps and self.breakpoints:
                    label = ' %s (%d) ' % (v.title, len([b for b in self.breakpoints if '.' not in str(b.get('number'))]))
                if v is self.v_watch and self.watches:
                    label = ' %s (%d) ' % (v.title, len(self.watches))
                if cx + len(label) >= x + w:
                    break
                a = (self.A['tab_focus'] if focused else self.A['tab']) if i == idx else hattr
                self.put(y, cx, label, a)
                self.tab_hits.append((y, cx, cx + len(label), pane, i))
                cx += len(label) + 1
            if self.zoom:
                self.put(y, x + w - 10, '[zoomed]', hattr, 9)
        self.draw_pane_body(pane, rect, focused)

    def draw_pane_body(self, pane, rect, focused):
        y, x, h, w = rect
        if h > 1:
            self.panes[pane][self.active[pane]].draw(self, y + 1, x, h - 1, w, focused)

    def draw_file_tabs(self, y, x, w, hattr, focused):
        sv = self.src_view
        labels = sv.tab_labels()
        info = ' Ln %d/%d Col %d ' % (sv.cur + 1, len(sv.file.lines), sv.col + 1) if sv.file else ''
        if self.zoom:
            info = '[zoomed] ' + info
        avail = max(10, w - len(info) - 1)
        marks = ['>' if (self.exec_path and t['file'].path == self.exec_path) else ' ' for t in sv.tabs]
        widths = [len(l) + 3 for l in labels]
        start = 0
        while start < sv.active_tab and sum(widths[start:sv.active_tab + 1]) + 2 > avail:
            start += 1
        cx = x
        if start:
            self.put(y, cx, '<', hattr | curses.A_BOLD)
            cx += 1
        for i in range(start, len(labels)):
            lab = '%s%s ' % (marks[i], labels[i])
            if cx + len(lab) + 1 > x + avail:
                self.put(y, x + avail - 1, '>', hattr | curses.A_BOLD)
                break
            a = (self.A['tab_focus'] if focused else self.A['tab']) if i == sv.active_tab else hattr
            self.put(y, cx, lab, a)
            self.tab_hits.append((y, cx, cx + len(lab), 'srcfile', i))
            cx += len(lab) + 1
        self.put(y, x + w - len(info), info, hattr, len(info))

    # ------------------------------------------------------------------ main loop
    def wait_input(self, timeout):
        fds = [sys.stdin.fileno()]
        if self.gdb.alive:
            fds.append(self.gdb.fd)
        if self.pty_master is not None:
            fds.append(self.pty_master)
        try:
            r, _w, _x = select.select(fds, [], [], timeout)
        except (OSError, select.error, ValueError):
            r = []
        if self.gdb.alive and self.gdb.fd in r:
            for rec in self.gdb.read():
                try:
                    self.on_record(rec)
                except Exception:
                    self.console_write(traceback.format_exc(), 'err')
            if not self.gdb.alive:
                self.on_gdb_exit()
        if self.pty_master is not None and self.pty_master in r:
            self.read_pty()
        if self.out_file:
            self.poll_out_file()
        if self.strace or self.resolver:
            self.poll_strace()
        self.poll_running_views()
        if self.message[0] and 8 < time.time() - self.message[2] < 8.5:
            self.dirty = True
        return self.read_keys()

    def read_keys(self):
        keys = []
        while len(keys) < 4096:
            try:
                k = self.scr.get_wch()
            except curses.error:
                break
            except KeyboardInterrupt:
                k = 3
            if isinstance(k, str):
                o = ord(k) if len(k) == 1 else 0
                if o < 32 or o == 127:
                    k = o
            keys.append(k)
        # drop Alt+key sequences (ESC immediately followed by a key)
        out = []
        i = 0
        while i < len(keys):
            if keys[i] == KEY_ESC and i + 1 < len(keys) and keys[i + 1] != KEY_ESC and \
                    (isinstance(keys[i + 1], str) or keys[i + 1] < 256):
                i += 2
                continue
            out.append(keys[i])
            i += 1
        return out

    def run(self):
        self.start()
        self.draw()
        while not self.quitting:
            for k in self.wait_input(0.1):
                try:
                    self.handle_key(k)
                except Exception:
                    self.console_write(traceback.format_exc(), 'err')
                    self.msg('Internal error - see Console', 'err')
                self.dirty = True
                if self.quitting:
                    break
            self.run_click_repeat()
            while self.pending_popups and not self.quitting:
                fn = self.pending_popups.pop(0)
                try:
                    fn()
                except Exception:
                    self.console_write(traceback.format_exc(), 'err')
            if self.dirty and not self.quitting:
                self.draw()

    def run_click_repeat(self):
        rep = self.click_repeat
        if not rep:
            return
        act, gen, t0 = rep
        if time.time() - t0 > 5:
            self.click_repeat = None
        elif self.stop_gen > gen and self.state != 'running':
            self.click_repeat = None
            if self.state == 'stopped' and not self.popups:
                getattr(self, 'act_' + act)()
                self.dirty = True

    def modal(self, popup):
        self.popups.append(popup)
        try:
            self.dirty = True
            while not self.quitting:
                if self.dirty:
                    self.draw()
                for k in self.wait_input(0.1):
                    if k == curses.KEY_RESIZE:
                        self.dirty = True
                        continue
                    if k == curses.KEY_MOUSE:
                        try:
                            curses.getmouse()
                        except curses.error:
                            pass
                        continue
                    res = popup.key(self, k)
                    self.dirty = True
                    if res is not None:
                        return res[1]
        finally:
            self.popups.remove(popup)
            self.dirty = True
        return None

    def prompt(self, title, initial='', hist_key=None, hint=''):
        hist = self.hist.setdefault(hist_key or title, [])
        return self.modal(InputPopup(title, initial, hist, hint))

    def choose(self, title, items, allow_free=False, hint=''):
        return self.modal(ListPopup(title, items, allow_free, hint))

    def show_text(self, title, lines):
        self.modal(TextPopup(title, lines))

    def on_gdb_exit(self):
        self.msg('GDB exited. Press any key to quit.', 'err')
        self.console_write('*** gdb process exited ***\n', 'err')
        self.draw()
        self.scr.nodelay(False)
        try:
            self.scr.get_wch()
        except Exception:
            pass
        self.quitting = True

    # ------------------------------------------------------------------ keys
    def handle_key(self, k):
        if k == curses.KEY_RESIZE:
            self.dirty = True
            return
        if k == curses.KEY_MOUSE:
            self.on_mouse()
            return
        self.click_repeat = None
        if isinstance(k, int) and k in self.keymap:
            act = self.keymap[k]
            fn = getattr(self, 'act_' + act, None)
            if fn:
                fn()
            return
        if k == 9:
            self.act_focus_next()
            return
        if k == curses.KEY_BTAB:
            order = self.pane_order()
            i = order.index(self.focus) if self.focus in order else 0
            self.focus = order[(i - 1) % len(order)]
            return
        if k == KEY_ESC:
            if self.zoom:
                self.zoom = None
            return
        view = self.cur_view()
        if view.key(self, k):
            return
        if view.arrows_switch_tabs and k in (curses.KEY_LEFT, curses.KEY_RIGHT) and self.focus != 'source':
            self.switch_tab(self.focus, 1 if k == curses.KEY_RIGHT else -1)
            return
        if isinstance(k, str) and k in '123456789' and self.focus != 'source' and \
                not isinstance(view, ConsoleView):
            i = int(k) - 1
            if i < len(self.panes[self.focus]):
                self.show_tab(self.focus, i)

    def cur_view(self):
        return self.panes[self.focus][self.active[self.focus]]

    def switch_tab(self, pane, delta):
        n = len(self.panes[pane])
        self.show_tab(pane, (self.active[pane] + delta) % n)

    def show_tab(self, pane, idx):
        self.active[pane] = idx
        v = self.panes[pane][idx]
        if self.state == 'stopped' or isinstance(v, (ModulesView, ThreadsView, FilesView)):
            v.on_show(self)
        self.dirty = True

    def show_view(self, view):
        for pane, views in self.panes.items():
            if view in views:
                if self.zoom and self.zoom != pane:
                    self.zoom = None
                self.show_tab(pane, views.index(view))
                self.focus = pane
                return

    def is_visible(self, view):
        for pane, views in self.panes.items():
            if view in views:
                return views[self.active[pane]] is view and (not self.zoom or self.zoom == pane)
        return False

    def set_mouse(self, on):
        self.mouse = on
        try:
            if on:
                curses.mousemask(curses.ALL_MOUSE_EVENTS | getattr(curses, 'REPORT_MOUSE_POSITION', 0) * 0)
                curses.mouseinterval(200)
            else:
                curses.mousemask(0)
        except curses.error:
            pass

    def on_mouse(self):
        try:
            _id, mx, my, _z, bs = curses.getmouse()
        except curses.error:
            return
        wheel_up = bs & getattr(curses, 'BUTTON4_PRESSED', 0)
        wheel_dn = bs & getattr(curses, 'BUTTON5_PRESSED', 0x200000)
        click = bs & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED)
        double = bs & (curses.BUTTON1_DOUBLE_CLICKED)
        if (click or double) and not (bs & curses.BUTTON1_RELEASED and not bs & curses.BUTTON1_CLICKED):
            if my == 0:
                # Top line: a click on a shortcut runs it, exactly as if its key was pressed.
                for (ty, x0, x1, act) in self.hint_hits:
                    if x0 <= mx < x1:
                        fn = getattr(self, 'act_' + act, None)
                        if fn:
                            gen = self.stop_gen
                            fn()
                            # curses reports two fast clicks as one double click. For the
                            # execution commands that means "do it twice": the second one
                            # runs when the program stops after the first.
                            if double and act in REPEATABLE_CLICK_ACTIONS:
                                self.click_repeat = (act, gen, time.time())
                        self.dirty = True
                        return
                return
            if my == self.H - 1:
                # Status line: a click clears the message, bringing back the frame information.
                self.message_dismissed = True
                self.dirty = True
                return
            for (ty, x0, x1, pane, i) in self.tab_hits:
                if my == ty and x0 <= mx < x1:
                    if pane == 'srcfile':
                        self.focus = 'source'
                        self.switch_file(i)
                        return
                    self.focus = pane
                    self.show_tab(pane, i)
                    return
        for pane, (y, x, h, w) in self.rects.items():
            if y <= my < y + h and x <= mx < x + w:
                view = self.panes[pane][self.active[pane]]
                if wheel_up:
                    view.scroll(self, -3)
                elif wheel_dn:
                    view.scroll(self, 3)
                elif click or double:
                    self.focus = pane
                    if my > y and not (bs & curses.BUTTON1_RELEASED):
                        view.click(self, my - y - 1, mx - x, bool(double))
                return

    # ------------------------------------------------------------------ gdb records
    def on_record(self, r):
        t = r['type']
        self.dirty = True
        if t in ('console', 'target', 'log'):
            text = r['text']
            if self.gdb.order:
                p = self.gdb.pending.get(self.gdb.order[0])
                if p and p[3] is not None:
                    p[3].append(text)
                    return
            self.console_write(text, 'dim' if t == 'log' else None)
        elif t == 'result':
            if r['results'].get('_parse_error'):
                self.console_write('warning: could not fully parse gdb output: %s\n' % r['raw'][:200], 'dim')
            tok = r.get('token')
            p = self.gdb.pending.pop(tok, None) if tok is not None else None
            if tok in self.gdb.order:
                self.gdb.order.remove(tok)
            if p is None:
                return
            cb, errcb, quiet, cap = p
            if cap is not None:
                r['captured'] = ''.join(cap)
            if r['class'] == 'error':
                if errcb:
                    errcb(r)
                elif not quiet:
                    self.cmd_error(r)
            elif cb:
                cb(r)
        elif t == 'exec':
            if r['class'] == 'running':
                self.on_running(r)
            elif r['class'] == 'stopped':
                self.on_stopped(r)
        elif t == 'notify':
            self.on_notify(r)
        elif t == 'other':
            self.console_write(r['text'] + '\n')

    def poll_running_views(self):
        """While the program runs, keep the Threads list current (gdb can list threads without stopping)."""
        if self.state == 'running' and self.is_visible(self.v_threads) and \
                time.time() - self.threads_refreshed > 1.0:
            self.refresh_threads()

    threads_refreshed = 0.0

    def on_running(self, r):
        for t in self.threads:
            if r['results'].get('thread-id') in ('all', t.get('id')):
                t['state'] = 'running'
                t.pop('frame', None)
        self.state = 'running'
        self.stop_reason = ''
        self.dirty = True

    def on_notify(self, r):
        c = r['class']
        res = r['results']
        if c == 'thread-group-started':
            self.inferior_pid = res.get('pid')
        elif c == 'thread-group-exited':
            if self.state != 'exited' and 'exit-code' in res:
                self.stop_reason = 'exit code %s' % parse_int(res.get('exit-code'))
        elif c.startswith('breakpoint-'):
            self.refresh_breakpoints()
        elif c in ('thread-created', 'thread-exited'):
            self.refresh_threads()
        elif c == 'library-loaded' and self.is_visible(self.v_modules):
            self.refresh_modules()

    def on_stopped(self, r):
        res = r['results']
        reason = res.get('reason', '')
        if isinstance(reason, list):
            reason = reason[0]
        hook = self.syscall_stop_hook(res, reason)
        if hook == 'step-done':
            reason = 'end-stepping-range'
        elif hook:
            return
        self.stop_gen += 1
        if reason.startswith('exited'):
            if reason == 'exited-normally':
                self.stop_reason = 'exited normally (code 0)'
            elif reason == 'exited':
                self.stop_reason = 'exited with code %s' % parse_int(res.get('exit-code'))
            else:
                self.stop_reason = 'terminated by %s %s' % (res.get('signal-name', ''), res.get('signal-meaning', ''))
            self.msg('Program ' + self.stop_reason, 'warn')
            self.output.write('\n---- program %s ----\n' % self.stop_reason, 'dim')
            self.program_gone('exited')
            self.run_after_stop()
            return
        self.state = 'stopped'
        self.core = self.core and not self.inferior_pid
        if 'thread-id' in res:
            self.thread_id = res.get('thread-id')
        frame = res.get('frame') or {}
        frame['level'] = '0'
        self.frame = frame
        interrupted = self.interrupting
        self.interrupting = False
        self.stop_reason = self.describe_stop(res, reason, interrupted)
        if self.run_after_stop():
            return
        self.show_frame(frame, center=True)
        if self.auto_open == 'all' and not self.auto_open_ext_done:
            # shared libraries are loaded now: their sources may be listed too
            self.auto_open_ext_done = True
            self.refresh_source_files(lambda: self.auto_open_sources(quiet=True))
        self.refresh_after_stop()
        if reason == 'signal-received' and not interrupted and \
                res.get('signal-name') in ('SIGSEGV', 'SIGBUS', 'SIGFPE', 'SIGILL', 'SIGABRT', 'SIGSYS'):
            name = res.get('signal-name', '')
            meaning = res.get('signal-meaning', '')
            lines = ['Exception thrown: %s (%s)' % (name, meaning), '',
                     'at %s' % frame_label(frame), '',
                     'Ctrl+R continues (passes the signal to the program), Ctrl+K stops.',
                     'See Call Stack (bottom panel) for the full stack.']
            self.pending_popups.append(lambda: self.show_text('Exception', lines))

    def describe_stop(self, res, reason, interrupted):
        if reason == 'breakpoint-hit':
            if self.entry_stop:
                self.entry_stop = False
                return 'Stopped at program entry point (no main symbol)'
            return 'Breakpoint %s hit' % res.get('bkptno', '')
        if reason == 'end-stepping-range':
            return 'Step complete'
        if reason == 'function-finished':
            rv = res.get('return-value')
            if rv is not None:
                s = 'Returned %s' % rv
                self.console_write('Step out: value returned is %s = %s\n' % (res.get('gdb-result-var', ''), rv), 'ok')
                return s
            return 'Step out complete'
        if reason == 'location-reached':
            return 'Location reached'
        if reason in ('watchpoint-trigger', 'access-watchpoint-trigger', 'read-watchpoint-trigger'):
            wpt = res.get('wpt') or res.get('hw-awpt') or res.get('hw-rwpt') or {}
            val = res.get('value') or {}
            if 'old' in val:
                return 'Data breakpoint %s: %s changed %s -> %s' % (wpt.get('number', ''), wpt.get('exp', ''),
                                                                  val.get('old'), val.get('new'))
            return 'Data breakpoint %s: %s = %s' % (wpt.get('number', ''), wpt.get('exp', ''),
                                                    val.get('new', val.get('value', '')))
        if reason == 'watchpoint-scope':
            return 'Data breakpoint %s went out of scope (deleted)' % res.get('wpnum', '')
        if reason == 'signal-received':
            if interrupted or res.get('signal-name') in ('SIGINT', '0'):
                return 'Paused (Break All)'
            return 'Signal %s (%s)' % (res.get('signal-name', ''), res.get('signal-meaning', ''))
        if reason == 'solib-event':
            return 'Shared library event'
        if reason == 'fork' or reason == 'vfork':
            return 'Fork'
        if reason == 'exec':
            return 'Exec'
        if reason == 'syscall-entry' or reason == 'syscall-return':
            return 'Syscall %s' % res.get('syscall-name', '')
        if not reason:
            return 'Paused'
        return reason

    def run_after_stop(self):
        if not self.after_stop:
            return False
        acts = self.after_stop
        self.after_stop = []
        for a in acts:
            a()
        return True

    def program_gone(self, state='idle'):
        self.state = state
        self.frame = {}
        self.exec_path = self.exec_line = self.exec_addr = None
        self.inferior_pid = None
        self.core = False
        self.attached = False
        self.clear_locals()
        self.stack = []
        self.stack_args = {}
        self.threads = []
        self.registers = []
        self.reg_prev = {}
        self.src_view.set_dis([])
        if self.disasm and not self.user_disasm:
            self.disasm = False
        if self.have_program and (self.no_debug or not self.src_view.file):
            self.show_static_disasm()
        self.refresh_breakpoints()
        self.dirty = True

    def clear_locals(self):
        for n in self.locals:
            self.delete_var(n)
        self.locals = []
        self.locals_sig = None

    # ------------------------------------------------------------------ source display
    def resolve_path(self, path):
        if not path:
            return None
        if os.path.isfile(path):
            return os.path.realpath(path)
        base = os.path.basename(path)
        for f in self.source_files:
            full = f.get('fullname') or f.get('file') or ''
            if (full.endswith('/' + path) or os.path.basename(full) == base) and os.path.isfile(full):
                return os.path.realpath(full)
        for d in self.opts.directory or []:
            cand = os.path.join(d, base)
            if os.path.isfile(cand):
                return os.path.realpath(cand)
        return None

    def load_source(self, path):
        rp = self.resolve_path(path)
        if not rp:
            return None
        sf = self.sources.get(rp)
        try:
            if sf is None or os.path.getmtime(rp) != sf.mtime:
                sf = SourceFile(rp, self.tabsize)
                self.sources[rp] = sf
        except (IOError, OSError) as e:
            self.msg('Cannot read %s: %s' % (rp, e), 'err')
            return None
        return sf

    def show_frame(self, frame, center=True):
        path = frame.get('fullname') or frame.get('file')
        line = parse_int(frame.get('line'))
        self.exec_addr = parse_int(frame.get('addr'))
        self.exec_top = str(frame.get('level', '0')) == '0'
        sf = self.load_source(path) if path else None
        if sf and line:
            self.exec_path = sf.path
            self.exec_line = line
            self.src_view.set_file(sf)
            self.src_view.goto_line(line - 1, center)
            self.auto_disasm = False
            if self.disasm and not self.user_disasm:
                self.disasm = False
        else:
            self.exec_path = None
            self.exec_line = None
            if not self.disasm:
                self.disasm = True
                self.user_disasm = False
                if path:
                    self.msg('Source file %s not found - showing disassembly '
                             '(Ctrl+X -> Source Path Substitution)' % path, 'warn')
        if self.disasm:
            self.refresh_disasm()
        self.dirty = True

    user_disasm = False

    # ------------------------------------------------------------------ refreshes
    def refresh_after_stop(self):
        self.update_vars()
        self.refresh_locals()
        self.retry_watches()
        self.refresh_stack()
        self.refresh_threads()
        self.refresh_breakpoints()
        if self.is_visible(self.v_regs):
            self.refresh_registers()
        if self.mem_addr is not None:
            self.refresh_memory(track_changes=True)
        if self.is_visible(self.v_modules):
            self.refresh_modules()

    def after_state_change(self):
        """Values may have changed (assignment etc.) - refresh value displays."""
        if self.state != 'stopped':
            return
        self.update_vars()
        self.refresh_stack()
        if self.is_visible(self.v_regs):
            self.refresh_registers()
        if self.mem_addr is not None:
            self.refresh_memory(track_changes=True)

    def update_vars(self):
        if not self.vobj_map:
            return

        def ok(r):
            for node in self.char_arrays:
                node.changed = False
            for n in self.vobj_map.values():
                n.changed = False
            for ch in as_list(r['results'].get('changelist')):
                if not isinstance(ch, dict):
                    continue
                node = self.vobj_map.get(ch.get('name'))
                if node is None:
                    continue
                if 'value' in ch:
                    node.value = ch['value']
                    node.changed = True
                isc = ch.get('in_scope', 'true')
                node.in_scope = (isc == 'true')
                if isc == 'invalid':
                    node.error = 'invalid'
                if ch.get('type_changed') == 'true':
                    node.type = ch.get('new_type', node.type)
                    if 'new_num_children' in ch:
                        node.numchild = parse_int(ch['new_num_children']) or 0
                    self.reset_children(node)
                elif 'new_num_children' in ch:
                    node.numchild = parse_int(ch['new_num_children']) or 0
                    self.reset_children(node)
                elif ch.get('new_children'):
                    self.reset_children(node)
                if 'has_more' in ch:
                    node.has_more = ch['has_more'] == '1'
            for node in list(self.char_arrays):
                if node.dead:
                    self.char_arrays.discard(node)
                elif node.in_scope:
                    self.eval_char_array(node)
            self.dirty = True
        self.gdb.send('-var-update --all-values *', ok, quiet=True)

    def refresh_locals(self, force=False):
        gen = self.stop_gen

        def ok(r):
            if gen != self.stop_gen and not force:
                return
            names = []
            for v in as_list(r['results'].get('variables')):
                nm = v.get('name') if isinstance(v, dict) else None
                if nm and nm not in names:
                    names.append(nm)
            sig = (self.thread_id, self.frame.get('level'), self.frame.get('func'),
                   self.frame.get('fullname') or self.frame.get('from'), tuple(names), self.hex)
            if sig == self.locals_sig and not force:
                return
            self.clear_locals()
            self.locals_sig = sig
            self.locals = [VarNode(nm, nm) for nm in names]
            for n in self.locals:
                self.create_var(n, '*')

        def err(r):
            self.clear_locals()
        self.gdb.send('-stack-list-variables 0', ok, errcb=err)

    def retry_watches(self):
        for w in self.watches:
            if not w.vobj and not w.loading:
                self.create_var(w, '@')

    def create_var(self, node, frame='@'):
        node.loading = True

        def ok(r):
            node.loading = False
            res = r['results']
            name = res.get('name')
            if node.dead:
                self.gdb.send('-var-delete ' + name, quiet=True)
                return
            node.vobj = name
            node.error = None
            node.in_scope = True
            self.vobj_map[name] = node
            self.fill_node(node, res)
            self.dirty = True

        def err(r):
            node.loading = False
            node.vobj = None
            node.value = ''
            node.error = r['results'].get('msg', 'error')
            self.dirty = True
            if 'unable to create variable object' in node.error:
                def better(r2):
                    node.error = r2['results'].get('msg', node.error)
                    self.dirty = True
                self.gdb.send('-data-evaluate-expression %s' % mi_quote(node.expr), errcb=better, quiet=True)
        self.gdb.send('-var-create - %s %s' % (frame, mi_quote(node.expr)), ok, errcb=err)

    def fill_node(self, node, res):
        if 'value' in res:
            node.value = res.get('value')
        node.type = res.get('type', node.type)
        node.numchild = parse_int(res.get('numchild')) or 0
        node.has_more = res.get('has_more') == '1'
        if res.get('displayhint'):
            node.displayhint = res.get('displayhint')
        if res.get('dynamic') == '1' and res.get('displayhint') in ('array', 'map') and 'value' not in res:
            node.value = '{...}'
        if node.vobj and re.match(r'^\[(0x)?[0-9a-fA-F]+\]$', node.value or '') and \
                re.match(r'^(const )?(unsigned |signed )?char\b|^(const )?(wchar_t|char16_t|char32_t|u?int8_t)\b',
                         node.type or ''):
            self.char_arrays.add(node)
            self.eval_char_array(node)

    def eval_char_array(self, node):
        def evaluate(expr):
            node.path = expr

            def ok(r):
                v = r['results'].get('value', '')
                v = re.sub(r'^0x[0-9a-fA-F]+ ', '', v)
                if node.strval is not None and node.strval != v:
                    node.changed = True
                node.strval = v
                if not node.dead:
                    node.value = v
                self.dirty = True
            self.gdb.send('-data-evaluate-expression %s' % mi_quote(expr), ok, quiet=True)
        if node.path:
            evaluate(node.path)
        elif node.parent is None and node.expr:
            evaluate(node.expr)
        elif node.vobj:
            self.gdb.send('-var-info-path-expression %s' % node.vobj,
                          lambda r: evaluate(r['results'].get('path_expr', node.name)), quiet=True)

    def expand(self, node):
        node.expanded = True
        if node.children is None:
            self.fetch_children(node)
        self.dirty = True

    def fetch_children(self, node):
        if not node.vobj or node.loading:
            return
        node.loading = True

        def ok(r):
            node.loading = False
            if node.dead:
                return
            res = r['results']
            kids = []
            ptype = (node.type or '').strip()
            for c in as_list(res.get('children')):
                if not isinstance(c, dict):
                    continue
                exp = c.get('exp', '?')
                ch = VarNode(exp)
                ch.vobj = c.get('name')
                ch.parent = node
                self.fill_node(ch, c)
                if exp in ('public', 'private', 'protected') and 'type' not in c:
                    ch.access = True
                elif exp.isdigit() and (ptype.endswith(']') or not node.access):
                    ch.name = '[%s]' % exp
                if ch.vobj:
                    self.vobj_map[ch.vobj] = ch
                kids.append(ch)
            if node.displayhint == 'map' and len(kids) >= 2:
                paired = []
                for i in range(0, len(kids) - 1, 2):
                    key, val = kids[i], kids[i + 1]
                    val.name = '[%s]' % key.value
                    node.hidden.append(key)
                    paired.append(val)
                kids = paired
            node.children = kids
            node.has_more = res.get('has_more') == '1'
            for k in kids:
                if k.access:
                    k.expanded = True
                    self.fetch_children(k)
            self.dirty = True

        def err(r):
            node.loading = False
            node.children = []
            self.msg('Cannot expand %s: %s' % (node.name, r['results'].get('msg', '')), 'err')
        self.gdb.send('-var-list-children --all-values %s 0 2000' % node.vobj, ok, errcb=err)

    def forget(self, node):
        node.dead = True
        if node.vobj and self.vobj_map.get(node.vobj) is node:
            del self.vobj_map[node.vobj]
        for c in (node.children or []) + node.hidden:
            self.forget(c)

    def reset_children(self, node):
        if node.children:
            for c in node.children + node.hidden:
                self.forget(c)
            node.hidden = []
            if node.vobj:
                self.gdb.send('-var-delete -c ' + node.vobj, quiet=True)
        node.children = None
        if node.expanded:
            self.fetch_children(node)

    def delete_var(self, node):
        vobj = node.vobj
        self.forget(node)
        if vobj:
            self.gdb.send('-var-delete ' + vobj, quiet=True)

    def refresh_stack(self):
        def ok(r):
            self.stack = [f for f in as_list(r['results'].get('stack')) if isinstance(f, dict)]
            if self.stack:
                self.gdb.send('-stack-list-arguments 2 0 %d' % (len(self.stack) - 1), ok_args, quiet=True)
            self.dirty = True

        def ok_args(r):
            d = {}
            for f in as_list(r['results'].get('stack-args')):
                if isinstance(f, dict):
                    d[str(f.get('level'))] = as_list(f.get('args'))
            self.stack_args = d
            self.dirty = True

        def err(r):
            self.stack = []
            self.stack_args = {}
        self.gdb.send('-stack-list-frames 0 299', ok, errcb=err)

    def refresh_threads(self):
        self.threads_refreshed = time.time()

        def ok(r):
            res = r['results']
            old = dict((t.get('id'), t) for t in self.threads)
            self.threads = [t for t in as_list(res.get('threads')) if isinstance(t, dict)]
            if res.get('current-thread-id'):
                self.thread_id = res.get('current-thread-id')
            if self.state == 'stopped':
                # where is each thread in the user's own code? (it is usually waiting inside libc)
                for t in self.threads:
                    self.fetch_thread_location(t)
            else:
                for t in self.threads:
                    prev = old.get(t.get('id'))
                    if prev and prev.get('user_where') and t.get('state') != 'running':
                        t['user_where'] = prev['user_where']
            self.dirty = True
        self.gdb.send('-thread-info', ok, quiet=True)

    def fetch_thread_location(self, t):
        def ok(r):
            frames = [f for f in as_list(r['results'].get('stack')) if isinstance(f, dict)]
            if frames:
                w = self.pick_user_frame(frames)
                if w.get('path') and frames[0].get('fullname') != w.get('path'):
                    t['user_where'] = w['text']
                    t['user_frame'] = w
                self.dirty = True
        self.gdb.send('-stack-list-frames --thread %s 0 30' % t.get('id'), ok, quiet=True)

    def refresh_breakpoints(self):
        self.gdb.send('-break-list', self.on_break_list, quiet=True)

    def on_break_list(self, r):
        body = (r['results'].get('BreakpointTable') or {}).get('body')
        bps = []
        for b in as_list(body):
            if not isinstance(b, dict):
                continue
            bps.append(b)
            for loc in as_list(b.get('locations')):
                if isinstance(loc, dict):
                    bps.append(loc)
        self.breakpoints = bps
        bp_map = {}
        bp_base = {}
        bp_addrs = {}
        parents = {}
        for b in bps:
            num = str(b.get('number', ''))
            if '.' not in num:
                parents[num] = b
        for b in bps:
            num = str(b.get('number', ''))
            parent = parents.get(num.split('.')[0], b)
            if parent.get('disp') == 'del':
                continue
            ptype = parent.get('type', 'breakpoint')
            if 'watchpoint' in ptype or ptype == 'catchpoint':
                continue
            en = b.get('enabled', 'y') == 'y' and parent.get('enabled', 'y') == 'y'
            ch = 'B'
            if parent.get('cond'):
                ch = 'C'
            if ptype == 'dprintf':
                ch = 'L'
            if parent.get('ignore'):
                ch = 'H'
            if not en:
                ch = ch.lower()
            line = parse_int(b.get('line'))
            path = b.get('fullname') or b.get('file')
            if not (path and line) and b.get('pending'):
                m = re.match(r'^(.*):(\d+)$', b.get('pending') or b.get('original-location') or '')
                if m:
                    path, line, ch = m.group(1), int(m.group(2)), 'P' if en else 'p'
            if path and line:
                rp = os.path.realpath(path) if os.path.isabs(path) else path
                bp_map.setdefault(rp, {})[line] = (ch, en, num)
                bp_base.setdefault(os.path.basename(path), {})[line] = (ch, en, num)
            addr = parse_int(b.get('addr')) if b.get('addr') not in (None, '<MULTIPLE>', '<PENDING>') else None
            if addr is not None:
                bp_addrs[addr] = (ch, en, num)
        self.bp_map = bp_map
        self.bp_base = bp_base
        self.bp_addrs = bp_addrs
        self.dirty = True
        self.autosave()

    def autosave(self):
        if self.session_ready and not self.opts.no_session:
            self.save_session()

    def bp_lines_for(self, path):
        d = self.bp_map.get(path)
        if d is not None:
            return d
        return self.bp_base.get(os.path.basename(path), {}) if not any(
            os.path.basename(p) == os.path.basename(path) for p in self.bp_map) else {}

    def refresh_registers(self):
        if self.state != 'stopped':
            return
        if self.reg_names is None:
            def ok_names(r):
                self.reg_names = as_list(r['results'].get('register-names'))
                self.refresh_registers()
            self.gdb.send('-data-list-register-names', ok_names, quiet=True)
            return

        def ok(r):
            regs = []
            prev = self.reg_prev
            cur = {}
            for v in as_list(r['results'].get('register-values')):
                if not isinstance(v, dict):
                    continue
                num = parse_int(v.get('number'))
                if num is None or num >= len(self.reg_names) or not self.reg_names[num]:
                    continue
                name = self.reg_names[num]
                val = v.get('value', '')
                cur[name] = val
                regs.append((name, val, name in prev and prev[name] != val))
            self.registers = regs
            self.reg_prev = cur
            self.dirty = True
        self.gdb.send('-data-list-register-values x', ok, quiet=True)

    def refresh_modules(self):
        def ok(r):
            self.modules = [m for m in as_list(r['results'].get('shared-libraries')) if isinstance(m, dict)]
            self.dirty = True

        def err(r):
            def ok2(r2):
                self.modules = [l for l in r2.get('captured', '').splitlines() if l.strip()]
                self.dirty = True
            self.gdb.send(cli('info sharedlibrary'), ok2, quiet=True, capture=True)
        self.gdb.send('-file-list-shared-libraries', ok, errcb=err)

    def refresh_source_files(self, then=None):
        def ok(r):
            files = []
            seen = set()
            for f in as_list(r['results'].get('files')):
                if isinstance(f, dict):
                    key = f.get('fullname') or f.get('file')
                    if key and key not in seen:
                        seen.add(key)
                        files.append(f)
            self.source_files = files
            if then:
                then()

        def err(r):
            if then:
                then()
        self.gdb.send('-file-list-exec-source-files', ok, errcb=err)

    def refresh_memory(self, track_changes=False):
        if self.mem_addr is None or self.state not in ('stopped',):
            return
        size = 2048
        start = self.mem_addr

        def ok(r):
            data = [None] * size
            for chunk in as_list(r['results'].get('memory')):
                if not isinstance(chunk, dict):
                    continue
                b = parse_int(chunk.get('begin'))
                hexs = chunk.get('contents', '')
                if b is None:
                    continue
                for i in range(len(hexs) // 2):
                    off = b - start + i
                    if 0 <= off < size:
                        data[off] = int(hexs[2 * i:2 * i + 2], 16)
            if track_changes:
                self.mem_prev = dict((start + i, v) for i, v in enumerate(self.mem_bytes_full or []) if v is not None) \
                    if self.mem_bytes_start == start else self.mem_prev
            self.mem_bytes = data
            self.mem_bytes_full = data
            self.mem_bytes_start = start
            self.mem_error = '' if any(v is not None for v in data) else 'Memory at 0x%x is not readable' % start
            self.dirty = True

        def err(r):
            self.mem_bytes = []
            self.mem_error = r['results'].get('msg', 'cannot read memory')
        self.gdb.send('-data-read-memory-bytes 0x%x %d' % (start, size), ok, errcb=err)

    mem_bytes_full = None
    mem_bytes_start = None

    def mem_move(self, delta):
        if self.mem_addr is None:
            return
        self.mem_addr = max(0, self.mem_addr + delta)
        self.mem_prev = {}
        self.refresh_memory()

    def mem_goto_expr(self, expr):
        def ok(r):
            res = r['results']
            chunks = [c for c in as_list(res.get('memory')) if isinstance(c, dict)]
            addr = parse_int(chunks[0].get('begin')) if chunks else None
            if addr is None:
                self.msg('Cannot resolve %s' % expr, 'err')
                return
            self.mem_addr = addr
            self.mem_label = expr
            self.mem_prev = {}
            self.mem_error = ''
            self.show_view(self.v_mem)
            self.refresh_memory()

        def err(r):
            self.mem_error = r['results'].get('msg', 'error')
            self.msg('Memory: %s' % self.mem_error, 'err')
        self.gdb.send('-data-read-memory-bytes %s 1' % mi_quote(expr), ok, errcb=err)

    def refresh_disasm(self, force=False):
        sv = self.src_view
        self.dis_error = ''
        if self.state == 'stopped' and self.frame:
            pc = self.exec_addr
            if not force and pc is not None and sv.goto_addr(pc):
                return
            self.gdb.send('-data-disassemble -a $pc -- 1', self.on_disasm,
                          errcb=lambda r: self.gdb.send('-data-disassemble -s $pc -e "$pc + 256" -- 0',
                                                        self.on_disasm, errcb=self.on_dis_error))
        elif sv.file and not self.no_debug:
            self.gdb.send('-data-disassemble -f %s -l %d -n -1 -- 1' % (mi_quote(sv.file.path), sv.cur + 1),
                          self.on_disasm, errcb=lambda r: self.show_static_disasm())
        elif self.have_program:
            self.show_static_disasm()
        else:
            self.dis_error = 'Nothing to disassemble yet - load a program first.'

    def disasm_source_line(self, path, line):
        def ok(r):
            rows = self.parse_disasm(r)
            sv = self.src_view
            sv.set_dis(rows)
            target = None
            for i, rw in enumerate(rows):          # first instruction generated for that line (or later)
                if rw.get('addr') is None and rw.get('path') == path and (rw.get('line') or 0) >= line:
                    target = i + 1
                    break
            if target is None:
                target = next((i for i, rw in enumerate(rows) if rw.get('addr') is not None), 0)
            sv.dis_cur = min(target, max(0, len(rows) - 1))
            sv.center_pending = True
            self.dirty = True

        def err(r):
            self.msg('No code for line %d (%s) - showing the current instruction' %
                     (line, r['results'].get('msg', '')), 'warn')
            self.refresh_disasm(force=True)
        self.gdb.send('-data-disassemble -f %s -l %d -n -1 -- 1' % (mi_quote(path), line), ok, errcb=err)

    def on_dis_error(self, r):
        self.dis_error = 'Disassembly failed: ' + r['results'].get('msg', '')
        self.src_view.set_dis([])
        self.dirty = True

    def on_disasm(self, r):
        rows = self.parse_disasm(r)
        sv = self.src_view
        sv.set_dis(rows)
        if self.state == 'stopped' and self.exec_addr is not None and sv.goto_addr(self.exec_addr):
            pass
        else:
            for i, rw in enumerate(rows):
                if rw.get('addr') is not None:
                    sv.dis_cur = i
                    break
        self.dirty = True

    def parse_disasm(self, r):
        rows = []

        def add_insn(i):
            addr = parse_int(i.get('address'))
            fn = i.get('func-name')
            off = i.get('offset', '0')
            where = '<%s+%s>' % (fn, off) if fn else ''
            rows.append({'addr': addr, 'func': fn, 'off': parse_int(off) or 0,
                         'text': '0x%x %-28s %s' % (addr or 0, where, i.get('inst', ''))})
        for item in as_list(r['results'].get('asm_insns')):
            if not isinstance(item, dict):
                continue
            if 'line_asm_insn' in item:
                insns = [i for i in as_list(item.get('line_asm_insn')) if isinstance(i, dict)]
                if not insns:
                    continue
                ln = parse_int(item.get('line'))
                src = ''
                sf = self.load_source(item.get('fullname') or item.get('file')) \
                    if (item.get('fullname') or item.get('file')) else None
                if sf and ln and 0 < ln <= len(sf.lines):
                    src = sf.lines[ln - 1].strip()
                rows.append({'addr': None, 'line': ln, 'path': sf.path if sf else None,
                             'text': '%s:%s  %s' % (os.path.basename(item.get('file', '')), ln, src)})
                for i in insns:
                    add_insn(i)
            elif 'address' in item:
                add_insn(item)
        return rows

    def show_static_disasm(self, target=None, then=None):
        """Disassemble a function (default main, else the entry point) without a running process."""
        self.disasm = True
        self.dis_error = ''

        def got(r):
            self.on_disasm(r)
            if then:
                then()
            self.dirty = True

        def entry_point(r=None):
            def ok(r2):
                m = re.search(r'Entry point:\s*(0x[0-9a-fA-F]+)', r2.get('captured', ''))
                if not m:
                    self.dis_error = 'No symbols and no entry point found - nothing to disassemble.'
                    self.src_view.set_dis([])
                    self.dirty = True
                    return
                a = int(m.group(1), 16)
                self.gdb.send('-data-disassemble -s 0x%x -e 0x%x -- 0' % (a, a + 512), got,
                              errcb=self.on_dis_error)
            self.gdb.send(cli('info files'), ok, quiet=True, capture=True)
        name = target or ('main' if self.has_main else None)
        if not name:
            entry_point()
            return
        self.gdb.send('-data-disassemble -a %s -- 0' % mi_quote(name), got,
                      errcb=entry_point if not target else self.on_dis_error)

    def extend_disasm(self, direction):
        """Load more instructions after (direction > 0) or before (< 0) the current listing."""
        sv = self.src_view
        addrs = [rw['addr'] for rw in sv.dis_rows if rw.get('addr') is not None]
        if not addrs or self.dis_loading:
            return
        self.dis_loading = True
        lo, hi = min(addrs), max(addrs)

        def ok(r):
            self.dis_loading = False
            new = self.parse_disasm(r)
            if direction > 0:
                add = [rw for rw in new if rw.get('addr') is None or rw['addr'] > hi]
                if add and add[0].get('addr') is None and len(add) == 1:
                    add = []
                sv.dis_rows.extend(add)
            else:
                add = [rw for rw in new if rw.get('addr') is None or rw['addr'] < lo]
                while add and add[-1].get('addr') is None:
                    add.pop()
                sv.dis_rows[:0] = add
                sv.dis_cur += len(add)
                sv.dis_top += len(add)
            if not add:
                self.msg('No more instructions in that direction', 'warn')
            self.dirty = True

        def err(r):
            self.dis_loading = False
            if direction < 0:
                self.gdb.send('-data-disassemble -s 0x%x -e 0x%x -- 0' % (max(0, lo - 64), lo), ok,
                              errcb=lambda r2: setattr(self, 'dis_loading', False))
        if direction > 0:
            self.gdb.send('-data-disassemble -s 0x%x -e 0x%x -- 0' % (hi, hi + 512), ok, errcb=err)
        else:
            self.gdb.send('-data-disassemble -a 0x%x -- 0' % (lo - 1), ok, errcb=err)

    dis_loading = False
    has_main = True
    is_pie = False

    # ------------------------------------------------------------------ program control
    def load_program(self, path, args=None, from_cmdline=False):
        self.program = os.path.abspath(os.path.expanduser(path))

        def ok(r):
            self.have_program = True
            self.reg_names = None
            self.no_debug = False
            self.is_pie = elf_is_pie(self.program)
            self.gdb.send('-data-evaluate-expression &main', lambda r2: setattr(self, 'has_main', True),
                          errcb=lambda r2: setattr(self, 'has_main', False))
            self.msg('Loaded %s' % self.program)
            if args:
                self.gdb.send('-exec-arguments ' + ' '.join(shlex.quote(a) for a in args))
            self.refresh_source_files(self.auto_open_sources)
            self.show_initial_source()
            if not self.opts.no_session:
                self.restore_session()
            self.session_ready = True
            self.refresh_breakpoints()

        def err(r):
            self.have_program = False
            self.error('Cannot load %s: %s' % (path, r['results'].get('msg', '')))
        self.gdb.send('-file-exec-and-symbols %s' % mi_quote(self.program), ok, errcb=err)

    def show_initial_source(self):
        def ok(r):
            res = r['results']
            path = res.get('fullname') or res.get('file')
            sf = self.load_source(path) if path else None
            if sf and self.tabs_restored:
                self.disasm = False             # keep the files restored from the last session
            elif sf:
                self.src_view.set_file(sf)
                self.src_view.goto_line((parse_int(res.get('line')) or 1) - 1)
                self.disasm = False
            elif path:
                self.msg('Source %s not found - showing disassembly (Ctrl+X -> Source Path Substitution)'
                         % path, 'warn')
                self.show_static_disasm()
            else:
                err(r)
            self.dirty = True

        def err(r):
            # try 'main'
            def ok2(r2):
                m = re.search(r'Line (\d+) of "([^"]+)"', r2.get('captured', ''))
                if m:
                    self.open_source(m.group(2), int(m.group(1)))
                else:
                    self.no_debug_warning()
            self.gdb.send(cli('info line main'), ok2, errcb=lambda r2: self.no_debug_warning(), capture=True)
        self.gdb.send('-file-list-exec-source-file', ok, errcb=err)

    def no_debug_warning(self):
        self.no_debug = True
        self.v_locals.empty_text = 'No locals: the program has no debug information (build with -g).'
        name = os.path.basename(self.program or 'the program')
        self.console_write(
            'warning: %s has no debug information (it was not built with -g).\n'
            '  Source-line breakpoints, locals and source stepping are not available.\n'
            '  You can still use: function breakpoints (Ctrl+X -> New Function Breakpoint),\n'
            '  breakpoints in the disassembly view (Ctrl+D then Ctrl+B), registers and memory.\n' % name, 'warn')
        if not self.has_main:
            self.console_write('  The binary is stripped (no symbols): %s stops at the program entry point.\n'
                               % key_name(self.action_keys.get('step_over')), 'warn')
        self.msg('%s has no debug info (not built with -g) - showing disassembly' % name, 'warn')
        self.user_disasm = False
        self.show_static_disasm()

    no_debug = False

    def open_source(self, path, line=None):
        sf = self.load_source(path)
        if not sf:
            self.msg('Source file not found: %s' % path, 'err')
            return False
        self.src_view.set_file(sf)
        if line:
            self.src_view.goto_line(line - 1)
        if self.disasm:
            self.disasm = False
            self.user_disasm = False
        self.dirty = True
        return True

    def attach(self, pid):
        def ok(r):
            self.attached = True
            self.have_program = True
            self.inferior_pid = str(pid)
            self.msg('Attached to process %s' % pid)
            self.refresh_source_files()
            if not self.program:
                try:
                    self.program = os.readlink('/proc/%s/exe' % pid)
                except OSError:
                    self.program = 'pid %s' % pid
        self.gdb.send('-target-attach %s' % pid, ok, errcb=self.cmd_error)

    def open_core(self, path):
        def ok(r):
            self.core = True
            self.have_program = True
            self.state = 'stopped'
            self.stop_reason = os.path.basename(path)
            self.stop_gen += 1

            def got_frame(r2):
                f = r2['results'].get('frame') or {}
                self.frame = f
                self.show_frame(f)
                self.refresh_after_stop()
            self.gdb.send('-stack-info-frame', got_frame, quiet=True)
            self.refresh_source_files()
        self.gdb.send(cli('core-file %s' % os.path.abspath(path).replace(' ', '\\ ')), ok, errcb=self.cmd_error)

    def require_stopped(self):
        if self.state == 'running':
            self.msg('The program is running - press %s to break first' % key_name(self.action_keys.get('break_all')), 'warn')
            return False
        if self.state != 'stopped':
            self.msg('The program is not running - press %s to start it' % key_name(self.action_keys.get('continue')), 'warn')
            return False
        return True

    def prepare_run(self):
        if self.out_file:
            try:
                open(self.out_file, 'w').close()
                self.out_file_pos = 0
            except IOError:
                pass
        self.output.write('---- program started %s ----\n' % time.strftime('%H:%M:%S'), 'dim')
        self.stop_reason = ''
        if self.sysrec:
            self.sysrec['t0'] = time.time()
            self.sysrec['pending'] = {}
            self.add_syscall({'src': 'gdb', 'note': 'program started (live recording)', 't': 0.0})

    def run_program(self, break_at_main=False):
        if not self.have_program:
            self.msg('No program loaded (Ctrl+X -> Load Program)', 'err')
            return
        if self.attached:
            self.msg('Attached process - cannot restart it', 'warn')
            return
        self.prepare_run()
        if break_at_main and not self.has_main:
            self.run_to_entry()
            return
        if break_at_main:
            self.gdb.send('-break-insert -t main', quiet=True)
        self.gdb.send('-exec-run', errcb=self.cmd_error)

    def run_to_entry(self):
        """No 'main' symbol (stripped binary): start, then stop at the program's entry point."""
        def at_first_insn():
            def ok(r):
                m = re.search(r'Entry point:\s*(0x[0-9a-fA-F]+)', r.get('captured', ''))
                if m:
                    self.entry_stop = True
                    self.gdb.send('-break-insert -t *%s' % m.group(1), quiet=True)
                    self.exec_cmd('-exec-continue')
                else:
                    self.show_frame(self.frame)
                    self.refresh_after_stop()
            self.gdb.send(cli('info files'), ok, quiet=True, capture=True)

        def err(r):
            self.after_stop = []
            self.msg('starti not supported by this gdb - running without stopping', 'warn')
            self.gdb.send('-exec-run', errcb=self.cmd_error)
        self.after_stop.append(at_first_insn)
        self.gdb.send(cli('starti'), errcb=err)

    entry_stop = False

    def exec_cmd(self, cmd):
        self.gdb.send(cmd, errcb=self.cmd_error)

    def cursor_location(self):
        sv = self.src_view
        if self.disasm:
            r = sv.selected_dis()
            if r and r.get('addr') is not None:
                if not r.get('func'):
                    return '*0x%x' % r['addr']
                # symbol+offset is recalculated on every run; a raw address breaks when the program
                # loads somewhere else (address randomization, or a PIE binary before it starts)
                return "*'%s'+%d" % (r['func'], r.get('off') or 0)
            return None
        if sv.file:
            return '%s:%d' % (sv.file.path, sv.cur + 1)
        return None

    def bps_at_cursor(self):
        sv = self.src_view
        nums = []
        if self.disasm:
            r = sv.selected_dis()
            if r and r.get('addr') is not None:
                for b in self.breakpoints:
                    if parse_int(b.get('addr')) == r['addr'] and b.get('disp') != 'del':
                        nums.append(str(b.get('number')).split('.')[0])
        elif sv.file:
            line = sv.cur + 1
            info = self.bp_lines_for(sv.file.path).get(line)
            if info:
                nums.append(info[2].split('.')[0])
        out = []
        for n in nums:
            if n not in out:
                out.append(n)
        return out

    def find_bp(self, num):
        for b in self.breakpoints:
            if str(b.get('number')) == str(num):
                return b
        return None

    # ------------------------------------------------------------------ actions: execution
    def act_continue(self):
        if self.state == 'running':
            self.msg('Already running (press %s to break)' % key_name(self.action_keys.get('break_all')), 'warn')
        elif self.state in ('idle', 'exited'):
            self.run_program()
        elif self.core:
            self.msg('Core dumps cannot be resumed', 'warn')
        else:
            self.exec_cmd('-exec-continue')

    def act_start(self):
        if self.state in ('running', 'stopped'):
            self.after_stop_or_now(lambda: self.run_program(True))
        else:
            self.run_program(True)

    def act_step_over(self):
        if self.state in ('idle', 'exited'):
            self.run_program(True)
        elif self.require_stopped() and not self.core:
            self.step_cmd('-exec-next-instruction' if self.disasm else '-exec-next')

    def act_step_into(self):
        if self.state in ('idle', 'exited'):
            self.run_program(True)
        elif self.require_stopped() and not self.core:
            self.step_cmd('-exec-step-instruction' if self.disasm else '-exec-step')

    def act_step_out(self):
        if self.require_stopped() and not self.core:
            self.step_cmd('-exec-finish')

    def act_run_to_cursor(self):
        loc = self.cursor_location()
        if not loc:
            self.msg('No cursor location', 'warn')
            return
        if self.state == 'running':
            self.msg('Program is running', 'warn')
            return
        self.gdb.send('-break-insert -t %s' % mi_quote(loc), errcb=self.cmd_error)
        if self.state == 'stopped':
            self.exec_cmd('-exec-continue')
        else:
            self.run_program()

    def act_set_next(self):
        loc = self.cursor_location()
        if not loc or not self.require_stopped():
            return
        self.gdb.send('-break-insert -t %s' % mi_quote(loc), errcb=self.cmd_error)
        self.exec_cmd('-exec-jump %s' % mi_quote(loc))

    def act_break_all(self):
        if self.state != 'running':
            self.msg('Program is not running', 'warn')
            return
        self.interrupting = True
        if self.sysrec:
            # a syscall stop may already be in flight; it will honour 'interrupting'
            self.gdb.send('-exec-interrupt', quiet=True)
            return

        def fallback(r):
            if self.inferior_pid:
                try:
                    os.kill(int(self.inferior_pid), signal.SIGINT)
                    return
                except (OSError, ValueError):
                    pass
            try:
                os.kill(self.gdb.proc.pid, signal.SIGINT)
            except OSError:
                self.cmd_error(r)
        self.gdb.send('-exec-interrupt', errcb=fallback)

    SIGNAL_CHOICES = [
        ('SIGTERM', 'terminate politely (can be caught)'), ('SIGINT', 'interrupt, like Ctrl+C'),
        ('SIGHUP', 'hang-up / reload configuration'), ('SIGUSR1', 'user-defined signal 1'),
        ('SIGUSR2', 'user-defined signal 2'), ('SIGQUIT', 'quit and dump core'),
        ('SIGABRT', 'abort and dump core'), ('SIGKILL', 'kill immediately (cannot be caught)'),
        ('SIGSTOP', 'stop / freeze (cannot be caught)'), ('SIGCONT', 'continue after SIGSTOP'),
        ('SIGALRM', 'timer alarm'), ('SIGPIPE', 'broken pipe'), ('SIGSEGV', 'segmentation fault'),
        ('SIGCHLD', 'child process changed state'), ('SIGWINCH', 'terminal window resized'),
    ]

    def signal_targets(self):
        targets = []
        if self.inferior_pid and self.state in ('running', 'stopped') and not self.core:
            targets.append(('Program being debugged (pid %s, %s)' % (self.inferior_pid, self.state),
                            ('gdb', int(self.inferior_pid)), ''))
        stt = self.strace
        if stt and not stt.get('done'):
            if stt.get('pid'):
                targets.append(('Process traced by strace (pid %s)' % stt['pid'], ('pid', int(stt['pid'])), ''))
            else:
                for child in self.child_pids(stt['proc'].pid):
                    targets.append(('Program running under strace (pid %d)' % child, ('pid', child), ''))
        targets.append(('Another process...', ('other', None), ''))
        return targets

    @staticmethod
    def child_pids(pid):
        kids = []
        try:
            for tid in os.listdir('/proc/%d/task' % pid):
                with open('/proc/%d/task/%s/children' % (pid, tid)) as f:
                    kids += [int(k) for k in f.read().split()]
        except (IOError, OSError, ValueError):
            pass
        return kids

    def act_send_signal(self):
        targets = self.signal_targets()
        target = targets[0][1] if len(targets) == 1 else \
            self.choose('Send a signal to...', targets, hint='Enter = choose   Esc = cancel')
        if not target:
            return
        kind, pid = target
        if kind == 'other':
            sel = self.choose('Send a signal to which process?', self.process_items(), allow_free=True,
                              hint='Type to filter (or a PID)   Enter = choose   Esc = cancel')
            if isinstance(sel, tuple):
                sel = sel[1].strip()
            if not sel or not str(sel).isdigit():
                return
            pid = int(sel)
            if self.inferior_pid and pid == int(self.inferior_pid):
                kind = 'gdb'
        items = [('%-9s %s' % (name, desc), name, '') for name, desc in self.SIGNAL_CHOICES]
        sig = self.choose('Signal to send to pid %d' % pid, items, allow_free=True,
                          hint='Type to filter, or type a name / number (e.g. SIGRTMIN+1, 34)   Enter = send')
        if isinstance(sig, tuple):
            sig = sig[1].strip().upper()
        if not sig:
            return
        num = parse_int(sig) if sig[0].isdigit() else getattr(signal, sig if sig.startswith('SIG') else
                                                             'SIG' + sig, None)
        if num is None:
            self.msg('Unknown signal %s' % sig, 'err')
            return
        num = int(num)
        try:
            name = signal.Signals(num).name
        except ValueError:
            name = 'SIG%d' % num          # real-time signals: gdb calls them SIG34, SIG35, ...
        # gdb's "signal N" uses gdb's own numbering (10 = SIGBUS there), so always pass the name
        if kind == 'gdb' and self.state == 'stopped':
            how = self.choose('The program is paused. How should %s be delivered?' % name, [
                ('Deliver it now and continue running (gdb "signal")', 'signal', ''),
                ('Queue it - delivered when you continue (gdb "queue-signal")', 'queue', ''),
                ('Send it with kill() - gdb sees it first when the program continues', 'kill', '')])
            if how == 'signal':
                self.deliver_signal(name, pid)
                return
            if how == 'queue':
                self.gdb.send(cli('queue-signal %s' % name), lambda r: self.msg(
                    '%s queued - it is delivered when the program continues (Ctrl+R)' % name),
                    errcb=self.cmd_error)
                return
            if how != 'kill':
                return
        if kind == 'gdb' and self.state == 'running' and num not in (signal.SIGKILL, signal.SIGSTOP):
            # gdb would intercept a kill()ed signal and pause; instead pause briefly and let gdb deliver it,
            # so the program receives it and keeps running.
            self.after_stop_or_now(lambda: self.deliver_signal(name, pid))
            return
        try:
            os.kill(pid, num)
        except OSError as e:
            self.msg('Cannot send %s to pid %d: %s' % (name, pid, e.strerror), 'err')
            return
        if kind == 'gdb':
            self.msg('Sent %s to pid %d - gdb sees it first and pauses the program; Ctrl+R passes it on'
                     % (name, pid), 'warn')
        else:
            self.msg('Sent %s to pid %d' % (name, pid))

    def deliver_signal(self, name, pid):
        """Paused program: hand it a signal and continue. queue-signal + -exec-continue keeps gdb responsive
        (the CLI 'signal' command would block gdb until the program stops again)."""
        def queued(r):
            self.gdb.send('-exec-continue', errcb=self.cmd_error)
            self.msg('Delivered %s to pid %d (program continues)' % (name, pid))

        def old_gdb(r):          # gdb < 7.9 has no queue-signal
            self.gdb.send(cli('signal %s' % name), errcb=self.cmd_error)
            self.msg('Delivered %s to pid %d' % (name, pid))
        self.gdb.send(cli('queue-signal %s' % name), queued, errcb=old_gdb)

    def after_stop_or_now(self, fn):
        if self.state == 'running':
            self.after_stop.append(fn)
            self.act_break_all()
        else:
            fn()

    def act_stop(self):
        if self.state in ('idle', 'exited'):
            self.msg('Not debugging', 'warn')
            return

        def do_stop():
            if self.attached:
                self.gdb.send('-target-detach', lambda r: (self.program_gone('idle'), self.msg('Detached')),
                              errcb=self.cmd_error)
            elif self.core:
                self.gdb.send(cli('core-file'), lambda r: (self.program_gone('idle'), self.msg('Core file closed')),
                              errcb=self.cmd_error)
            else:
                self.gdb.send(cli('kill'), lambda r: (self.program_gone('idle'), self.msg('Debugging stopped')),
                              errcb=self.cmd_error)
        self.after_stop_or_now(do_stop)

    def act_restart(self):
        if self.attached:
            self.msg('Cannot restart an attached process', 'warn')
            return
        self.after_stop_or_now(lambda: self.run_program())

    def act_attach(self):
        procs = self.process_items()
        pid = self.choose('Attach to Process', procs, allow_free=True,
                          hint='Type to filter (or type a PID)   Enter = attach   Esc = cancel')
        if isinstance(pid, tuple):
            pid = pid[1].strip()
        if pid and str(pid).isdigit():
            if self.state in ('running', 'stopped') and not self.attached:
                self.msg('Stop the current program first (Ctrl+K)', 'warn')
                return
            self.attach(pid)

    def process_items(self):
        procs = []
        me = os.getpid()
        try:
            for d in os.listdir('/proc'):
                if not d.isdigit() or int(d) in (me, self.gdb.proc.pid):
                    continue
                try:
                    with open('/proc/%s/cmdline' % d, 'rb') as f:
                        cmd = f.read().replace(b'\0', b' ').decode('utf-8', 'replace').strip()
                except (IOError, OSError):
                    continue
                if not cmd:
                    try:
                        with open('/proc/%s/comm' % d) as f:
                            cmd = '[%s]' % f.read().strip()
                    except (IOError, OSError):
                        cmd = '?'
                procs.append(('%7s  %s' % (d, cmd), d, ''))
        except OSError:
            pass
        procs.sort(key=lambda p: int(p[1]))
        return procs

    def act_detach(self):
        if not self.attached:
            self.msg('Not attached to a process', 'warn')
            return
        self.act_stop()

    def act_load_core(self):
        p = self.prompt('Core dump file', '', 'core')
        if p:
            self.open_core(os.path.expanduser(p))

    def act_load_program(self):
        p = self.prompt('Program to debug', self.program or '', 'program')
        if p:
            self.load_program(p)

    def act_set_args(self):
        def ok(r):
            m = re.search(r'"(.*)"', r.get('captured', ''), re.S)
            cur = m.group(1) if m else ''
            a = self.prompt('Program arguments (shell syntax, e.g.  -v "a b" < in.txt)', cur, 'args')
            if a is not None:
                self.gdb.send('-exec-arguments ' + a, lambda r2: self.msg('Arguments set'), errcb=self.cmd_error)
        self.gdb.send(cli('show args'), lambda r: self.pending_popups.append(lambda: ok(r)), quiet=True,
                      capture=True)

    def act_set_env(self):
        e = self.prompt('Environment variable (NAME=VALUE, or -NAME to unset)', '', 'env')
        if e:
            if e.startswith('-'):
                self.gdb.send(cli('unset environment %s' % e[1:]), lambda r: self.msg('Unset %s' % e[1:]),
                              errcb=self.cmd_error)
            else:
                self.gdb.send(cli('set environment %s' % e), lambda r: self.msg('Set %s' % e), errcb=self.cmd_error)

    def act_set_cwd(self):
        d = self.prompt('Working directory for the program', '', 'cwd')
        if d:
            self.gdb.send(cli('set cwd %s' % d), lambda r: self.msg('Working directory: %s' % d),
                          errcb=lambda r: self.gdb.send('-environment-cd %s' % mi_quote(d),
                                                        lambda r2: self.msg('Working directory: %s' % d),
                                                        errcb=self.cmd_error))

    # ------------------------------------------------------------------ actions: breakpoints
    def act_toggle_bp(self):
        nums = self.bps_at_cursor()
        if nums:
            for n in nums:
                self.gdb.send('-break-delete %s' % n, errcb=self.cmd_error)
            self.refresh_breakpoints()
            return
        loc = self.cursor_location()
        if not loc:
            self.msg('Open a source file first (Ctrl+O)', 'warn')
            return
        if self.disasm and loc.startswith('*0x') and self.is_pie and self.state not in ('running', 'stopped'):
            self.msg('This address will move when the program starts (PIE, no symbol here). '
                     'Start with %s first, then set it.' % key_name(self.action_keys.get('step_over')), 'warn')
            return
        line = self.src_view.cur
        sf = self.src_view.file
        disasm = self.disasm

        def ok(r):
            self.refresh_breakpoints()
            b = r['results'].get('bkpt') or {}
            if b.get('pending') and not disasm and sf:
                func = guess_function(sf.lines, line)
                self.pending_popups.append(lambda: self.offer_function_bp(b.get('number'), loc, func))
        def err(r):
            row = self.src_view.selected_dis() if disasm else None
            if loc.startswith("*'") and row and row.get('addr') is not None:
                # the symbol could not be used (e.g. a local libc symbol): fall back to the address
                self.gdb.send('-break-insert -f *0x%x' % row['addr'], ok, errcb=self.cmd_error)
                self.msg('Breakpoint set by address: it only applies to this run of the program', 'warn')
            else:
                self.cmd_error(r)
        self.gdb.send('-break-insert -f %s' % mi_quote(loc), ok, errcb=err)

    def offer_function_bp(self, num, loc, func):
        why = ('the program has no debug information (rebuild with -g)' if self.no_debug
               else 'gdb has no line information for this file')
        items = []
        if func:
            items.append(('Break on function %s() instead' % func, 'func', ''))
        items.append(('Keep it pending (resolves if a library with this file is loaded later)', 'keep', ''))
        items.append(('Remove it', 'remove', ''))
        ans = self.choose('Cannot set a line breakpoint here: %s' % why, items,
                          hint='Enter = choose   Esc = keep pending')
        if ans in ('func', 'remove') and num:
            self.gdb.send('-break-delete %s' % num, quiet=True)
        if ans == 'func':
            def ok(r):
                b = r['results'].get('bkpt') or {}
                if b.get('pending'):
                    self.msg('Function %s not found in the binary either (stripped?) - left pending' % func, 'warn')
                else:
                    self.msg('Breakpoint set on function %s' % func)
                self.refresh_breakpoints()
            self.gdb.send('-break-insert -f %s' % mi_quote(func), ok, errcb=self.cmd_error)
        self.refresh_breakpoints()

    def cli_then_refresh(self, cmd):
        self.gdb.send(cli(cmd), lambda r: self.refresh_breakpoints(), errcb=self.cmd_error)

    def act_func_bp(self):
        f = self.prompt('Break at function (e.g. main, MyClass::method)', self.src_view.word_at_cursor(), 'func')
        if f:
            self.gdb.send('-break-insert -f %s' % mi_quote(f), lambda r: self.refresh_breakpoints(),
                          errcb=self.cmd_error)

    def act_location_bp(self):
        f = self.prompt('New breakpoint location', '', 'bploc',
                        hint='file.c:42   function   *0x401000   file.c:func   (add "if cond" for a condition)')
        if f:
            self.cli_then_refresh('break %s' % f)

    def _bp_for_cursor_or_create(self, then):
        nums = self.bps_at_cursor()
        if nums:
            then(nums[0])
            return
        loc = self.cursor_location()
        if not loc:
            self.msg('No cursor location', 'warn')
            return

        def ok(r):
            num = (r['results'].get('bkpt') or {}).get('number')
            self.refresh_breakpoints()
            if num:
                self.pending_popups.append(lambda: then(num))
        self.gdb.send('-break-insert -f %s' % mi_quote(loc), ok, errcb=self.cmd_error)

    def edit_bp_condition(self, num, cur=None):
        if cur is None:
            b = self.find_bp(num)
            cur = b.get('cond', '') if b else ''
        c = self.prompt('Condition for breakpoint %s (empty = none)' % num, cur, 'cond',
                        hint='Break only when this expression is true, e.g.  i == 42 && p != 0')
        if c is None:
            return
        cmd = '-break-condition %s %s' % (num, c) if c.strip() else '-break-condition %s' % num
        self.gdb.send(cmd, lambda r: self.refresh_breakpoints(),
                      errcb=lambda r: self.cli_then_refresh('condition %s %s' % (num, c)))

    def edit_bp_ignore(self, num):
        c = self.prompt('Hit count: skip breakpoint %s this many times' % num, '0', 'ignore')
        if c is not None and c.strip().isdigit():
            self.gdb.send('-break-after %s %s' % (num, c.strip()), lambda r: self.refresh_breakpoints(),
                          errcb=self.cmd_error)

    def act_cond_bp(self):
        self._bp_for_cursor_or_create(lambda n: self.edit_bp_condition(n))

    def act_hit_bp(self):
        self._bp_for_cursor_or_create(lambda n: self.edit_bp_ignore(n))

    def act_enable_bp(self):
        nums = self.bps_at_cursor()
        if not nums:
            self.msg('No breakpoint at cursor', 'warn')
            return
        for n in nums:
            b = self.find_bp(n)
            en = b and b.get('enabled') == 'y'
            self.cli_then_refresh('%s %s' % ('disable' if en else 'enable', n))

    def act_logpoint(self):
        loc = self.cursor_location()
        if not loc:
            self.msg('No cursor location', 'warn')
            return
        word = self.src_view.word_at_cursor()
        default = '"%s = %%d\\n", %s' % (word, word) if word else '"reached here\\n"'
        fmt = self.prompt('Logpoint message (printf format, args) - printed without stopping', default, 'dprintf',
                          hint='e.g.  "i=%d name=%s\\n", i, name')
        if fmt:
            self.cli_then_refresh('dprintf %s,%s' % (loc.replace(' ', '\\ '), fmt))

    def act_data_bp(self):
        e = self.prompt('Data breakpoint: break when this expression changes', self.src_view.word_at_cursor(), 'wp',
                        hint='e.g.  counter    obj->field    *(int*)0x601040')
        if not e:
            return
        kind = self.choose('Break when...', [('Value is written (changes)', '', ''),
                                             ('Value is read', '-r', ''),
                                             ('Value is read or written', '-a', '')])
        if kind is None:
            return
        self.gdb.send('-break-watch %s %s' % (kind, mi_quote(e)), lambda r: self.refresh_breakpoints(),
                      errcb=self.cmd_error)

    def act_catch_throw(self):
        self.cli_then_refresh('catch throw')

    def act_catch_catch(self):
        self.cli_then_refresh('catch catch')

    def act_handle_signal(self):
        s = self.prompt('Signal handling (gdb "handle" syntax)', 'SIGPIPE nostop noprint pass', 'handle',
                        hint='SIGNAL [stop|nostop] [print|noprint] [pass|nopass]   e.g. SIGUSR1 nostop noprint')
        if s:
            self.gdb.send(cli('handle ' + s), lambda r: self.console_write(r.get('captured', ''), None),
                          errcb=self.cmd_error, capture=True)

    def act_enable_all_bp(self):
        self.cli_then_refresh('enable')

    def act_disable_all_bp(self):
        self.cli_then_refresh('disable')

    def act_delete_all_bp(self):
        self.cli_then_refresh('delete')

    def act_save_bp(self):
        p = self.prompt('Save breakpoints to file', 'breakpoints.gdb', 'bpfile')
        if p:
            self.gdb.send(cli('save breakpoints %s' % p), lambda r: self.msg('Breakpoints saved to %s' % p),
                          errcb=self.cmd_error)

    def act_load_bp(self):
        p = self.prompt('Load breakpoints from file', 'breakpoints.gdb', 'bpfile')
        if p:
            self.gdb.send(cli('source %s' % p), lambda r: (self.refresh_breakpoints(), self.msg('Loaded %s' % p)),
                          errcb=self.cmd_error)

    def goto_bp(self, b):
        path = b.get('fullname') or b.get('file')
        if path and b.get('line'):
            if self.open_source(path, parse_int(b.get('line'))):
                self.focus = 'source'
            return
        addr = parse_int(b.get('addr'))
        if addr is not None:
            self.goto_address(addr)

    # ------------------------------------------------------------------ actions: data
    def act_quickwatch(self):
        e = self.prompt('QuickWatch: expression to evaluate', self.src_view.word_at_cursor(), 'expr',
                        hint='Any C/C++ expression: x, *ptr, arr[3], obj.field, sizeof(T), func(1)  ...')
        if e:
            self.modal(QuickWatchPopup(self, e))
            self.after_state_change()

    def act_add_watch(self, prompt_default=None):
        default = self.src_view.word_at_cursor() if prompt_default is None else prompt_default
        e = self.prompt('Add watch expression', default, 'expr')
        if e:
            self.add_watch(e)
            self.show_view(self.v_watch)
            self.v_watch.sel = len(self.watches) - 1

    def add_watch(self, expr):
        node = VarNode(expr, expr)
        self.watches.append(node)
        self.create_var(node, '@')
        self.dirty = True
        self.autosave()

    def remove_watch(self, node):
        if node in self.watches:
            self.watches.remove(node)
            self.delete_var(node)
            self.autosave()

    def edit_watch(self, node):
        e = self.prompt('Edit watch expression', node.expr, 'expr')
        if e and e != node.expr:
            i = self.watches.index(node)
            self.delete_var(node)
            n = VarNode(e, e)
            self.watches[i] = n
            self.create_var(n, '@')
            self.autosave()

    def act_delete_all_watch(self):
        for w in self.watches:
            self.delete_var(w)
        self.watches = []
        self.autosave()

    def path_expr(self, node, then):
        if node.parent is None:
            then(node.expr or node.name)
            return
        self.gdb.send('-var-info-path-expression %s' % node.vobj,
                      lambda r: then(r['results'].get('path_expr', node.name)), errcb=self.cmd_error)

    def watch_node(self, node):
        def add(e):
            self.add_watch(e)
            self.msg('Added watch: %s' % e)
        self.path_expr(node, add)

    def mem_show_node(self, node):
        def show(e):
            t = node.type or ''
            self.mem_goto_expr(e if t.rstrip().endswith(('*', ']')) else '&(%s)' % e)
        self.path_expr(node, show)

    def edit_value(self, node):
        if not node.vobj:
            return
        if not self.require_stopped():
            return
        v = self.prompt('New value for %s' % node.name, node.value, 'value')
        if v is None or v == '':
            return

        def ok(r):
            node.value = r['results'].get('value', v)
            self.after_state_change()
        self.gdb.send('-var-assign %s %s' % (node.vobj, mi_quote(v)), ok, errcb=self.cmd_error)

    def act_mem_goto(self):
        default = self.mem_label or self.src_view.word_at_cursor()
        e = self.prompt('Memory: address or expression', default, 'mem',
                        hint='e.g.  0x601040   &var   ptr   buf   $sp')
        if e:
            if self.state != 'stopped':
                self.msg('Memory can be read only while the program is paused', 'warn')
                return
            self.mem_goto_expr(e)

    def act_toggle_hex(self):
        self.hex = not self.hex
        self.gdb.send('-gdb-set output-radix %d' % (16 if self.hex else 10), quiet=True)
        exprs = [w.expr for w in self.watches]
        self.act_delete_all_watch()
        for e in exprs:
            self.add_watch(e)
        if self.state == 'stopped':
            self.refresh_locals(force=True)
            self.refresh_stack()
        self.msg('Hexadecimal display %s' % ('on' if self.hex else 'off'))

    # ------------------------------------------------------------------ actions: navigation
    def act_toggle_disasm(self):
        sv = self.src_view
        if self.disasm and not sv.file:
            self.msg('No source file is open - Ctrl+O to open one', 'warn')
            return
        self.disasm = not self.disasm
        self.user_disasm = self.disasm
        if self.disasm:
            on_exec_line = (self.state == 'stopped' and sv.file is not None and
                            self.exec_path == sv.file.path and self.exec_line == sv.cur + 1)
            if sv.file and not on_exec_line and not self.no_debug:
                self.disasm_source_line(sv.file.path, sv.cur + 1)   # like VS "Go To Disassembly"
            else:
                self.refresh_disasm(force=True)
        self.focus = 'source'
        if self.zoom and self.zoom != 'source':
            self.zoom = None

    def act_show_next(self):
        if self.state != 'stopped' or not self.frame:
            self.msg('No current statement (program not paused)', 'warn')
            return
        self.show_frame(self.frame)
        self.focus = 'source'

    def program_dir(self):
        if self.program and os.path.isabs(self.program) and os.path.exists(self.program):
            return os.path.dirname(os.path.realpath(self.program))
        return os.getcwd()

    def act_open_file(self):
        def popup():
            dbg = []
            for f in self.source_files:
                full = f.get('fullname') or f.get('file')
                if full:
                    dbg.append(full)
            self.src_view._save_tab()
            opened = [t['file'].path for t in self.src_view.tabs]
            res = self.modal(FilePopup(self.program_dir(), dbg, opened))
            if res:
                if self.open_source(res):
                    self.focus = 'source'
        self.refresh_source_files(lambda: self.pending_popups.append(popup))

    def act_find(self):
        sv = self.src_view
        if not sv.file or self.disasm:
            self.msg('Find works in the source view', 'warn')
            return
        hist = self.hist.setdefault('find', [])
        q = self.prompt('Find (Enter = next match; lowercase = case-insensitive)', hist[-1] if hist else '', 'find')
        if not q:
            return
        self.find_next(q)

    def find_next(self, q):
        sv = self.src_view
        lines = sv.file.lines
        ci = q == q.lower()
        qq = q.lower() if ci else q
        n = len(lines)
        start_line, start_col = sv.cur, sv.col + 1
        for step in range(n + 1):
            i = (start_line + step) % n
            text = lines[i].lower() if ci else lines[i]
            j = text.find(qq, start_col if step == 0 else 0)
            if j >= 0:
                if step == n and j >= start_col:
                    break
                sv.cur = i
                sv.col = j
                sv.center_pending = not (sv.top <= i < sv.top + sv.h)
                self.focus = 'source'
                if step and i < start_line:
                    self.msg('Search wrapped to top', 'warn')
                return
        self.msg('Not found: %s' % q, 'err')

    def act_goto(self):
        t = self.prompt('Go to line number, function, file:line or *address', '', 'goto',
                        hint='42   main   foo.c:120   *0x401136   (empty = current statement)')
        if t is None:
            return
        t = t.strip()
        if not t:
            self.act_show_next()
            return
        if t.isdigit():
            if self.disasm:
                self.disasm = False
            self.src_view.goto_line(int(t) - 1)
            self.focus = 'source'
            return
        if t.startswith('*') or re.match(r'^0x[0-9a-fA-F]+$', t):
            addr_expr = t.lstrip('*')

            def ok(r):
                addr = parse_int(r['results'].get('value'))
                if addr is not None:
                    self.goto_address(addr)
            self.gdb.send('-data-evaluate-expression %s' % mi_quote('(long)(%s)' % addr_expr), ok,
                          errcb=self.cmd_error)
            return

        def ok_line(r):
            m = re.search(r'Line (\d+) of "([^"]+)"', r.get('captured', ''))
            if not m or not self.resolve_path(m.group(2)) and self.no_debug:
                self.goto_function_disasm(t)
                return
            path = m.group(2)
            line = int(m.group(1))
            if not self.resolve_path(path):
                self.refresh_source_files(lambda: self.open_source(path, line))
            else:
                self.open_source(path, line)
            self.focus = 'source'
        self.gdb.send(cli('info line %s' % t), ok_line, errcb=lambda r: self.goto_function_disasm(t),
                      capture=True)

    def goto_function_disasm(self, name):
        def ok(r):
            self.user_disasm = True
            self.focus = 'source'
            self.on_disasm(r)
        self.gdb.send('-data-disassemble -a %s -- 0' % mi_quote(name), ok,
                      errcb=lambda r: self.msg('Cannot find %s: %s' % (name, r['results'].get('msg', '')), 'err'))
        self.disasm = True

    def act_browse_functions(self):
        def ok(r):
            items = []
            cur_file = None
            debug_sec = True
            for line in r.get('captured', '').splitlines():
                if line.startswith('File '):
                    cur_file = line[5:].rstrip(':')
                    continue
                if line.startswith('Non-debugging symbols'):
                    debug_sec = False
                    continue
                if debug_sec and cur_file:
                    m = re.match(r'^(\d+):\s+(.*?);?$', line)
                    if m:
                        items.append((m.group(2), ('line', cur_file, int(m.group(1))), os.path.basename(cur_file)))
                elif not debug_sec:
                    m = re.match(r'^(0x[0-9a-fA-F]+)\s+(\S.*)$', line)
                    if m:
                        items.append((m.group(2), ('func', m.group(2)), m.group(1)))
                if len(items) >= 20000:
                    break
            if not items:
                self.msg('No functions found - the binary has no symbols (stripped)', 'warn')
                return
            items.sort(key=lambda i: (i[1][0] != 'line', i[0].lower().startswith(('std::', '_', '__gnu')), i[0].lower()))

            def popup():
                res = self.choose('Functions (%d) - Enter shows it, then Ctrl+B sets a breakpoint' % len(items), items,
                                  hint='Type to filter   Enter = go to function   Esc = cancel')
                if not res:
                    return
                if res[0] == 'line':
                    if self.resolve_path(res[1]) and self.open_source(res[1], res[2]):
                        self.focus = 'source'
                    else:
                        self.refresh_source_files(lambda: self.open_source(res[1], res[2]))
                else:
                    self.goto_function_disasm(res[1].split('@')[0])
            self.pending_popups.append(popup)
        self.gdb.send(cli('info functions'), ok, errcb=self.cmd_error, capture=True)

    def goto_address(self, addr):
        self.disasm = True
        self.user_disasm = True
        self.focus = 'source'

        def ok(r):
            self.on_disasm(r)
            self.src_view.goto_addr(addr)
        self.gdb.send('-data-disassemble -s 0x%x -e 0x%x -- 0' % (max(0, addr - 64), addr + 256), ok,
                      errcb=self.on_dis_error)

    def select_frame(self, level):
        if self.state != 'stopped':
            return

        def ok(r):
            f = r['results'].get('frame') or {}
            f.setdefault('level', str(level))
            self.frame = f
            self.show_frame(f)
            self.refresh_locals(force=True)
            self.update_vars()
            if self.is_visible(self.v_regs):
                self.refresh_registers()
        self.gdb.send('-stack-select-frame %d' % level, errcb=self.cmd_error)
        self.gdb.send('-stack-info-frame', ok, quiet=True)

    def select_thread(self, tid):
        if self.state != 'stopped':
            return

        def ok(r):
            res = r['results']
            self.thread_id = res.get('new-thread-id', tid)
            f = res.get('frame') or {}
            f.setdefault('level', '0')
            self.frame = f
            self.show_frame(f)
            self.refresh_locals(force=True)
            self.update_vars()
            self.refresh_stack()
            self.refresh_threads()
            if self.is_visible(self.v_regs):
                self.refresh_registers()
        self.gdb.send('-thread-select %s' % tid, ok, errcb=self.cmd_error)

    def act_frame_up(self):
        lvl = parse_int(self.frame.get('level')) or 0
        if lvl + 1 < len(self.stack):
            self.select_frame(lvl + 1)

    def act_frame_down(self):
        lvl = parse_int(self.frame.get('level')) or 0
        if lvl > 0:
            self.select_frame(lvl - 1)

    # ------------------------------------------------------------------ actions: ui
    def act_next_tab(self):
        if self.focus == 'source':
            self.act_next_file()
            return
        self.switch_tab(self.focus, 1)

    def switch_file(self, i):
        self.src_view.activate_tab(i)
        if self.disasm:
            self.disasm = False
            self.user_disasm = False
        self.dirty = True

    def act_next_file(self):
        sv = self.src_view
        if len(sv.tabs) < 2:
            self.msg('Only one source file is open (Ctrl+O opens another)', 'warn')
            return
        self.switch_file((sv.active_tab + 1) % len(sv.tabs))
        self.focus = 'source'

    def act_prev_file(self):
        sv = self.src_view
        if len(sv.tabs) < 2:
            self.msg('Only one source file is open (Ctrl+O opens another)', 'warn')
            return
        self.switch_file((sv.active_tab - 1) % len(sv.tabs))
        self.focus = 'source'

    def act_close_file(self):
        sv = self.src_view
        if not sv.tabs:
            self.msg('No source file is open', 'warn')
            return
        name = os.path.basename(sv.file.path) if sv.file else ''
        sv.close_tab()
        self.msg('Closed %s' % name)
        if not sv.tabs and self.have_program and (self.no_debug or self.state == 'stopped'):
            self.show_static_disasm()

    def act_close_other_files(self):
        self.src_view.close_other_tabs()
        self.msg('Closed the other source files')

    def act_switch_file(self):
        sv = self.src_view
        if not sv.tabs:
            self.msg('No source files are open (Ctrl+O)', 'warn')
            return
        sv._save_tab()
        labels = sv.tab_labels()
        items = [('%s%-24s line %d' % ('> ' if i == sv.active_tab else '  ', labels[i], t['cur'] + 1), i,
                  os.path.dirname(t['file'].path))
                 for i, t in enumerate(sv.tabs)]
        i = self.choose('Open Files', items, hint='Type to filter   Enter = switch   Esc = cancel')
        if i is not None:
            self.switch_file(i)
            self.focus = 'source'

    def act_focus_next(self):
        order = self.pane_order()
        i = order.index(self.focus) if self.focus in order else 0
        self.focus = order[(i + 1) % len(order)]

    def act_zoom(self):
        self.zoom = None if self.zoom else self.focus

    def act_redraw(self):
        try:
            self.scr.clear()
        except curses.error:
            pass
        self.dirty = True

    def act_toggle_mouse(self):
        self.set_mouse(not self.mouse)
        self.msg('Mouse support %s%s' % ('on' if self.mouse else 'off',
                                         ' (hold Shift to select text in most terminals)' if self.mouse else ''))

    def act_subst_path(self):
        s = self.prompt('Substitute source path: FROM TO', '', 'subst',
                        hint='e.g.  /build/src /home/me/src   (paths recorded at compile time -> where files are now)')
        if s and len(s.split()) == 2:
            a, b = s.split()
            self.gdb.send(cli('set substitute-path %s %s' % (a, b)), lambda r: self.after_path_change(),
                          errcb=self.cmd_error)

    def act_add_dir(self):
        d = self.prompt('Add source directory', '', 'dir')
        if d:
            self.opts.directory = (self.opts.directory or []) + [os.path.expanduser(d)]
            self.gdb.send(cli('directory %s' % d), lambda r: self.after_path_change(), errcb=self.cmd_error)

    def after_path_change(self):
        self.sources = {}
        self.refresh_source_files(lambda: self.show_frame(self.frame) if self.state == 'stopped' and self.frame
                                  else None)
        self.msg('Source path updated')

    def act_gdb_command(self):
        c = self.prompt('GDB command', '', 'gdbcmd')
        if c:
            self.run_console_command(c)
            self.show_view(self.v_console)

    def run_console_command(self, text):
        self.console_write('(gdb) %s\n' % text, 'cmd')
        t = text.strip()
        if t in ('q', 'quit', 'exit'):
            self.act_quit()
            return
        if t.startswith('-'):
            def show(r):
                self.console_write(r.get('raw', '') + '\n', 'dim')
            self.gdb.send(t, show, errcb=lambda r: self.console_write(r.get('raw', '') + '\n', 'err'))
            return
        if t in ('r', 'run', 'start') or t.startswith(('run ', 'r ', 'start ')):
            self.prepare_run()
        self.gdb.send(cli(text), lambda r: self.after_console_command(t),
                      errcb=lambda r: self.console_write(r['results'].get('msg', 'error') + '\n', 'err'))

    def after_console_command(self, t):
        # Commands typed in the console may change anything.
        if self.state == 'stopped':
            self.after_state_change()
        word = t.split()[0] if t.split() else ''
        if word in ('file', 'exec-file', 'symbol-file'):
            parts = t.split(None, 1)
            if len(parts) == 2:
                self.program = os.path.abspath(os.path.expanduser(parts[1].strip()))
                self.have_program = True
                self.refresh_source_files()
                self.show_initial_source()
        elif word in ('attach',):
            self.attached = True
            self.have_program = True
        elif word in ('frame', 'f', 'up', 'down', 'thread') and self.state == 'stopped':
            def ok(r):
                f = r['results'].get('frame') or {}
                self.frame = f
                self.show_frame(f)
                self.refresh_locals(force=True)
                self.refresh_threads()
            self.gdb.send('-stack-info-frame', ok, quiet=True)

    def send_program_input(self, text):
        if self.pty_master is None:
            self.msg('No program input available (no pseudo-terminal)', 'err')
            return
        if self.state != 'running':
            self.msg('Note: program is not running; input is buffered until it reads stdin', 'warn')
        self.output.write(text, 'cmd')
        try:
            os.write(self.pty_master, text.encode('utf-8'))
        except OSError as e:
            self.msg('Cannot write to program: %s' % e, 'err')

    def read_pty(self):
        try:
            data = os.read(self.pty_master, 65536)
        except (OSError, IOError) as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                return
            data = b''
            if e.errno == errno.EIO:
                return
        if data:
            self.append_output(data)

    def poll_out_file(self):
        try:
            with open(self.out_file, 'rb') as f:
                f.seek(self.out_file_pos)
                data = f.read(65536)
                self.out_file_pos += len(data)
        except (IOError, OSError):
            return
        if data:
            self.append_output(data)

    def append_output(self, data):
        text = self.out_decoder.decode(data)
        text = self.ansi_re.sub('', text).replace('\r\n', '\n').replace('\r', '\n')
        self.output.write(text)
        self.dirty = True
        if not self.is_visible(self.v_output) and self.v_output.title == 'Output':
            self.v_output.title = 'Output*'
        elif self.is_visible(self.v_output):
            self.v_output.title = 'Output'

    def act_show_locals(self):
        self.show_view(self.v_locals)

    def act_show_watch(self):
        self.show_view(self.v_watch)

    def act_show_registers(self):
        self.show_view(self.v_regs)

    def act_show_memory(self):
        self.show_view(self.v_mem)

    def act_show_console(self):
        self.show_view(self.v_console)

    def act_show_output(self):
        self.v_output.title = 'Output'
        self.show_view(self.v_output)

    def act_show_stack(self):
        self.show_view(self.v_stack)

    def act_show_breakpoints(self):
        self.show_view(self.v_bps)

    def act_show_threads(self):
        self.show_view(self.v_threads)

    def auto_open_sources(self, quiet=False):
        """Open the program's source files (and, if set, external sources) as tabs."""
        if self.auto_open == 'off' or self.no_debug:
            return
        own, ext = [], []
        seen = set()
        for f in self.source_files:
            full = f.get('fullname') or f.get('file')
            if not full or not os.path.isfile(full):
                continue
            real = os.path.realpath(full)
            if real in seen:
                continue
            seen.add(real)
            (ext if is_system_path(full) else own).append(real)
        own.sort(key=lambda p: (os.path.splitext(p)[1] in ('.h', '.hh', '.hpp', '.hxx'), os.path.basename(p).lower()))
        ext.sort(key=lambda p: os.path.basename(p).lower())
        wanted = own + (ext if self.auto_open == 'all' else [])
        sv = self.src_view
        opened = 0
        skipped = 0
        for path in wanted:
            if any(t['file'].path == path for t in sv.tabs):
                continue
            if len(sv.tabs) >= sv.MAX_TABS:
                skipped += 1
                continue
            sf = self.load_source(path)
            if sf and sv.add_tab(sf):
                opened += 1
        if self.disasm and sv.file and not self.user_disasm and self.state != 'stopped':
            self.disasm = False
        if opened and not quiet:
            extra = ' (%d more not opened: limit %d, "max_open_files" in %s)' % (skipped, sv.MAX_TABS, CONFIG_PATH) \
                if skipped else ''
            self.msg('Opened %d source file%s of %s%s - Ctrl+] cycles them' % (
                opened, '' if opened == 1 else 's', os.path.basename(self.program or ''), extra))
        self.dirty = True

    def act_auto_open_setting(self):
        names = {'program': 'Program files only (default)', 'all': 'Program files and external sources',
                 'off': 'Off - open files only when stepping into them or from Ctrl+O / Files'}
        items = [(('> ' if k == self.auto_open else '  ') + names[k], k, '') for k in ('program', 'all', 'off')]
        choice = self.choose('Open source files automatically when a program is loaded', items,
                             hint='External = system / library sources such as /usr/include/c++/...   Enter = save')
        if not choice:
            return
        self.auto_open = choice
        self.config['auto_open'] = choice
        save_config(self.config)
        self.auto_open_ext_done = False
        self.msg('Saved: open source files automatically = %s' % choice)
        if choice != 'off' and self.have_program:
            self.refresh_source_files(self.auto_open_sources)

    def act_show_files(self):
        self.show_view(self.v_files)

    def act_show_modules(self):
        self.show_view(self.v_modules)

    def act_grow_bottom(self):
        self.bottom_ratio = min(0.8, self.bottom_ratio + 0.05)

    def act_shrink_bottom(self):
        self.bottom_ratio = max(0.1, self.bottom_ratio - 0.05)

    def act_grow_right(self):
        self.right_ratio = min(0.8, self.right_ratio + 0.05)

    def act_shrink_right(self):
        self.right_ratio = max(0.15, self.right_ratio - 0.05)

    def act_menu(self):
        items = []
        for act, label, _d in ACTIONS:
            if act == 'menu':
                continue
            items.append((label, act, key_name(self.action_keys.get(act))))
        act = self.choose('Command Menu', items,
                          hint='Type to filter   Up/Down select   Enter = run   Esc = close')
        if act:
            getattr(self, 'act_' + act)()

    def act_help(self):
        lines = [('gdbtui %s - Visual Studio style debugging for GDB' % VERSION, 'bold'), '',
                 ('GLOBAL SHORTCUTS', 'warn')]
        for act, label, _d in ACTIONS:
            code = self.action_keys.get(act)
            if code:
                lines.append('  %-9s %s' % (key_name(code), label))
        lines += ['  Tab       Focus next panel (Source -> right panel -> bottom panel)',
                  '  Shift+Tab Focus previous panel',
                  '  Esc       Unzoom',
                  '', ('SOURCE / DISASSEMBLY PANEL', 'warn'),
                  '  Arrows, PgUp/PgDn, Home/End move the cursor. The word under the cursor is the',
                  '  default for QuickWatch, Add Watch, Data Breakpoint and Memory.',
                  '  Gutter marks: B breakpoint  C conditional  H hit count  L logpoint',
                  '                (lower case = disabled)    => next statement',
                  '  Yellow line = next statement of the current frame; green = selected caller frame.',
                  '', ('RIGHT / BOTTOM PANELS', 'warn'),
                  '  Ctrl+] (or Left/Right in lists, or 1-9) switches tabs.',
                  '  Source panel: open files are tabs in its title bar; Ctrl+] cycles them',
                  '  (focus the Source panel first). Ctrl+X -> "Files: ..." switches / closes.',
                  '  Send a signal: Ctrl+X -> "Send Signal to Program / Process".',
                  '  Mouse (--mouse, or Ctrl+X -> "Toggle Mouse Support"): click a tab to switch',
                  '  to it; click a shortcut on the top line to run it (double-click Step or',
                  '  Continue to do it twice); click a message on the status line to clear it.',
                  '  Locals/Watch:  Enter/Right expand   Left collapse   e edit value',
                  '                 w add to Watch   x show in Memory',
                  '                 Watch only: a/Insert add   d/Delete remove   r edit expression',
                  '  Call Stack:    Enter switch to frame (shows its locals and source)',
                  '  Threads:       live while running; after a pause shows where each thread is',
                  '                 in your code. Enter switch to thread (while paused)',
                  '  Files:         your program\'s source files (debug info). Enter open / switch,',
                  '                 a show system headers, c close an open file',
                  '  Breakpoints:   Enter go to   Space enable/disable   d delete   c condition',
                  '                 i hit count (ignore count)   n new breakpoint',
                  '  Registers:     a show all/general registers   e edit value',
                  '  Memory:        Enter/g go to address   Up/Down/PgUp/PgDn scroll   r refresh',
                  '  Console:       type any gdb command (Up/Down history, Enter repeats last,',
                  '                 PgUp/PgDn scroll). Lines starting with "-" are sent as raw MI.',
                  '  Output:        program output; type a line + Enter to send it to program stdin.',
                  '  Syscalls:      r start/stop live recording   t run under strace',
                  '                 Enter go to the source line that made the call   i details + stack',
                  '                 f filter   s summary   b break on this syscall   w save   x clear',
                  '                 Start it from Ctrl+X: "Syscalls: Start / Stop Live Recording" (gdb,',
                  '                 while debugging) or "Run Program Under strace" / "Attach strace" /',
                  '                 "Load strace Log". Source lines show "<< syscalls: ..." notes.',
                  '', ('NOTES', 'warn'),
                  '  * Ctrl+S, Ctrl+P and Ctrl+V are deliberately unused (flow control,',
                  '    docker detach and terminal paste).',
                  '  * Line breakpoints need a binary built with -g. Without debug info the',
                  '    disassembly is shown: Ctrl+B sets a breakpoint on the selected instruction,',
                  '    Ctrl+X -> Browse Functions lists functions to jump to. A stripped binary',
                  '    (no symbols) stops at its entry point when started with Ctrl+N.',
                  '  * A debug build opens all of its own source files as tabs when loaded.',
                  '    Ctrl+X -> "Settings: Open Source Files Automatically" chooses program files',
                  '    (default), program + external (system/library) sources, or off.',
                  '  * Ctrl+O starts in the folder of the program being debugged. Enter opens a',
                  '    file or folder, Backspace on an empty filter goes up, or type a path.',
                  '  * Scrolling past either end of the disassembly loads more instructions.',
                  '  * Ctrl+D from the source shows the disassembly of the line under the cursor.',
                  '  * Breakpoints set in the disassembly are stored as function+offset, so they',
                  '    still work when the program loads at a different address on the next run.',
                  '  * Key bindings can be changed in %s, e.g.' % CONFIG_PATH,
                  '      {"keys": {"toggle_bp": "^A", "show_next": null}}',
                  '  * Breakpoints and watches are remembered per program in the same file.',
                  '  * If Ctrl+_ does not open help in your terminal, try Ctrl+/ or Ctrl+Shift+-,',
                  '    or use Ctrl+X and pick Help.']
        self.show_text('Help', lines)

    def act_save_session(self):
        self.save_session()
        self.msg('Session saved to %s' % CONFIG_PATH)

    def act_quit(self):
        if self.state in ('running', 'stopped') and not self.core:
            what = 'detached from' if self.attached else 'killed'
            ans = self.choose('Quit gdbtui?', [('Yes - quit (the program will be %s)' % what, 'yes', ''),
                                              ('No - keep debugging', None, '')],
                              hint='Enter = choose   Esc = keep debugging')
            if ans != 'yes':
                return
        if not self.opts.no_session:
            self.save_session()
        self.quitting = True

    # ------------------------------------------------------------------ session persistence
    def session_key(self):
        if self.program and os.path.isabs(self.program):
            return self.program
        return None

    def save_session(self):
        key = self.session_key()
        if not key:
            return
        bps = []
        for b in self.breakpoints:
            num = str(b.get('number', ''))
            if '.' in num or b.get('disp') == 'del':
                continue
            if b.get('type', 'breakpoint') not in ('breakpoint', 'hw breakpoint'):
                continue
            ol = b.get('original-location')
            if ol and re.match(r'^\*\s*0x[0-9a-fA-F]+$', ol):
                continue        # a raw address is only valid for one run of the program
            if ol and not ol.startswith('-'):
                loc = ol
            elif b.get('fullname') and b.get('line'):
                loc = '%s:%s' % (b['fullname'], b['line'])
            else:
                continue
            bps.append({'loc': loc, 'cond': b.get('cond', ''), 'enabled': b.get('enabled', 'y') == 'y',
                        'ignore': b.get('ignore', '')})
        sessions = self.config.setdefault('sessions', {})
        sv = self.src_view
        sv._save_tab()
        files = [{'path': t['file'].path, 'line': t['cur'] + 1} for t in sv.tabs]
        active = sv.file.path if sv.file else None
        sessions[key] = {'breakpoints': bps, 'watches': [w.expr for w in self.watches], 'time': time.time(),
                         'files': files, 'active_file': active}
        if len(sessions) > 50:
            for k in sorted(sessions, key=lambda k: sessions[k].get('time', 0))[:len(sessions) - 50]:
                del sessions[k]
        save_config(self.config)

    def restore_session(self):
        key = self.session_key()
        sess = self.config.get('sessions', {}).get(key) if key else None
        if not sess:
            return
        n = 0
        for b in sess.get('breakpoints', []):
            cmd = '-break-insert -f'
            if not b.get('enabled', True):
                cmd += ' -d'
            if b.get('cond'):
                cmd += ' -c %s' % mi_quote(b['cond'])
            if b.get('ignore') and str(b['ignore']).isdigit():
                cmd += ' -i %s' % b['ignore']
            cmd += ' ' + mi_quote(b['loc'])
            self.gdb.send(cmd, quiet=True)
            n += 1
        existing = set(w.expr for w in self.watches)
        for e in sess.get('watches', []):
            if e not in existing:
                self.add_watch(e)
        self.refresh_breakpoints()
        nf = 0
        for f in sess.get('files', []):
            sf = self.load_source(f.get('path')) if f.get('path') and os.path.isfile(f['path']) else None
            if sf:
                self.src_view.set_file(sf)
                self.src_view.goto_line(int(f.get('line') or 1) - 1)
                nf += 1
        if nf:
            self.tabs_restored = True
            for i, t in enumerate(self.src_view.tabs):
                if t['file'].path == sess.get('active_file'):
                    self.src_view.activate_tab(i)
            self.disasm = False
        if n or sess.get('watches') or nf:
            self.msg('Restored %d breakpoint(s), %d watch(es) and %d open file(s) from last session'
                     % (n, len(sess.get('watches', [])), nf))

    tabs_restored = False


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def load_config():
    try:
        with open(CONFIG_PATH) as f:
            c = json.load(f)
            return c if isinstance(c, dict) else {}
    except (IOError, OSError, ValueError):
        return {}


def save_config(cfg):
    tmp = CONFIG_PATH + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump(cfg, f, indent=1, sort_keys=True)
        os.replace(tmp, CONFIG_PATH)
    except (IOError, OSError):
        pass


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def parse_args(argv):
    p = argparse.ArgumentParser(
        prog='gdbtui.py',
        description='Visual Studio style terminal UI for GDB (Python 3.6+, standard library only).',
        epilog='Examples:\n'
               '  python3 gdbtui.py ./myprog arg1 arg2\n'
               '  python3 gdbtui.py --args ./myprog -v input.txt\n'
               '  python3 gdbtui.py -p 1234\n'
               '  python3 gdbtui.py -c core.1234 ./myprog\n\n'
               'Inside the UI press Ctrl+X for the command menu and Ctrl+_ (Ctrl+/) for help.',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('-p', '--pid', help='attach to a running process id')
    p.add_argument('-c', '--core', help='open a core dump (give the program too)')
    p.add_argument('-g', '--gdb', default=os.environ.get('GDBTUI_GDB', 'gdb'), help='gdb executable (default: gdb)')
    p.add_argument('-x', '--command', action='append', help='gdb script file to source at startup (repeatable)')
    p.add_argument('-ex', '--eval-command', action='append', help='gdb command to run at startup (repeatable)')
    p.add_argument('-d', '--directory', action='append', help='add a source directory (repeatable)')
    p.add_argument('--cd', help='change gdb working directory')
    p.add_argument('--nx', action='store_true', help='do not read ~/.gdbinit')
    p.add_argument('--mouse', action='store_true', help='enable mouse support')
    p.add_argument('--syscalls', action='store_true',
                   help='record system calls live from the start (Syscalls tab)')
    p.add_argument('--syscall-filter', metavar='CALLS',
                   help='record only these system calls, e.g. "openat read write" (implies --syscalls)')
    p.add_argument('--tabsize', type=int, default=None, help='tab width for source files (default 4)')
    p.add_argument('--open-sources', choices=('off', 'program', 'all'), default=None,
                   help="open source files as tabs when the program loads: the program's own files "
                        "(default), also external/system sources (all), or none (off)")
    p.add_argument('--no-tty', action='store_true', help='do not give the program its own pseudo-terminal')
    p.add_argument('--no-session', action='store_true', help='do not save/restore breakpoints and watches')
    p.add_argument('--log', help='write the raw GDB/MI conversation to this file (for troubleshooting)')
    p.add_argument('--args', action='store_true', dest='args_flag', help='gdb-style: program and its arguments follow')
    p.add_argument('--version', action='version', version='gdbtui ' + VERSION)
    p.add_argument('program', nargs='?', help='program to debug')
    p.add_argument('args', nargs=argparse.REMAINDER, help='arguments for the program')
    return p.parse_args(argv)


def check_gdb(path):
    try:
        out = subprocess.check_output([path, '--version'], stderr=subprocess.STDOUT)
        return out.decode('utf-8', 'replace').splitlines()[0]
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv=None):
    opts = parse_args(sys.argv[1:] if argv is None else argv)
    if sys.version_info < (3, 6):
        sys.stderr.write('gdbtui needs Python 3.6 or newer\n')
        return 2
    if not check_gdb(opts.gdb):
        sys.stderr.write('gdbtui: cannot run gdb (%s). Is it installed / on the PATH? Use --gdb PATH.\n' % opts.gdb)
        return 2
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        sys.stderr.write('gdbtui: needs an interactive terminal (use "docker exec -it" / "kubectl exec -it").\n')
        return 2
    if not os.environ.get('TERM') or os.environ.get('TERM') == 'dumb':
        os.environ['TERM'] = 'xterm'
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error:
        pass
    os.environ.setdefault('ESCDELAY', '25')
    config = load_config()
    app_holder = []

    def run(scr):
        try:
            curses.raw()          # Ctrl+C / Ctrl+Z / Ctrl+\ / Ctrl+S / Ctrl+Q arrive as keys
        except curses.error:
            pass
        curses.noecho()
        scr.keypad(True)
        scr.nodelay(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        app = App(scr, opts, config)
        app_holder.append(app)
        app.run()

    old_int = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        curses.wrapper(run)
    except curses.error as e:
        sys.stderr.write('gdbtui: terminal error: %s\nTry: export TERM=xterm (or xterm-256color)\n' % e)
        return 1
    finally:
        signal.signal(signal.SIGINT, old_int)
        for app in app_holder:
            try:
                app.gdb.close()
            except Exception:
                pass
            for fd in (app.pty_master, app.pty_slave):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
