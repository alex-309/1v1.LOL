#!/usr/bin/env python3
"""
Build-Fighter -- a 1v1.LOL-style build shooter.

Pure Python 3 standard library: no pip installs, no venv, no Node.
Serves the game page AND the realtime game socket on a single port.

    python3 server.py [--port 8080]

Then open the URL it prints. Your friend opens the LAN URL from a device on the
same Wi-Fi / hotspot.
"""

import argparse
import base64
import errno
import hashlib
import json
import math
import mimetypes
import os
import random
import signal
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Bumped on every change to the rules the client mirrors. The client carries the
# same stamp and shouts if the two disagree -- editing index.html and forgetting
# to restart server.py leaves the old rules in charge, and the symptom (pieces
# floating that the ghost said were illegal) looks exactly like a code bug.
BUILD_ID = "BUILD 2026-09-05 build-at-your-feet"
TICK_HZ = 30.0
TICK_DT = 1.0 / TICK_HZ

# ---------------------------------------------------------------------------
# CONFIG -- the single source of truth for tuning.
#
# This dict is shipped to every client in `welcome`. The JS player controller
# and the Python bot controller both read these numbers, so the two can never
# drift apart. Retuning the game means editing this dict and nothing else.
# ---------------------------------------------------------------------------
CONFIG = {
    # --- grid / building ---
    "CELL": 4.0,
    "WALL_T": 0.25,          # wall + floor slab thickness
    "FLOOR_HOLE_PAD": 1.0,   # how far a cut floor quadrant eats into the slabs
                             # beside it. Climbing a ramp through a hole means
                             # STEPPING UP under it, and a step needs the whole
                             # P_W-wide box clear of the slab -- not just the
                             # leading edge. Pad 0.8 puts the hole exactly on
                             # the trailing edge and the step fails by a
                             # rounding epsilon, so: player width plus slack.
    "PIECE_HP": 150.0,
    "PIECE_COST": 10,
    "MAT_CAP": 999,
    "MAT_START": 500,
    "MAT_REGEN": 12.0,       # per second
    "MAT_REGEN_DELAY": 3.0,  # seconds after your last placement
    "REJOIN_GRACE": 45.0,    # seconds a dropped player's slot is held open
    "LAG_COMP_MS": 260,      # furthest a shot may rewind other players. Covers
                             # the client's 100ms interpolation delay plus a
                             # round trip on a bad connection, and no more --
                             # an unbounded rewind is an invitation to ask to
                             # shoot at where someone stood a minute ago.
    "MAT_PER_SWING": 16,     # pickaxe into the arena -- farming, not regen.
                             # Deliberately more than a piece costs, so going
                             # and hitting a crate is a real opening move and
                             # not a worse version of standing still.
    "PICKAXE_REFUND": 5,
    # Build reach is measured in CELLS, not metres. You reach the cell you are
    # standing in plus two more in every direction, trimmed to a circle -- so
    # (2,0) and (1,1) are in range but (2,1) and (2,2) are not. See
    # cell_in_reach(): the same rule runs on both sides of the wire.
    "BUILD_RADIUS": 2,       # cells, horizontally, from the cell you stand in
    "BUILD_RADIUS_Y": 1,     # storeys up/down
    "BUILD_REACH": 8.0,      # == BUILD_RADIUS * CELL, in metres. Only the client
                             # still uses it, to decide how far away a piece can be
                             # and still show its health / be editable.
    "EDIT_REACH": 8.0,
    # Building used to run at 9 pieces a second, which is faster than anyone
    # can aim: a held mouse button emptied 90 materials a second into cells you
    # never meant to fill. The cooldown is enforced on BOTH sides -- the client
    # throttles for feel, the server for truth.
    "TURBO_RATE": 3.2,       # pieces per second while holding LMB
    "BUILD_COOLDOWN": 0.3125,   # == 1 / TURBO_RATE
    "CELL_Y_MIN": 0,
    "CELL_Y_MAX": 30,
    "CELL_XZ_MAX": 40,
    "PIECE_LIMIT": 2000,
    "ROOF_H": 2.0,           # cone height -- deliberately shorter than a full cell
    "RAMP_T": 0.6,           # ramp is a slanted SLAB, not a solid wedge
    "BUILD_IN_TIME": 0.2,    # scale/fade tween

    # --- character ---
    "P_W": 0.8,              # player AABB width/depth
    "P_H": 1.8,              # standing height
    "P_H_CROUCH": 1.25,
    "EYE": 1.62,             # eye height above feet when standing
    "EYE_CROUCH": 1.10,
    "HEAD_H": 0.45,          # head hitbox cube edge, sits on top of the body box
    "GRAVITY": 30.0,
    "MAX_FALL": 24.0,        # terminal velocity -- see "vertical tunneling" note
    "SPEED": 7.0,
    "SPEED_CROUCH": 3.5,
    "SPEED_ADS": 4.2,
    "JUMP": 9.5,
    "AIR_CONTROL": 0.35,
    "STEP_UP": 0.6,
    "ACCEL": 60.0,
    "FRICTION": 12.0,
    "SUBSTEP": 1.0 / 120.0,
    "DT_CLAMP": 0.1,         # never advance more than 100ms of physics in a frame
    "KILL_Y": -20.0,

    # --- combat ---
    "HP_MAX": 100.0,
    "SHIELD_MAX": 100.0,
    "SPAWN_PROTECT": 2.0,
    "RESPAWN_TIME": 3.0,
    "ROUND_COUNTDOWN": 3.0,
    "DUEL_TARGET": 5,
    "DM_TARGET": 15,
    "TEAM_TARGET": 5,        # rounds to win a 2v2


    # --- camera ---
    "FOV": 75.0,
    "FOV_ADS": 55.0,
    "FOV_SCOPE": 22.0,
    "CAM_OFF": [0.55, 1.55, -3.2],
    "CAM_OFF_ADS": [0.35, 1.62, -1.1],

    "WEAPONS": {
        "pickaxe": {
            "slot": 1, "name": "Pickaxe", "dmg": 20.0, "build_dmg": 20.0,
            "range": 3.0, "rate": 0.7, "mag": 0, "reload": 0.0,
            "pellets": 1, "spread": 0.0, "move_spread": 0.0, "recoil": 0.0,
            "head_mult": 1.0, "ads": False, "melee": True,
        },
        "ar": {
            "slot": 2, "name": "Assault Rifle", "dmg": 22.0, "build_dmg": 22.0,
            "range": 220.0, "rate": 0.125, "mag": 30, "reload": 2.2,
            "pellets": 1, "spread": 0.012, "move_spread": 0.030, "recoil": 0.9,
            "head_mult": 1.5, "ads": True, "melee": False,
        },
        "shotgun": {
            "slot": 3, "name": "Shotgun", "dmg": 9.0, "build_dmg": 11.0,
            "range": 60.0, "rate": 0.83, "mag": 5, "reload": 3.4,
            "pellets": 9, "spread": 0.075, "move_spread": 0.020, "recoil": 2.6,
            "head_mult": 1.5, "ads": True, "melee": False,
        },
        "sniper": {
            "slot": 4, "name": "Sniper", "dmg": 100.0, "build_dmg": 120.0,
            "range": 400.0, "rate": 1.4, "mag": 1, "reload": 2.4,
            "pellets": 1, "spread": 0.0, "move_spread": 0.055, "recoil": 3.4,
            "head_mult": 2.5, "ads": True, "melee": False, "scope": True,
        },
        "grenade": {
            "slot": 5, "name": "Grenade", "dmg": 80.0, "build_dmg": 160.0,
            "range": 0.0, "rate": 1.2, "mag": 0, "reload": 0.0,
            "pellets": 1, "spread": 0.0, "move_spread": 0.0, "recoil": 0.0,
            "head_mult": 1.0, "ads": False, "melee": False,
            "fuse": 3.0, "radius": 6.0, "throw_speed": 22.0,
        },
    },
    # The master list. Order here is the wire order -- a player's held weapon
    # travels as an index into THIS list, so never reorder it per mode.
    "LOADOUT": ["pickaxe", "ar", "shotgun", "sniper", "grenade"],
    # What each mode actually hands you. A 1v1 duel is a gunfight: three guns,
    # no pickaxe and no grenade, so nobody wins a duel by chipping a wall down
    # with a melee swing or by tossing an explosive into a box fight. Any mode
    # missing from here gets the full LOADOUT.
    "MODE_LOADOUT": {
        "duel": ["ar", "shotgun", "sniper"],
    },
    "BUILD_PIECES": ["wall", "ramp", "floor", "roof"],

    "GRENADE_GRAVITY": 26.0,
    "GRENADE_BOUNCE": 0.35,

    "BOT": {
        "easy":   {"react": 0.40, "err": 0.075, "turn": 3.0, "build_cd": 2.2, "push": 0.25, "burst": 4},
        "medium": {"react": 0.22, "err": 0.035, "turn": 5.5, "build_cd": 1.1, "push": 0.55, "burst": 7},
        "hard":   {"react": 0.11, "err": 0.014, "turn": 9.0, "build_cd": 0.55, "push": 0.85, "burst": 12},
    },
    # A difficulty dial only makes the same bot faster. A personality changes
    # what it is TRYING to do, which is the part you actually practise against:
    # a turtle teaches you to break builds, a rusher teaches you to hold an
    # angle, a builder teaches you to fight someone above you. Multipliers on
    # the difficulty numbers, so the two axes stay independent.
    "BOT_STYLE": {
        "balanced": {"label": "Balanced", "push": 1.0, "build": 1.0, "range": 1.0,
                     "ramp": 0.35, "wall_first": 0.5, "retreat": 0.35},
        "rusher":   {"label": "Rusher", "push": 2.2, "build": 0.55, "range": 0.5,
                     "ramp": 0.1, "wall_first": 0.15, "retreat": 0.05},
        "turtle":   {"label": "Turtle", "push": 0.15, "build": 1.9, "range": 1.6,
                     "ramp": 0.2, "wall_first": 0.95, "retreat": 0.85},
        "builder":  {"label": "Builder", "push": 0.7, "build": 2.4, "range": 1.1,
                     "ramp": 0.85, "wall_first": 0.4, "retreat": 0.5},
    },

    "AIM_TRAINER": {"lifetime": 2.6, "gap": 0.35, "count": 3, "radius": 0.55},
    # BUILD TRAINER -- courses of checkpoints you can only reach by building.
    # Scored the way the aim trainer is scored: a clock, a best time, and
    # nothing else. Each course is a list of [x, y, z] gates, and the heights
    # are chosen so no gate is reachable by jumping -- the only way up is a
    # ramp, and the only way across a gap is a floor.
    "BUILD_TRAINER": {
        "radius": 2.2,           # how close counts as through the gate
        "courses": {
            "ramp": {"label": "Ramp Rush",
                     "desc": "Straight up. Ramps only, as fast as you can.",
                     "gates": [[0, 4, -6], [0, 8, -12], [0, 12, -18],
                               [0, 16, -24], [0, 20, -30]]},
            "tower": {"label": "Tower",
                      "desc": "Box up and climb. Turns at every level.",
                      "gates": [[0, 4, -5], [6, 8, -5], [6, 12, 1],
                                [0, 16, 1], [0, 20, -5]]},
            "bridge": {"label": "Bridge",
                       "desc": "Across, not up. Floors over open ground.",
                       "gates": [[0, 4, -8], [10, 4, -16], [-2, 8, -24],
                                 [-14, 8, -16], [-14, 12, -4]]},
        },
    },
    "DUMMY_HP": 100.0,
    "DUMMY_SHIELD": 100.0,
    "DUMMY_RESPAWN": 4.0,
}

# ---------------------------------------------------------------------------
# ARENA -- defined here, shipped to clients in `welcome`.
#
# The server needs real arena collision (the bot walks on it and takes cover
# behind it), and the client needs meshes. Defining it twice guarantees drift,
# so it lives here once as data. Each box is [cx, cy, cz, sx, sy, sz, tag]
# where the position is the CENTER and s* are full extents.
# ---------------------------------------------------------------------------
OPEN_ARENA = [
    [0, -1.0, 0, 80, 2.0, 80, "ground"],
    # border lip
    [0, 0.4, -40.5, 81, 1.0, 1.0, "lip"],
    [0, 0.4, 40.5, 81, 1.0, 1.0, "lip"],
    [-40.5, 0.4, 0, 1.0, 1.0, 81, "lip"],
    [40.5, 0.4, 0, 1.0, 1.0, 81, "lip"],
    # cover
    [-14, 1.5, -14, 6, 3.0, 6, "crate"],
    [14, 1.5, 14, 6, 3.0, 6, "crate"],
    [14, 1.0, -14, 8, 2.0, 4, "crate"],
    [-14, 1.0, 14, 8, 2.0, 4, "crate"],
    [0, 2.0, 0, 10, 4.0, 2.0, "pillar"],
    [-24, 1.0, 6, 4, 2.0, 10, "crate"],
    [24, 1.0, -6, 4, 2.0, 10, "crate"],
]

OPEN_SPAWNS = [
    [-32.0, 0.5, -32.0, 45.0],
    [32.0, 0.5, 32.0, 225.0],
    [-32.0, 0.5, 32.0, 135.0],
    [32.0, 0.5, -32.0, 315.0],
    [0.0, 0.5, -34.0, 0.0],
    [0.0, 0.5, 34.0, 180.0],
]

# ---------------------------------------------------------------------------
# BOX FIGHT -- the 1v1 arena.
#
# The open arena is a deathmatch map: 80 wide, cover scattered around, and two
# duellists spend the first twenty seconds walking toward each other. A duel
# wants the opposite -- close enough that the fight starts immediately, and
# WALLED, so the only way out of a bad position is up. Everything here follows
# from that:
#
#   48 wide, on the 4-unit build grid, so builds line up with the floor
#   perimeter walls 12 high -- higher than anyone can ramp in one go, so the
#     fight stays inside the box instead of leaking onto the skybox
#   a raised centre platform, which is the thing worth taking: high ground you
#     have to either ramp onto or wall someone off of
#   two low side ledges for the opening trade, and nothing else. Empty floor is
#     the point -- the cover in a build fight is the cover you build.
# ---------------------------------------------------------------------------
BOX_ARENA = [
    [0, -1.0, 0, 48, 2.0, 48, "ground"],
    # perimeter -- 12 high, 2 thick, sitting just inside the ground edge
    [0, 6.0, -24.0, 48, 12.0, 2.0, "lip"],
    [0, 6.0, 24.0, 48, 12.0, 2.0, "lip"],
    [-24.0, 6.0, 0, 2.0, 12.0, 48, "lip"],
    [24.0, 6.0, 0, 2.0, 12.0, 48, "lip"],
    # centre high ground
    [0, 2.0, 0, 12, 4.0, 12, "pillar"],
    # the two ledges either side of it
    [-12, 1.0, 0, 4, 2.0, 12, "crate"],
    [12, 1.0, 0, 4, 2.0, 12, "crate"],
    # something to break line of sight off spawn, so the first shot is not free
    [-7, 1.5, -15, 6, 3.0, 3, "crate"],
    [7, 1.5, 15, 6, 3.0, 3, "crate"],
]

BOX_SPAWNS = [
    [0.0, 0.5, -20.0, 0.0],
    [0.0, 0.5, 20.0, 180.0],
    [-20.0, 0.5, 0.0, 90.0],
    [20.0, 0.5, 0.0, 270.0],
]

# ---------------------------------------------------------------------------
# TOWERS -- the vertical map.
#
# The box is a ground fight with a roof on it; this is the opposite. Four
# stepped towers at the corners and a tall spine down the middle mean the
# useful ground is all above you, and the only way onto it is to build. It is
# wider than the box because a 2v2 needs room for two fights at once.
# ---------------------------------------------------------------------------
TOWER_ARENA = [
    [0, -1.0, 0, 64, 2.0, 64, "ground"],
    # perimeter, lower than the box: getting out is not the problem here
    [0, 3.0, -32.0, 64, 6.0, 2.0, "lip"],
    [0, 3.0, 32.0, 64, 6.0, 2.0, "lip"],
    [-32.0, 3.0, 0, 2.0, 6.0, 64, "lip"],
    [32.0, 3.0, 0, 2.0, 6.0, 64, "lip"],
    # centre spine -- tall and thin, so it splits the map without being cover
    [0, 5.0, 0, 4, 10.0, 20, "pillar"],
    # four stepped corner towers: each is two blocks you can ramp between
    [-18, 2.0, -18, 10, 4.0, 10, "crate"],
    [-18, 4.0, -18, 5, 8.0, 5, "pillar"],
    [18, 2.0, 18, 10, 4.0, 10, "crate"],
    [18, 4.0, 18, 5, 8.0, 5, "pillar"],
    [18, 2.0, -18, 10, 4.0, 10, "crate"],
    [18, 4.0, -18, 5, 8.0, 5, "pillar"],
    [-18, 2.0, 18, 10, 4.0, 10, "crate"],
    [-18, 4.0, 18, 5, 8.0, 5, "pillar"],
]

TOWER_SPAWNS = [
    [0.0, 0.5, -27.0, 0.0],
    [0.0, 0.5, 27.0, 180.0],
    [-27.0, 0.5, 0.0, 90.0],
    [27.0, 0.5, 0.0, 270.0],
    [-24.0, 0.5, -24.0, 45.0],
    [24.0, 0.5, 24.0, 225.0],
]

# Which layout each mode plays on by default. The host can override it from the
# lobby; "auto" means "whatever this mode wants".
ARENAS = {
    "open": (OPEN_ARENA, OPEN_SPAWNS),
    "box": (BOX_ARENA, BOX_SPAWNS),
    "towers": (TOWER_ARENA, TOWER_SPAWNS),
}
ARENA_NAMES = {"open": "Open Yard", "box": "Box Fight", "towers": "Towers"}
MODE_ARENA = {"duel": "box", "team": "towers", "trainer": "open"}

# The layout currently loaded. Rebound by load_arena() whenever a match starts,
# which is also what rebuilds ARENA_BOXES -- every collision path reads that
# list, so nothing else has to know the map changed.
ARENA_NAME = "open"
ARENA, SPAWNS = ARENAS["open"]

DUMMIES = [
    [-8.0, 0.0, -20.0],
    [0.0, 0.0, -24.0],
    [8.0, 0.0, -20.0],
]


# ---------------------------------------------------------------------------
# Deterministic PRNG -- mulberry32, bit-identical to the JS implementation.
#
# The client rolls the pellet pattern to draw tracers; the server rolls it
# again to validate hits. Same seed must produce the same spread on both sides
# or every shotgun blast disagrees.
# ---------------------------------------------------------------------------
def _imul(x, y):
    return (x * y) & 0xFFFFFFFF


def mulberry32(seed):
    a = seed & 0xFFFFFFFF

    def rnd():
        nonlocal a
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        t = _imul(a ^ (a >> 15), 1 | a)
        t = ((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rnd


# ---------------------------------------------------------------------------
# Vector / AABB math
# ---------------------------------------------------------------------------
def v_add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def v_sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def v_scale(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def v_len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def v_norm(a):
    l = v_len(a)
    if l < 1e-9:
        return [0.0, 0.0, 1.0]
    return [a[0] / l, a[1] / l, a[2] / l]


def v_dist(a, b):
    return v_len(v_sub(a, b))


class Box(object):
    """Axis-aligned box stored as min/max corners, tagged with what it belongs to."""
    __slots__ = ("lo", "hi", "kind", "ref")

    def __init__(self, lo, hi, kind, ref=None):
        self.lo = lo
        self.hi = hi
        self.kind = kind   # 'arena' | 'piece' | 'body' | 'head' | 'target'
        self.ref = ref     # piece key, player id, or target id

    def overlaps(self, lo, hi):
        return (lo[0] < self.hi[0] and hi[0] > self.lo[0] and
                lo[1] < self.hi[1] and hi[1] > self.lo[1] and
                lo[2] < self.hi[2] and hi[2] > self.lo[2])


def box_from_center(c, s, kind, ref=None):
    return Box([c[0] - s[0] / 2.0, c[1] - s[1] / 2.0, c[2] - s[2] / 2.0],
               [c[0] + s[0] / 2.0, c[1] + s[1] / 2.0, c[2] + s[2] / 2.0],
               kind, ref)


ARENA_BOXES = [box_from_center([b[0], b[1], b[2]], [b[3], b[4], b[5]], "arena", b[6])
               for b in ARENA]


def load_arena(name):
    """Swap the live layout. Rebuilds ARENA_BOXES in place, because every
    collision path in the server holds a reference to that one list."""
    global ARENA, SPAWNS, ARENA_NAME
    if name not in ARENAS or name == ARENA_NAME:
        return False
    ARENA_NAME = name
    ARENA, SPAWNS = ARENAS[name]
    ARENA_BOXES[:] = [box_from_center([b[0], b[1], b[2]], [b[3], b[4], b[5]],
                                      "arena", b[6]) for b in ARENA]
    return True


def ray_box(origin, d, box, tmax):
    """Slab test. Returns entry distance or None.

    Everything in this world is an AABB -- ramps and roofs are stepped boxes --
    so this one routine covers all geometry, on both the client and the server.
    """
    t0 = 0.0
    t1 = tmax
    for i in range(3):
        di = d[i]
        if abs(di) < 1e-9:
            if origin[i] < box.lo[i] or origin[i] > box.hi[i]:
                return None
            continue
        inv = 1.0 / di
        ta = (box.lo[i] - origin[i]) * inv
        tb = (box.hi[i] - origin[i]) * inv
        if ta > tb:
            ta, tb = tb, ta
        if ta > t0:
            t0 = ta
        if tb < t1:
            t1 = tb
        if t0 > t1:
            return None
    return t0


def raycast(origin, d, boxes, tmax, skip_ref=None):
    """Nearest hit among `boxes`. Returns (dist, box) or (None, None)."""
    best_t = None
    best_b = None
    for b in boxes:
        if skip_ref is not None and b.ref == skip_ref and b.kind in ("body", "head"):
            continue
        t = ray_box(origin, d, b, tmax)
        if t is not None and (best_t is None or t < best_t):
            best_t = t
            best_b = b
    return best_t, best_b


# ---------------------------------------------------------------------------
# Build grid
#
# CANONICAL KEYS. A wall between cell A and cell B is one physical wall with
# two valid addresses; storing it under both would let two players place the
# same wall and would make occupancy checks miss. So:
#
#   wallX at (cx,cy,cz) := the slab on the -X face of that cell (plane x=cx*CELL)
#   wallZ at (cx,cy,cz) := the slab on the -Z face of that cell (plane z=cz*CELL)
#   floor at (cx,cy,cz) := the slab this cell sits ON (plane y=cy*CELL)
#   ramp/roof at (cx,cy,cz) := fills the cell volume
#
# Facing becomes a render property, not part of identity.
# ---------------------------------------------------------------------------
CELL = CONFIG["CELL"]
WALL_T = CONFIG["WALL_T"]

# Cardinal steps, indexed by facing: 0=-Z 1=+X 2=+Z 3=-X.
CARD = ((0, -1), (1, 0), (0, 1), (-1, 0))

# ---------------------------------------------------------------------------
# Edit masks
#
# A piece carries a bitmask of the tiles it still has. The grid is NOT the same
# shape for every piece, because the edits are not:
#
#   wallX / wallZ  3x3, bit r*3+c, r=0 is the TOP row.   -> windows and doors
#   floor / roof   2x2, bit r*2+c, r spans +Z, c spans +X. A cleared quadrant
#                  is a hole you can drop through.
#   ramp           2x2, bit r*2+c, r=0 is the HIGH half of the slope, c is
#                  across it. A cleared quadrant does NOT vanish -- it POPS UP
#                  to a flat platform at the height the ramp reached there,
#                  which is what a ramp edit does in the game this borrows from.
#
# FULL_MASK stays 3x3 for the wire default; full_mask() is what code should ask.
# ---------------------------------------------------------------------------
FULL_MASK = 0b111111111
MASK_GRID = {"wallX": (3, 3), "wallZ": (3, 3),
             "floor": (2, 2), "roof": (2, 2), "ramp": (2, 2)}


def mask_grid(ptype):
    return MASK_GRID.get(ptype, (3, 3))


def full_mask(ptype):
    rows, cols = mask_grid(ptype)
    return (1 << (rows * cols)) - 1


def mask_is_rect(bits, rows, cols):
    """True when the set bits form one solid axis-aligned rectangle.

    This is the whole "no random edits" rule. Every edit the original game
    actually has -- window, door, doorway, half wall, side opening, floor
    quadrant, half floor, half ramp -- is a rectangle of tiles. Scattered
    diagonal picks are not, and are refused rather than silently accepted."""
    idx = [i for i in range(rows * cols) if (bits >> i) & 1]
    if not idx:
        return False
    rs = [i // cols for i in idx]
    cs = [i % cols for i in idx]
    r0, r1, c0, c1 = min(rs), max(rs), min(cs), max(cs)
    return len(idx) == (r1 - r0 + 1) * (c1 - c0 + 1)


# --- corner cuts -------------------------------------------------------------
# The other shape a wall edit can make, and the one the games this borrows from
# lean on hardest. You do NOT have to paint the whole 2x2 corner block: three
# tiles -- the corner tile, the one beside it and the one above/below it -- are
# enough, exactly as in 1v1.lol and Fortnite.
#
# What comes out is not those three tiles missing. The wall is cut along the
# diagonal between the two corners you did NOT touch: half of it disappears and
# what is left is a TRIANGLE. That is why three tiles is the right gesture --
# the fourth tile of the corner block is already half inside the triangle that
# survives, so asking for it would be asking for a shape the edit cannot make.
#
# Keyed by the bits being CUT -> the corner being opened, as (u, v) with u
# across the wall (u = c / 2) and v up it (v = (2 - r) / 2).
WALL_CORNER_CUTS = {
    0b000001011: (0, 1),    # tiles (0,0) (0,1) (1,0)  -> top-left
    0b000100110: (1, 1),    # tiles (0,1) (0,2) (1,2)  -> top-right
    0b011001000: (0, 0),    # tiles (1,0) (2,0) (2,1)  -> bottom-left
    0b110100000: (1, 0),    # tiles (1,2) (2,1) (2,2)  -> bottom-right
}


def wall_corner_cut(ptype, mask):
    """The corner a wall mask opens, or None. `mask` is what REMAINS."""
    if ptype not in ("wallX", "wallZ"):
        return None
    return WALL_CORNER_CUTS.get(FULL_MASK & ~mask)


def corner_keep(corner, u):
    """Vertical span (v_lo, v_hi) of the surviving triangle at across-wall `u`.

    The diagonal runs between the two corners the cut did not touch, so cutting
    a bottom corner leaves the top half and vice versa."""
    uc, vc = corner
    edge = (1.0 - u) if uc == vc else u
    return (edge, 1.0) if vc == 0 else (0.0, edge)


def edit_mask_ok(ptype, mask):
    """Validate a requested mask for `ptype`. `mask` is what REMAINS."""
    if ptype not in MASK_GRID:
        return False
    rows, cols = mask_grid(ptype)
    full = full_mask(ptype)
    if mask & ~full:
        return False
    if mask == full:
        return True                       # reset to unedited
    if mask == 0:
        return False                      # editing is not a way to delete
    if wall_corner_cut(ptype, mask):
        return True
    return mask_is_rect(full & ~mask, rows, cols)


def sub_rect(a, b):
    """`a` minus `b`, as up to four rectangles. Each is (x0, x1, z0, z1).

    Used to bite a padded hole out of the floor slabs beside it, which is the
    one place a surviving tile stops being a whole tile."""
    ax0, ax1, az0, az1 = a
    bx0, bx1, bz0, bz1 = b
    if bx1 <= ax0 or bx0 >= ax1 or bz1 <= az0 or bz0 >= az1:
        return [a]                        # no overlap, `a` survives whole
    out = []
    if bx0 > ax0:
        out.append((ax0, bx0, az0, az1))              # strip left of the hole
    if bx1 < ax1:
        out.append((bx1, ax1, az0, az1))              # strip right of it
    mx0, mx1 = max(ax0, bx0), min(ax1, bx1)
    if bz0 > az0:
        out.append((mx0, mx1, az0, bz0))              # strip below it
    if bz1 < az1:
        out.append((mx0, mx1, bz1, az1))              # strip above it
    return [r for r in out if r[1] - r[0] > 1e-6 and r[3] - r[2] > 1e-6]


def floor_hole(mask, x0, z0):
    """The padded opening a floor mask cuts, as (x0, x1, z0, z1), or None.

    The cut quadrants are a rectangle (edit_mask_ok() saw to that), so the
    opening is one rectangle too -- grown by FLOOR_HOLE_PAD into the slabs
    beside it, but never past the edge of the piece."""
    gone = [i for i in range(4) if not (mask >> i) & 1]
    if not gone:
        return None
    half = CELL / 2.0
    pad = CONFIG["FLOOR_HOLE_PAD"]
    rs = [i // 2 for i in gone]
    cs = [i % 2 for i in gone]
    hx0 = max(x0, x0 + min(cs) * half - pad)
    hx1 = min(x0 + CELL, x0 + (max(cs) + 1) * half + pad)
    hz0 = max(z0, z0 + min(rs) * half - pad)
    hz1 = min(z0 + CELL, z0 + (max(rs) + 1) * half + pad)
    return (hx0, hx1, hz0, hz1)


# Modes that run in rounds with two sides rather than free-for-all.
TEAM_MODES = ("team",)
ROUND_MODES = ("duel", "team")


def is_team(mode):
    return mode in TEAM_MODES


def target_for(mode):
    if mode == "duel":
        return CONFIG["DUEL_TARGET"]
    if mode == "team":
        return CONFIG["TEAM_TARGET"]
    return CONFIG["DM_TARGET"]


def loadout_for(mode):
    """The weapons a mode hands out, in hotbar order."""
    return CONFIG["MODE_LOADOUT"].get(mode) or CONFIG["LOADOUT"]


def canon_wall(cx, cy, cz, direction):
    """Map (cell, facing) onto the canonical edge key. dir: 0=-Z 1=+X 2=+Z 3=-X."""
    if direction == 0:
        return ("wallZ", cx, cy, cz)
    if direction == 1:
        return ("wallX", cx + 1, cy, cz)
    if direction == 2:
        return ("wallZ", cx, cy, cz + 1)
    return ("wallX", cx, cy, cz)


def piece_key(ptype, cx, cy, cz):
    return "%s:%d,%d,%d" % (ptype, cx, cy, cz)


def parse_key(key):
    ptype, coords = key.split(":")
    cx, cy, cz = [int(v) for v in coords.split(",")]
    return ptype, cx, cy, cz


def _ramp_span(direction, x0, z0, s_lo, s_hi, u_lo, u_hi):
    """Ramp-local (along-slope, across-slope) rectangle -> world x/z rectangle.

    `s` runs 0 at the low end to 1 at the high end; `u` runs across the slope.
    Everything about a ramp -- treads, quadrants, popped platforms -- is easier
    to reason about in those two numbers than in four rotated cases."""
    sl, sh = s_lo * CELL, s_hi * CELL
    ul, uh = u_lo * CELL, u_hi * CELL
    if direction == 0:          # rises toward -Z
        return (x0 + ul, x0 + uh, z0 + CELL - sh, z0 + CELL - sl)
    if direction == 2:          # rises toward +Z
        return (x0 + ul, x0 + uh, z0 + sl, z0 + sh)
    if direction == 1:          # rises toward +X
        return (x0 + sl, x0 + sh, z0 + ul, z0 + uh)
    return (x0 + CELL - sh, x0 + CELL - sl, z0 + ul, z0 + uh)   # toward -X


def tile_boxes(ptype, cx, cy, cz, direction, mask):
    """Collision boxes for a piece, honouring its edit mask.

    Mirrored tile-for-tile by tileBoxes() in index.html. The two must agree or
    the ghost promises placements the server then throws away."""
    x0, y0, z0 = cx * CELL, cy * CELL, cz * CELL
    out = []

    if ptype in ("wallX", "wallZ"):
        corner = wall_corner_cut(ptype, mask)
        if corner:
            # A corner cut is a diagonal, and a diagonal has no tiles. It
            # renders as a real triangle and collides as a staircase of thin
            # columns sampled down the middle of each one, so the two never
            # drift by more than half a column -- the same render-smooth /
            # collide-boxy split a ramp already uses.
            cols_n = 8
            step = CELL / cols_n
            for i in range(cols_n):
                v_lo, v_hi = corner_keep(corner, (i + 0.5) / cols_n)
                if v_hi - v_lo < 1e-6:
                    continue
                ylo, yhi = y0 + v_lo * CELL, y0 + v_hi * CELL
                ulo, uhi = i * step, (i + 1) * step
                if ptype == "wallX":
                    out.append(Box([x0 - WALL_T / 2, ylo, z0 + ulo],
                                   [x0 + WALL_T / 2, yhi, z0 + uhi], "piece"))
                else:
                    out.append(Box([x0 + ulo, ylo, z0 - WALL_T / 2],
                                   [x0 + uhi, yhi, z0 + WALL_T / 2], "piece"))
            return out
        third = CELL / 3.0
        for r in range(3):          # r=0 is the top row
            for c in range(3):
                if not (mask >> (r * 3 + c)) & 1:
                    continue
                ylo = y0 + (2 - r) * third
                yhi = ylo + third
                if ptype == "wallX":
                    out.append(Box([x0 - WALL_T / 2, ylo, z0 + c * third],
                                   [x0 + WALL_T / 2, yhi, z0 + (c + 1) * third],
                                   "piece"))
                else:
                    out.append(Box([x0 + c * third, ylo, z0 - WALL_T / 2],
                                   [x0 + (c + 1) * third, yhi, z0 + WALL_T / 2],
                                   "piece"))
        return out

    if ptype == "floor":
        half = CELL / 2.0
        hole = floor_hole(mask, x0, z0)
        for r in range(2):          # r spans Z, c spans X
            for c in range(2):
                if not (mask >> (r * 2 + c)) & 1:
                    continue
                quad = (x0 + c * half, x0 + (c + 1) * half,
                        z0 + r * half, z0 + (r + 1) * half)
                # the surviving quadrants pull back from the hole, so what you
                # climb through is FLOOR_HOLE_PAD wider than the quadrant you
                # cut. A slab trimmed on two sides is an L, hence sub_rect().
                for qx0, qx1, qz0, qz1 in (sub_rect(quad, hole) if hole
                                           else [quad]):
                    out.append(Box([qx0, y0 - WALL_T / 2, qz0],
                                   [qx1, y0 + WALL_T / 2, qz1], "piece"))
        return out

    if ptype == "ramp":
        # Renders as a smooth slanted slab, collides as 8 stair treads. With
        # step-up in the controller, walking a ramp falls out of plain AABB
        # resolution -- no slope normals, no sliding, no seam-sticking.
        #
        # Each tread is only RAMP_T thick instead of reaching all the way down
        # to the cell floor, so a ramp is a slanted wall you can walk under --
        # not a solid wedge that fills the whole cell.
        #
        # An edited quadrant is not removed: it becomes a flat platform at HALF
        # the cell height -- the landing the ramp surface is at when it crosses
        # the middle of the cell. That one rule produces every ramp shape the
        # game this borrows from has, and all of them stay walkable:
        #
        #   low half cut   -> flat landing, then the ramp climbs from mid to top
        #   high half cut  -> the ramp climbs to mid, then levels off
        #   one side cut   -> half-width ramp with a platform running beside it
        #
        # Popping to the quadrant's own top edge instead was the obvious first
        # try and it is wrong: cutting the high half then lands the platform a
        # clear 1.4 above the tread that feeds it, so you walk up the ramp and
        # into a wall.
        steps = 8
        rt = CONFIG["RAMP_T"]
        mid = y0 + CELL / 2.0
        for r in range(2):
            for c in range(2):
                present = (mask >> (r * 2 + c)) & 1
                s_lo, s_hi = (0.5, 1.0) if r == 0 else (0.0, 0.5)
                u_lo, u_hi = (0.0, 0.5) if c == 0 else (0.5, 1.0)
                if not present:
                    xa, xb, za, zb = _ramp_span(direction, x0, z0,
                                                s_lo, s_hi, u_lo, u_hi)
                    out.append(Box([xa, max(y0, mid - rt), za],
                                   [xb, mid, zb], "piece"))
                    continue
                lo_i = int(s_lo * steps)
                for i in range(lo_i, int(s_hi * steps)):
                    h = (i + 1) * (CELL / steps)
                    xa, xb, za, zb = _ramp_span(
                        direction, x0, z0, i / float(steps), (i + 1) / float(steps),
                        u_lo, u_hi)
                    out.append(Box([xa, max(y0, y0 + h - rt), za],
                                   [xb, y0 + h, zb], "piece"))
        return out

    if ptype == "roof":
        levels = 4
        rh = CONFIG["ROOF_H"]
        half = CELL / 2.0
        for r in range(2):          # r spans Z, c spans X -- same as a floor
            for c in range(2):
                if not (mask >> (r * 2 + c)) & 1:
                    continue
                qx0, qx1 = x0 + c * half, x0 + (c + 1) * half
                qz0, qz1 = z0 + r * half, z0 + (r + 1) * half
                for i in range(levels):
                    inset = i * (CELL / (2.0 * levels))
                    xa, xb = max(qx0, x0 + inset), min(qx1, x0 + CELL - inset)
                    za, zb = max(qz0, z0 + inset), min(qz1, z0 + CELL - inset)
                    if xb - xa < 1e-6 or zb - za < 1e-6:
                        continue
                    out.append(Box([xa, y0 + i * (rh / levels), za],
                                   [xb, y0 + (i + 1) * (rh / levels), zb], "piece"))
        return out

    return out


# ---------------------------------------------------------------------------
# Structural support
#
# Nothing may float. A piece is legal only if it rests on the arena or touches
# something that (transitively) does. When a piece is destroyed, anything left
# hanging is destroyed with it.
#
# Contact is judged on bounding boxes: two pieces are connected when they
# genuinely share a face -- overlapping on two axes and touching on the third.
# Corner-to-corner alone is not enough to hold weight.
# ---------------------------------------------------------------------------
SUPPORT_EPS = 0.06


def piece_bounds(pc):
    """Support is judged on the piece's FULL footprint, never its edit mask.
    Cutting a doorway through the bottom of a wall must not delete the wall --
    the piece still occupies its cell and still carries load."""
    b = pc.get("sbounds")
    if b is not None:
        return b
    bs = pc["boxes"]
    if not bs:
        return None
    return ([min(x.lo[i] for x in bs) for i in range(3)],
            [max(x.hi[i] for x in bs) for i in range(3)])


def faces_touch(a, b, eps=SUPPORT_EPS):
    if a is None or b is None:
        return False
    alo, ahi = a
    blo, bhi = b
    overlaps = 0
    for i in range(3):
        if alo[i] < bhi[i] - eps and ahi[i] > blo[i] + eps:
            overlaps += 1
        elif alo[i] - eps <= bhi[i] and ahi[i] + eps >= blo[i]:
            pass                      # just touching on this axis
        else:
            return False
    return overlaps >= 2


def rests_on_arena(bounds):
    if bounds is None:
        return False
    for ab in ARENA_BOXES:
        if faces_touch(bounds, (ab.lo, ab.hi)):
            return True
    return False


# ---------------------------------------------------------------------------
# Character controller -- shared by the bot here and mirrored in JS.
# ---------------------------------------------------------------------------
def player_aabb(pos, crouch):
    w = CONFIG["P_W"]
    h = CONFIG["P_H_CROUCH"] if crouch else CONFIG["P_H"]
    return ([pos[0] - w / 2, pos[1], pos[2] - w / 2],
            [pos[0] + w / 2, pos[1] + h, pos[2] + w / 2])


def _overlap_any(lo, hi, boxes):
    for b in boxes:
        if b.overlaps(lo, hi):
            return b
    return None


def move_axis(pos, crouch, delta, axis, boxes):
    """Move one axis and resolve penetration along that axis only."""
    if delta == 0.0:
        return False
    pos[axis] += delta
    w = CONFIG["P_W"]
    h = CONFIG["P_H_CROUCH"] if crouch else CONFIG["P_H"]
    hit = False
    for _ in range(4):
        lo, hi = player_aabb(pos, crouch)
        b = _overlap_any(lo, hi, boxes)
        if b is None:
            break
        hit = True
        if axis == 1:
            pos[1] = (b.hi[1] + 1e-4) if delta < 0 else (b.lo[1] - h - 1e-4)
        else:
            half = w / 2
            pos[axis] = (b.lo[axis] - half - 1e-4) if delta > 0 else (b.hi[axis] + half + 1e-4)
    return hit


def _step_rise(pos, crouch, boxes, max_rise):
    """Smallest lift that frees the player box at `pos`, or None inside max_rise.

    Stepping by the whole of STEP_UP regardless of how tall the step actually
    is costs headroom nobody asked to spend: a 0.5 ramp tread under a ceiling
    2.4 up would be probed at 0.6, the probe would clip the ceiling, and the
    player would stop dead on a step they fit through. Ask the obstacle how
    high it is instead."""
    lift = 0.0
    for _ in range(4):
        lo, hi = player_aabb([pos[0], pos[1] + lift, pos[2]], crouch)
        b = _overlap_any(lo, hi, boxes)
        if b is None:
            return lift
        need = b.hi[1] + 1e-3 - pos[1]
        if need <= lift or need > max_rise:
            return None
        lift = need
    return None


def unstick(pos, crouch, boxes):
    """Last line of defence against ending up inside the world.

    move_axis() resolves along the axis it was asked to move on, which is right
    while you are moving but useless when you START already embedded -- a piece
    appearing around you, a shove, a rounding error on a seam. Left to itself it
    picks the shallowest way out, and that is often downwards, which is how you
    fall through the map. Up is always the safe direction: worst case you end up
    standing on the thing. Mirrored by unstick() in index.html."""
    for k in range(6):
        lo, hi = player_aabb(pos, crouch)
        b = _overlap_any(lo, hi, boxes)
        if b is None:
            return k > 0
        pos[1] = b.hi[1] + 1e-3
    return True


def step_move(pos, vel, crouch, grounded, dt, boxes):
    """One physics substep. Returns (grounded, landed)."""
    g = CONFIG["GRAVITY"]
    vel[1] -= g * dt
    if vel[1] < -CONFIG["MAX_FALL"]:
        vel[1] = -CONFIG["MAX_FALL"]

    landed = False
    if move_axis(pos, crouch, vel[1] * dt, 1, boxes):
        if vel[1] < 0:
            grounded = True
            landed = True
        vel[1] = 0.0
    else:
        grounded = False

    step_up = CONFIG["STEP_UP"]
    for axis in (0, 2):
        d = vel[axis] * dt
        if d == 0.0:
            continue
        before = pos[axis]
        blocked = move_axis(pos, crouch, d, axis, boxes)
        # Step-up only when grounded, or players climb sheer walls by jumping.
        if blocked and grounded:
            target = list(pos)
            target[axis] = before + d
            rise = _step_rise(target, crouch, boxes, step_up)
            if rise is None:
                continue
            trial = list(pos)
            trial[axis] = before
            trial[1] += rise
            if _overlap_any(*player_aabb(trial, crouch), boxes=boxes) is None:
                if not move_axis(trial, crouch, d, axis, boxes):
                    down = list(trial)
                    move_axis(down, crouch, -rise, 1, boxes)
                    pos[0], pos[1], pos[2] = down
    return grounded, landed


def can_stand(pos, boxes):
    """Headroom check, so standing from crouch never wedges you inside a floor."""
    lo, hi = player_aabb(pos, False)
    return _overlap_any(lo, hi, boxes) is None


# ---------------------------------------------------------------------------
# WebSocket framing
# ---------------------------------------------------------------------------
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_PAYLOAD = 1 << 20


class WSError(Exception):
    pass


class FrameReader(object):
    """Buffered reader whose state survives socket timeouts.

    TCP is a stream, not a message queue: one recv() is not one frame. And a
    timeout firing midway through a payload must not discard what we already
    read, or every following byte is misinterpreted and the stream is corrupt
    for good. So the buffer lives on the instance and reads resume where they
    stopped.
    """

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.closed = False

    def _fill(self, n):
        while len(self.buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise                      # caller decides on liveness; buffer is kept
            except OSError:
                self.closed = True
                raise WSError("socket error")
            if not chunk:
                self.closed = True
                raise WSError("peer closed")
            self.buf.extend(chunk)

    def take(self, n):
        self._fill(n)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def read_message(self):
        """Returns (opcode, payload) for one complete message, reassembling
        fragments and handling control frames interleaved mid-fragment."""
        frags = bytearray()
        frag_op = None
        while True:
            h = self.take(2)
            b0, b1 = h[0], h[1]
            fin = bool(b0 & 0x80)
            op = b0 & 0x0F
            masked = bool(b1 & 0x80)
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self.take(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self.take(8))[0]
            if ln > MAX_PAYLOAD:
                raise WSError("payload too large: %d" % ln)
            mask = self.take(4) if masked else None
            data = bytearray(self.take(ln))
            if mask:
                for i in range(len(data)):
                    data[i] ^= mask[i & 3]

            if op in (OP_CLOSE, OP_PING, OP_PONG):
                return op, bytes(data)

            if op == OP_CONT:
                if frag_op is None:
                    raise WSError("continuation without start")
                frags.extend(data)
            else:
                if frag_op is not None:
                    raise WSError("new frame during fragment")
                frag_op = op
                frags.extend(data)

            if fin:
                return frag_op, bytes(frags)


def ws_frame(payload, op=OP_TEXT):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    n = len(payload)
    if n < 126:
        head = struct.pack("!BB", 0x80 | op, n)
    elif n < 65536:
        head = struct.pack("!BBH", 0x80 | op, 126, n)
    else:
        head = struct.pack("!BBQ", 0x80 | op, 127, n)
    return head + payload


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class Client(object):
    """One connected browser. Owns a bounded outbound queue + sender thread.

    A blocking sendall on one slow client must never stall the 30Hz tick, or
    everybody freezes because one player's Wi-Fi hiccuped. The tick thread only
    enqueues. Under backpressure, stale `state` snapshots are dropped (the next
    one supersedes them) while events are never dropped.
    """
    MAX_Q = 512

    def __init__(self, sock, addr, pid):
        self.sock = sock
        self.addr = addr
        self.pid = pid
        self.alive = True
        self.q = []
        self.cv = threading.Condition()
        self.last_seen = time.time()
        self.sender = threading.Thread(target=self._run, daemon=True)
        self.sender.start()

    def send(self, msg, droppable=False):
        with self.cv:
            if not self.alive:
                return
            if droppable:
                # keep only the freshest state snapshot
                self.q = [m for m in self.q if not m[1]]
            if len(self.q) >= self.MAX_Q:
                self.alive = False
                self.cv.notify_all()
                return
            self.q.append((msg, droppable))
            self.cv.notify_all()

    def _run(self):
        while True:
            with self.cv:
                while self.alive and not self.q:
                    self.cv.wait(0.5)
                if not self.alive:
                    break
                msg, _ = self.q.pop(0)
            try:
                self.sock.sendall(ws_frame(json.dumps(msg, separators=(",", ":"))))
            except Exception:
                self.alive = False
                break
        try:
            self.sock.close()
        except Exception:
            pass

    def kill(self):
        with self.cv:
            self.alive = False
            self.cv.notify_all()


# ---------------------------------------------------------------------------
# Game
# ---------------------------------------------------------------------------
class Game(object):
    def __init__(self):
        self.lock = threading.RLock()
        self.players = {}
        self.clients = {}
        self.pieces = {}
        self.piece_order = []
        self.grenades = []
        self.targets = []
        self.dummies = []
        self.next_id = 1
        self.next_gid = 1
        self.next_tid = 1
        self.host = None
        self.mode = "lobby"
        self.phase = "lobby"       # lobby | countdown | live | ended
        self.phase_until = 0.0
        self.round_no = 0
        self.team_score = [0, 0]      # rounds won, only used by team modes
        self.map_choice = "auto"      # host override; "auto" = the mode's own
        self.tick = 0
        self.aim_stats = {}
        self.aim_next = 0.0
        self.winner = None

    # -- helpers ----------------------------------------------------------
    def new_player(self, name, is_bot=False, diff="medium", style="balanced"):
        pid = self.next_id
        self.next_id += 1
        p = {
            "id": pid, "name": name, "bot": is_bot, "diff": diff,
            "style": style if style in CONFIG["BOT_STYLE"] else "balanced",
            "pos": [0.0, 1.0, 0.0], "vel": [0.0, 0.0, 0.0],
            "yaw": 0.0, "pitch": 0.0, "crouch": False, "grounded": True,
            "hp": CONFIG["HP_MAX"], "shield": CONFIG["SHIELD_MAX"],
            "mats": CONFIG["MAT_START"], "alive": False, "spectator": False,
            "hand": "ar", "ads": False,
            "ammo": {w: CONFIG["WEAPONS"][w]["mag"] for w in CONFIG["LOADOUT"]},
            "last_shot": {}, "reload_until": 0.0, "reloading": None,
            "kills": 0, "deaths": 0,
            # Match stats. Kills and deaths alone say who won but nothing about
            # HOW, and the aim trainer already proves the readout is worth
            # having. Accuracy is counted per TRIGGER PULL, not per pellet --
            # a shotgun that lands two pellets of nine hit its target, and
            # scoring it as 22% would be a lie about what happened.
            "st": {"shots": 0, "hits": 0, "dmg": 0.0, "built": 0,
                   "farmed": 0, "best": 0.0},
            "respawn_at": 0.0, "protect_until": 0.0, "last_build": 0.0,
            "last_dmg_from": None, "last_dmg_at": 0.0,
            "hist": [],                 # (server ms, pos, crouch) for rewinds
            # Rejoining. A dropped connection is not a player leaving: the slot,
            # the score and the builds all stay put until the grace runs out.
            "token": uuid.uuid4().hex,
            "offline_since": 0.0,
            "team": 0,                  # only meaningful in a team mode
            "tr": None,                 # build-trainer run state
            # bot-only scratch
            "bt": {"state": "IDLE", "target": None, "next_fire": 0.0,
                   "next_build": 0.0, "stuck_t": 0.0, "last_pos": [0, 0, 0],
                   "wander": [0.0, 0.0, 0.0], "seen_at": 0.0, "burst": 0},
        }
        self.players[pid] = p
        return p

    def public_player(self, p):
        return {"id": p["id"], "name": p["name"], "bot": p["bot"],
                "hp": p["hp"], "shield": p["shield"], "alive": p["alive"],
                "kills": p["kills"], "deaths": p["deaths"],
                "spectator": p["spectator"], "pos": p["pos"], "yaw": p["yaw"],
                "offline": bool(p["offline_since"]), "team": p["team"]}

    def broadcast(self, msg, droppable=False, exclude=None):
        for pid, c in list(self.clients.items()):
            if exclude is not None and pid == exclude:
                continue
            c.send(msg, droppable)

    def send_to(self, pid, msg):
        c = self.clients.get(pid)
        if c:
            c.send(msg)

    # -- world ------------------------------------------------------------
    def collision_boxes(self, exclude_pid=None, include_players=False, at_ms=None):
        boxes = list(ARENA_BOXES)
        for key, pc in self.pieces.items():
            boxes.extend(pc["boxes"])
        if include_players:
            for p in self.players.values():
                if not p["alive"] or p["id"] == exclude_pid:
                    continue
                boxes.extend(self.hitboxes(p, at_ms))
        return boxes

    def hitboxes(self, p, at_ms=None):
        """Body and head boxes for `p`, optionally REWOUND to a past instant.

        See rewind_to() for why shots carry a time at all."""
        pos, crouch = p["pos"], p["crouch"]
        if at_ms is not None:
            r = self.rewind_to(p, at_ms)
            if r is not None:
                pos, crouch = r
        w = CONFIG["P_W"]
        h = CONFIG["P_H_CROUCH"] if crouch else CONFIG["P_H"]
        hh = CONFIG["HEAD_H"]
        body = Box([pos[0] - w / 2, pos[1], pos[2] - w / 2],
                   [pos[0] + w / 2, pos[1] + h - hh, pos[2] + w / 2],
                   "body", p["id"])
        head = Box([pos[0] - hh / 2, pos[1] + h - hh, pos[2] - hh / 2],
                   [pos[0] + hh / 2, pos[1] + h, pos[2] + hh / 2],
                   "head", p["id"])
        return [body, head]

    # -- lag compensation -------------------------------------------------
    #
    # A client does not see the present. It renders remote players INTERP_MS
    # behind the newest snapshot, and that snapshot is already one network trip
    # old. So when someone puts their crosshair on a head and clicks, the head
    # they are looking at is somewhere between 100ms and 250ms in the past --
    # and by the time the shot arrives here, that player has moved. Validating
    # against the present is what makes a strafing target feel bulletproof and
    # makes you feel like you are shooting behind them. You are.
    #
    # So each shot carries the server timestamp the client was RENDERING when
    # it fired (`at`, taken straight from the interpolation clock), and every
    # other player is rewound to that instant before the ray is cast. The
    # shooter is never rewound: they see themselves in the present.
    #
    # The window is clamped hard. Trusting a client-supplied rewind without a
    # bound means anyone can ask to shoot at where you stood a minute ago.
    def record_history(self, now_ms):
        """One sample per state broadcast, on the same clock the client
        interpolates against -- that is what makes `at` directly comparable."""
        keep = CONFIG["LAG_COMP_MS"] + 250
        for p in self.players.values():
            h = p["hist"]
            h.append((now_ms, list(p["pos"]), p["crouch"]))
            cut = now_ms - keep
            while len(h) > 2 and h[0][0] < cut:
                h.pop(0)

    def rewind_to(self, p, at_ms):
        """(pos, crouch) for `p` at server time `at_ms`, or None to use now."""
        h = p["hist"]
        if not h:
            return None
        newest = h[-1][0]
        # never forward in time, and never further back than the window allows
        at_ms = min(at_ms, newest)
        at_ms = max(at_ms, newest - CONFIG["LAG_COMP_MS"])
        if at_ms >= newest:
            return None
        if at_ms <= h[0][0]:
            return list(h[0][1]), h[0][2]
        for i in range(len(h) - 1):
            a, b = h[i], h[i + 1]
            if a[0] <= at_ms <= b[0]:
                span = b[0] - a[0]
                f = 0.0 if span <= 0 else (at_ms - a[0]) / float(span)
                pos = [a[1][j] + (b[1][j] - a[1][j]) * f for j in range(3)]
                # crouch is a state, not a number -- take the sample you are
                # closer to rather than blending two heights into a third
                return pos, (a[2] if f < 0.5 else b[2])
        return None

    def shot_time(self, m):
        """The rewind instant a shot message asks for, or None for 'now'."""
        at = m.get("at")
        if not isinstance(at, (int, float)):
            return None
        return float(at)

    def spawn_dummies(self):
        self.dummies = []
        for i, d in enumerate(DUMMIES):
            self.dummies.append({
                "id": i + 1, "pos": list(d), "alive": True,
                "hp": CONFIG["DUMMY_HP"], "shield": CONFIG["DUMMY_SHIELD"],
                "respawn_at": 0.0,
            })
        self.broadcast({"t": "dummies", "d": self.wire_dummies()})

    def wire_dummies(self):
        return [{"id": d["id"], "pos": d["pos"], "alive": d["alive"],
                 "hp": d["hp"], "shield": d["shield"]} for d in self.dummies]

    def dummy_boxes(self):
        out = []
        w, h, hh = CONFIG["P_W"], CONFIG["P_H"], CONFIG["HEAD_H"]
        for d in self.dummies:
            if not d["alive"]:
                continue
            p = d["pos"]
            out.append(Box([p[0] - w / 2, p[1], p[2] - w / 2],
                           [p[0] + w / 2, p[1] + h - hh, p[2] + w / 2],
                           "dummy", d["id"]))
            out.append(Box([p[0] - hh / 2, p[1] + h - hh, p[2] - hh / 2],
                           [p[0] + hh / 2, p[1] + h, p[2] + hh / 2],
                           "dummyhead", d["id"]))
        return out

    def hit_dummy(self, did, amount, head, by_pid, at):
        for d in self.dummies:
            if d["id"] != did or not d["alive"]:
                continue
            if d["shield"] > 0:
                used = min(d["shield"], amount)
                d["shield"] -= used
                amount -= used
            if amount > 0:
                d["hp"] -= amount
            if d["hp"] <= 0:
                d["alive"] = False
                d["hp"] = 0.0
                d["respawn_at"] = time.time() + CONFIG["DUMMY_RESPAWN"]
            self.broadcast({"t": "dummy", "id": d["id"], "hp": d["hp"],
                            "shield": d["shield"], "alive": d["alive"]})
            return

    def tick_dummies(self, now):
        for d in self.dummies:
            if not d["alive"] and d["respawn_at"] and now >= d["respawn_at"]:
                d["alive"] = True
                d["hp"] = CONFIG["DUMMY_HP"]
                d["shield"] = CONFIG["DUMMY_SHIELD"]
                d["respawn_at"] = 0.0
                self.broadcast({"t": "dummy", "id": d["id"], "hp": d["hp"],
                                "shield": d["shield"], "alive": True})

    def target_boxes(self):
        out = []
        r = CONFIG["AIM_TRAINER"]["radius"]
        for t in self.targets:
            c = t["pos"]
            out.append(Box([c[0] - r, c[1] - r, c[2] - r],
                           [c[0] + r, c[1] + r, c[2] + r], "target", t["id"]))
        return out

    # -- building ---------------------------------------------------------
    def piece_index(self):
        """Pieces bucketed by cell, so support checks stay local."""
        grid = {}
        for k, pc in self.pieces.items():
            grid.setdefault((pc["cx"], pc["cy"], pc["cz"]), []).append(k)
        return grid

    def neighbour_keys(self, cx, cy, cz, grid):
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    out.extend(grid.get((cx + dx, cy + dy, cz + dz), ()))
        return out

    def is_supported(self, boxes, cx, cy, cz, grid=None):
        """True when these boxes rest on the arena or touch an existing piece.
        Every stored piece is already supported, so touching any one of them is
        enough -- the chain back to the ground is an invariant."""
        bounds = ([min(b.lo[i] for b in boxes) for i in range(3)],
                  [max(b.hi[i] for b in boxes) for i in range(3)])
        if rests_on_arena(bounds):
            return True
        if grid is None:
            grid = self.piece_index()
        for nk in self.neighbour_keys(cx, cy, cz, grid):
            if faces_touch(bounds, piece_bounds(self.pieces[nk])):
                return True
        return False

    def prune_unsupported(self):
        """Flood-fill from everything touching the arena; destroy the rest."""
        if not self.pieces:
            return []
        grid = self.piece_index()
        bounds = {k: piece_bounds(pc) for k, pc in self.pieces.items()}
        stack = [k for k, b in bounds.items() if rests_on_arena(b)]
        seen = set(stack)
        while stack:
            k = stack.pop()
            pc = self.pieces[k]
            for nk in self.neighbour_keys(pc["cx"], pc["cy"], pc["cz"], grid):
                if nk in seen:
                    continue
                if faces_touch(bounds[k], bounds[nk]):
                    seen.add(nk)
                    stack.append(nk)
        orphans = [k for k in self.pieces if k not in seen]
        for k in orphans:
            del self.pieces[k]
            if k in self.piece_order:
                self.piece_order.remove(k)
            self.broadcast({"t": "unbuild", "key": k, "orphan": True})
        return orphans

    def cell_volume_taken(self, cx, cy, cz):
        """A cell has ONE volume slot. A ramp and a cone both fill it, so they
        can never share a cell -- but walls and floors sit on the cell's faces
        and stack freely alongside whichever volume piece is there."""
        return (piece_key("ramp", cx, cy, cz) in self.pieces or
                piece_key("roof", cx, cy, cz) in self.pieces)

    def in_bounds(self, cx, cy, cz):
        return (CONFIG["CELL_Y_MIN"] <= cy <= CONFIG["CELL_Y_MAX"] and
                abs(cx) <= CONFIG["CELL_XZ_MAX"] and abs(cz) <= CONFIG["CELL_XZ_MAX"])

    def cell_in_reach(self, p, ptype, cx, cy, cz, direction):
        """Grid reach, not metres.

        Think of the map as invisible build boxes. You reach the box you are
        standing in and two more in every direction, trimmed to a circle -- so
        (2,0) and (1,1) are in, (2,1) and (2,2) are out. Measuring in cells
        rather than in metres is what makes reach predictable: it does not
        wobble depending on where inside the box you happen to be standing, and
        it steps forward by exactly one box when you walk into the next one.

        Ramps are a stride, not a throw: one in front, or the box you stand in.

        A wall lives on the edge BETWEEN two boxes rather than in one, and it
        used to count as in reach when EITHER of those boxes was -- which gave
        walls a full box more range than everything else. They now use the same
        circle as the rest, so the furthest wall you can place is one floor tile
        closer than it was. Walls are the piece you throw out under pressure and
        the extra box of reach let you seal off ground you had no business
        holding."""
        bx = int(math.floor(p["pos"][0] / CELL))
        by = int(math.floor((p["pos"][1] + 0.05) / CELL))
        bz = int(math.floor(p["pos"][2] / CELL))
        dx, dy, dz = cx - bx, cy - by, cz - bz
        if abs(dy) > CONFIG["BUILD_RADIUS_Y"]:
            return False
        r = CONFIG["BUILD_RADIUS"]
        step = CARD[direction & 3]
        if ptype == "ramp":
            # A stride, not a throw: the box you stand in, or one box away.
            # Deliberately ANY of the four neighbours rather than only the one
            # the ramp faces -- reach is a question of distance, and tying it to
            # facing meant a rotated ramp could only be placed under your own
            # feet. You aim at a cell and turn the ramp inside it; those are two
            # separate things.
            return abs(dx) + abs(dz) <= 1
        return dx * dx + dz * dz <= r * r

    def build_sight_boxes(self, p):
        """Collision for the build line-of-sight test, minus your OWN pieces.

        The rule exists to stop you building through an ENEMY's wall, and only
        that. Counting your own builds as blockers is what made going vertical
        so awkward: inside your own box every cell worth filling is behind a
        wall you just placed, so the ramp above your head was refused by the
        floor you were standing on. Your own build is not cover you are seeing
        through -- it is the structure you are extending."""
        boxes = list(ARENA_BOXES)
        for key, pc in self.pieces.items():
            if pc["owner"] == p["id"]:
                continue
            boxes.extend(pc["boxes"])
        return boxes

    def build_los_clear(self, p, boxes):
        """Nothing solid may stand between you and the box you are filling.

        Without this, "two cells of reach" quietly meant "two cells THROUGH a
        wall": you could drop a floor on the far side of an enemy's build and
        walk out of your own box. Anything in the way now caps you at the last
        box you can actually see into.

        Aimed at the NEAREST point of the new piece, not its centre. Aiming at
        the centre means the ray has to pass through the piece you are standing
        on to get there, so standing on a ramp refused the next ramp -- which is
        the single most common thing anyone builds."""
        eye_h = CONFIG["EYE_CROUCH"] if p["crouch"] else CONFIG["EYE"]
        eye = [p["pos"][0], p["pos"][1] + eye_h, p["pos"][2]]
        plo = [min(b.lo[i] for b in boxes) for i in range(3)]
        phi = [max(b.hi[i] for b in boxes) for i in range(3)]
        near = [min(max(eye[i], plo[i]), phi[i]) for i in range(3)]
        seg = v_sub(near, eye)
        dist = v_len(seg)
        if dist < 1.2:
            return True
        d = v_scale(seg, 1.0 / dist)
        t, _ = raycast(eye, d, self.build_sight_boxes(p), dist - 0.15)
        # A hit at t=0 means the ray STARTED inside something -- the player is
        # embedded in a piece, not looking through one. Counting that as blocked
        # would take building away from someone stuck inside geometry, which is
        # the worst possible moment to lose it.
        return t is None or t < 1e-4

    def resolve_player_out(self, other, ptype, hit, world):
        """Where a player standing inside a new piece should end up.

        Returns a position, or False when there is nowhere safe -- in which
        case the caller refuses the placement rather than wedging someone into
        the map. Refusing outright was the old behaviour for every case, and it
        is why building on top of yourself did nothing; letting the AABB solver
        sort it out afterwards was worse, because it resolves along whichever
        axis is shallowest and that is regularly straight down through the
        floor. Hence an explicit, per-piece rule:

          floor / ramp / cone -> stand ON it, never under it
          wall                -> pushed along the wall's own normal, to the
                                 side you were already heading. Never sideways:
                                 a wall shoving you left or right is how you end
                                 up somewhere you did not choose."""
        pos = other["pos"]
        if ptype in ("floor", "ramp", "roof"):
            # Lift repeatedly, not once. A ramp is eight treads and a cone is
            # four rings, and a player box is wider than one of them -- clearing
            # the tread you are standing in drops you straight into the next one
            # up. Settle on the first height that is clear of everything.
            cand = list(pos)
            for _ in range(8):
                lo, hi = player_aabb(cand, other["crouch"])
                b = _overlap_any(lo, hi, world)
                if b is None:
                    return cand
                cand[1] = b.hi[1] + 0.02
            return False
        else:
            axis = 0 if ptype == "wallX" else 2
            half = CONFIG["P_W"] / 2.0 + 0.02
            lo_e = min(b.lo[axis] for b in hit)
            hi_e = max(b.hi[axis] for b in hit)
            fwd_pos = list(pos)
            fwd_pos[axis] = hi_e + half
            back_pos = list(pos)
            back_pos[axis] = lo_e - half
            head = other["vel"][axis]
            if abs(head) < 0.5:
                yaw = math.radians(other["yaw"])
                head = -math.sin(yaw) if axis == 0 else -math.cos(yaw)
            cands = [fwd_pos, back_pos] if head >= 0 else [back_pos, fwd_pos]
        for c in cands:
            lo, hi = player_aabb(c, other["crouch"])
            if _overlap_any(lo, hi, world) is None:
                return c
        return False

    def place(self, p, ptype, cx, cy, cz, direction):
        """Validate + place. Returns the piece dict, or None if refused."""
        now = time.time()
        if now - p.get("last_build", 0.0) < CONFIG["BUILD_COOLDOWN"] * 0.9:
            return None
        if not self.cell_in_reach(p, ptype, cx, cy, cz, direction):
            return None
        if ptype == "wall":
            ptype, cx, cy, cz = canon_wall(cx, cy, cz, direction)
        if not self.in_bounds(cx, cy, cz):
            return None
        key = piece_key(ptype, cx, cy, cz)
        if key in self.pieces:
            return None
        if ptype in ("ramp", "roof") and self.cell_volume_taken(cx, cy, cz):
            return None

        infinite = self.mode in ("build", "trainer")
        cost = CONFIG["PIECE_COST"]
        if not infinite and p["mats"] < cost:
            return None

        boxes = tile_boxes(ptype, cx, cy, cz, direction, full_mask(ptype))

        # No floating builds: it must rest on the arena or touch something that
        # already does.
        if not self.is_supported(boxes, cx, cy, cz):
            return None

        if not self.build_los_clear(p, boxes):
            return None

        # A piece landing on a player moves the PLAYER. Work out every push
        # first and only commit if all of them are safe -- half-applied pushes
        # are how someone ends up inside the map.
        world = self.collision_boxes() + boxes
        pushes = []
        for other in self.players.values():
            if not other["alive"]:
                continue
            lo, hi = player_aabb(other["pos"], other["crouch"])
            hit = [b for b in boxes if b.overlaps(lo, hi)]
            if not hit:
                continue
            dest = self.resolve_player_out(other, ptype, hit, world)
            if dest is False:
                return None
            pushes.append((other, dest))

        if len(self.pieces) >= CONFIG["PIECE_LIMIT"] and self.piece_order:
            old = self.piece_order.pop(0)
            if old in self.pieces:
                del self.pieces[old]
                self.broadcast({"t": "unbuild", "key": old})

        pc = {"key": key, "type": ptype, "cx": cx, "cy": cy, "cz": cz,
              "dir": direction, "hp": CONFIG["PIECE_HP"], "owner": p["id"],
              "mask": full_mask(ptype), "boxes": boxes, "t": now,
              "sbounds": ([min(b.lo[i] for b in boxes) for i in range(3)],
                          [max(b.hi[i] for b in boxes) for i in range(3)])}
        self.pieces[key] = pc
        self.piece_order.append(key)
        if not infinite:
            p["mats"] -= cost
        p["st"]["built"] += 1
        p["last_build"] = now
        self.broadcast({"t": "build", "p": self.wire_piece(pc)})
        self.send_to(p["id"], {"t": "you", "mats": p["mats"]})
        for other, dest in pushes:
            other["pos"] = list(dest)
            other["vel"][1] = 0.0
            other["grounded"] = False
            self.send_to(other["id"], {"t": "correct", "pos": other["pos"]})
        return pc

    def wire_piece(self, pc):
        return {"key": pc["key"], "type": pc["type"], "cx": pc["cx"], "cy": pc["cy"],
                "cz": pc["cz"], "dir": pc["dir"], "hp": pc["hp"],
                "owner": pc["owner"], "mask": pc["mask"]}

    def damage_piece(self, key, amount, by_pid):
        pc = self.pieces.get(key)
        if pc is None:
            return
        pc["hp"] -= amount
        if pc["hp"] <= 0:
            del self.pieces[key]
            if key in self.piece_order:
                self.piece_order.remove(key)
            self.broadcast({"t": "unbuild", "key": key})
            self.prune_unsupported()      # anything it was holding up goes too
            breaker = self.players.get(by_pid)
            if breaker and self.mode != "build":
                breaker["mats"] = min(CONFIG["MAT_CAP"],
                                      breaker["mats"] + CONFIG["PICKAXE_REFUND"])
                self.send_to(by_pid, {"t": "you", "mats": breaker["mats"]})
        else:
            self.broadcast({"t": "pdmg", "key": key, "hp": pc["hp"]}, droppable=False)

    def edit_piece(self, p, key, mask):
        pc = self.pieces.get(key)
        if pc is None or pc["owner"] != p["id"]:
            return
        # edit_mask_ok() carries the whole rule: the right grid for the piece,
        # a rectangle of tiles and never all of them. Editing every tile away
        # would leave an invisible piece that still occupies its cell and still
        # holds up whatever is stacked on it -- a wall you cannot see, cannot
        # shoot and cannot rebuild over. A piece is destroyed by damage, never
        # by editing.
        if not edit_mask_ok(pc["type"], mask):
            return
        if mask == pc["mask"]:
            return
        pc["mask"] = mask
        boxes = tile_boxes(pc["type"], pc["cx"], pc["cy"], pc["cz"], pc["dir"], mask)
        pc["boxes"] = boxes
        self.broadcast({"t": "editbuild", "key": key, "mask": mask})
        # A ramp edit raises geometry rather than removing it, so an edit can
        # put a player inside the piece exactly the way a fresh placement can.
        world = self.collision_boxes()
        for other in self.players.values():
            if not other["alive"]:
                continue
            lo, hi = player_aabb(other["pos"], other["crouch"])
            hit = [b for b in boxes if b.overlaps(lo, hi)]
            if not hit:
                continue
            dest = self.resolve_player_out(other, pc["type"], hit, world)
            if dest is False:
                continue
            other["pos"] = list(dest)
            other["vel"][1] = 0.0
            self.send_to(other["id"], {"t": "correct", "pos": other["pos"]})

    # -- combat -----------------------------------------------------------
    def apply_damage(self, target, amount, by_pid, head=False, weapon=None):
        if not target["alive"]:
            return
        if time.time() < target["protect_until"]:
            return
        if self.mode in ("build", "trainer"):
            return
        # No friendly fire. A teammate's wall is still breakable -- that is a
        # build game and sometimes the wall in your way is theirs -- but a
        # teammate is not, and grenades in particular would be unplayable.
        if is_team(self.mode) and by_pid != target["id"]:
            src = self.players.get(by_pid)
            if src and src["team"] == target["team"]:
                return
        # Credit the damage that actually lands, not what was rolled: overkill
        # on a player with 3hp left is 3 damage, and counting the full swing
        # would make the readout flattering rather than true.
        dealt = min(amount, target["shield"] + max(0.0, target["hp"]))
        killer = self.players.get(by_pid)
        if killer and killer is not target:
            killer["st"]["dmg"] += dealt
            if dealt > killer["st"]["best"]:
                killer["st"]["best"] = dealt
        shield = target["shield"]
        if shield > 0:
            used = min(shield, amount)
            target["shield"] = shield - used
            amount -= used
        if amount > 0:
            target["hp"] -= amount
        target["last_dmg_from"] = by_pid
        target["last_dmg_at"] = time.time()
        src = self.players.get(by_pid)
        self.send_to(target["id"], {"t": "you", "hp": target["hp"],
                                    "shield": target["shield"],
                                    "from": src["pos"] if src else None,
                                    "by": by_pid if src else None,
                                    "bn": src["name"] if src else "the void",
                                    "dmg": round(dealt), "head": head,
                                    "w": weapon})
        # The combat report is a two-sided ledger: one landed shot is one
        # event, and both ends of it want the same row. The victim learns it
        # from `you`; the shooter learns it here, credited with what actually
        # landed rather than what was rolled. Routing it through apply_damage
        # rather than the hitscan path is what gets grenades onto the feed --
        # an explosion never sends a `hit`.
        if src and src is not target and dealt > 0:
            self.send_to(by_pid, {"t": "dealt", "id": target["id"],
                                  "n": target["name"], "dmg": round(dealt),
                                  "head": head, "w": weapon,
                                  "pos": target["pos"],
                                  "hp": max(0.0, round(target["hp"])),
                                  "sh": round(target["shield"]),
                                  "dead": target["hp"] <= 0})
        if target["hp"] <= 0:
            self.kill_player(target, by_pid)

    def kill_player(self, target, by_pid):
        target["alive"] = False
        target["hp"] = 0.0
        target["deaths"] += 1
        killer = self.players.get(by_pid)
        if killer and killer is not target:
            killer["kills"] += 1
        self._last_round_winner = by_pid if (killer and killer is not target) else None
        # How long the corpse waits, so the client can run a respawn clock
        # rather than guessing. Round-based modes respawn on the round, not on
        # a timer, and say so with 0.
        rt = (0.0 if (self.mode == "duel" or is_team(self.mode))
              else CONFIG["RESPAWN_TIME"])
        self.broadcast({"t": "die", "id": target["id"], "by": by_pid,
                        "kn": killer["name"] if killer else "the void",
                        "vn": target["name"], "rt": rt})
        self.broadcast({"t": "score", "s": {str(p["id"]): p["kills"]
                                            for p in self.players.values()}})
        if self.mode == "duel":
            self.end_round()
        elif is_team(self.mode):
            # A team round ends when one side is entirely down, not on the
            # first kill -- the 2-on-1 that follows a trade is the whole reason
            # to play with a partner.
            if self.team_wiped() is not None:
                self.end_round()
        else:
            target["respawn_at"] = time.time() + CONFIG["RESPAWN_TIME"]
        self.check_win()

    def team_wiped(self):
        """The team that has just been eliminated, or None."""
        alive = {0: 0, 1: 0}
        for p in self.participants():
            if p["alive"]:
                alive[p["team"]] = alive.get(p["team"], 0) + 1
        if alive[0] and alive[1]:
            return None
        if not alive[0] and not alive[1]:
            return None                     # a mutual wipe scores for nobody
        return 0 if not alive[0] else 1

    def check_win(self):
        if self.phase == "ended":
            return
        goal = target_for(self.mode)
        if is_team(self.mode):
            for side in (0, 1):
                if self.team_score[side] >= goal:
                    self.phase = "ended"
                    self.winner = side
                    names = [q["name"] for q in self.participants()
                             if q["team"] == side]
                    self.broadcast({"t": "matchend", "winner": None,
                                    "team": side, "teams": self.team_score,
                                    "name": " & ".join(names) or ("Team " + str(side + 1)),
                                    "s": [self.wire_stats(q)
                                          for q in self.players.values()]})
                    return
            return
        if self.mode not in ("duel", "dm"):
            return
        for p in self.players.values():
            if p["kills"] >= goal:
                self.phase = "ended"
                self.winner = p["id"]
                self.broadcast({"t": "matchend", "winner": p["id"],
                                "name": p["name"],
                                "s": [self.wire_stats(q)
                                      for q in self.players.values()]})
                return

    def wire_stats(self, q):
        st = q["st"]
        acc = (100.0 * st["hits"] / st["shots"]) if st["shots"] else 0.0
        return {"id": q["id"], "name": q["name"], "bot": q["bot"],
                "kills": q["kills"], "deaths": q["deaths"],
                "acc": round(acc, 1), "shots": st["shots"], "hits": st["hits"],
                "dmg": round(st["dmg"]), "built": st["built"],
                "farmed": st["farmed"], "best": round(st["best"])}

    def reset_stats(self):
        for q in self.players.values():
            q["st"] = {"shots": 0, "hits": 0, "dmg": 0.0, "built": 0,
                       "farmed": 0, "best": 0.0}

    def participants(self):
        # A parked player is still in the match -- they keep their slot and
        # their score -- but they are not someone a round can wait on or that
        # a duel can be won against, so they do not count while they are away.
        return [p for p in self.players.values()
                if not p["spectator"] and not p["offline_since"]]

    def team_spawn_index(self, p):
        """Teams start at opposite ends. The spawn list alternates sides, so
        stepping by two keeps a team together and the other team across."""
        mates = [q for q in self.participants() if q["team"] == p["team"]]
        slot = mates.index(p) if p in mates else 0
        return (p["team"] + slot * 2) % max(1, len(SPAWNS))

    def spawn(self, p, index=None):
        parts = [q for q in self.participants() if q is not p and q["alive"]]
        if index is not None:
            s = SPAWNS[index % len(SPAWNS)]
        elif not parts:
            s = SPAWNS[0]
        else:
            # furthest spawn from the nearest living enemy, or a 2-player
            # deathmatch on an 80x80 arena becomes a spawn-kill loop
            best, bestd = SPAWNS[0], -1.0
            for s2 in SPAWNS:
                d = min(v_dist([s2[0], s2[1], s2[2]], q["pos"]) for q in parts)
                if d > bestd:
                    bestd, best = d, s2
            s = best
        p["pos"] = [s[0], s[1], s[2]]
        p["vel"] = [0.0, 0.0, 0.0]
        p["yaw"] = s[3]
        p["pitch"] = 0.0
        p["hp"] = CONFIG["HP_MAX"]
        p["shield"] = CONFIG["SHIELD_MAX"]
        p["alive"] = True
        p["crouch"] = False
        p["mats"] = CONFIG["MAT_START"]
        p["ammo"] = {w: CONFIG["WEAPONS"][w]["mag"] for w in CONFIG["LOADOUT"]}
        p["reloading"] = None
        p["reload_until"] = 0.0
        # A mode can take a weapon away between rounds -- holding a grenade
        # when a duel starts would leave you holding something you cannot use
        # and no longer have a hotbar key for.
        if not self.can_use(p["hand"]) and p["hand"] not in CONFIG["BUILD_PIECES"]:
            p["hand"] = loadout_for(self.mode)[0]
        p["protect_until"] = time.time() + (CONFIG["SPAWN_PROTECT"]
                                            if self.mode == "dm" else 0.0)
        self.broadcast({"t": "respawn", "id": p["id"], "pos": p["pos"],
                        "yaw": p["yaw"], "hp": p["hp"], "shield": p["shield"],
                        "mats": p["mats"]})

    def wipe_builds(self):
        self.pieces.clear()
        self.piece_order = []
        self.broadcast({"t": "wipe"})

    _last_round_winner = None
    _last_round_team = None

    def end_round(self):
        if is_team(self.mode):
            lost = self.team_wiped()
            if lost is not None:
                self.team_score[1 - lost] += 1
                self._last_round_winner = None
                self._last_round_team = 1 - lost
        self.round_no += 1
        self.phase = "countdown"
        self.phase_until = time.time() + CONFIG["ROUND_COUNTDOWN"]
        self.wipe_builds()
        parts = self.participants()
        for i, p in enumerate(parts):
            self.spawn(p, index=self.team_spawn_index(p) if is_team(self.mode) else i)
        # The round card carries the score and who took it. A first-to-5 that
        # runs five rounds back to back with no beat between them does not feel
        # like a match, it feels like the same fight restarting.
        self.broadcast({"t": "round", "n": self.round_no,
                        "until": CONFIG["ROUND_COUNTDOWN"],
                        "goal": target_for(self.mode),
                        "by": self._last_round_winner,
                        "team": self._last_round_team if is_team(self.mode) else None,
                        "teams": self.team_score if is_team(self.mode) else None,
                        "s": [self.wire_stats(q) for q in self.participants()]})

    def send_arena(self):
        self.broadcast({"t": "arena", "name": ARENA_NAME,
                        "label": ARENA_NAMES.get(ARENA_NAME, ARENA_NAME),
                        "arena": ARENA, "spawns": SPAWNS})

    def start_match(self, mode):
        self.mode = mode
        # A duel plays in the box; everything else plays on the open map. The
        # layout has to land before spawn() runs, or players are placed at the
        # old map's spawn points -- outside the new one's walls.
        want = self.map_choice if self.map_choice in ARENAS else MODE_ARENA.get(mode, "open")
        if load_arena(want):
            self.send_arena()
        self.round_no = 0
        self.team_score = [0, 0]
        self._last_round_team = None
        self.winner = None
        self.grenades = []
        self.targets = []
        self.aim_stats = {}
        self.wipe_builds()
        self.reset_stats()
        for p in self.players.values():
            p["kills"] = 0
            p["deaths"] = 0
            p["spectator"] = False
        parts = self.participants()
        self.dummies = []
        if mode == "build":
            self.spawn_dummies()
        if mode == "trainer":
            for p in self.players.values():
                if not p["bot"]:
                    self.trainer_reset(p)
        if mode == "duel":
            for i, p in enumerate(parts):
                p["spectator"] = i >= 2
        if is_team(mode):
            # Alternate rather than split down the middle: whoever joined first
            # would otherwise always end up on the same side as the other early
            # joiners, and with bots that reliably means all the humans vs all
            # the bots. Up to 8, the rest spectate.
            for i, p in enumerate(parts):
                p["spectator"] = i >= 8
                p["team"] = i % 2
        else:
            for p in parts:
                p["team"] = 0
        for i, p in enumerate(self.participants()):
            self.spawn(p, index=self.team_spawn_index(p) if is_team(mode) else i)
        for p in self.players.values():
            if p["spectator"]:
                p["alive"] = False
        self.phase = "countdown"
        self.phase_until = time.time() + CONFIG["ROUND_COUNTDOWN"]
        self.broadcast({"t": "mode", "mode": mode, "phase": self.phase,
                        "until": CONFIG["ROUND_COUNTDOWN"],
                        "players": [self.public_player(p) for p in self.players.values()]})

    def to_lobby(self):
        self.mode = "lobby"
        self.phase = "lobby"
        if load_arena("open"):
            self.send_arena()
        self.winner = None
        self.wipe_builds()
        self.grenades = []
        self.targets = []
        self.dummies = []
        self.broadcast({"t": "dummies", "d": []})
        for p in self.players.values():
            p["alive"] = False
            p["spectator"] = False
            p["kills"] = 0
            p["deaths"] = 0
        for pid in [q["id"] for q in self.players.values() if q["bot"]]:
            self.remove_player(pid)
        self.broadcast({"t": "mode", "mode": "lobby", "phase": "lobby",
                        "players": [self.public_player(p) for p in self.players.values()]})

    # -- dropping and rejoining -------------------------------------------
    #
    # Down at the socket a dropped connection and a player quitting look
    # identical, and only one of them should cost someone their match. So a
    # lost connection parks the player: they stay in the scoreboard, keep their
    # score, their builds and their spot in a duel, and go still. Coming back
    # inside REJOIN_GRACE with the same token walks straight back into that
    # slot; past it they are gone for real.
    def go_offline(self, pid):
        p = self.players.get(pid)
        if p is None:
            return
        if p["bot"] or self.phase == "lobby" or not self.mode or self.mode == "lobby":
            # nothing to come back to -- treat it as leaving
            nm = p["name"]
            self.remove_player(pid)
            print("  - %s left" % nm)
            return
        p["offline_since"] = time.time()
        # A parked player must not be shootable, and must not be holding a
        # duel open by still counting as a participant who is alive.
        p["alive"] = False
        self.broadcast({"t": "offline", "id": pid, "name": p["name"]})
        print("  ~ %s dropped (slot held %ds)" % (p["name"], int(CONFIG["REJOIN_GRACE"])))
        # the host going quiet cannot leave the lobby unable to start anything
        if self.host == pid:
            live = [q["id"] for q in self.players.values()
                    if not q["bot"] and not q["offline_since"]]
            if live:
                self.host = live[0]
                self.broadcast({"t": "host", "id": self.host})

    def reclaim(self, token):
        """The parked player holding `token`, put back online. None if there
        isn't one -- an unknown or expired token just joins fresh."""
        if not token:
            return None
        for p in self.players.values():
            if p["offline_since"] and p["token"] == token:
                p["offline_since"] = 0.0
                if self.host is None:
                    self.host = p["id"]
                return p
        return None

    # -- attaching a connection -------------------------------------------
    #
    # Both transports land here: the socket handler below, and the ASGI adapter
    # in app.py that Vercel runs. Keeping the handshake in ONE place is not
    # tidiness. A welcome missing `token` silently disables the client's
    # reconnect -- index.html only retries while it holds one -- so a second
    # copy of this that drifts turns every dropped connection into a dead
    # session instead of a two-second blip. That is exactly what a duplicated
    # copy of it did.
    def attach(self, make_client, name, token, where=""):
        """Put a new connection into the game. Returns (player, client, rejoined).

        `make_client` is handed the player id and returns the transport's own
        client object, so each transport keeps its own sender without this
        method needing to know which one it is talking to.
        """
        p = self.reclaim(token)
        rejoined = p is not None
        if not rejoined:
            p = self.new_player(name)
        pid = p["id"]
        client = make_client(pid)
        self.clients[pid] = client
        if self.host is None:
            self.host = pid
        client.send(self.welcome(p, rejoined))
        if rejoined:
            self.broadcast({"t": "rejoin", "p": self.public_player(p)}, exclude=pid)
            print("  * %s rejoined%s" % (p["name"], where))
        else:
            self.broadcast({"t": "join", "p": self.public_player(p)}, exclude=pid)
            print("  + %s joined%s" % (p["name"], where))
        return p, client, rejoined

    def welcome(self, p, rejoined):
        """Everything a client needs to render the world it just walked into."""
        return {
            "t": "welcome", "id": p["id"], "host": self.host,
            "build": BUILD_ID, "token": p["token"],
            "rejoined": rejoined,
            "config": CONFIG, "arena": ARENA, "spawns": SPAWNS,
            "maps": ARENA_NAMES, "map": self.map_choice,
            "mode": self.mode, "phase": self.phase,
            "you": self.private_player(p),
            "players": [self.public_player(q) for q in self.players.values()],
            "pieces": [self.wire_piece(pc) for pc in self.pieces.values()],
            "dummies": self.wire_dummies(),
        }

    def detach(self, pid):
        """A connection went away. go_offline decides whether that means
        "back in a moment" or "gone for good"."""
        self.clients.pop(pid, None)
        self.go_offline(pid)

    def sweep_offline(self, now):
        """Anyone past the grace period has really gone."""
        grace = CONFIG["REJOIN_GRACE"]
        for pid, p in list(self.players.items()):
            if p["offline_since"] and now - p["offline_since"] > grace:
                nm = p["name"]
                self.remove_player(pid)
                print("  - %s left (did not come back)" % nm)

    def private_player(self, p):
        """The parts of your own record only you see. Sent on welcome so a
        rejoin lands you back with your health, materials and ammo rather than
        a fresh set that would be worth dying for."""
        return {"hp": p["hp"], "shield": p["shield"], "mats": p["mats"],
                "ammo": p["ammo"], "alive": p["alive"], "hand": p["hand"],
                "kills": p["kills"], "deaths": p["deaths"],
                "pos": p["pos"], "yaw": p["yaw"],
                "spectator": p["spectator"]}

    def remove_player(self, pid):
        p = self.players.pop(pid, None)
        if p is None:
            return
        self.broadcast({"t": "leave", "id": pid})
        if self.host == pid:
            humans = [q["id"] for q in self.players.values() if not q["bot"]]
            self.host = humans[0] if humans else None
            self.broadcast({"t": "host", "id": self.host})
        if self.mode == "duel" and self.phase in ("live", "countdown") and not p["bot"]:
            if len([q for q in self.participants()]) < 2:
                self.broadcast({"t": "forfeit", "name": p["name"]})
                self.to_lobby()

    # -- shooting ---------------------------------------------------------
    def can_use(self, weapon):
        """Is this weapon in the current mode's loadout? One gate, asked by
        every path that can put a weapon in someone's hands -- switching,
        shooting, reloading, throwing -- so a mode's loadout cannot be stepped
        around by sending the message for a weapon you were never given."""
        return weapon in loadout_for(self.mode)

    def do_shoot(self, p, weapon, origin, direction, seed, at_ms=None):
        if not p["alive"] or self.phase != "live":
            return
        w = CONFIG["WEAPONS"].get(weapon)
        if w is None or weapon == "grenade" or not self.can_use(weapon):
            return
        now = time.time()
        if now - p["last_shot"].get(weapon, 0.0) < w["rate"] * 0.85:
            return
        if w["mag"] > 0:
            if p["ammo"].get(weapon, 0) <= 0:
                return
            p["ammo"][weapon] -= 1
            self.send_to(p["id"], {"t": "you", "ammo": p["ammo"]})
        p["last_shot"][weapon] = now
        p["reloading"] = None
        # Keep the server's view of the held weapon in step with what is
        # actually being fired, so a reload can never target a different gun
        # than the one that just went empty.
        p["hand"] = weapon

        # Fire from the player's own eye, not the client-claimed origin, but
        # keep the claimed direction. Range/rate/ammo are ours; aim is theirs.
        eye = [p["pos"][0], p["pos"][1] + (CONFIG["EYE_CROUCH"] if p["crouch"]
                                           else CONFIG["EYE"]), p["pos"][2]]
        if v_dist(origin, eye) > 3.0:
            origin = eye
        d = v_norm(direction)

        # everyone but the shooter, rewound to what the shooter was looking at
        boxes = self.collision_boxes(exclude_pid=p["id"], include_players=True,
                                     at_ms=at_ms)
        if self.mode == "aim":
            boxes.extend(self.target_boxes())
        if self.dummies:
            boxes.extend(self.dummy_boxes())
        piece_lookup = {}
        for key, pc in self.pieces.items():
            for b in pc["boxes"]:
                b.ref = key
                piece_lookup[id(b)] = key

        rnd = mulberry32(seed)
        pellets = w["pellets"]
        spread = w["spread"]
        if p["vel"][0] ** 2 + p["vel"][2] ** 2 > 1.0:
            spread += w["move_spread"]
        if p["ads"]:
            spread *= 0.35

        results = []
        for _ in range(pellets):
            dd = d
            if spread > 0:
                ang = rnd() * math.pi * 2.0
                mag = math.sqrt(rnd()) * spread
                right = v_norm([d[2], 0.0, -d[0]])
                up = [right[1] * d[2] - right[2] * d[1],
                      right[2] * d[0] - right[0] * d[2],
                      right[0] * d[1] - right[1] * d[0]]
                dd = v_norm(v_add(d, v_add(v_scale(right, math.cos(ang) * mag),
                                           v_scale(up, math.sin(ang) * mag))))
            t, box = raycast(origin, dd, boxes, w["range"], skip_ref=p["id"])
            end = v_add(origin, v_scale(dd, t if t is not None else w["range"]))
            results.append((box, t, end))

        p["st"]["shots"] += 1
        landed = False
        for box, t, end in results:
            if box is None:
                continue
            if box.kind in ("body", "head"):
                target = self.players.get(box.ref)
                if target and target["alive"]:
                    landed = True
                    head = box.kind == "head"
                    dmg = w["dmg"] * (w["head_mult"] if head else 1.0)
                    self.apply_damage(target, dmg, p["id"], head, weapon)
                    self.send_to(p["id"], {"t": "hit", "target": box.ref,
                                           "dmg": round(dmg), "head": head,
                                           "pos": end})
            elif box.kind == "piece":
                self.damage_piece(box.ref, w["build_dmg"], p["id"])
                self.send_to(p["id"], {"t": "hit", "kind": "piece",
                                       "dmg": round(w["build_dmg"]), "pos": end})
            elif box.kind in ("dummy", "dummyhead"):
                head = box.kind == "dummyhead"
                landed = True            # a dummy is a target; it counts
                dmg = w["dmg"] * (w["head_mult"] if head else 1.0)
                self.hit_dummy(box.ref, dmg, head, p["id"], end)
                self.send_to(p["id"], {"t": "hit", "kind": "dummy", "target": box.ref,
                                       "dmg": round(dmg), "head": head, "pos": end})
            elif box.kind == "target":
                self.hit_target(box.ref, p)

        if landed:
            p["st"]["hits"] += 1

        self.broadcast({"t": "tracer", "by": p["id"], "w": weapon,
                        "o": [round(v, 2) for v in origin],
                        "seed": seed, "d": [round(v, 4) for v in d]},
                       exclude=p["id"])

    def melee(self, p, direction, at_ms=None):
        if not p["alive"] or self.phase != "live":
            return
        # The pickaxe is a weapon like any other, and a mode that does not hand
        # it out must not be reachable by sending `shoot` with w="pickaxe" --
        # do_shoot() is gated, so this has to be too or the gate has a door in it.
        if not self.can_use("pickaxe"):
            return
        w = CONFIG["WEAPONS"]["pickaxe"]
        now = time.time()
        if now - p["last_shot"].get("pickaxe", 0.0) < w["rate"] * 0.85:
            return
        p["last_shot"]["pickaxe"] = now
        eye_h = CONFIG["EYE_CROUCH"] if p["crouch"] else CONFIG["EYE"]
        eye = [p["pos"][0], p["pos"][1] + eye_h, p["pos"][2]]
        boxes = self.collision_boxes(exclude_pid=p["id"], include_players=True,
                                     at_ms=at_ms)
        if self.mode == "aim":
            boxes.extend(self.target_boxes())
        if self.dummies:
            boxes.extend(self.dummy_boxes())
        for key, pc in self.pieces.items():
            for b in pc["boxes"]:
                b.ref = key
        t, box = raycast(eye, v_norm(direction), boxes, w["range"], skip_ref=p["id"])
        if box is None:
            return
        end = v_add(eye, v_scale(v_norm(direction), t))
        if box.kind in ("body", "head"):
            target = self.players.get(box.ref)
            if target and target["alive"]:
                self.apply_damage(target, w["dmg"], p["id"], box.kind == "head", "pickaxe")
                self.send_to(p["id"], {"t": "hit", "target": box.ref,
                                       "dmg": round(w["dmg"]), "head": False, "pos": end})
        elif box.kind in ("dummy", "dummyhead"):
            self.hit_dummy(box.ref, w["dmg"], False, p["id"], end)
            self.send_to(p["id"], {"t": "hit", "kind": "dummy", "target": box.ref,
                                   "dmg": round(w["dmg"]), "head": False, "pos": end})
        elif box.kind == "piece":
            self.damage_piece(box.ref, w["build_dmg"], p["id"])
            self.send_to(p["id"], {"t": "hit", "kind": "piece",
                                   "dmg": round(w["build_dmg"]), "pos": end})
        elif box.kind == "target":
            self.hit_target(box.ref, p)
        elif box.kind == "arena":
            # Farming. Regen alone means materials are a function of time and
            # nothing else, so there is no reason to ever leave your box; a
            # pickaxe that pays makes the ground itself worth something.
            if self.mode != "build":
                p["st"]["farmed"] += CONFIG["MAT_PER_SWING"]
                p["mats"] = min(CONFIG["MAT_CAP"],
                                p["mats"] + CONFIG["MAT_PER_SWING"])
                self.send_to(p["id"], {"t": "you", "mats": p["mats"]})
            self.send_to(p["id"], {"t": "hit", "kind": "farm",
                                   "dmg": CONFIG["MAT_PER_SWING"], "pos": end})

    def throw_grenade(self, p, origin, direction):
        if not self.can_use("grenade"):
            return
        w = CONFIG["WEAPONS"]["grenade"]
        now = time.time()
        if now - p["last_shot"].get("grenade", 0.0) < w["rate"]:
            return
        p["last_shot"]["grenade"] = now
        gid = self.next_gid
        self.next_gid += 1
        d = v_norm(direction)
        self.grenades.append({
            "id": gid, "pos": list(origin), "owner": p["id"],
            "vel": v_scale(d, w["throw_speed"]),
            "boom_at": now + w["fuse"],
        })
        self.broadcast({"t": "gren", "id": gid, "pos": origin})

    def explode(self, g):
        w = CONFIG["WEAPONS"]["grenade"]
        r = w["radius"]
        self.broadcast({"t": "boom", "pos": g["pos"], "r": r})
        for p in self.players.values():
            if not p["alive"]:
                continue
            c = [p["pos"][0], p["pos"][1] + 0.9, p["pos"][2]]
            d = v_dist(c, g["pos"])
            if d > r:
                continue
            # line of sight, so a grenade doesn't damage through a floor
            dirv = v_norm(v_sub(c, g["pos"]))
            boxes = self.collision_boxes()
            t, _ = raycast(g["pos"], dirv, boxes, max(d - 0.4, 0.01))
            if t is not None:
                continue
            self.apply_damage(p, w["dmg"] * (1.0 - d / r), g["owner"], False, "grenade")
        for key in list(self.pieces.keys()):
            pc = self.pieces[key]
            c = [pc["cx"] * CELL + CELL / 2, pc["cy"] * CELL + CELL / 2,
                 pc["cz"] * CELL + CELL / 2]
            if v_dist(c, g["pos"]) <= r + CELL / 2:
                self.damage_piece(key, w["build_dmg"], g["owner"])

    # -- aim trainer ------------------------------------------------------
    def hit_target(self, tid, p):
        for i, t in enumerate(self.targets):
            if t["id"] == tid:
                st = self.aim_stats.setdefault(p["id"], {"hit": 0, "miss": 0, "rt": []})
                st["hit"] += 1
                st["rt"].append(time.time() - t["born"])
                self.targets.pop(i)
                self.broadcast({"t": "targetgone", "id": tid, "hit": True})
                self.send_to(p["id"], {"t": "aimstat", "s": self.aim_summary(p["id"])})
                return

    def aim_summary(self, pid):
        st = self.aim_stats.get(pid, {"hit": 0, "miss": 0, "rt": []})
        total = st["hit"] + st["miss"]
        acc = (100.0 * st["hit"] / total) if total else 0.0
        rt = (sum(st["rt"]) / len(st["rt"])) if st["rt"] else 0.0
        return {"hit": st["hit"], "miss": st["miss"], "acc": round(acc, 1),
                "rt": round(rt * 1000), "score": st["hit"] * 10 - st["miss"] * 3}

    # -- build trainer ----------------------------------------------------
    #
    # A course is a list of gates you can only reach by building, and the whole
    # score is how long it took. Per player, not per lobby: everyone runs the
    # same course at once on their own clock, so there is nothing to wait for
    # and nothing to take turns over.
    def trainer_reset(self, p, course=None):
        cfg = CONFIG["BUILD_TRAINER"]
        # `tr` exists but is None until the first run, so .get with a default
        # is not enough here -- the default only covers a MISSING key.
        name = course or (p.get("tr") or {}).get("course") or "ramp"
        if name not in cfg["courses"]:
            name = "ramp"
        p["tr"] = {"course": name, "gate": 0, "start": 0.0, "done": 0.0,
                   "best": (p.get("tr") or {}).get("best", {})}
        self.send_to(p["id"], {"t": "course", "name": name,
                               "label": cfg["courses"][name]["label"],
                               "desc": cfg["courses"][name]["desc"],
                               "gates": cfg["courses"][name]["gates"],
                               "gate": 0, "radius": cfg["radius"],
                               "best": p["tr"]["best"].get(name)})

    def tick_trainer(self, p, now):
        tr = p.get("tr")
        if not tr or not p["alive"]:
            return
        cfg = CONFIG["BUILD_TRAINER"]
        gates = cfg["courses"][tr["course"]]["gates"]
        if tr["gate"] >= len(gates):
            return
        g = gates[tr["gate"]]
        # measured from the chest, so standing under a gate does not count and
        # standing on the floor you just built to reach it does
        c = [p["pos"][0], p["pos"][1] + 0.9, p["pos"][2]]
        if v_dist(c, g) > cfg["radius"]:
            return
        if tr["gate"] == 0 and not tr["start"]:
            tr["start"] = now          # the clock starts at the FIRST gate
        tr["gate"] += 1
        if tr["gate"] >= len(gates):
            took = max(0.0, now - tr["start"]) if tr["start"] else 0.0
            tr["done"] = took
            prev = tr["best"].get(tr["course"])
            best = prev is None or took < prev
            if best:
                tr["best"][tr["course"]] = took
            self.send_to(p["id"], {"t": "coursedone", "time": round(took, 2),
                                   "best": best,
                                   "record": round(tr["best"][tr["course"]], 2)})
        else:
            self.send_to(p["id"], {"t": "gate", "gate": tr["gate"],
                                   "t0": tr["start"] and round(tr["start"] * 1000)})

    def tick_aim(self, now):
        cfg = CONFIG["AIM_TRAINER"]
        for t in list(self.targets):
            if now > t["born"] + cfg["lifetime"]:
                self.targets.remove(t)
                self.broadcast({"t": "targetgone", "id": t["id"], "hit": False})
                for p in self.players.values():
                    if p["bot"] or p["spectator"]:
                        continue
                    st = self.aim_stats.setdefault(p["id"], {"hit": 0, "miss": 0, "rt": []})
                    st["miss"] += 1
                    self.send_to(p["id"], {"t": "aimstat", "s": self.aim_summary(p["id"])})
        if len(self.targets) < cfg["count"] and now >= self.aim_next:
            self.aim_next = now + cfg["gap"]
            tid = self.next_tid
            self.next_tid += 1
            pos = [random.uniform(-22, 22), random.uniform(1.6, 7.0), random.uniform(-26, -6)]
            self.targets.append({"id": tid, "pos": pos, "born": now})
            self.broadcast({"t": "target", "id": tid, "pos": pos})

    # -- bots -------------------------------------------------------------
    def tick_bot(self, p, dt, now):
        cfg = CONFIG["BOT"][p["diff"]]
        sty = CONFIG["BOT_STYLE"].get(p.get("style", "balanced"),
                                      CONFIG["BOT_STYLE"]["balanced"])
        bt = p["bt"]
        if not p["alive"]:
            return
        boxes = self.collision_boxes(exclude_pid=p["id"])

        enemies = [q for q in self.players.values()
                   if q["id"] != p["id"] and q["alive"] and not q["spectator"]]
        target = None
        if enemies:
            target = min(enemies, key=lambda q: v_dist(q["pos"], p["pos"]))
        bt["target"] = target["id"] if target else None

        want = [0.0, 0.0, 0.0]
        eye = [p["pos"][0], p["pos"][1] + CONFIG["EYE"], p["pos"][2]]
        see = False
        if target:
            tc = [target["pos"][0], target["pos"][1] + 1.0, target["pos"][2]]
            dist = v_dist(tc, eye)
            dirv = v_norm(v_sub(tc, eye))
            t, box = raycast(eye, dirv, boxes, dist - 0.3)
            see = t is None
            if see:
                bt["seen_at"] = now
                # aim with a capped turn rate so it swings on rather than snapping
                want_yaw = math.degrees(math.atan2(-dirv[0], -dirv[2]))
                want_pitch = math.degrees(math.asin(max(-1.0, min(1.0, dirv[1]))))
                dy = (want_yaw - p["yaw"] + 540.0) % 360.0 - 180.0
                maxturn = cfg["turn"] * 60.0 * dt
                p["yaw"] += max(-maxturn, min(maxturn, dy))
                p["pitch"] += max(-maxturn, min(maxturn, want_pitch - p["pitch"]))

                if now >= bt["next_fire"] and now - bt["seen_at"] >= 0.0:
                    bt["next_fire"] = now + max(0.09, cfg["react"] * 0.5)
                    err = cfg["err"]
                    shot = v_norm([dirv[0] + random.uniform(-err, err),
                                   dirv[1] + random.uniform(-err, err),
                                   dirv[2] + random.uniform(-err, err)])
                    weapon = "ar" if dist > 12 else "shotgun"
                    if dist > 45:
                        weapon = "sniper"
                    p["hand"] = weapon
                    if p["ammo"].get(weapon, 0) <= 0:
                        p["ammo"][weapon] = CONFIG["WEAPONS"][weapon]["mag"]
                    self.do_shoot(p, weapon, eye, shot, random.getrandbits(32))

                # close distance when pushing, back off otherwise. Style is
                # what decides the range it wants to fight at -- a rusher lives
                # in your face, a turtle will not come off its wall.
                push = min(1.0, cfg["push"] * sty["push"])
                desired = (8.0 if random.random() < push else 18.0) * sty["range"]
                if p["hp"] < 45 and random.random() < sty["retreat"]:
                    desired += 12.0        # hurt, and this one values its life
                move = v_sub(tc, p["pos"])
                move[1] = 0.0
                if v_len(move) > 0.1:
                    fwd = v_norm(move)
                    sign = 1.0 if dist > desired else -1.0
                    strafe = v_norm([fwd[2], 0.0, -fwd[0]])
                    ph = math.sin(now * 1.7 + p["id"])
                    want = v_add(v_scale(fwd, sign), v_scale(strafe, ph * 0.8))
            else:
                move = v_sub(target["pos"], p["pos"])
                move[1] = 0.0
                if v_len(move) > 0.1:
                    want = v_norm(move)

        # panic-wall when recently hurt, ramp for height when it wants an angle
        if now >= bt["next_build"] and self.mode != "build":
            hurt = now - p["last_dmg_at"] < 1.2
            urge = 0.25 * sty["build"]
            if hurt or (see and random.random() < urge):
                bt["next_build"] = now + cfg["build_cd"] / max(0.2, sty["build"])
                yaw = math.radians(p["yaw"])
                fwd = [-math.sin(yaw), 0.0, -math.cos(yaw)]
                ahead = v_add(p["pos"], v_scale(fwd, CELL * 0.6))
                cx = int(math.floor(ahead[0] / CELL))
                cy = int(math.floor(p["pos"][1] / CELL))
                cz = int(math.floor(ahead[2] / CELL))
                d = self.dir_from_vec(fwd)
                # hurt -> cover. Otherwise the style decides whether it
                # walls up or takes height.
                if hurt:
                    self.place(p, "wall" if random.random() < sty["wall_first"]
                               else "ramp", cx, cy, cz, d)
                elif random.random() < sty["ramp"]:
                    self.place(p, "ramp", cx, cy, cz, d)
                else:
                    self.place(p, "wall", cx, cy, cz, d)

        # stuck detection: steering, not pathfinding, so it will wedge
        if v_dist(p["pos"], bt["last_pos"]) < 0.06 and v_len(want) > 0.1:
            bt["stuck_t"] += dt
        else:
            bt["stuck_t"] = 0.0
        bt["last_pos"] = list(p["pos"])
        if bt["stuck_t"] > 1.5:
            bt["stuck_t"] = 0.0
            if p["grounded"]:
                p["vel"][1] = CONFIG["JUMP"]
            yaw = math.radians(p["yaw"])
            fwd = [-math.sin(yaw), 0.0, -math.cos(yaw)]
            cx = int(math.floor((p["pos"][0] + fwd[0] * 2) / CELL))
            cy = int(math.floor(p["pos"][1] / CELL))
            cz = int(math.floor((p["pos"][2] + fwd[2] * 2) / CELL))
            self.place(p, "ramp", cx, cy, cz, self.dir_from_vec(fwd))

        if not enemies:
            if now > bt.get("wander_at", 0.0):
                bt["wander_at"] = now + 2.5
                bt["wander"] = v_norm([random.uniform(-1, 1), 0, random.uniform(-1, 1)])
            want = bt["wander"]

        # integrate with the shared controller
        speed = CONFIG["SPEED"]
        if v_len(want) > 0.001:
            want = v_norm(want)
        target_v = v_scale(want, speed)
        accel = CONFIG["ACCEL"] * dt
        for ax in (0, 2):
            diff = target_v[ax] - p["vel"][ax]
            p["vel"][ax] += max(-accel, min(accel, diff))
        if abs(want[0]) < 0.01 and abs(want[2]) < 0.01:
            damp = max(0.0, 1.0 - CONFIG["FRICTION"] * dt)
            p["vel"][0] *= damp
            p["vel"][2] *= damp

        if unstick(p["pos"], p["crouch"], boxes) and p["vel"][1] < 0:
            p["vel"][1] = 0.0

        sub = CONFIG["SUBSTEP"]
        left = dt
        while left > 1e-6:
            h = min(sub, left)
            left -= h
            p["grounded"], _ = step_move(p["pos"], p["vel"], p["crouch"],
                                         p["grounded"], h, boxes)
        if p["pos"][1] < CONFIG["KILL_Y"]:
            self.kill_player(p, p["last_dmg_from"] or p["id"])

    def dir_from_vec(self, v):
        if abs(v[0]) > abs(v[2]):
            return 1 if v[0] > 0 else 3
        return 2 if v[2] > 0 else 0

    # -- main tick --------------------------------------------------------
    def step(self, dt):
        now = time.time()

        if self.phase == "countdown" and now >= self.phase_until:
            self.phase = "live"
            self.broadcast({"t": "phase", "phase": "live"})

        self.sweep_offline(now)

        # material regen
        if self.mode in ("duel", "dm"):
            for p in self.players.values():
                if p["alive"] and now - p["last_build"] > CONFIG["MAT_REGEN_DELAY"]:
                    if p["mats"] < CONFIG["MAT_CAP"]:
                        p["mats"] = min(CONFIG["MAT_CAP"],
                                        p["mats"] + CONFIG["MAT_REGEN"] * dt)

        # reloads
        for p in self.players.values():
            if p["reloading"] and now >= p["reload_until"]:
                w = CONFIG["WEAPONS"][p["reloading"]]
                p["ammo"][p["reloading"]] = w["mag"]
                self.send_to(p["id"], {"t": "you", "ammo": p["ammo"]})
                p["reloading"] = None

        # respawns (deathmatch)
        if self.mode == "dm" and self.phase == "live":
            for p in self.players.values():
                if not p["alive"] and not p["spectator"] and p["respawn_at"] and now >= p["respawn_at"]:
                    p["respawn_at"] = 0.0
                    self.spawn(p)

        # bots
        if self.phase == "live":
            for p in list(self.players.values()):
                if p["bot"]:
                    self.tick_bot(p, dt, now)

        # grenades
        if self.grenades:
            boxes = self.collision_boxes()
            for g in list(self.grenades):
                g["vel"][1] -= CONFIG["GRENADE_GRAVITY"] * dt
                for ax in range(3):
                    step = g["vel"][ax] * dt
                    g["pos"][ax] += step
                    r = 0.18
                    lo = [g["pos"][0] - r, g["pos"][1] - r, g["pos"][2] - r]
                    hi = [g["pos"][0] + r, g["pos"][1] + r, g["pos"][2] + r]
                    hit = _overlap_any(lo, hi, boxes)
                    if hit:
                        g["pos"][ax] -= step
                        g["vel"][ax] *= -CONFIG["GRENADE_BOUNCE"]
                if now >= g["boom_at"] or g["pos"][1] < CONFIG["KILL_Y"]:
                    self.grenades.remove(g)
                    if g["pos"][1] > CONFIG["KILL_Y"]:
                        self.explode(g)

        # aim trainer
        if self.mode == "aim" and self.phase == "live":
            self.tick_aim(now)

        # build trainer
        if self.mode == "trainer" and self.phase == "live":
            for p in self.players.values():
                if not p["bot"]:
                    self.tick_trainer(p, now)

        if self.dummies:
            self.tick_dummies(now)

        # fall-off deaths for humans (their client stops sending once dead)
        for p in self.players.values():
            if p["alive"] and not p["bot"] and p["pos"][1] < CONFIG["KILL_Y"]:
                self.kill_player(p, p["last_dmg_from"] or p["id"])

        # snapshot
        self.tick += 1
        ps = []
        for p in self.players.values():
            flags = (1 if p["alive"] else 0) | (2 if p["crouch"] else 0) | \
                    (4 if p["grounded"] else 0) | (8 if p["ads"] else 0)
            ps.append([p["id"],
                       round(p["pos"][0], 3), round(p["pos"][1], 3), round(p["pos"][2], 3),
                       round(p["yaw"], 1), round(p["pitch"], 1), flags,
                       round(p["hp"]), round(p["shield"]),
                       CONFIG["LOADOUT"].index(p["hand"]) if p["hand"] in CONFIG["LOADOUT"] else -1])
        now_ms = round(now * 1000)
        # Sampled here, with the very number the clients interpolate against,
        # so a shot's `at` lands on this timeline exactly.
        self.record_history(now_ms)
        msg = {"t": "state", "k": self.tick, "ts": now_ms, "ps": ps}
        if self.grenades:
            msg["gs"] = [[g["id"], round(g["pos"][0], 2), round(g["pos"][1], 2),
                          round(g["pos"][2], 2)] for g in self.grenades]
        self.broadcast(msg, droppable=True)

    # -- message handling -------------------------------------------------
    def handle(self, pid, m):
        p = self.players.get(pid)
        if p is None:
            return
        t = m.get("t")

        if t == "ping":
            self.send_to(pid, {"t": "pong", "c": m.get("c")})
            return

        if t == "input":
            if not p["alive"]:
                return
            pos = m.get("p")
            if not (isinstance(pos, list) and len(pos) == 3):
                return
            try:
                pos = [float(v) for v in pos]
            except (TypeError, ValueError):
                return
            if any(math.isnan(v) or math.isinf(v) for v in pos):
                return
            # plausibility clamp: reject impossible deltas, keep the old position
            maxd = CONFIG["SPEED"] * 3.0 * 0.25 + 6.0
            if v_dist(pos, p["pos"]) > maxd:
                self.send_to(pid, {"t": "correct", "pos": p["pos"]})
            else:
                p["pos"] = pos
            v = m.get("v") or [0, 0, 0]
            p["vel"] = [float(v[0]), float(v[1]), float(v[2])]
            p["yaw"] = float(m.get("yaw", p["yaw"]))
            p["pitch"] = float(m.get("pitch", p["pitch"]))
            p["crouch"] = bool(m.get("cr"))
            p["grounded"] = bool(m.get("g"))
            p["ads"] = bool(m.get("ads"))
            return

        if t == "shoot":
            if self.phase != "live":
                return
            w = m.get("w")
            if w == "pickaxe":
                self.melee(p, m.get("d") or [0, 0, 1], self.shot_time(m))
                return
            self.do_shoot(p, w, m.get("o") or p["pos"], m.get("d") or [0, 0, 1],
                          int(m.get("seed", 0)) & 0xFFFFFFFF, self.shot_time(m))
            return

        if t == "grenade":
            if self.phase == "live" and p["alive"]:
                self.throw_grenade(p, m.get("o") or p["pos"], m.get("d") or [0, 0, 1])
            return

        if t == "build":
            if self.phase != "live" or not p["alive"]:
                return
            pt = m.get("pt")
            if pt not in CONFIG["BUILD_PIECES"]:
                return
            try:
                cx, cy, cz = int(m["cx"]), int(m["cy"]), int(m["cz"])
                d = int(m.get("dir", 0)) & 3
            except (KeyError, TypeError, ValueError):
                return
            self.place(p, pt, cx, cy, cz, d)
            return

        if t == "edit":
            if p["alive"]:
                self.edit_piece(p, str(m.get("key", "")), int(m.get("mask", FULL_MASK)))
            return

        if t == "switch":
            h = m.get("hand")
            if self.can_use(h) or h in CONFIG["BUILD_PIECES"]:
                p["hand"] = h
                p["reloading"] = None
            return

        if t == "reload":
            h = m.get("w") if m.get("w") in CONFIG["WEAPONS"] else p["hand"]
            w = CONFIG["WEAPONS"].get(h) if self.can_use(h) else None
            if w and w["mag"] > 0 and p["ammo"].get(h, 0) < w["mag"] and not p["reloading"]:
                p["reloading"] = h
                p["reload_until"] = time.time() + w["reload"]
                self.send_to(pid, {"t": "reloading", "w": h, "for": w["reload"]})
            return

        if t == "chat":
            txt = str(m.get("m", ""))[:140]
            self.broadcast({"t": "chat", "from": p["name"], "m": txt})
            return

        # ---- host-only ----
        if pid != self.host:
            return

        if t == "setmode":
            mode = m.get("mode")
            if mode in ("duel", "team", "dm", "build", "aim", "trainer"):
                self.mode = mode
                self.broadcast({"t": "mode", "mode": mode, "phase": self.phase})
            return

        if t == "addbot":
            diff = m.get("diff", "medium")
            if diff not in CONFIG["BOT"]:
                diff = "medium"
            style = m.get("style", "balanced")
            if style not in CONFIG["BOT_STYLE"]:
                style = "balanced"
            n = len([q for q in self.players.values() if q["bot"]]) + 1
            label = CONFIG["BOT_STYLE"][style]["label"]
            b = self.new_player("Bot %d (%s %s)" % (n, diff, label),
                                is_bot=True, diff=diff, style=style)
            self.broadcast({"t": "join", "p": self.public_player(b)})
            return

        if t == "course":
            if self.mode == "trainer":
                self.trainer_reset(p, m.get("name"))
            return

        if t == "setmap":
            want = m.get("map")
            if want == "auto" or want in ARENAS:
                self.map_choice = want
                self.broadcast({"t": "map", "map": want})
            return

        if t == "kickbots":
            for bid in [q["id"] for q in self.players.values() if q["bot"]]:
                self.remove_player(bid)
            return

        if t == "start":
            mode = m.get("mode", self.mode)
            if mode not in ("duel", "team", "dm", "build", "aim", "trainer"):
                return
            if mode == "duel" and len(self.players) != 2:
                self.send_to(pid, {"t": "err",
                                   "m": "Duel needs exactly 2 players. Add a bot or switch mode."})
                return
            if mode == "team" and len(self.players) < 3:
                self.send_to(pid, {"t": "err",
                                   "m": "Team mode needs at least 3 players. Add bots."})
                return
            self.start_match(mode)
            return

        if t == "tolobby":
            self.to_lobby()
            return


GAME = Game()


# ---------------------------------------------------------------------------
# HTTP + WebSocket handler
# ---------------------------------------------------------------------------
STATIC_OK = {".html", ".js", ".css", ".png", ".jpg", ".svg", ".ico", ".json", ".map"}

# Browsers request /favicon.ico unconditionally; serving a real one keeps the
# console clean instead of a stream of 404s.
FAVICON_BYTES = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    b'<rect width="32" height="32" rx="6" fill="#12161f"/>'
    b'<path d="M6 22 L16 8 L26 22 Z" fill="none" stroke="#4da3ff" stroke-width="3"/>'
    b'<circle cx="16" cy="18" r="2.5" fill="#ff5470"/></svg>'
)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self._handle()
        except (WSError, socket.timeout, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            traceback.print_exc()

    # -- request parsing ---------------------------------------------------
    def _read_request(self):
        self._leftover = b""
        buf = bytearray()
        self.request.settimeout(15.0)
        while b"\r\n\r\n" not in buf:
            chunk = self.request.recv(4096)
            if not chunk:
                return None, None, None
            buf.extend(chunk)
            if len(buf) > 65536:
                return None, None, None
        head, rest = bytes(buf).split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return None, None, None
        method, path = parts[0], parts[1]
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        # Anything already read past the blank line belongs to the next layer.
        # A client may pack its first WebSocket frame into the same TCP segment
        # as the upgrade request; dropping those bytes loses the frame and
        # desyncs the stream permanently.
        self._leftover = rest
        return method, path, headers

    def _handle(self):
        method, path, headers = self._read_request()
        if method is None:
            return
        if headers.get("upgrade", "").lower() == "websocket":
            self._websocket(headers)
        else:
            self._static(method, path)

    # -- static files ------------------------------------------------------
    def _send_http(self, code, reason, body=b"", ctype="text/plain; charset=utf-8",
                   extra=None, head_only=False):
        hdrs = [
            "HTTP/1.1 %d %s" % (code, reason),
            "Content-Type: %s" % ctype,
            "Content-Length: %d" % len(body),
            "Connection: close",
        ]
        if extra:
            hdrs.extend(extra)
        out = ("\r\n".join(hdrs) + "\r\n\r\n").encode("utf-8")
        if not head_only:
            out += body
        try:
            self.request.sendall(out)
        except OSError:
            pass

    def _static(self, method, path):
        # HEAD matters: `curl -I` is how the server gets smoke-tested, and a
        # GET-only server fails that check for reasons unrelated to any bug.
        if method not in ("GET", "HEAD"):
            self._send_http(405, "Method Not Allowed", b"method not allowed")
            return
        head_only = (method == "HEAD")

        rel = path.split("?", 1)[0].split("#", 1)[0]
        if rel in ("/", ""):
            rel = "/index.html"
        try:
            from urllib.parse import unquote
            rel = unquote(rel)
        except Exception:
            pass

        # Identity endpoint. A launcher that finds the port busy uses this to
        # tell "an older copy of me" apart from some unrelated program, so it
        # can offer to take the port over instead of just giving up.
        if rel == "/whoami":
            body = json.dumps({"app": "build-fighter", "build": BUILD_ID,
                               "pid": os.getpid()}).encode()
            self._send_http(200, "OK", body, "application/json",
                            head_only=head_only)
            return

        if rel == "/favicon.ico":
            self._send_http(200, "OK", FAVICON_BYTES, "image/svg+xml",
                            head_only=head_only)
            return

        # Traversal defense: resolve, then require the result to still be inside
        # HERE. A naive `".." in path` check misses encoded and symlinked forms.
        target = os.path.realpath(os.path.join(HERE, rel.lstrip("/")))
        if target != HERE and not target.startswith(HERE + os.sep):
            self._send_http(403, "Forbidden", b"forbidden")
            return
        ext = os.path.splitext(target)[1].lower()
        if ext not in STATIC_OK or not os.path.isfile(target):
            self._send_http(404, "Not Found", b"not found")
            return

        ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if ext == ".js":
            ctype = "application/javascript"
        try:
            with open(target, "rb") as f:
                body = f.read()
        except OSError:
            self._send_http(404, "Not Found", b"not found")
            return

        # index.html must never be served stale, or an edit is invisible to
        # whoever already loaded the page once.
        extra = ["Cache-Control: no-store"] if ext == ".html" else \
                ["Cache-Control: public, max-age=86400"]
        self._send_http(200, "OK", body, ctype, extra, head_only=head_only)

    # -- websocket ---------------------------------------------------------
    def _websocket(self, headers):
        key = headers.get("sec-websocket-key")
        if not key or headers.get("sec-websocket-version") != "13":
            self._send_http(400, "Bad Request", b"bad websocket request")
            return
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode("utf-8")).digest()).decode("ascii")
        resp = ("HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                "Sec-WebSocket-Accept: %s\r\n\r\n" % accept)
        self.request.sendall(resp.encode("utf-8"))

        sock = self.request
        sock.settimeout(10.0)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass

        reader = FrameReader(sock)
        reader.buf.extend(getattr(self, "_leftover", b""))
        pid = None
        client = None
        last_rx = time.time()
        try:
            while True:
                try:
                    op, data = reader.read_message()
                except socket.timeout:
                    # Buffer is preserved; just check liveness and resume.
                    if time.time() - last_rx > 30.0:
                        break
                    try:
                        sock.sendall(ws_frame(b"", OP_PING))
                    except OSError:
                        break
                    continue
                last_rx = time.time()

                if op == OP_CLOSE:
                    break
                if op == OP_PING:
                    sock.sendall(ws_frame(data, OP_PONG))
                    continue
                if op == OP_PONG:
                    continue
                if op != OP_TEXT:
                    continue

                try:
                    msg = json.loads(data.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(msg, dict):
                    continue

                with GAME.lock:
                    if pid is None:
                        if msg.get("t") != "hello":
                            continue
                        name = str(msg.get("name", "Player"))[:16].strip() or "Player"
                        # A token means "I was already here" -- see reclaim().
                        addr = self.client_address[0]
                        p, client, _ = GAME.attach(
                            lambda i: Client(sock, self.client_address, i),
                            name, msg.get("token"), " from %s" % addr)
                        pid = p["id"]
                        continue
                    try:
                        GAME.handle(pid, msg)
                    except Exception:
                        traceback.print_exc()
        finally:
            with GAME.lock:
                if pid is not None:
                    # Hold the slot rather than deleting it. A dropped
                    # connection and a player leaving look identical down here,
                    # and only one of them should cost someone their match.
                    GAME.detach(pid)
            if client:
                client.kill()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


# ---------------------------------------------------------------------------
def lan_ip():
    """Routing-table lookup. gethostbyname(gethostname()) returns 127.0.0.1 on
    macOS, which would print a URL the friend can't use."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))       # no packet is actually sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def tick_loop(stop):
    last = time.time()
    while not stop.is_set():
        now = time.time()
        dt = now - last
        last = now
        if dt > 0.5:
            dt = 0.5
        try:
            with GAME.lock:
                GAME.step(dt)
        except Exception:
            traceback.print_exc()
        slp = TICK_DT - (time.time() - now)
        if slp > 0:
            stop.wait(slp)


def whos_on_port(port, timeout=1.5):
    """Ask whatever holds `port` whether it is a copy of this server.

    Returns its {build, pid} or None. Identity comes from the process itself
    over HTTP rather than from a PID lookup, so we can never mistake an
    unrelated program for our own and stop it.
    """
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except OSError:
        return None
    try:
        c.settimeout(timeout)
        c.sendall(b"GET /whoami HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                  b"Connection: close\r\n\r\n")
        buf = b""
        while len(buf) < 65536:
            chunk = c.recv(4096)
            if not chunk:
                break
            buf += chunk
    except OSError:
        return None
    finally:
        try:
            c.close()
        except OSError:
            pass
    if b"\r\n\r\n" not in buf:
        return None
    try:
        info = json.loads(buf.split(b"\r\n\r\n", 1)[1].decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(info, dict) or info.get("app") != "build-fighter":
        return None
    if not isinstance(info.get("pid"), int):
        return None
    return info


def _http_get(port, path, timeout=1.5):
    try:
        c = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except OSError:
        return None
    try:
        c.settimeout(timeout)
        c.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                   "Connection: close\r\n\r\n" % path).encode())
        buf = b""
        while len(buf) < 400000:
            chunk = c.recv(8192)
            if not chunk:
                break
            buf += chunk
        return buf
    except OSError:
        return None
    finally:
        try:
            c.close()
        except OSError:
            pass


def pid_on_port(port):
    """The listening pid, via lsof. Only used as a fallback for a copy of this
    server old enough to predate /whoami."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
            capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    pids = [int(x) for x in out.split() if x.strip().isdigit()]
    return pids[0] if pids else None


def occupant(port):
    """Who holds `port`? Returns {"pid","build"} for a copy of this server.

    A version new enough to answer /whoami identifies itself outright. An older
    one does not have that endpoint, so it is recognised by the page it serves
    -- which is exactly the case that matters, because an old process is the one
    running stale rules.
    """
    info = whos_on_port(port)
    if info:
        return info
    body = _http_get(port, "/")
    if not body or b'id="buildid"' not in body:
        return None
    pid = pid_on_port(port)
    if pid is None:
        return None
    return {"pid": pid, "build": None}


def stop_pid(pid, timeout=6.0):
    """SIGTERM, wait for the port owner to actually exit, then SIGKILL."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.15)
        try:
            os.kill(pid, 0)
        except OSError:
            return True
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.4)
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Build-Fighter game server")
    # NOT 8080: on this machine something intercepts that port -- plain HTTP
    # passes but the WebSocket upgrade is broken, which looks like the game
    # loading and then hanging at "Connecting...". 8080 is a common transparent
    # proxy port. Use --port 8080 only if you know yours is clear.
    ap.add_argument("--port", type=int, default=7777)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--takeover", action="store_true",
                    help="if an older copy of THIS server holds the port, stop it "
                         "and take over (never touches any other program)")
    args = ap.parse_args()

    if not os.path.isfile(os.path.join(HERE, "index.html")):
        print("!! index.html is missing from %s" % HERE)
        return 1

    srv = None
    for attempt in (1, 2):
        try:
            srv = Server((args.host, args.port), Handler)
            break
        except OSError as e:
            if e.errno not in (errno.EADDRINUSE, 48):
                raise
            other = occupant(args.port)
            if other is None:
                print("!! Port %d is already in use by something that is not this"
                      % args.port)
                print("   game. Try:  python3 server.py --port %d" % (args.port + 1))
                return 1
            same = (other.get("build") == BUILD_ID)
            print("!! Port %d is held by %s copy of this server (pid %d, %s)."
                  % (args.port,
                     "another" if same else "an OLDER",
                     other["pid"], other.get("build") or "version unknown - predates build stamps"))
            if not args.takeover or attempt == 2:
                print("   That old process is still running the OLD game rules, so")
                print("   editing server.py changes nothing until it is stopped.")
                print("   Stop it with:   kill %d" % other["pid"])
                print("   Or start this one anyway on another port:")
                print("                   python3 server.py --port %d" % (args.port + 1))
                return 1
            print("   Stopping it and taking the port over...")
            if not stop_pid(other["pid"]):
                print("   !! Could not stop pid %d. Run:  kill -9 %d"
                      % (other["pid"], other["pid"]))
                return 1
            print("   Stopped. Starting fresh.")
    if srv is None:
        return 1

    stop = threading.Event()
    ticker = threading.Thread(target=tick_loop, args=(stop,), daemon=True)
    ticker.start()

    ip = lan_ip()
    print("")
    print("  BUILD-FIGHTER server running")
    print("  " + BUILD_ID)
    print("  " + "-" * 46)
    print("  You:          http://localhost:%d" % args.port)
    print("  Your friend:  http://%s:%d" % (ip, args.port))
    print("")
    print("  Your friend must be on the same Wi-Fi / hotspot.")
    print("  macOS may ask to allow incoming connections -- click Allow.")
    print("  Ctrl-C to stop.")
    print("")

    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2},
                         daemon=True)
    t.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  shutting down...")
    finally:
        stop.set()
        srv.shutdown()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
