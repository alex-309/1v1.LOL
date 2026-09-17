"""Headless harness for server.py. Drives Game directly -- no sockets.

The sim gates almost everything on time.time() -- fire rates, reloads, build
cooldowns, reaction times -- so tests that sleep in real time are both slow and
flaky. install_clock() swaps server.py's `time` module for a shim whose clock
only moves when a tick moves it, which makes a 6-second fight run instantly and
identically every time.
"""
import os, sys, time as _real_time, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S


class _Clock(object):
    """Stands in for the `time` module inside server.py."""
    def __init__(self, t0=1700000000.0):
        self.t = t0
    def time(self):
        return self.t
    def advance(self, dt):
        self.t += dt
    def sleep(self, n):
        self.t += n
    def __getattr__(self, name):
        return getattr(_real_time, name)


CLOCK = _Clock()
S.time = CLOCK          # server.py calls time.time() through its module global


def seed(n=1234):
    """Bot behaviour is stochastic; pin it so a run is reproducible."""
    random.seed(n)
    S.random.seed(n)


PASS, FAIL = [], []

def check(name, cond, detail=""):
    detail = "" if detail == "" else str(detail)
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  -- " + detail) if detail and not cond else ""))

def report():
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    return 1 if FAIL else 0

def new_game(mode="dm", live=True, arena="open"):
    g = S.Game()
    g.mode = mode
    S.load_arena(arena)
    if live:
        g.phase = "live"
    return g

def add(g, name="p", bot=False, diff="medium", style="balanced", alive=True):
    p = g.new_player(name, is_bot=bot, diff=diff, style=style)
    p["alive"] = alive
    return p

class FakeClient(object):
    """Stands in for a connected socket. broadcast() calls c.send() with no
    None check (send_to() does guard), so a test that wants messages captured
    needs a real object rather than None."""
    def __init__(self):
        self.sent = []
    def send(self, msg, droppable=False):
        self.sent.append(msg)


def attach(g, p):
    """Give a player a capturing client. Returns the list of messages."""
    c = FakeClient()
    g.clients[p["id"]] = c
    return c.sent


def sent(g, p):
    """Messages a player's fake client has received."""
    return g.clients[p["id"]].sent


def run(g, seconds, dt=S.TICK_DT, each=None):
    """Advance the sim by `seconds` of VIRTUAL time."""
    n = int(round(seconds / dt))
    for _ in range(n):
        CLOCK.advance(dt)
        g.step(dt)
        if each:
            each(g)
    return n
