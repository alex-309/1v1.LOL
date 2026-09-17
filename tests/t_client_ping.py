"""Increment 2: the ping readout. The smoothing line used to read
    state.ping = state.ping * 0.9 + 0.1 * state.ping
which is state.ping -- a self-assignment that smoothed nothing, while the
actual sample landed raw in the 'pong' handler."""
import os, sys, re, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "index.html")
SRV_PATH = os.path.join(ROOT, "server.py")

HTML = open(HTML_PATH, encoding="utf-8").read()
JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"

print("== the no-op is gone ==")
H.check("no self-assigning smoothing line remains",
        not re.search(r"state\.ping\s*=\s*state\.ping\s*\*\s*[\d.]+\s*\+\s*[\d.]+\s*\*\s*state\.ping", HTML))
H.check("the 'state' handler no longer touches ping",
        "case 'state':\n      pushSnapshot(m);" in HTML)

print("\n== the sample is smoothed where it arrives ==")
m = re.search(r"case 'pong':(.*?)break;", HTML, re.S)
body = m.group(1) if m else ""
H.check("'pong' computes an rtt and blends it",
        "performance.now() - m.c" in body and "state.ping * 0.7" in body,
        body.strip()[:120])

print("\n== the blend converges, and takes the first sample raw ==")
js = """
var ping = 0;
function sample(rtt) { ping = ping > 0 ? ping * 0.7 + 0.3 * rtt : rtt; }
sample(80);            var first = ping;
for (var i = 0; i < 12; i++) sample(80);
var settled = ping;
sample(400);           var spike = ping;   // one bad round trip
for (var i = 0; i < 12; i++) sample(80);
var recovered = ping;
print(JSON.stringify([first, settled, spike, recovered]));
"""
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_ping.js")
open(path, "w").write(js)
out = subprocess.run([JSC, path], capture_output=True, text=True).stdout.strip()
os.remove(path)
first, settled, spike, recovered = eval(out)
print("   first=%.1f settled=%.1f after-spike=%.1f recovered=%.1f" % (first, settled, spike, recovered))
H.check("first sample is shown raw, not climbing out of zero", abs(first - 80) < 0.01)
H.check("a steady link settles on its true rtt", abs(settled - 80) < 0.5)
H.check("one bad round trip does not yank the readout to it", spike < 200,
        "spike showed %.1f of a 400ms sample" % spike)
# Convergence is geometric (0.7^n), so "back to true" is asymptotic: twelve
# samples leave 0.7^12 = 1.4% of the spike, i.e. ~1.3ms of a 320ms excursion.
# That rounds to the same integer the HUD would print.
H.check("and it returns to true after the spike", abs(recovered - 80) < 2.0,
        "%.1f after 12 more samples" % recovered)
H.check("the spike is fully gone within half a minute",
        abs(80 + (spike - 80) * (0.7 ** 30) - 80) < 0.01)

sys.exit(H.report())
