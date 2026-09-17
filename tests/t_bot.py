"""Increment 1: bot combat honesty -- reaction time, real reloads, burst."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
S = H.S

def rig(diff="easy", build=False, gap=10.0):
    """Bot facing a stationary foe down a clear lane, `gap` metres away.
    build=False silences the bot's own construction, so the fire logic is
    tested on its own."""
    H.seed()
    g = H.new_game("dm")
    bot = H.add(g, "bot", bot=True, diff=diff)
    foe = H.add(g, "foe")
    bot["pos"] = [-30.0, 0.05, -gap]; bot["yaw"] = 180.0
    foe["pos"] = [-30.0, 0.05, 0.0]
    if not build:
        g.place = lambda *a, **k: None
    shots = []
    real = g.do_shoot
    def spy(p, weapon, origin, direction, seed_, at_ms=None):
        # Record what actually FIRED, not what was attempted: do_shoot refuses
        # for rate, ammo and swap, and counting refusals as shots measures the
        # bot's intent rather than its output.
        before = p["last_shot"].get(weapon, 0.0)
        r = real(p, weapon, origin, direction, seed_, at_ms)
        if p["last_shot"].get(weapon, 0.0) != before:
            shots.append((H.CLOCK.t, weapon))
        return r
    g.do_shoot = spy
    return g, bot, foe, shots

print("== 1a. reaction time is honoured, and scales with difficulty ==")
for diff, want in (("easy", 0.40), ("medium", 0.22), ("hard", 0.11)):
    g, bot, foe, shots = rig(diff)
    t0 = H.CLOCK.t
    H.run(g, 1.5)
    d = (shots[0][0] - t0) if shots else None
    H.check("%-6s bot waits >= %.2fs before its first round" % (diff, want),
            d is not None and d >= want,
            "first shot at %s" % (round(d, 3) if d is not None else "never"))

print("\n== 1b. the magazine is real ==")
g, bot, foe, shots = rig("hard")
bot["ammo"]["shotgun"] = 1
saw = []
H.run(g, 2.0, each=lambda _g: saw.append(bot["reloading"]) if bot["reloading"] else None)
H.check("bot starts a real reload when the mag runs dry", bool(saw))
H.check("it reloads the weapon it was firing", set(saw) == {"shotgun"}, set(saw))

g, bot, foe, shots = rig("hard")
bot["ammo"]["shotgun"] = 1
H.run(g, 1.0)                      # shotgun reload is 3.4s, so it cannot finish
H.check("bot cannot fire while reloading", len(shots) <= 1,
        "%d shots from a 1-round mag inside the reload" % len(shots))

g, bot, foe, shots = rig("hard")
bot["ammo"]["shotgun"] = 1
# One shell, then a 3.4s reload, then shells at the shotgun's own 0.83s -- plus
# the 0.35s swap out of the rifle it starts holding. Eight seconds so the
# assertion is about the reload completing, not about the shotgun's rate.
H.run(g, 8.0)
H.check("the reload completes and the bot fires again", len(shots) >= 3,
        "%d shots over 8s" % len(shots))
H.check("it fired again AFTER the reload, not just before it",
        len(shots) >= 2 and (shots[-1][0] - shots[0][0]) >= S.CONFIG["WEAPONS"]["shotgun"]["reload"],
        "first %.2f last %.2f" % (shots[0][0], shots[-1][0]) if shots else "no shots")
H.check("mag was refilled by the tick, not by the bot", bot["reloading"] is None)

print("\n== 1c. burst discipline ==")
g, bot, foe, shots = rig("easy", gap=20.0)          # 20m -> AR, mag 30
H.run(g, 4.0)
gaps = [round(shots[i+1][0] - shots[i][0], 3) for i in range(len(shots)-1)]
cfg = S.CONFIG["BOT"]["easy"]
runs, cur = [], 1
for gp in gaps:
    if gp >= cfg["burst_gap"] * 0.9:
        runs.append(cur); cur = 1
    else:
        cur += 1
runs.append(cur)
H.check("easy bot lets go of the trigger between bursts", len(runs) >= 2,
        "no pause found in gaps=%s" % gaps)
H.check("no run of fire exceeds cfg['burst'] (%d)" % cfg["burst"],
        max(runs) <= cfg["burst"], "runs=%s" % runs)

print("\n== 1d. regression: bots still fight ==")
# At rifle range. Ten metres is shotgun range -- 0.83s a shell, so two rounds
# in two seconds is the entire budget there and says nothing about volume.
g, bot, foe, shots = rig("hard", gap=20.0)
H.run(g, 2.0)
H.check("hard bot puts real volume downrange in 2s", len(shots) >= 5,
        "%d shots" % len(shots))
g, bot, foe, shots = rig("hard", gap=10.0)
H.run(g, 3.0)
H.check("and closes to the shotgun at knife range",
        shots and all(w == "shotgun" for _, w in shots),
        [w for _, w in shots])

sys.exit(H.report())
