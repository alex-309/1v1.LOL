"""Increment 1: bots keep fighting while building, and the four styles stay
distinct. The failure this guards against is a bot placing a piece across its
own sight line, losing LOS, and standing behind it for the rest of the round."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
S = H.S

def sample(style, secs=12.0, bleed=False, diff="medium", seed=1234):
    """bleed=True puts the bot under bursty incoming fire, which is what the
    cover path keys off. Real fire is a magazine then a gap -- pinning
    last_dmg_at every tick would model being hit 30x a second forever, and the
    bot's whole cover response is built around the 1.2s window after a hit."""
    H.seed(seed)
    g = H.new_game("dm")
    bot = H.add(g, "bot", bot=True, diff=diff, style=style)
    foe = H.add(g, "foe")
    bot["pos"] = [-30.0, 0.05, -12.0]; bot["yaw"] = 180.0
    foe["pos"] = [-30.0, 0.05, 0.0]
    shots, walls, dists, maxy = [0], [0], [], [0.0]
    real_shoot, real_place = g.do_shoot, g.place
    g.do_shoot = lambda p, w, o, d, s, at_ms=None: (
        shots.__setitem__(0, shots[0] + 1), real_shoot(p, w, o, d, s, at_ms))[1]
    def place_spy(p, t, cx, cy, cz, d, *a, **k):
        if t == "wall":
            walls[0] += 1
        return real_place(p, t, cx, cy, cz, d, *a, **k)
    g.place = place_spy
    def each(_g):
        foe["hp"] = 100.0; foe["shield"] = 100.0; foe["alive"] = True
        if bleed and int(H.CLOCK.t * 1000 // 700) % 3 == 0:
            bot["last_dmg_at"] = H.CLOCK.t
            # WHO shot you matters: cover answers incoming fire, and damage
            # with no attacker (a fall, your own grenade) deliberately does not
            # make a bot wall up.
            bot["last_dmg_from"] = foe["id"]
            bot["hp"] = 60.0
        dists.append(S.v_dist(bot["pos"], foe["pos"]))
        maxy[0] = max(maxy[0], bot["pos"][1])
    H.run(g, secs, each=each)
    return {"shots": shots[0], "walls": walls[0], "pieces": len(g.pieces),
            "avg_dist": sum(dists) / len(dists), "max_y": maxy[0]}

def avg(style, n=5, **kw):
    runs = [sample(style, seed=1000 + i, **kw) for i in range(n)]
    return {k: sum(r[k] for r in runs) / float(n) for k in runs[0]}

STYLES = ("balanced", "rusher", "turtle", "builder")

print("== calm: clear sight line, nobody shooting back ==")
calm = {s: avg(s) for s in STYLES}
print("  %-9s %7s %9s %7s %6s" % ("style", "shots", "avg dist", "max y", "walls"))
for s in STYLES:
    r = calm[s]
    print("  %-9s %7.1f %9.1f %7.1f %6.1f"
          % (s, r["shots"], r["avg_dist"], r["max_y"], r["walls"]))

# These separate FIGHTING from FROZEN, which is the failure they exist to
# catch -- a bot that walls across its own sight line fired 0-1 shots in a
# whole match. They are deliberately not DPS assertions: fire volume moves
# with balance (the 0.35s weapon swap alone roughly halved it) and a test
# pinned near the ceiling breaks every time a number is tuned. A shotgun at
# knife range is the slowest case -- 0.83s a shell, 3.4s to reload five.
for s in STYLES:
    H.check("%-9s sustains fire while building" % s, calm[s]["shots"] >= 5,
            "%.1f shots in 12s" % calm[s]["shots"])
H.check("nobody walls across their own sight line while calm",
        all(calm[s]["walls"] == 0 for s in STYLES),
        {s: calm[s]["walls"] for s in STYLES})
H.check("rusher fights closer than turtle",
        calm["rusher"]["avg_dist"] < calm["turtle"]["avg_dist"],
        "rusher %.1f vs turtle %.1f" % (calm["rusher"]["avg_dist"], calm["turtle"]["avg_dist"]))
# What separates a builder is VOLUME, not altitude. Every style that ramps at
# all reaches the same one-cell height inside six seconds, and a rusher that
# jumps off its own ramp can top a builder by half a metre -- so max_y is
# noise, not signal, and is deliberately not asserted on. Pieces placed is the
# real signature: build 2.4 against the rusher's 0.55.
H.check("builder builds more than rusher",
        calm["builder"]["pieces"] > calm["rusher"]["pieces"],
        "builder %.1f vs rusher %.1f pieces"
        % (calm["builder"]["pieces"], calm["rusher"]["pieces"]))

print("\n== under fire: the cover path ==")
hot = {s: avg(s, bleed=True) for s in STYLES}
print("  %-9s %7s %6s %7s" % ("style", "shots", "walls", "pieces"))
for s in STYLES:
    r = hot[s]
    print("  %-9s %7.1f %6.1f %7.1f" % (s, r["shots"], r["walls"], r["pieces"]))

H.check("a turtle under fire walls up", hot["turtle"]["walls"] >= 1,
        "%.1f walls" % hot["turtle"]["walls"])
H.check("turtle walls more than rusher",
        hot["turtle"]["walls"] > hot["rusher"]["walls"],
        "turtle %.1f vs rusher %.1f" % (hot["turtle"]["walls"], hot["rusher"]["walls"]))
# The turtle is the floor here by design -- push 0.15, retreat 0.85, and it
# walls on 95% of cover builds -- so this separates "returning fire" from
# "frozen" rather than asserting a rate. Frozen was 0 to 1 shots in a whole
# match: a bot that had walled across its own sight line and never regained it.
H.check("no style is frozen under fire -- all still return fire",
        all(hot[s]["shots"] >= 2 for s in STYLES),
        {s: round(hot[s]["shots"], 1) for s in STYLES})

sys.exit(H.report())
