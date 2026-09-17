"""Syntax-check every inline <script> block in index.html.

No node on this machine, but macOS ships JavaScriptCore. `new Function(src)`
parses the whole body without running a line of it, which is exactly the check
we want: it catches syntax errors and nothing else.
"""
import re, sys, subprocess, json, os

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "index.html")
OUT = os.path.dirname(os.path.abspath(__file__))

html = open(HTML, encoding="utf-8").read()
blocks = []
for m in re.finditer(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, re.S):
    if "src=" in m.group("attrs"):
        continue
    line = html.count("\n", 0, m.start()) + 1
    blocks.append((line, m.group("body")))

if not os.path.exists(JSC):
    print("jsc not available; skipping"); sys.exit(0)

bad = 0
for i, (line, body) in enumerate(blocks):
    # document.write of a <script> tag confuses nothing here -- we only parse.
    src = os.path.join(OUT, "_blk%d.js" % i)
    with open(src, "w", encoding="utf-8") as f:
        f.write(body)
    chk = os.path.join(OUT, "_chk%d.js" % i)
    with open(chk, "w", encoding="utf-8") as f:
        f.write(
            "var fs = read('%s');\n"
            "try { new Function(fs); print('OK'); }\n"
            "catch (e) { print('ERR ' + e); }\n" % src)
    r = subprocess.run([JSC, chk], capture_output=True, text=True)
    out = (r.stdout or "").strip() + (r.stderr or "").strip()
    status = "ok " if out.startswith("OK") else "ERR"
    if not out.startswith("OK"):
        bad += 1
    print("  %s block #%d at index.html:%d (%d lines)%s"
          % (status, i, line, body.count("\n"),
             "" if out.startswith("OK") else "\n       " + out))
    os.remove(src); os.remove(chk)

print("\n%d script block(s), %d with syntax errors" % (len(blocks), bad))
sys.exit(1 if bad else 0)
