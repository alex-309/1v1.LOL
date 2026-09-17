"""Increment 5: fall damage.

The server already computed the landing event and threw it away. Height is the
resource the whole game turns on, and abandoning it used to be free."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
S = H.S
C = S.CONFIG
SAFE, PER_M = C["FALL_SAFE_H"], C["FALL_DMG_PER_M"]

def drop(height, mode="dm", start_hp=None, start_shield=None):
    """Lift a player to `height`, let go, and land them. Drives the same
    entry point a real client uses: the 'input' message."""
    H.seed()
    g = H.new_game(mode)
    p = H.add(g, "p")
    p["pos"] = [-30.0, 0.0, -30.0]
    p["grounded"] = True
    if start_hp is not None: p["hp"] = start_hp
    if start_shield is not None: p["shield"] = start_shield
    p["protect_until"] = 0.0
    g.track_fall(p)                                   # grounded, nothing owed
    p["pos"] = [-30.0, height, -30.0]; p["grounded"] = False
    g.track_fall(p)                                   # airborne at the peak
    for y in (height * 0.5, height * 0.2):            # falling
        p["pos"] = [-30.0, y, -30.0]; g.track_fall(p)
    p["pos"] = [-30.0, 0.0, -30.0]; p["grounded"] = True
    g.track_fall(p)                                   # landed
    return g, p

def expected(h):
    return 0.0 if h <= SAFE else (h - SAFE) * PER_M

print("== the curve ==")
print("  %8s %10s %8s %8s" % ("drop", "expected", "hp", "shield"))
for h in (1.5, 4.0, 5.5, 8.0, 12.0, 16.0):
    g, p = drop(h)
    print("  %7.1fm %10.1f %8.1f %8.1f" % (h, expected(h), p["hp"], p["shield"]))
    H.check("a %.1fm drop costs %.1f" % (h, expected(h)),
            abs((100.0 - p["hp"]) - min(expected(h), 100.0)) < 0.01,
            "lost %.1f hp" % (100.0 - p["hp"]))

print("\n== the thresholds that matter ==")
g, p = drop(C["JUMP"] ** 2 / (2 * C["GRAVITY"]))
H.check("a plain jump never hurts", p["hp"] == 100.0,
        "jump peaks at %.2fm" % (C["JUMP"] ** 2 / (2 * C["GRAVITY"])))
g, p = drop(S.CELL)
H.check("riding down one storey of your own ramp is free", p["hp"] == 100.0)
g, p = drop(S.CELL * 2)
H.check("two storeys hurts but is survivable", 0 < (100 - p["hp"]) < 100,
        "%.1f damage" % (100 - p["hp"]))
g, p = drop(S.CELL * 4)
H.check("four storeys kills outright", not p["alive"], "hp %.1f" % p["hp"])

print("\n== shields do not stop the ground ==")
g, p = drop(12.0, start_shield=100.0)
H.check("shield is untouched by a fall", p["shield"] == 100.0,
        "shield %.1f" % p["shield"])
H.check("health took all of it", abs(p["hp"] - (100.0 - expected(12.0))) < 0.01)

print("\n== it is not a fall if you did not fall ==")
H.seed(); g = H.new_game("dm"); p = H.add(g, "p")
p["pos"] = [-30.0, 0.0, -30.0]; p["grounded"] = True
for _ in range(50):
    g.track_fall(p)
H.check("standing still never charges anything", p["hp"] == 100.0)

# walking UP is not a drop
H.seed(); g = H.new_game("dm"); p = H.add(g, "p"); p["protect_until"] = 0.0
p["pos"] = [-30.0, 0.0, -30.0]; p["grounded"] = True; g.track_fall(p)
for y in (2.0, 6.0, 12.0, 20.0):
    p["pos"] = [-30.0, y, -30.0]; p["grounded"] = True; g.track_fall(p)
H.check("climbing a tower charges nothing", p["hp"] == 100.0, "hp %.1f" % p["hp"])

print("\n== spawning is not a landing ==")
H.seed(); g = H.new_game("dm"); p = H.add(g, "p")
p["pos"] = [-30.0, 40.0, -30.0]; p["grounded"] = False; g.track_fall(p)
g.spawn(p)
p["grounded"] = True; g.track_fall(p)
H.check("a respawn clears the tracked fall", p["hp"] == 100.0, "hp %.1f" % p["hp"])

print("\n== sandbox modes are exempt ==")
for mode in ("build", "trainer"):
    g, p = drop(30.0, mode=mode)
    H.check("%s mode takes no fall damage" % mode, p["hp"] == 100.0,
            "hp %.1f" % p["hp"])

print("\n== the death is attributed, not blamed on a phantom ==")
seen = []
H.seed(); g = H.new_game("dm"); p = H.add(g, "p"); p["protect_until"] = 0.0
g.broadcast = lambda msg, *a, **k: seen.append(msg)
p["pos"] = [-30.0, 0.0, -30.0]; p["grounded"] = True; g.track_fall(p)
p["pos"] = [-30.0, 40.0, -30.0]; p["grounded"] = False; g.track_fall(p)
p["pos"] = [-30.0, 0.0, -30.0]; p["grounded"] = True; g.track_fall(p)
die = [m for m in seen if m.get("t") == "die"]
H.check("a lethal fall broadcasts a death", len(die) == 1, seen)
H.check("it is tagged as a fall", die and die[0].get("cause") == "fall",
        die[0] if die else None)
H.check("it credits no killer", die and die[0]["by"] == p["id"])
H.check("the victim's own kill count is untouched", p["kills"] == 0)
H.check("but the death counts", p["deaths"] == 1)

print("\n== the real client path, through handle('input') ==")
H.seed(); g = H.new_game("dm"); p = H.add(g, "p"); p["protect_until"] = 0.0
# Start the player where the inputs are, or the plausibility clamp rejects the
# first one as a 42m teleport -- which it should.
p["pos"] = [-30.0, 0.0, -30.0]
g.clients[p["id"]] = None                 # send_to() tolerates a missing client
def inp(y, grounded):
    g.handle(p["id"], {"t": "input", "p": [-30.0, y, -30.0], "v": [0, 0, 0],
                       "yaw": 0.0, "pitch": 0.0, "cr": 0,
                       "g": 1 if grounded else 0, "ads": 0})
inp(0.0, True)
inp(5.0, False)          # the plausibility clamp allows this (maxd is ~11m)
inp(9.0, False)
inp(5.0, False)
inp(0.0, True)
H.check("the inputs were accepted, not clamped away",
        abs(p["pos"][0] + 30.0) < 0.01, "pos %s" % p["pos"])
H.check("an input-driven fall is charged", p["hp"] < 100.0, "hp %.1f" % p["hp"])
H.check("and charged the right amount",
        abs((100.0 - p["hp"]) - expected(9.0)) < 0.01,
        "lost %.1f, expected %.1f" % (100.0 - p["hp"], expected(9.0)))

print("\n== a real bot match produces no phantom falls ==")
H.seed(); g = H.new_game("dm")
bots = []
for i in range(3):
    b = H.add(g, "bot%d" % i, bot=True, diff="hard", style=("builder","rusher","balanced")[i])
    b["pos"] = [-30.0 + i*4, 0.05, -30.0]; b["protect_until"] = 0.0
    bots.append(b)
foe = H.add(g, "foe"); foe["pos"] = [-20.0, 0.05, -18.0]
hits = {b["id"]: [] for b in bots}
real = g.apply_damage
def spy(target, amount, by_pid, head=False, weapon=None, bypass_shield=False):
    if weapon == "fall" and target["id"] in hits:
        hits[target["id"]].append(round(amount, 1))
    return real(target, amount, by_pid, head, weapon, bypass_shield)
g.apply_damage = spy
def each(_g):
    foe["hp"] = 100.0; foe["shield"] = 100.0; foe["alive"] = True
    for b in bots:
        b["hp"] = 100.0; b["shield"] = 100.0; b["alive"] = True
H.run(g, 30.0, each=each)
allhits = [x for v in hits.values() for x in v]
print("   fall damage events over 30s of 3 bots: %s" % (allhits or "none"))
H.check("bots ramping and fighting take no phantom fall damage",
        not allhits, allhits)

sys.exit(H.report())
