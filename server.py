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

HERE = os.path.dirname(os.path.abspath(__file__))
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Bumped on every change to the rules the client mirrors. The client carries the
# same stamp and shouts if the two disagree -- editing index.html and forgetting
# to restart server.py leaves the old rules in charge, and the symptom (pieces
# floating that the ghost said were illegal) looks exactly like a code bug.
BUILD_ID = "BUILD 2026-09-01 20:40"
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
    "PIECE_HP": 150.0,
    "PIECE_COST": 10,
    "MAT_CAP": 999,
    "MAT_START": 500,
    "MAT_REGEN": 12.0,       # per second
    "MAT_REGEN_DELAY": 3.0,  # seconds after your last placement
    "PICKAXE_REFUND": 5,
    "BUILD_REACH": 12.0,
    "EDIT_REACH": 8.0,
    "TURBO_RATE": 9.0,       # pieces per second while holding LMB
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

    # --- camera ---
    "FOV": 75.0,
    "FOV_ADS": 55.0,
    "FOV_SCOPE": 22.0,
    "CAM_OFF": [0.55, 1.55, -3.2],
    "CAM_OFF_ADS": [0.35, 1.62, -1.1],

    "WEAPONS": {
        "pickaxe": {
            "slot": 1, "name": "Pickaxe", "dmg": 25.0, "build_dmg": 100.0,
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
    "LOADOUT": ["pickaxe", "ar", "shotgun", "sniper", "grenade"],
    "BUILD_PIECES": ["wall", "ramp", "floor", "roof"],

    "GRENADE_GRAVITY": 26.0,
    "GRENADE_BOUNCE": 0.35,

    "BOT": {
        "easy":   {"react": 0.40, "err": 0.075, "turn": 3.0, "build_cd": 2.2, "push": 0.25, "burst": 4},
        "medium": {"react": 0.22, "err": 0.035, "turn": 5.5, "build_cd": 1.1, "push": 0.55, "burst": 7},
        "hard":   {"react": 0.11, "err": 0.014, "turn": 9.0, "build_cd": 0.55, "push": 0.85, "burst": 12},
    },

    "AIM_TRAINER": {"lifetime": 2.6, "gap": 0.35, "count": 3, "radius": 0.55},
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
ARENA = [
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

SPAWNS = [
    [-32.0, 0.5, -32.0, 45.0],
    [32.0, 0.5, 32.0, 225.0],
    [-32.0, 0.5, 32.0, 135.0],
    [32.0, 0.5, -32.0, 315.0],
    [0.0, 0.5, -34.0, 0.0],
    [0.0, 0.5, 34.0, 180.0],
]

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
FULL_MASK = 0b111111111


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


def tile_boxes(ptype, cx, cy, cz, direction, mask):
    """Collision boxes for a piece, honouring its 3x3 edit mask."""
    x0, y0, z0 = cx * CELL, cy * CELL, cz * CELL
    third = CELL / 3.0
    out = []

    if ptype in ("wallX", "wallZ"):
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
        for r in range(3):          # r spans Z, c spans X
            for c in range(3):
                if not (mask >> (r * 3 + c)) & 1:
                    continue
                out.append(Box([x0 + c * third, y0 - WALL_T / 2, z0 + r * third],
                               [x0 + (c + 1) * third, y0 + WALL_T / 2, z0 + (r + 1) * third],
                               "piece"))
        return out

    if ptype == "ramp":
        # Renders as a smooth slanted slab, collides as 8 stair treads. With
        # step-up in the controller, walking a ramp falls out of plain AABB
        # resolution -- no slope normals, no sliding, no seam-sticking.
        #
        # Each tread is only RAMP_T thick instead of reaching all the way down
        # to the cell floor, so a ramp is a slanted wall you can walk under --
        # not a solid wedge that fills the whole cell.
        steps = 8
        d = CELL / steps
        rt = CONFIG["RAMP_T"]
        for i in range(steps):
            h = (i + 1) * (CELL / steps)
            ylo = max(y0, y0 + h - rt)
            yhi = y0 + h
            if direction == 0:      # rises toward -Z
                lo = [x0, ylo, z0 + CELL - (i + 1) * d]
                hi = [x0 + CELL, yhi, z0 + CELL - i * d]
            elif direction == 2:    # rises toward +Z
                lo = [x0, ylo, z0 + i * d]
                hi = [x0 + CELL, yhi, z0 + (i + 1) * d]
            elif direction == 1:    # rises toward +X
                lo = [x0 + i * d, ylo, z0]
                hi = [x0 + (i + 1) * d, yhi, z0 + CELL]
            else:                   # rises toward -X
                lo = [x0 + CELL - (i + 1) * d, ylo, z0]
                hi = [x0 + CELL - i * d, yhi, z0 + CELL]
            out.append(Box(lo, hi, "piece"))
        return out

    if ptype == "roof":
        levels = 4
        rh = CONFIG["ROOF_H"]
        for i in range(levels):
            inset = i * (CELL / (2.0 * levels))
            ylo = y0 + i * (rh / levels)
            yhi = y0 + (i + 1) * (rh / levels)
            out.append(Box([x0 + inset, ylo, z0 + inset],
                           [x0 + CELL - inset, yhi, z0 + CELL - inset],
                           "piece"))
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
            trial = list(pos)
            trial[axis] = before
            trial[1] += step_up
            if _overlap_any(*player_aabb(trial, crouch), boxes=boxes) is None:
                if not move_axis(trial, crouch, d, axis, boxes):
                    down = list(trial)
                    move_axis(down, crouch, -step_up, 1, boxes)
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
        self.tick = 0
        self.aim_stats = {}
        self.aim_next = 0.0
        self.winner = None

    # -- helpers ----------------------------------------------------------
    def new_player(self, name, is_bot=False, diff="medium"):
        pid = self.next_id
        self.next_id += 1
        p = {
            "id": pid, "name": name, "bot": is_bot, "diff": diff,
            "pos": [0.0, 1.0, 0.0], "vel": [0.0, 0.0, 0.0],
            "yaw": 0.0, "pitch": 0.0, "crouch": False, "grounded": True,
            "hp": CONFIG["HP_MAX"], "shield": CONFIG["SHIELD_MAX"],
            "mats": CONFIG["MAT_START"], "alive": False, "spectator": False,
            "hand": "ar", "ads": False,
            "ammo": {w: CONFIG["WEAPONS"][w]["mag"] for w in CONFIG["LOADOUT"]},
            "last_shot": {}, "reload_until": 0.0, "reloading": None,
            "kills": 0, "deaths": 0,
            "respawn_at": 0.0, "protect_until": 0.0, "last_build": 0.0,
            "last_dmg_from": None, "last_dmg_at": 0.0,
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
                "spectator": p["spectator"], "pos": p["pos"], "yaw": p["yaw"]}

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
    def collision_boxes(self, exclude_pid=None, include_players=False):
        boxes = list(ARENA_BOXES)
        for key, pc in self.pieces.items():
            boxes.extend(pc["boxes"])
        if include_players:
            for p in self.players.values():
                if not p["alive"] or p["id"] == exclude_pid:
                    continue
                boxes.extend(self.hitboxes(p))
        return boxes

    def hitboxes(self, p):
        w = CONFIG["P_W"]
        h = CONFIG["P_H_CROUCH"] if p["crouch"] else CONFIG["P_H"]
        hh = CONFIG["HEAD_H"]
        pos = p["pos"]
        body = Box([pos[0] - w / 2, pos[1], pos[2] - w / 2],
                   [pos[0] + w / 2, pos[1] + h - hh, pos[2] + w / 2],
                   "body", p["id"])
        head = Box([pos[0] - hh / 2, pos[1] + h - hh, pos[2] - hh / 2],
                   [pos[0] + hh / 2, pos[1] + h, pos[2] + hh / 2],
                   "head", p["id"])
        return [body, head]

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

    def place(self, p, ptype, cx, cy, cz, direction):
        """Validate + place. Returns the piece dict, or None with a reason."""
        if ptype == "wall":
            ptype, cx, cy, cz = canon_wall(cx, cy, cz, direction)
        if not self.in_bounds(cx, cy, cz):
            return None
        key = piece_key(ptype, cx, cy, cz)
        if key in self.pieces:
            return None
        if ptype in ("ramp", "roof") and self.cell_volume_taken(cx, cy, cz):
            return None

        infinite = (self.mode == "build")
        cost = CONFIG["PIECE_COST"]
        if not infinite and p["mats"] < cost:
            return None

        boxes = tile_boxes(ptype, cx, cy, cz, direction, FULL_MASK)
        # Never trap a living player inside a piece -- but ignore the bottom of
        # the body box, so dropping a floor at your own feet still works the way
        # it does in the games this borrows from.
        for other in self.players.values():
            if not other["alive"]:
                continue
            lo, hi = player_aabb(other["pos"], other["crouch"])
            lo = [lo[0], lo[1] + 0.35, lo[2]]
            for b in boxes:
                if b.overlaps(lo, hi):
                    return None

        # No floating builds: it must rest on the arena or touch something that
        # already does.
        if not self.is_supported(boxes, cx, cy, cz):
            return None

        # Reach check, measured to the piece's ACTUAL centre rather than to the
        # centre of its key cell. For a wall those differ by a full cell -- the
        # canonical cell sits behind the plane -- which made wall range depend
        # on which way you were facing.
        plo = [min(b.lo[i] for b in boxes) for i in range(3)]
        phi = [max(b.hi[i] for b in boxes) for i in range(3)]
        cc = [(plo[i] + phi[i]) / 2.0 for i in range(3)]
        if v_dist(cc, p["pos"]) > CONFIG["BUILD_REACH"] + CELL:
            return None

        if len(self.pieces) >= CONFIG["PIECE_LIMIT"] and self.piece_order:
            old = self.piece_order.pop(0)
            if old in self.pieces:
                del self.pieces[old]
                self.broadcast({"t": "unbuild", "key": old})

        pc = {"key": key, "type": ptype, "cx": cx, "cy": cy, "cz": cz,
              "dir": direction, "hp": CONFIG["PIECE_HP"], "owner": p["id"],
              "mask": FULL_MASK, "boxes": boxes, "t": time.time(),
              "sbounds": ([min(b.lo[i] for b in boxes) for i in range(3)],
                          [max(b.hi[i] for b in boxes) for i in range(3)])}
        self.pieces[key] = pc
        self.piece_order.append(key)
        if not infinite:
            p["mats"] -= cost
        p["last_build"] = time.time()
        self.broadcast({"t": "build", "p": self.wire_piece(pc)})
        self.send_to(p["id"], {"t": "you", "mats": p["mats"]})
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
        if pc["type"] not in ("wallX", "wallZ", "floor"):
            return
        mask &= FULL_MASK
        if mask == 0:
            # Editing every tile away would leave an invisible piece that still
            # occupies its cell and still holds up whatever is stacked on it --
            # a wall you cannot see, cannot shoot and cannot rebuild over. A
            # piece is destroyed by damage, never by editing.
            return
        pc["mask"] = mask
        pc["boxes"] = tile_boxes(pc["type"], pc["cx"], pc["cy"], pc["cz"], pc["dir"], mask)
        self.broadcast({"t": "editbuild", "key": key, "mask": mask})

    # -- combat -----------------------------------------------------------
    def apply_damage(self, target, amount, by_pid, head=False):
        if not target["alive"]:
            return
        if time.time() < target["protect_until"]:
            return
        if self.mode == "build":
            return
        shield = target["shield"]
        if shield > 0:
            used = min(shield, amount)
            target["shield"] = shield - used
            amount -= used
        if amount > 0:
            target["hp"] -= amount
        target["last_dmg_from"] = by_pid
        target["last_dmg_at"] = time.time()
        self.send_to(target["id"], {"t": "you", "hp": target["hp"],
                                    "shield": target["shield"],
                                    "from": self.players[by_pid]["pos"] if by_pid in self.players else None})
        if target["hp"] <= 0:
            self.kill_player(target, by_pid)

    def kill_player(self, target, by_pid):
        target["alive"] = False
        target["hp"] = 0.0
        target["deaths"] += 1
        killer = self.players.get(by_pid)
        if killer and killer is not target:
            killer["kills"] += 1
        self.broadcast({"t": "die", "id": target["id"], "by": by_pid,
                        "kn": killer["name"] if killer else "the void",
                        "vn": target["name"]})
        self.broadcast({"t": "score", "s": {str(p["id"]): p["kills"]
                                            for p in self.players.values()}})
        if self.mode == "duel":
            self.end_round()
        else:
            target["respawn_at"] = time.time() + CONFIG["RESPAWN_TIME"]
        self.check_win()

    def check_win(self):
        if self.phase == "ended":
            return
        goal = CONFIG["DUEL_TARGET"] if self.mode == "duel" else CONFIG["DM_TARGET"]
        if self.mode not in ("duel", "dm"):
            return
        for p in self.players.values():
            if p["kills"] >= goal:
                self.phase = "ended"
                self.winner = p["id"]
                self.broadcast({"t": "matchend", "winner": p["id"],
                                "name": p["name"],
                                "s": [{"id": q["id"], "name": q["name"],
                                       "kills": q["kills"], "deaths": q["deaths"]}
                                      for q in self.players.values()]})
                return

    def participants(self):
        return [p for p in self.players.values() if not p["spectator"]]

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
        p["protect_until"] = time.time() + (CONFIG["SPAWN_PROTECT"]
                                            if self.mode == "dm" else 0.0)
        self.broadcast({"t": "respawn", "id": p["id"], "pos": p["pos"],
                        "yaw": p["yaw"], "hp": p["hp"], "shield": p["shield"],
                        "mats": p["mats"]})

    def wipe_builds(self):
        self.pieces.clear()
        self.piece_order = []
        self.broadcast({"t": "wipe"})

    def end_round(self):
        self.round_no += 1
        self.phase = "countdown"
        self.phase_until = time.time() + CONFIG["ROUND_COUNTDOWN"]
        self.wipe_builds()
        parts = self.participants()
        for i, p in enumerate(parts):
            self.spawn(p, index=i)
        self.broadcast({"t": "round", "n": self.round_no,
                        "until": CONFIG["ROUND_COUNTDOWN"]})

    def start_match(self, mode):
        self.mode = mode
        self.round_no = 0
        self.winner = None
        self.grenades = []
        self.targets = []
        self.aim_stats = {}
        self.wipe_builds()
        for p in self.players.values():
            p["kills"] = 0
            p["deaths"] = 0
            p["spectator"] = False
        parts = self.participants()
        self.dummies = []
        if mode == "build":
            self.spawn_dummies()
        if mode == "duel":
            for i, p in enumerate(parts):
                p["spectator"] = i >= 2
        for i, p in enumerate(self.participants()):
            self.spawn(p, index=i)
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
    def do_shoot(self, p, weapon, origin, direction, seed):
        if not p["alive"] or self.phase != "live":
            return
        w = CONFIG["WEAPONS"].get(weapon)
        if w is None or weapon == "grenade":
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

        boxes = self.collision_boxes(exclude_pid=p["id"], include_players=True)
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

        for box, t, end in results:
            if box is None:
                continue
            if box.kind in ("body", "head"):
                target = self.players.get(box.ref)
                if target and target["alive"]:
                    head = box.kind == "head"
                    dmg = w["dmg"] * (w["head_mult"] if head else 1.0)
                    self.apply_damage(target, dmg, p["id"], head)
                    self.send_to(p["id"], {"t": "hit", "target": box.ref,
                                           "dmg": round(dmg), "head": head,
                                           "pos": end})
            elif box.kind == "piece":
                self.damage_piece(box.ref, w["build_dmg"], p["id"])
                self.send_to(p["id"], {"t": "hit", "kind": "piece",
                                       "dmg": round(w["build_dmg"]), "pos": end})
            elif box.kind in ("dummy", "dummyhead"):
                head = box.kind == "dummyhead"
                dmg = w["dmg"] * (w["head_mult"] if head else 1.0)
                self.hit_dummy(box.ref, dmg, head, p["id"], end)
                self.send_to(p["id"], {"t": "hit", "kind": "dummy", "target": box.ref,
                                       "dmg": round(dmg), "head": head, "pos": end})
            elif box.kind == "target":
                self.hit_target(box.ref, p)

        self.broadcast({"t": "tracer", "by": p["id"], "w": weapon,
                        "o": [round(v, 2) for v in origin],
                        "seed": seed, "d": [round(v, 4) for v in d]},
                       exclude=p["id"])

    def melee(self, p, direction):
        w = CONFIG["WEAPONS"]["pickaxe"]
        now = time.time()
        if now - p["last_shot"].get("pickaxe", 0.0) < w["rate"] * 0.85:
            return
        p["last_shot"]["pickaxe"] = now
        eye = [p["pos"][0], p["pos"][1] + CONFIG["EYE"], p["pos"][2]]
        boxes = self.collision_boxes(exclude_pid=p["id"], include_players=True)
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
                self.apply_damage(target, w["dmg"], p["id"], box.kind == "head")
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

    def throw_grenade(self, p, origin, direction):
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
            self.apply_damage(p, w["dmg"] * (1.0 - d / r), g["owner"])
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

                # close distance when pushing, back off otherwise
                push = cfg["push"]
                desired = 8.0 if random.random() < push else 18.0
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
            if hurt or (see and random.random() < 0.25):
                bt["next_build"] = now + cfg["build_cd"]
                yaw = math.radians(p["yaw"])
                fwd = [-math.sin(yaw), 0.0, -math.cos(yaw)]
                ahead = v_add(p["pos"], v_scale(fwd, CELL * 0.6))
                cx = int(math.floor(ahead[0] / CELL))
                cy = int(math.floor(p["pos"][1] / CELL))
                cz = int(math.floor(ahead[2] / CELL))
                d = self.dir_from_vec(fwd)
                if hurt:
                    self.place(p, "wall", cx, cy, cz, d)
                else:
                    self.place(p, "ramp", cx, cy, cz, d)

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
        msg = {"t": "state", "k": self.tick, "ts": round(now * 1000),
               "ps": ps}
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
                self.melee(p, m.get("d") or [0, 0, 1])
                return
            self.do_shoot(p, w, m.get("o") or p["pos"], m.get("d") or [0, 0, 1],
                          int(m.get("seed", 0)) & 0xFFFFFFFF)
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
            if h in CONFIG["LOADOUT"] or h in CONFIG["BUILD_PIECES"]:
                p["hand"] = h
                p["reloading"] = None
            return

        if t == "reload":
            h = m.get("w") if m.get("w") in CONFIG["WEAPONS"] else p["hand"]
            w = CONFIG["WEAPONS"].get(h)
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
            if mode in ("duel", "dm", "build", "aim"):
                self.mode = mode
                self.broadcast({"t": "mode", "mode": mode, "phase": self.phase})
            return

        if t == "addbot":
            diff = m.get("diff", "medium")
            if diff not in CONFIG["BOT"]:
                diff = "medium"
            n = len([q for q in self.players.values() if q["bot"]]) + 1
            b = self.new_player("Bot %d (%s)" % (n, diff), is_bot=True, diff=diff)
            self.broadcast({"t": "join", "p": self.public_player(b)})
            return

        if t == "kickbots":
            for bid in [q["id"] for q in self.players.values() if q["bot"]]:
                self.remove_player(bid)
            return

        if t == "start":
            mode = m.get("mode", self.mode)
            if mode not in ("duel", "dm", "build", "aim"):
                return
            if mode == "duel" and len(self.players) != 2:
                self.send_to(pid, {"t": "err",
                                   "m": "Duel needs exactly 2 players. Add a bot or switch mode."})
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
                        p = GAME.new_player(name)
                        pid = p["id"]
                        client = Client(sock, self.client_address, pid)
                        GAME.clients[pid] = client
                        if GAME.host is None:
                            GAME.host = pid
                        client.send({
                            "t": "welcome", "id": pid, "host": GAME.host,
                            "build": BUILD_ID,
                            "config": CONFIG, "arena": ARENA, "spawns": SPAWNS,
                            "mode": GAME.mode, "phase": GAME.phase,
                            "players": [GAME.public_player(q) for q in GAME.players.values()],
                            "pieces": [GAME.wire_piece(pc) for pc in GAME.pieces.values()],
                            "dummies": GAME.wire_dummies(),
                        })
                        GAME.broadcast({"t": "join", "p": GAME.public_player(p)},
                                       exclude=pid)
                        print("  + %s joined from %s" % (name, self.client_address[0]))
                        continue
                    try:
                        GAME.handle(pid, msg)
                    except Exception:
                        traceback.print_exc()
        finally:
            with GAME.lock:
                if pid is not None:
                    GAME.clients.pop(pid, None)
                    nm = GAME.players.get(pid, {}).get("name", "?")
                    GAME.remove_player(pid)
                    print("  - %s left" % nm)
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
