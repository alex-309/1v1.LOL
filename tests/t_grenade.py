"""Increment 6: grenades are carried, not infinite.

They had mag 0 and a 1.2s cooldown, which meant 80 to a player and 160 to a
build, forever, for free."""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "index.html")
SRV_PATH = os.path.join(ROOT, "server.py")
S = H.S
C = S.CONFIG
CARRY = C["WEAPONS"]["grenade"]["carry"]
RATE = C["WEAPONS"]["grenade"]["rate"]

def rig(mode="dm"):
    H.seed()
    g = H.new_game(mode)
    p = H.add(g, "p")
    p["pos"] = [-30.0, 0.1, -30.0]
    return g, p

def throw(g, p):
    before = len(g.grenades)
    g.throw_grenade(p, list(p["pos"]), [0.0, 0.3, -1.0])
    return len(g.grenades) > before

print("== you start with a countable number ==")
g, p = rig()
H.check("a fresh life carries %d grenades" % CARRY, p["ammo"]["grenade"] == CARRY,
        p["ammo"]["grenade"])
H.check("the other weapons still start on full magazines",
        all(p["ammo"][w] == C["WEAPONS"][w]["mag"]
            for w in ("ar", "shotgun", "sniper")),
        p["ammo"])

print("\n== and they run out ==")
g, p = rig()
thrown = 0
for i in range(10):
    H.CLOCK.advance(RATE * 1.1)       # never the cooldown that stops us
    if throw(g, p):
        thrown += 1
H.check("exactly %d throws land, then nothing" % CARRY, thrown == CARRY,
        "%d thrown" % thrown)
H.check("the count bottoms out at zero, never negative",
        p["ammo"]["grenade"] == 0, p["ammo"]["grenade"])

print("\n== the cooldown still applies on top ==")
g, p = rig()
H.check("first throw lands", throw(g, p))
H.check("an immediate second is refused by the rate limit", not throw(g, p))
H.check("and that refusal did not cost a grenade",
        p["ammo"]["grenade"] == CARRY - 1, p["ammo"]["grenade"])
H.CLOCK.advance(RATE * 1.1)
H.check("after the cooldown it lands", throw(g, p))

print("\n== reloading is not a resupply ==")
g, p = rig()
H.attach(g, p)
for _ in range(CARRY):
    H.CLOCK.advance(RATE * 1.1); throw(g, p)
H.check("out of grenades", p["ammo"]["grenade"] == 0)
p["hand"] = "grenade"
g.handle(p["id"], {"t": "reload"})
H.run(g, 5.0)
H.check("pressing reload never refills them", p["ammo"]["grenade"] == 0,
        p["ammo"]["grenade"])
H.check("and no phantom reload was started", p["reloading"] is None,
        p["reloading"])

print("\n== respawning does resupply ==")
g, p = rig()
for _ in range(CARRY):
    H.CLOCK.advance(RATE * 1.1); throw(g, p)
g.spawn(p)
H.check("a fresh life restores the pouch", p["ammo"]["grenade"] == CARRY,
        p["ammo"]["grenade"])

print("\n== a duel still has no grenades at all ==")
g, p = rig("duel")
H.check("duel loadout excludes them", "grenade" not in S.loadout_for("duel"))
H.check("and the throw is refused outright", not throw(g, p))

print("\n== the client agrees ==")
HTML = open(HTML_PATH, encoding="utf-8").read()
SRV = open(SRV_PATH, encoding="utf-8").read()
H.check("build stamps match",
        re.search(r"const BUILD_ID = '([^']+)'", HTML).group(1) ==
        re.search(r'BUILD_ID = "([^"]+)"', SRV).group(1))
H.check("HUD shows a carried count, not a magazine fraction", "if (spec.carry)" in HTML)
H.check("client refuses a throw it has no grenade for",
        "(state.ammo.grenade || 0) <= 0" in HTML)
H.check("client predicts the decrement so the HUD does not lag",
        "state.ammo.grenade = (state.ammo.grenade || 0) - 1" in HTML)
H.check("server is still the authority (it sends the real count back)",
        '"t": "you", "ammo": p["ammo"]' in SRV)

sys.exit(H.report())
