"""Increment 4: the collision-box cache must never be able to go stale.

static_boxes() hands out a shared list rebuilt only when world_epoch (or the
arena) moves. That is only safe if EVERY mutation bumps it, so this compares
the cached list against a freshly computed one after every kind of change the
world can undergo."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
S = H.S
CELL = S.CELL

def fresh(g):
    """What static_boxes() would return with no caching at all."""
    boxes = list(S.ARENA_BOXES)
    for pc in g.pieces.values():
        boxes.extend(pc["boxes"])
    return boxes

def ident(boxes):
    return sorted((b.kind, round(b.lo[0], 4), round(b.lo[1], 4), round(b.lo[2], 4),
                   round(b.hi[0], 4), round(b.hi[1], 4), round(b.hi[2], 4))
                  for b in boxes)

def agrees(g, label):
    H.check("cache matches a fresh build after %s" % label,
            ident(g.static_boxes()) == ident(fresh(g)),
            "%d cached vs %d fresh" % (len(g.static_boxes()), len(fresh(g))))

def rig():
    H.seed()
    g = H.new_game("build")
    p = H.add(g, "p"); p["mats"] = 10**9
    def put(ptype, cx, cy, cz, d=0, at=None):
        if at: p["pos"] = list(at)
        p["last_build"] = 0.0
        return g.place(p, ptype, cx, cy, cz, d)
    return g, p, put

print("== every mutation invalidates ==")
g, p, put = rig()
agrees(g, "a cold start")

a = put("wall", -8, 0, -8, 0, at=[-30.0, 0.1, -30.0])
agrees(g, "a placement")
n_after_place = len(g.static_boxes())

put("wall", -8, 1, -8, 0, at=[-30.0, 0.1, -30.0])
agrees(g, "a second placement")
H.check("the list actually grew", len(g.static_boxes()) > n_after_place)

# An edit changes a piece's geometry WITHOUT changing its key or the piece
# count, which is the one mutation a naive len()-based cache key would miss.
key = a["key"]
before = len(g.static_boxes())
mask_before = g.pieces[key]["mask"]
# `mask` is what REMAINS, and the CUT has to be the rectangle -- so a window
# is "everything except the centre tile", not "the centre tile".
window = S.full_mask(g.pieces[key]["type"]) & ~0b000010000
g.edit_piece(p, key, window)
H.check("the edit was accepted", g.pieces[key]["mask"] != mask_before,
        "mask still %s" % bin(g.pieces[key]["mask"]))
H.check("the edit changed the piece's box count",
        len(g.static_boxes()) != before,
        "%d boxes before and after" % before)
agrees(g, "an edit (geometry changed, key and count did not)")

g.damage_piece(key, 10.0, p["id"])
agrees(g, "partial damage (no geometry change)")

g.damage_piece(key, 10**6, p["id"])
agrees(g, "a piece being destroyed (and its cascade)")

put("floor", -8, 1, -8, 0, at=[-30.0, 0.1, -30.0])
agrees(g, "rebuilding after the cascade")

g.wipe_builds()
agrees(g, "a build wipe")
H.check("a wipe leaves the arena and nothing else",
        len(g.static_boxes()) == len(S.ARENA_BOXES),
        "%d boxes" % len(g.static_boxes()))

print("\n== swapping the arena invalidates too ==")
g, p, put = rig()
put("wall", -8, 0, -8, 0, at=[-30.0, 0.1, -30.0])
n_open = len(g.static_boxes())
S.load_arena("box")
agrees(g, "an arena swap")
H.check("the swap actually changed the world", len(g.static_boxes()) != n_open,
        "%d then %d" % (n_open, len(g.static_boxes())))
S.load_arena("open")
agrees(g, "swapping back")

print("\n== the shared list is not corrupted by its callers ==")
g, p, put = rig()
put("wall", -8, 0, -8, 0, at=[-30.0, 0.1, -30.0])
n = len(g.static_boxes())
owned = g.collision_boxes()
owned.extend([1, 2, 3])                       # a caller mutating ITS list
H.check("collision_boxes() hands back a list the caller owns",
        len(g.static_boxes()) == n,
        "shared list grew to %d" % len(g.static_boxes()))
H.check("and it contains the same world", len(owned) == n + 3)

print("\n== identity: the hot path really is shared, not copied ==")
H.check("two static_boxes() calls return the same object",
        g.static_boxes() is g.static_boxes())
H.check("two collision_boxes() calls do not",
        g.collision_boxes() is not g.collision_boxes())

print("\n== a full simulated match leaves the cache honest ==")
H.seed()
g = H.new_game("dm")
for i in range(4):
    b = H.add(g, "bot%d" % i, bot=True, diff="hard")
    b["pos"] = [-30.0 + i * 3, 0.05, -30.0]
foe = H.add(g, "foe"); foe["pos"] = [-24.0, 0.05, -18.0]
def each(_g):
    foe["hp"] = 100.0; foe["shield"] = 100.0; foe["alive"] = True
H.run(g, 20.0, each=each)
agrees(g, "20s of four hard bots building and fighting")
H.check("the match actually built something", len(g.pieces) > 0,
        "%d pieces" % len(g.pieces))

sys.exit(H.report())
