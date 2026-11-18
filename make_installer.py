#!/usr/bin/env python3
"""Build install_gdbtui.txt: a block of shell text you can paste into a container's
terminal. It recreates gdbtui.py there (compressed + base64, checksum verified).

    python3 make_installer.py            # writes install_gdbtui.txt next to gdbtui.py
"""
import base64
import hashlib
import os
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, 'gdbtui.py')
OUT = os.path.join(HERE, 'install_gdbtui.txt')

TEMPLATE = r"""python3 - <<'GDBTUI_EOF'
import base64, hashlib, os, sys, zlib
D = '''
%(data)s
'''
try:
    raw = zlib.decompress(base64.b64decode(''.join(D.split())))
except Exception as e:
    sys.exit('gdbtui install FAILED: the pasted text is damaged (%%s). Paste it again.' %% e)
if hashlib.sha256(raw).hexdigest() != '%(sha)s':
    sys.exit('gdbtui install FAILED: checksum mismatch. Paste it again.')
with open('gdbtui.py', 'wb') as f:
    f.write(raw)
os.chmod('gdbtui.py', 0o755)
print('gdbtui.py written to %%s (%%d bytes, sha256 OK)' %% (os.path.abspath('gdbtui.py'), len(raw)))
print('Run it with:  python3 gdbtui.py ./your_program [args]')
GDBTUI_EOF
"""


def main():
    raw = open(SRC, 'rb').read()
    b64 = base64.b64encode(zlib.compress(raw, 9)).decode('ascii')
    lines = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    text = TEMPLATE % {'data': '\n'.join(lines), 'sha': hashlib.sha256(raw).hexdigest()}
    with open(OUT, 'w') as f:
        f.write(text)
    print('%s: %d lines, %d bytes (source %d bytes, sha256 %s)' % (
        OUT, text.count('\n'), len(text), len(raw), hashlib.sha256(raw).hexdigest()[:16]))


if __name__ == '__main__':
    main()
