"""Increment 8: weapon swap time, and flinch.

Swapping was instant, so a shotgun-sniper-rifle burst beat mastering any one
of them: each weapon's fire rate is tracked separately and chaining sidestepped
all three. Flinch is the other half -- without it a fight is a damage race."""
import os, sys, re, os, subprocess, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "index.html")
SRV_PATH = os.path.join(ROOT, "server.py")
S = H.S
C = S.CONFIG
SWAP = C["SWAP_TIME"]

def rig(mode="dm"):
    H.seed()
    g = H.new_game(mode)
    p = H.add(g, "p")
    H.attach(g, p)
    p["pos"] = [-30.0, 0.1, -30.0]
    p["protect_until"] = 0.0
    foe = H.add(g, "foe"); foe["pos"] = [-30.0, 0.1, -18.0]
    shots = []
    real = g.do_shoot
    def spy(pl, w, o, d, s, at_ms=None):
        before = pl["last_shot"].get(w, 0.0)
        r = real(pl, w, o, d, s, at_ms)
        if pl["last_shot"].get(w, 0.0) != before:
            shots.append(w)
        return r
    g.do_shoot = spy
    return g, p, foe, shots

def fire(g, p, w):
    g.handle(p["id"], {"t": "shoot", "w": w, "o": list(p["pos"]),
                       "d": [0.0, 0.0, 1.0], "seed": 1})

def switch(g, p, h):
    g.handle(p["id"], {"t": "switch", "hand": h})

print("== a gun-to-gun swap costs time ==")
g, p, foe, shots = rig()
p["hand"] = "ar"
fire(g, p, "ar")
H.check("you can fire what you are already holding", shots == ["ar"], shots)
switch(g, p, "shotgun")
H.check("the switch is registered", p["hand"] == "shotgun")
fire(g, p, "shotgun")
H.check("but not immediately", shots == ["ar"], shots)
H.CLOCK.advance(SWAP * 0.6)
fire(g, p, "shotgun")
H.check("nor part way through the swap", shots == ["ar"], shots)
H.CLOCK.advance(SWAP * 0.6)
fire(g, p, "shotgun")
H.check("once it is up, it fires", shots == ["ar", "shotgun"], shots)

print("\n== the three-weapon burst is gone ==")
g, p, foe, shots = rig()
p["hand"] = "ar"
for w in ("ar", "shotgun", "sniper"):
    switch(g, p, w)
    fire(g, p, w)
H.check("chaining three weapons lands at most one shot", len(shots) <= 1, shots)

print("\n== building is not taxed ==")
g, p, foe, shots = rig()
p["hand"] = "ar"
switch(g, p, "wall")
H.check("gun to wall is free", p["swap_ready"] == 0.0, p["swap_ready"])
switch(g, p, "ar")
H.check("wall back to gun is free too", p["swap_ready"] == 0.0, p["swap_ready"])
fire(g, p, "ar")
H.check("and you can shoot straight out of a build", shots == ["ar"], shots)

print("\n== a shot the server never saw you draw pays the swap ==")
g, p, foe, shots = rig()
p["hand"] = "ar"
fire(g, p, "sniper")                   # no switch message at all
H.check("the unannounced shot is refused", shots == [], shots)
H.check("and it is charged as a swap", p["swap_ready"] > H.CLOCK.t, p["swap_ready"])
H.CLOCK.advance(SWAP * 1.2)
fire(g, p, "sniper")
H.check("after the delay it fires", shots == ["sniper"], shots)

print("\n== grenades respect it too ==")
g, p, foe, shots = rig()
p["hand"] = "ar"
switch(g, p, "grenade")
before = len(g.grenades)
g.handle(p["id"], {"t": "grenade", "o": list(p["pos"]), "d": [0, 0.3, -1]})
H.check("no throw during the swap", len(g.grenades) == before, len(g.grenades))
H.CLOCK.advance(SWAP * 1.2)
g.handle(p["id"], {"t": "grenade", "o": list(p["pos"]), "d": [0, 0.3, -1]})
H.check("and one after it", len(g.grenades) == before + 1)

print("\n== spawning hands you a ready weapon ==")
g, p, foe, shots = rig()
switch(g, p, "shotgun")
H.check("mid-swap before the spawn", p["swap_ready"] > 0)
g.spawn(p)
H.check("a fresh life is not mid-swap", p["swap_ready"] == 0.0, p["swap_ready"])

print("\n== bots pay it as well ==")
H.seed()
g = H.new_game("dm")
bot = H.add(g, "bot", bot=True, diff="hard"); H.attach(g, bot)
bot["pos"] = [-30.0, 0.05, -10.0]; bot["yaw"] = 180.0
foe = H.add(g, "foe"); foe["pos"] = [-30.0, 0.05, 0.0]
g.place = lambda *a, **k: None
seen = []
real = g.do_shoot
def spy2(pl, w, o, d, s, at_ms=None):
    b = pl["last_shot"].get(w, 0.0)
    r = real(pl, w, o, d, s, at_ms)
    if pl["last_shot"].get(w, 0.0) != b:
        seen.append((round(H.CLOCK.t, 2), w))
    return r
g.do_shoot = spy2
H.run(g, 4.0)
changes = [i for i in range(1, len(seen)) if seen[i][1] != seen[i-1][1]]
gaps = [seen[i][0] - seen[i-1][0] for i in changes]
H.check("a bot that changes weapon waits for it",
        all(gp >= SWAP * 0.9 for gp in gaps),
        "gaps at weapon changes: %s" % [round(x, 2) for x in gaps])
# The bot closes to 10m, which is shotgun range: rate 0.83s, minus its 0.11s
# reaction and the 0.35s swap out of the rifle it spawned holding. Four rounds
# in four seconds is the whole budget, not a shortfall.
H.check("the bot still fights", len(seen) >= 3, len(seen))

print("\n== flinch ==")
HTML = open(HTML_PATH, encoding="utf-8").read()
SRV = open(SRV_PATH, encoding="utf-8").read()
H.check("build stamps match",
        re.search(r"const BUILD_ID = '([^']+)'", HTML).group(1) ==
        re.search(r'BUILD_ID = "([^"]+)"', SRV).group(1))
H.check("flinch rides the existing recoil punch",
        "recoilPitch += kick;" in HTML)
H.check("it is scaled by the damage that landed", "(CFG.FLINCH || 0) * m.dmg / 100" in HTML)
H.check("and capped", "Math.min(CFG.FLINCH_MAX || 0," in HTML)
H.check("self-inflicted damage does not flinch you", "if (!hurtBy) {" in HTML)
H.check("the client announces every weapon change",
        "Net.send({ t: 'switch', hand: h });" in HTML)
H.check("and predicts the delay so the click is refused locally",
        "if (now < state.swapReady) return false;" in HTML)

JSC = "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc"
js = """
var FLINCH = %r, FLINCH_MAX = %r;
function kick(dmg) { return Math.min(FLINCH_MAX, FLINCH * dmg / 100); }
print(JSON.stringify([kick(22), kick(80), kick(100), kick(220)]));
""" % (C["FLINCH"], C["FLINCH_MAX"])
path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fl.js")
open(path, "w").write(js)
out = subprocess.run([JSC, path], capture_output=True, text=True).stdout.strip()
os.remove(path)
r = json.loads(out)
print("   rifle 22dmg -> %.2f deg | shotgun 80 -> %.2f | sniper 100 -> %.2f | overkill 220 -> %.2f"
      % tuple(r))
H.check("a rifle round nudges rather than throws", r[0] < 0.5, r[0])
H.check("a sniper hit is a real disturbance", r[2] >= 1.0, r[2])
H.check("nothing exceeds the cap", max(r) <= C["FLINCH_MAX"], r)
H.check("the kick never exceeds one weapon's recoil",
        max(r) <= max(w["recoil"] for w in C["WEAPONS"].values()),
        "%.2f vs max recoil %.2f" % (max(r), max(w["recoil"] for w in C["WEAPONS"].values())))

sys.exit(H.report())
