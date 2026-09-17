"""Increment 3: PIECE_LIMIT eviction must run the support cascade.

Every other path that removes a piece calls prune_unsupported(), so whatever
the piece was holding up falls with it. The eviction at the piece cap did a
bare del + broadcast, leaving orphans hanging in mid-air on both sides."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H
S = H.S
CELL = S.CELL

def stacked_world(limit):
    """A two-high wall stack (the upper one held up only by the lower) plus
    filler on the ground, with the cap set so the next placement evicts the
    load-bearing piece at the bottom."""
    H.seed()
    old_limit = S.CONFIG["PIECE_LIMIT"]
    S.CONFIG["PIECE_LIMIT"] = limit
    g = H.new_game("build")
    p = H.add(g, "p")
    p["mats"] = 99999
    def put(ptype, cx, cy, cz, d=0, at=None):
        if at:
            p["pos"] = list(at)
        p["last_build"] = 0.0            # the cooldown is not what is under test
        return g.place(p, ptype, cx, cy, cz, d)
    return g, p, put, old_limit

# Open ground in the far corner. Cell (0,0,0) is inside the open arena's
# centre pillar, where build_los_clear refuses everything.
print("== a load-bearing piece evicted at the cap takes its dependents with it ==")
g, p, put, old = stacked_world(3)
CORNER = [-30.0, 0.1, -30.0]
lower = put("wall", -8, 0, -8, 0, at=CORNER)
upper = put("wall", -8, 1, -8, 0, at=CORNER)      # one storey up, still in reach
H.check("built a wall resting on the arena", lower is not None)
H.check("built a second wall resting only on the first", upper is not None)

lower_key = lower["key"] if lower else None
upper_key = upper["key"] if upper else None
# a third piece on open ground, so the stack is the OLDEST thing in the world
put("wall", -8, 0, -6, 0, at=[-30.0, 0.1, -22.0])
H.check("world is at the cap", len(g.pieces) == 3, "%d pieces" % len(g.pieces))

# the placement that forces an eviction; oldest is `lower`
put("wall", -8, 0, -4, 0, at=[-30.0, 0.1, -14.0])
H.check("the oldest, load-bearing piece was evicted", lower_key not in g.pieces)
H.check("the piece it was holding up went with it", upper_key not in g.pieces,
        "orphan %s left floating" % upper_key)

print("\n== the bookkeeping stays consistent ==")
H.check("piece_order holds exactly the live keys",
        sorted(g.piece_order) == sorted(g.pieces.keys()),
        "order=%s pieces=%s" % (sorted(g.piece_order), sorted(g.pieces.keys())))
H.check("no piece in the world is unsupported",
        g.prune_unsupported() == [],
        "a second prune still found orphans")
S.CONFIG["PIECE_LIMIT"] = old

print("\n== regression: eviction still bounds the world ==")
g, p, put, old = stacked_world(5)
for i in range(20):
    put("wall", -8, 0, -8 + i, 0, at=[-30.0, 0.1, (-8 + i) * CELL + 2.0])
H.check("piece count never exceeds the cap", len(g.pieces) <= 5,
        "%d pieces with a cap of 5" % len(g.pieces))
H.check("and building still works at the cap", len(g.pieces) >= 1)
S.CONFIG["PIECE_LIMIT"] = old

sys.exit(H.report())
