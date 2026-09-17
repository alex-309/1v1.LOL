"""Increment 7: the match clock.

A first-to-N has no way to end when neither side will push, and on Vercel the
function's own ceiling was acting as the timer -- cutting the socket mid-fight
instead of finishing the match."""
import os, sys, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(ROOT, "index.html")
SRV_PATH = os.path.join(ROOT, "server.py")
S = H.S
C = S.CONFIG

def rig(mode, n=2, teams=False):
    H.seed()
    g = H.new_game(mode, live=False)
    g.mode = "lobby"
    ps = []
    for i in range(n):
        p = H.add(g, "p%d" % i, alive=False)
        H.attach(g, p)
        if teams:
            p["team_pick"] = i % 2
        ps.append(p)
    g.host = ps[0]["id"]
    g.start_match(mode)
    return g, ps

print("== modes that should have a clock, do ==")
for mode in ("duel", "dm", "team"):
    g, ps = rig(mode, n=4 if mode == "team" else 2, teams=(mode == "team"))
    left = g.clock_left()
    want = C["MATCH_TIME"][mode]
    H.check("%-5s starts a %.0fs clock" % (mode, want),
            left is not None and abs(left - (want + C["ROUND_COUNTDOWN"])) < 0.2,
            "clock_left=%s" % left)

print("\n== modes that should not, do not ==")
for mode in ("build", "aim", "trainer"):
    g, ps = rig(mode, n=1)
    H.check("%-7s has no clock" % mode, g.clock_left() is None, g.clock_left())

print("\n== it ticks down, and it ends the match ==")
g, ps = rig("dm")
a, b = ps
a["kills"] = 4; b["kills"] = 2
H.run(g, 10.0)
H.check("the clock is counting down",
        g.clock_left() < C["MATCH_TIME"]["dm"], g.clock_left())
H.check("the match is still live", g.phase == "live", g.phase)
H.run(g, C["MATCH_TIME"]["dm"])
H.check("time runs out and the match ends", g.phase == "ended", g.phase)
H.check("the clock reports nothing once spent", g.clock_left() is None)

print("\n== whoever is ahead takes it ==")
msgs = [m for m in H.sent(g, a) if m.get("t") == "matchend"]
H.check("a matchend was sent", len(msgs) == 1, len(msgs))
end = msgs[0]
H.check("the leader wins on time", end["winner"] == a["id"], end)
H.check("it is not called a draw", end["draw"] is False)
H.check("and it says it was the clock", end["timeup"] is True)

print("\n== level is a draw, not a coin toss ==")
g, ps = rig("dm")
a, b = ps
a["kills"] = 3; b["kills"] = 3
H.run(g, C["MATCH_TIME"]["dm"] + 5.0)
end = [m for m in H.sent(g, a) if m.get("t") == "matchend"][0]
H.check("a tie ends the match", g.phase == "ended")
H.check("with no winner", end["winner"] is None, end["winner"])
H.check("flagged as a draw", end["draw"] is True)
H.check("and named so", end["name"] == "Draw", end["name"])

print("\n== a team match resolves on rounds won ==")
g, ps = rig("team", n=4, teams=True)
g.team_score = [3, 1]
H.run(g, C["MATCH_TIME"]["team"] + 5.0)
end = [m for m in H.sent(g, ps[0]) if m.get("t") == "matchend"][0]
H.check("the leading side wins", end["team"] == 0, end)
H.check("not a draw", end["draw"] is False)
g, ps = rig("team", n=4, teams=True)
g.team_score = [2, 2]
H.run(g, C["MATCH_TIME"]["team"] + 5.0)
end = [m for m in H.sent(g, ps[0]) if m.get("t") == "matchend"][0]
H.check("a level team match is a draw", end["draw"] is True and end["team"] is None, end)

print("\n== winning on score still beats the clock to it ==")
g, ps = rig("dm")
a, b = ps
H.run(g, 1.0)
a["kills"] = C["DM_TARGET"]
g.check_win()
H.check("the match ended on score", g.phase == "ended")
end = [m for m in H.sent(g, a) if m.get("t") == "matchend"][0]
H.check("and is not reported as a timeout", not end.get("timeup"), end)
before = g.phase
H.run(g, C["MATCH_TIME"]["dm"] + 5.0)
ends = [m for m in H.sent(g, a) if m.get("t") == "matchend"]
H.check("the clock does not end it a second time", len(ends) == 1, len(ends))

print("\n== leaving to the lobby stops the clock ==")
g, ps = rig("dm")
g.to_lobby()
H.check("no clock in the lobby", g.clock_left() is None)

print("\n== the client is told ==")
HTML = open(HTML_PATH, encoding="utf-8").read()
SRV = open(SRV_PATH, encoding="utf-8").read()
H.check("build stamps match",
        re.search(r"const BUILD_ID = '([^']+)'", HTML).group(1) ==
        re.search(r'BUILD_ID = "([^"]+)"', SRV).group(1))
H.check("welcome carries the clock (so a rejoin sees it)", '"clock": self.clock_left()' in SRV)
H.check("client sets it from welcome and from mode", HTML.count("setClock(m.clock") == 2)
H.check("client clears it when the match ends", "setClock(null)" in HTML)
H.check("the end card understands a draw", "const draw = !!m.draw;" in HTML)
H.check("and says when the clock decided it", "m.timeup ?" in HTML)

sys.exit(H.report())
