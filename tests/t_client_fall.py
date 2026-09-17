"""Increment 5, client half: a fall must not present as a self-murder.

The server credits a fall to the victim because there is nowhere else to put
it, so untreated the killfeed reads "Alice -> Alice" and the death panel reads
"Eliminated by Alice" to Alice."""
import os, sys, re, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "index.html")
SRV_PATH = os.path.join(ROOT, "server.py")

HTML = open(HTML_PATH, encoding="utf-8").read()
SRV = open(SRV_PATH, encoding="utf-8").read()
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"

print("== the two builds agree ==")
cb = re.search(r"const BUILD_ID = '([^']+)'", HTML).group(1)
sb = re.search(r'BUILD_ID = "([^"]+)"', SRV).group(1)
H.check("client and server carry the same BUILD_ID", cb == sb, "%r vs %r" % (cb, sb))

print("\n== the cause reaches the client ==")
H.check("server tags the death with a cause", '"cause": cause' in SRV)
H.check("server passes the weapon through as that cause", "cause=weapon" in SRV)
H.check("client reads it", "selfCause(m.cause, m.by, m.id)" in HTML)

print("\n== presentation logic ==")
block = re.search(r"const SELF_CAUSE = \{.*?\n\};", HTML, re.S).group(0)
fn = re.search(r"function selfCause\(.*?\n\}", HTML, re.S).group(0)
js = block + "\n" + fn + """
function t(cause, by, victim) { var r = selfCause(cause, by, victim); return r ? [r.they, r.you] : null; }
print(JSON.stringify({
  fallSelf:   t('fall', 7, 7),
  fallOther:  t('fall', 3, 7),
  gunSelf:    t('ar', 7, 7),
  nadeSelf:   t('grenade', 7, 7),
  nadeOther:  t('grenade', 3, 7),
  noCause:    t(undefined, 7, 7)
}));
"""
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fall.js")
open(path, "w").write(js)
out = subprocess.run([JSC, path], capture_output=True, text=True)
os.remove(path)
import json
r = json.loads(out.stdout.strip())
for k, v in r.items():
    print("   %-10s -> %s" % (k, v))

H.check("a fall on yourself names the fall", r["fallSelf"] == ["fell", "Fell"])
H.check("a fall credited to someone else is a normal kill", r["fallOther"] is None)
H.check("being shot by yourself is not a special case", r["gunSelf"] is None)
H.check("your own grenade is", r["nadeSelf"] == ["blew themselves up", "Your own grenade"])
H.check("someone else's grenade is not", r["nadeOther"] is None)
H.check("a death with no cause is a normal kill", r["noCause"] is None)

print("\n== the wiring that uses it ==")
H.check("killfeed uses the third-person form", "how.they" in HTML)
H.check("death panel uses the first-person form", "deathcam.how.you" in HTML)
H.check("combat report names the cause instead of you",
        "hurtBy ? hurtBy.you : m.bn" in HTML)
H.check("no damage arrow is drawn for a fall",
        "damageDirection(hurtBy ? null : m.from)" in HTML)

sys.exit(H.report())
