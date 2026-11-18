#!/usr/bin/env python3
"""Unit tests for the pure-Python parts of gdbtui.py (no gdb, no terminal needed).

    python3 tests/test_units.py            (or: python3 -m unittest discover tests)

Standard library only; runs on Python 3.6+.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import gdbtui  # noqa: E402

TESTAPP = os.path.join(ROOT, 'testapp')
BUILD = os.path.join(TESTAPP, 'build')


class MIParserTests(unittest.TestCase):
    def test_result_record_with_token(self):
        r = gdbtui.parse_mi_line('12^done,value="42"')
        self.assertEqual(r['type'], 'result')
        self.assertEqual(r['token'], 12)
        self.assertEqual(r['class'], 'done')
        self.assertEqual(r['results'], {'value': '42'})

    def test_error_with_escaped_quotes(self):
        r = gdbtui.parse_mi_line(r'5^error,msg="No symbol \"x\" in current context."')
        self.assertEqual(r['class'], 'error')
        self.assertEqual(r['results']['msg'], 'No symbol "x" in current context.')

    def test_nested_tuples_and_lists(self):
        r = gdbtui.parse_mi_line('*stopped,reason="breakpoint-hit",frame={addr="0x1",func="main",'
                                 'args=[{name="argc",value="1"},{name="argv",value="0x7ff"}]},thread-id="1"')
        self.assertEqual(r['type'], 'exec')
        self.assertEqual(r['class'], 'stopped')
        self.assertEqual(r['results']['frame']['func'], 'main')
        self.assertEqual(r['results']['frame']['args'][1]['name'], 'argv')

    def test_list_of_results_drops_keys(self):
        r = gdbtui.parse_mi_line('^done,stack=[frame={level="0"},frame={level="1"}]')
        self.assertEqual([f['level'] for f in r['results']['stack']], ['0', '1'])

    def test_console_stream_octal_utf8(self):
        r = gdbtui.parse_mi_line(r'~"caf\303\251 \"q\"\n"')
        self.assertEqual(r['type'], 'console')
        self.assertEqual(r['text'], 'café "q"\n')

    def test_bare_value_inside_tuple(self):
        # gdb emits script={"printf ..."} for dprintf breakpoints: a value inside a tuple
        r = gdbtui.parse_mi_line(r'^done,BreakpointTable={body=[bkpt={number="2",type="dprintf",'
                                 r'script={"printf \"n=%d\\n\", n"},line="44"}]}')
        b = r['results']['BreakpointTable']['body'][0]
        self.assertEqual(b['number'], '2')
        self.assertEqual(b['line'], '44')

    def test_notify_and_prompt_and_other(self):
        self.assertEqual(gdbtui.parse_mi_line('=thread-group-started,id="i1",pid="42"')['results']['pid'], '42')
        self.assertEqual(gdbtui.parse_mi_line('(gdb) ')['type'], 'prompt')
        self.assertEqual(gdbtui.parse_mi_line('warning: something')['type'], 'other')

    def test_duplicate_keys_become_list(self):
        r = gdbtui.parse_mi_line('^done,a="1",a="2"')
        self.assertEqual(list(r['results']['a']), ['1', '2'])

    def test_malformed_line_does_not_raise(self):
        r = gdbtui.parse_mi_line('^done,value="unterminated')
        self.assertTrue(r['results'].get('_parse_error'))

    def test_mi_quote_and_cli(self):
        self.assertEqual(gdbtui.mi_quote('a "b"\n\\'), r'"a \"b\"\n\\"')
        self.assertEqual(gdbtui.cli('print x'), '-interpreter-exec console "print x"')

    def test_parse_int(self):
        self.assertEqual(gdbtui.parse_int('0x1f'), 31)
        self.assertEqual(gdbtui.parse_int('42'), 42)
        self.assertEqual(gdbtui.parse_int('(int *) 0x601040 <arr>'), 0x601040)
        self.assertIsNone(gdbtui.parse_int('<MULTIPLE>'))


class StraceParserTests(unittest.TestCase):
    LOG = (
        '100   12:00:00.000100 openat(AT_FDCWD, "/etc/hosts", O_RDONLY) = 3 <0.000010>\n'
        ' > /lib/libc.so.6(__open64+0x5b) [0x114f8b]\n'
        ' > /app/demo(load_config()+0x2a) [0x2a1c]\n'
        ' > /app/demo(std::thread::_State_impl<int>::~_State_impl()+0x12) [0x3a00]\n'
        '100   12:00:00.000200 read(3, "abc", 4096) = 3 <0.000005>\n'
        '101   12:00:00.000300 rt_sigprocmask(SIG_BLOCK, ~[RT_1],  <unfinished ...>\n'
        '100   12:00:00.000400 openat(AT_FDCWD, "/nope", O_RDONLY) = -1 ENOENT (No such file or directory) <0.000003>\n'
        '101   12:00:00.000500 <... rt_sigprocmask resumed>NULL, 8) = 0 <0.000020>\n'
        '101   12:00:00.000600 +++ exited with 0 +++\n'
    )

    def setUp(self):
        self.recs = gdbtui.StraceParser().feed(self.LOG, final=True)

    def test_records_and_notes(self):
        names = [r.get('name') or r.get('note') for r in self.recs]
        self.assertEqual(names, ['openat', 'read', 'rt_sigprocmask', 'openat', 'exited with 0'])

    def test_fields(self):
        first = self.recs[0]
        self.assertEqual(first['tid'], '100')
        self.assertAlmostEqual(first['t'], 0.0)
        self.assertEqual(first['args'], 'AT_FDCWD, "/etc/hosts", O_RDONLY')
        self.assertEqual(first['ret'], '3')
        self.assertEqual(first['dur'], '0.000010')
        self.assertFalse(first['err'])
        self.assertTrue(self.recs[3]['err'])

    def test_stack_lines_with_parentheses_in_symbols(self):
        sites = self.recs[0]['sites']
        self.assertEqual(len(sites), 3)
        self.assertEqual(sites[1], ('/app/demo', 'load_config()+0x2a', 0x2a1c))
        self.assertEqual(sites[2][1], 'std::thread::_State_impl<int>::~_State_impl()+0x12')

    def test_unfinished_resumed_merge(self):
        r = self.recs[2]
        self.assertEqual(r['args'], 'SIG_BLOCK, ~[RT_1], NULL, 8')
        self.assertEqual(r['ret'], '0')

    def test_incremental_feed(self):
        p = gdbtui.StraceParser()
        out = []
        for i in range(0, len(self.LOG), 7):          # feed in awkward chunks
            out += p.feed(self.LOG[i:i + 7])
        out += p.feed('', final=True)
        self.assertEqual(len(out), len(self.recs))


class SyscallFormattingTests(unittest.TestCase):
    def test_open_flags(self):
        self.assertEqual(gdbtui.fmt_open_flags(0), 'O_RDONLY')
        self.assertEqual(gdbtui.fmt_open_flags(0o2 | 0o100 | 0o2000000), 'O_RDWR|O_CREAT|O_CLOEXEC')

    def test_escape_bytes(self):
        self.assertEqual(gdbtui.escape_bytes(b'hi\n\x01"'), '"hi\\n\\x01\\""')
        self.assertTrue(gdbtui.escape_bytes(b'x' * 40, 32).endswith('"...'))

    def test_return_values(self):
        self.assertEqual(gdbtui.fmt_syscall_ret('read', 5), ('5', False))
        ret, err = gdbtui.fmt_syscall_ret('openat', (1 << 64) - 2)          # -ENOENT
        self.assertTrue(err)
        self.assertTrue(ret.startswith('-1 ENOENT'))
        ret, err = gdbtui.fmt_syscall_ret('futex', (1 << 64) - 512)         # kernel restart code
        self.assertFalse(err)
        self.assertTrue(ret.startswith('? ERESTARTSYS'))
        self.assertEqual(gdbtui.fmt_syscall_ret('mmap', 0x7f0000001000)[0], '0x7f0000001000')

    def test_registers_by_arch(self):
        self.assertEqual(gdbtui.syscall_regs('i386:x86-64')[1], '$rax')
        self.assertEqual(gdbtui.syscall_regs('aarch64')[0][0], '$x0')
        self.assertEqual(gdbtui.syscall_regs('i386')[2], 32)

    def test_to_signed(self):
        self.assertEqual(gdbtui.to_signed((1 << 32) - 100, 32), -100)
        self.assertEqual(gdbtui.to_signed(7, 64), 7)


class HelperTests(unittest.TestCase):
    def test_ctrl_codes(self):
        self.assertEqual(gdbtui.ctrl_code('R'), 18)
        self.assertEqual(gdbtui.ctrl_code('^\\'), 28)
        self.assertEqual(gdbtui.ctrl_code('Ctrl+]'), 29)
        self.assertEqual(gdbtui.key_name(31), 'Ctrl+_')

    def test_highlight_block_comment_state(self):
        segs, inc = gdbtui.highlight('int x = 1; /* start', False)
        self.assertTrue(inc)
        self.assertIn(('int', 'type'), segs)
        segs, inc = gdbtui.highlight('end */ return 0;', True)
        self.assertFalse(inc)
        self.assertEqual(segs[0], ('end */', 'com'))
        self.assertIn(('return', 'kw'), segs)

    def test_highlight_string_containing_comment_marker(self):
        segs, _ = gdbtui.highlight('s = "http://x"; // real', False)
        self.assertIn(('"http://x"', 'str'), segs)
        self.assertIn(('// real', 'com'), segs)

    def test_guess_function(self):
        lines = ['int helper(int a)', '{', '    return a;', '}', '', 'void Cls::run()', '{', '    if (x)',
                 '        go();', '}']
        self.assertEqual(gdbtui.guess_function(lines, 2), 'helper')
        self.assertEqual(gdbtui.guess_function(lines, 8), 'Cls::run')
        self.assertIsNone(gdbtui.guess_function(lines, 4))

    def test_is_system_path(self):
        self.assertTrue(gdbtui.is_system_path('/usr/include/c++/13/vector'))
        self.assertTrue(gdbtui.is_system_path('./nptl/futex-internal.c'))
        self.assertFalse(gdbtui.is_system_path('/home/me/src/main.cpp'))

    def test_line_edit(self):
        e = gdbtui.LineEdit('', [])
        for ch in 'hello':
            e.key(ch)
        e.key(1)                    # Ctrl+A: home
        e.key('>')
        self.assertEqual(e.text, '>hello')
        self.assertEqual(e.key(10), 'enter')
        self.assertEqual(e.hist, ['>hello'])
        e.set('')
        e.key(259)                  # KEY_UP recalls history
        self.assertEqual(e.text, '>hello')

    def test_line_edit_fresh_text_is_replaced(self):
        e = gdbtui.LineEdit('word')
        e.fresh = True
        e.key('x')
        self.assertEqual(e.text, 'x')

    def test_text_buffer_partial_lines(self):
        b = gdbtui.TextBuffer()
        b.write('abc')
        b.write('def\nghi')
        self.assertEqual([t for t, _a in b.all()], ['abcdef', 'ghi'])


@unittest.skipUnless(os.path.isdir(BUILD), 'testapp not built (sh testapp/build_variants.sh)')
class ElfTests(unittest.TestCase):
    def test_pie_and_non_pie(self):
        self.assertTrue(gdbtui.elf_is_pie(os.path.join(BUILD, 'demo_debug')))
        self.assertEqual(gdbtui.elf_load_base(os.path.join(BUILD, 'demo_debug')), 0)
        nopie = os.path.join(BUILD, 'demo_nopie')
        if os.path.exists(nopie):
            self.assertFalse(gdbtui.elf_is_pie(nopie))
            self.assertEqual(gdbtui.elf_load_base(nopie), 0x400000)

    def test_not_elf(self):
        self.assertFalse(gdbtui.elf_is_pie(os.path.join(ROOT, 'README.md')))
        self.assertEqual(gdbtui.elf_load_base(os.path.join(ROOT, 'README.md')), 0)


class InstallerTests(unittest.TestCase):
    def test_installer_matches_script(self):
        import hashlib
        import re
        inst = os.path.join(ROOT, 'install_gdbtui.txt')
        if not os.path.exists(inst):
            self.skipTest('install_gdbtui.txt not generated (python3 make_installer.py)')
        with open(os.path.join(ROOT, 'gdbtui.py'), 'rb') as f:
            want = hashlib.sha256(f.read()).hexdigest()
        with open(inst) as f:
            m = re.search(r"hexdigest\(\) != '([0-9a-f]{64})'", f.read())
        self.assertEqual(m.group(1), want, 'install_gdbtui.txt is stale: run python3 make_installer.py')


if __name__ == '__main__':
    unittest.main(verbosity=2)
