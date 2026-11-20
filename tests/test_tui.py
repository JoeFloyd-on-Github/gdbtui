#!/usr/bin/env python3
"""End-to-end tests: drive the real gdbtui inside tmux, the same way it was tested by hand.

Each test starts gdbtui in a detached tmux session (fixed size), sends keystrokes with
`tmux send-keys`, and checks the screen text captured with `tmux capture-pane`.
Every test gets its own temporary HOME, so ~/.gdbtui.json is never touched.

Needs: tmux, gdb, the testapp built (sh testapp/build_variants.sh); strace for two tests.

    python3 tests/test_tui.py                    all tests
    python3 tests/test_tui.py TuiTests.test_file_tabs
    GDBTUI_TEST_KEEP=1 python3 tests/test_tui.py ...   leave the tmux session running on failure
                                                       (tmux attach -t gdbtui-test-N to look)
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GDBTUI = os.path.join(ROOT, 'gdbtui.py')
TESTAPP = os.path.join(ROOT, 'testapp')
BUILD = os.path.join(TESTAPP, 'build')

HAVE_TOOLS = all(shutil.which(t) for t in ('tmux', 'gdb'))
HAVE_BUILD = os.path.exists(os.path.join(BUILD, 'demo_debug'))
HAVE_STRACE = bool(shutil.which('strace'))


class Tui(object):
    """A gdbtui instance running in a detached tmux session."""
    counter = 0

    def __init__(self, args, width=170, height=45, config=None, home=None):
        Tui.counter += 1
        self.name = 'gdbtui-test-%d-%d' % (os.getpid(), Tui.counter)
        self.home = home or tempfile.mkdtemp(prefix='gdbtui-home-')
        if config is not None:
            import json
            with open(os.path.join(self.home, '.gdbtui.json'), 'w') as f:
                json.dump(config, f)
        cmd = 'cd %s && HOME=%s %s %s %s; echo __GDBTUI_EXITED__; sleep 60' % (
            shlex.quote(TESTAPP), shlex.quote(self.home), shlex.quote(sys.executable),
            shlex.quote(GDBTUI), ' '.join(shlex.quote(a) for a in args))
        subprocess.check_call(['tmux', 'new-session', '-d', '-s', self.name, '-x', str(width),
                               '-y', str(height), cmd])

    # ---- input
    def keys(self, *keys, delay=0.25):
        """tmux key names: 'C-r', 'Enter', 'Escape', 'Down', 'Tab' ..."""
        for k in keys:
            subprocess.check_call(['tmux', 'send-keys', '-t', self.name, k])
            time.sleep(delay)

    def type(self, text, delay=0.25):
        subprocess.check_call(['tmux', 'send-keys', '-t', self.name, '-l', '--', text])
        time.sleep(delay)

    def menu(self, entry):
        """Run a command from the Ctrl+X menu by typing (part of) its name."""
        self.keys('C-x')
        self.type(entry)
        self.keys('Enter')

    def click(self, x, y, double=False):
        """Left click at 0-based column x, row y, sent as xterm X10 mouse sequences."""
        seq = []
        for _ in range(2 if double else 1):
            for button in (0x20, 0x23):                       # press, release
                seq += ['1b', '5b', '4d', '%x' % button, '%x' % (x + 33), '%x' % (y + 33)]
        subprocess.check_call(['tmux', 'send-keys', '-t', self.name, '-H'] + seq)
        time.sleep(0.4)

    def click_text(self, text, row=None, double=False):
        """Click the middle of the first occurrence of text (on screen row `row`, if given)."""
        rows = self.screen().splitlines()
        for y, line in enumerate(rows):
            if (row is None or y == row % len(rows)) and text in line:
                self.click(line.index(text) + len(text) // 2, y, double)
                return
        raise AssertionError('%r not on screen:\n%s' % (text, '\n'.join(rows)))

    # ---- output
    def screen(self):
        return subprocess.check_output(['tmux', 'capture-pane', '-p', '-t', self.name]).decode('utf-8', 'replace')

    def wait_for(self, pattern, timeout=10.0):
        """Wait until the screen matches a regex; returns the match. Fails with the last screen."""
        deadline = time.time() + timeout
        rx = re.compile(pattern, re.M)
        scr = ''
        while time.time() < deadline:
            scr = self.screen()
            m = rx.search(scr)
            if m:
                return m
            time.sleep(0.2)
        raise AssertionError('timed out after %.0fs waiting for %r. Screen was:\n%s' % (timeout, pattern, scr))

    def status(self):
        return self.screen().splitlines()[-1]

    def close(self, keep=False):
        if not keep:
            subprocess.call(['tmux', 'kill-session', '-t', self.name], stderr=subprocess.DEVNULL)
            shutil.rmtree(self.home, ignore_errors=True)


def source_line(path, text):
    """1-based line number of the first line containing text (so tests survive source edits)."""
    with open(os.path.join(TESTAPP, 'src', path)) as f:
        for i, line in enumerate(f, 1):
            if text in line:
                return i
    raise ValueError('%r not found in %s' % (text, path))


@unittest.skipUnless(HAVE_TOOLS, 'needs tmux and gdb')
@unittest.skipUnless(HAVE_BUILD, 'testapp not built: sh testapp/build_variants.sh')
class TuiTests(unittest.TestCase):

    def start(self, *args, **kw):
        self.tui = Tui(list(args), **kw)
        return self.tui

    def tearDown(self):
        tui = getattr(self, 'tui', None)
        if tui:
            if os.environ.get('GDBTUI_TEST_KEEP'):
                print('\n  (kept: tmux attach -t %s)' % tui.name)
                return
            tui.keys('C-q', 'Enter', delay=0.3)
            tui.close()

    # ------------------------------------------------------------ basics
    def test_start_and_step(self):
        t = self.start('--no-session', 'build/demo_debug', '--quick')
        t.wait_for(r'Opened \d+ source files|Loaded')
        t.keys('C-n')                                         # starts and stops at main
        t.wait_for(r'Paused:')
        first = t.wait_for(r'^ =>\s+(\d+) ').group(1)
        t.keys('C-n')
        t.wait_for(r'Step complete')
        second = t.wait_for(r'^ =>\s+(\d+) ').group(1)
        self.assertGreater(int(second), int(first))

    def test_function_breakpoint_locals_stack_step_out(self):
        t = self.start('--no-session', 'build/demo_debug', '--quick')
        t.wait_for(r'Loaded|Opened')
        t.menu('new function break')
        t.type('factorial')
        t.keys('Enter')
        t.keys('C-r')
        t.wait_for(r'Breakpoint 1 hit')
        t.wait_for(r'n = 5')                                  # Locals panel
        t.menu('show call stack')
        t.wait_for(r'#0\s+factorial\(n=5\) at .*mathutil\.cpp')
        t.keys('C-u')                                         # step out: recursion hits the breakpoint again
        t.wait_for(r'factorial\(n=4\)')

    def test_locals_tree_expands_stl_and_classes(self):
        line = source_line('main.cpp', 'for (size_t i = 0; i < numbers.size()')
        t = self.start('--no-session', 'build/demo_debug', '--quick')
        t.wait_for(r'Loaded|Opened')
        t.keys('C-g')
        t.type(str(line))
        t.keys('Enter', 'C-y')                                # run to cursor
        t.wait_for(r'Paused:')
        t.keys('Tab', 'Home')
        scr = t.screen()
        rows = [l.split('│', 1)[-1] for l in scr.splitlines()[2:28]]
        idx = next(i for i, r in enumerate(rows) if 'ages = std::map' in r)
        t.keys(*(['Down'] * idx))
        t.keys('Enter')
        t.wait_for(r'\["alice"\] = 30')                       # std::map shown as key = value

    def test_file_tabs_and_files_tab(self):
        t = self.start('--no-session', 'build/demo_debug')
        t.wait_for(r'Opened \d+ source files of demo_debug')
        t.wait_for(r'main\.cpp\s+mathutil\.cpp\s+net_channel\.cpp')   # tab bar
        with open(os.path.join(TESTAPP, 'src', 'mathutil.cpp')) as f:
            n = len(f.read().splitlines())
        t.menu('show files')
        t.wait_for(r'mathutil\.cpp')
        rows = t.screen().splitlines()
        files = [r.split('│', 1)[-1] for r in rows[2:20]]
        idx = next(i for i, r in enumerate(files) if r.strip().startswith(('* mathutil.cpp', '> mathutil.cpp',
                                                                          'mathutil.cpp')))
        t.keys('Home', *(['Down'] * idx))
        t.keys('Enter')
        t.wait_for(r'Ln \d+/%d ' % n)                          # header shows mathutil.cpp's line count
        t.keys('C-]')                                          # Source panel focused: next file
        t.wait_for(r'Ln \d+/(?!%d )\d+ ' % n)

    def test_session_restores_breakpoints_and_tabs(self):
        t = self.start('build/demo_debug', '--quick')
        t.wait_for(r'Opened')
        t.menu('new function break')
        t.type('checksum')
        t.keys('Enter')
        t.wait_for(r'Breakpoints \(1\)')
        t.keys('C-q', 'Enter')
        t.wait_for(r'__GDBTUI_EXITED__')
        subprocess.call(['tmux', 'kill-session', '-t', t.name], stderr=subprocess.DEVNULL)
        # second run with the same HOME (so the same ~/.gdbtui.json)
        t2 = self.tui = Tui(['build/demo_debug', '--quick'], home=t.home)
        t2.wait_for(r'Restored 1 breakpoint')
        t2.wait_for(r'Breakpoints \(1\)')

    # ------------------------------------------------------------ threads, signals, I/O
    def test_threads_live_and_after_break(self):
        t = self.start('--no-session', 'build/demo_debug')
        t.wait_for(r'Opened')
        t.keys('C-r')
        t.wait_for(r'\| Running')
        time.sleep(1.5)
        t.menu('show threads')
        t.wait_for(r'running\s+core \d+\s+\(running')          # live list while running
        t.keys('C-c')
        t.wait_for(r'Paused \(Break All\)')
        t.wait_for(r'stopped\s+core \d+\s+in (shm_channel|net_channel|main)\.cpp:\d+')
        t.menu('show call stack')
        t.wait_for(r'#0 ')

    def test_send_signal_to_running_program(self):
        t = self.start('--no-session', 'build/demo_debug')
        t.wait_for(r'Opened')
        t.keys('C-r')
        t.wait_for(r'\| Running')
        time.sleep(1.5)
        t.menu('send signal')
        t.wait_for(r'Program being debugged')
        t.keys('Enter')
        t.type('usr1')
        t.keys('Enter')
        t.wait_for(r'Delivered SIGUSR1')
        t.menu('show program output')
        t.wait_for(r'SIGUSR1: shm produced=\d+')
        self.assertIn('Running', t.status())                   # still running afterwards

    def test_program_input_via_output_tab(self):
        t = self.start('--no-session', 'build/demo_debug', 'read', '--quick')
        t.wait_for(r'Opened|Loaded')
        t.keys('C-r')
        t.menu('show program output')
        t.wait_for(r'type something:')
        t.type('hello there')
        t.keys('Enter')
        t.wait_for(r'you typed: hello there')
        t.wait_for(r'Exited: exited normally')

    def test_crash_shows_exception_popup(self):
        t = self.start('--no-session', 'build/demo_debug', 'crash')
        t.wait_for(r'Opened|Loaded')
        t.keys('C-r')
        t.wait_for(r'Exception thrown: SIGSEGV')
        t.keys('Escape')
        t.wait_for(r'Signal SIGSEGV')

    # ------------------------------------------------------------ system calls
    def test_live_syscall_recording_links_source(self):
        t = self.start('--no-session', '--syscall-filter', 'write sendto', 'build/demo_debug')
        t.wait_for(r'Recording system calls')
        t.keys('C-r')
        time.sleep(4)
        t.menu('show syscalls')
        t.wait_for(r'sendto\(\d+, "(PING|SUM|FACT|PONG|OK).*net_channel\.cpp:\d+ send_all')
        t.keys('f')                                            # filter: the list scrolls with new calls
        t.type('demo starting')
        t.keys('Enter')
        t.wait_for(r'write\(1, "demo starting.*= \d+\s+main\.cpp:\d+ main')

    def test_step_over_while_recording_lands_on_next_line(self):
        line = source_line('main.cpp', 'printf("demo starting')
        t = self.start('--no-session', 'build/demo_debug', '--quick')
        t.wait_for(r'Opened|Loaded')
        t.keys('C-g')
        t.type(str(line))
        t.keys('Enter', 'C-y')
        t.wait_for(r'^ =>\s+%d ' % line)
        t.menu('start / stop live')
        t.keys('Enter')                                        # record all calls
        t.wait_for(r'SYSCALL REC')
        t.keys('C-n')                                          # step over the printf
        t.wait_for(r'^ =>\s+%d ' % (line + 1), timeout=15)
        t.menu('show syscalls')
        t.wait_for(r'write\(1, "demo starting')

    @unittest.skipUnless(HAVE_STRACE, 'strace not installed')
    def test_strace_run_links_source_with_k(self):
        t = self.start('--no-session', 'build/demo_debug', '--seconds', '2')
        t.wait_for(r'Opened|Loaded')
        t.menu('run program under strace')
        t.wait_for(r'strace options')
        t.keys('Enter')
        t.wait_for(r'strace finished', timeout=20)
        t.menu('show syscalls')
        t.keys('f')
        t.type('main.cpp')
        t.keys('Enter')
        t.wait_for(r' s\s+[\d.]+ \[\d+\] write\(1, .*main\.cpp:\d+')

    # ------------------------------------------------------------ binaries without debug info
    def test_nodebug_opens_disassembly(self):
        t = self.start('--no-session', 'build/demo_nodebug', '--quick')
        t.wait_for(r'Disassembly: main')
        t.wait_for(r'has no debug info')

    def test_stripped_stops_at_entry_point(self):
        t = self.start('--no-session', 'build/demo_stripped', '--quick')
        t.wait_for(r'Disassembly')
        t.keys('C-n')
        t.wait_for(r'Stopped at program entry point', timeout=15)

    def test_assembly_breakpoint_before_run_hits_after_relocation(self):
        t = self.start('--no-session', 'build/demo_nodebug', '--quick')
        t.wait_for(r'Disassembly: main')
        t.keys('Down', 'Down', 'Down', 'Down', 'C-b')
        t.wait_for(r'^B\s+0x[0-9a-f]+ <main\+\d+>')
        t.keys('C-r')
        t.wait_for(r'Breakpoint 1 hit')
        t.wait_for(r'^B=>0x55[0-9a-f]+ <main\+\d+>')          # relocated address, same instruction

    # ------------------------------------------------------------ address randomization (ASLR)
    def test_disasm_breakpoint_survives_restart_with_aslr(self):
        line = source_line('main.cpp', 'if (sig == SIGUSR1)')
        t = self.start('--no-session', 'build/demo_debug', config={'disable_aslr': False})
        t.wait_for(r'Opened')
        t.keys('C-r')
        t.wait_for(r'\| Running')
        time.sleep(1)
        t.keys('C-c')
        t.wait_for(r'Paused')
        t.keys('C-g')
        t.type(str(line))
        t.keys('Enter', 'C-d')
        t.wait_for(r'main\.cpp:%d\s+if \(sig == SIGUSR1\)' % line)
        t.keys('C-b')
        t.wait_for(r'Breakpoints \(1\)')
        t.menu('restart')
        t.wait_for(r'\| Running')
        time.sleep(1)
        t.menu('send signal')
        t.keys('Enter')
        t.type('usr1')
        t.keys('Enter')
        t.wait_for(r'Breakpoint 1 hit.*on_signal|on_signal.*Breakpoint 1 hit|Breakpoint 1 hit', timeout=15)
        self.assertIn('on_signal', t.screen())

    def test_stale_address_breakpoint_recovery(self):
        t = self.start('--no-session', 'build/demo_debug', config={'disable_aslr': False})
        t.wait_for(r'Opened')
        t.keys('C-r')
        t.wait_for(r'\| Running')
        time.sleep(1)
        t.keys('C-c')
        t.wait_for(r'Paused')
        t.menu('run gdb command')
        t.type('eval "break *0x%lx", (long)&on_signal + 15')    # a raw address, valid for this run only
        t.keys('Enter')
        t.wait_for(r'Breakpoints \(1\)')
        t.menu('restart')
        t.wait_for(r'Breakpoint Problem', timeout=15)
        t.keys('Escape')
        t.wait_for(r'What should happen to breakpoint')
        t.keys('Enter')                                        # Delete and continue
        t.wait_for(r'\| Running')

    # ------------------------------------------------------------ misc
    def test_mouse_clicks_shortcuts_and_tabs(self):
        t = self.start('--no-session', '--mouse', 'build/demo_debug')
        t.wait_for(r'Opened \d+ source files')
        t.wait_for(r'\A \^RRun  \^NOver')                      # shortcuts on the top line
        t.wait_for(r'^ gdbtui .*Opened \d+ source files')       # messages on the status line
        t.click_text('^NOver', row=0)                          # starts and stops at main
        t.wait_for(r'Paused:')
        first = int(t.wait_for(r'^ =>\s+(\d+) ').group(1))
        t.click_text('^NOver', row=0)
        t.wait_for(r'^ =>\s+(?!%d )\d+ ' % first)
        second = int(t.wait_for(r'^ =>\s+(\d+) ').group(1))
        self.assertGreater(second, first)
        self.assertEqual(second, source_line('main.cpp', 'Options opt = parse_options'))
        t.click_text('^NOver', row=0, double=True)           # double click: two steps
        t.wait_for(r'^ =>\s+%d ' % source_line('main.cpp', 'signal(SIGTERM, on_signal)'))
        t.click_text(' Threads ')                              # a tab label in the bottom panel
        t.wait_for(r'^\* 1 .*stopped.*main\(\) at src/main\.cpp')
        # A message goes on the status line; the shortcuts on the top line stay untouched.
        t.menu('toggle hexadecimal')
        t.wait_for(r'^ gdbtui .*Paused.*Hexadecimal display on')
        self.assertTrue(t.screen().startswith(' ^RCont  ^NOver  ^TInto'), t.screen())
        t.click_text('Hexadecimal display on', row=-1)       # clicking the status line clears it
        t.wait_for(r'^ gdbtui .*Paused(?!.*Hexadecimal).*Thread 1 ')
        t.click_text('^RCont', row=0)
        t.wait_for(r'\^CBreak')
        t.click_text('^CBreak', row=0)
        t.wait_for(r'Paused: Paused \(Break All\)')

    def test_help_and_menu_open(self):
        t = self.start('--no-session', 'build/demo_debug', '--quick')
        t.wait_for(r'Opened|Loaded')
        t.keys('C-_')
        t.wait_for(r'GLOBAL SHORTCUTS')
        t.keys('Escape', 'C-x')
        t.wait_for(r'Command Menu')
        t.keys('Escape')

    def test_quit_cleans_up_processes(self):
        t = self.start('--no-session', 'build/demo_debug')
        t.wait_for(r'Opened')
        t.keys('C-r')
        m = t.wait_for(r'\[pid (\d+)\]\s+\| Running')
        pid = int(m.group(1))
        t.keys('C-q')
        t.wait_for(r'Quit gdbtui\?')
        t.keys('Enter')
        t.wait_for(r'__GDBTUI_EXITED__')
        time.sleep(0.5)
        self.assertFalse(os.path.exists('/proc/%d' % pid), 'debugged program still running after quit')


if __name__ == '__main__':
    unittest.main(verbosity=2)
