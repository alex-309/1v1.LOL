# Build Fighter

A browser build-battle shooter — third-person movement and gunplay with instant
grid building, in the style of 1v1.LOL. You host the server on this Mac and your
friend joins from the same Wi-Fi or hotspot.

Zero installs. No Node, no npm, no `pip install`. The server is one Python file
using only the standard library, including a hand-rolled WebSocket
implementation.

---

## Run it

Double-click **`start.command`** in Finder.

Or from a terminal:

```
cd "/Users/alejandro/Desktop/Projects/1v1.LOL"
python3 server.py
```

It prints two links:

```
You:          http://localhost:7777
Your friend:  http://192.168.x.x:7777
```

Open the first yourself. Send the second to your friend — they must be on the
**same Wi-Fi or hotspot** as this Mac.

macOS will ask to allow incoming connections the first time. **Click Allow.**
If you dismissed it, re-enable under System Settings → Network → Firewall →
Options, or turn the firewall off briefly.

Stop the server with `Ctrl-C`. Use `--port 9000` if 7777 is taken.

**If you already had a server running**, `start.command` now stops it and takes
the port over, telling you what it did. It confirms over HTTP that the process
really is this game before stopping anything, so nothing else on your machine is
ever touched. Running `python3 server.py` by hand instead will refuse and print
the exact `kill` command — pass `--takeover` if you want the automatic
behaviour.

This matters more than it sounds: an old server process keeps serving the
**old rules** to a freshly reloaded page. Reloading the browser updates the
page; only restarting the server updates the game.

---

## Controls

| | |
|---|---|
| `W A S D` | Move |
| `Space` | Jump |
| `Shift` | Crouch |
| `1` – `5` | Pickaxe · Rifle · Shotgun · Sniper · Grenade |
| `Z` `X` `C` `V` | Wall · Ramp · Floor · Cone |
| `F` | Edit a piece you built — wall, floor, ramp or cone (press again on an edited piece to reset it) |
| `R` | Reload · `G` throw grenade |
| Left mouse | Fire, or place a build piece (hold to turbo-build) |
| Right mouse | Aim down sights |
| `Tab` | Scoreboard · `Enter` chat · `Esc` pause |
| Mouse wheel | Cycle weapons |

Every key is rebindable in the pause menu (`Esc`), and there are **three
independent presets** — say one for a mouse, one for a trackpad, one for whoever
else uses the machine. Click a preset tab to switch layouts instantly; name it
whatever you like. Rebinding and "Reset to defaults" only ever touch the preset
you are looking at, and everything is remembered between sessions. The hotbar
always shows the bindings that are actually live.

**Building.** Pick a piece, look where you want it, click. The blue ghost shows
exactly where it lands; red means it won't go there. Every piece costs 10
materials out of a 999 cap, and materials regenerate after three seconds without
building. Holding the button builds about three pieces a second — slow enough
that a held mouse doesn't quietly spend a hundred materials on boxes you never
looked at.

**The build grid.** The map is divided into invisible boxes one cell on a side.
Each box holds four walls (one per face), a floor at its base, and one ramp *or*
one cone in its volume — nothing else fits, and nothing lands off the grid.

**Reach is measured in boxes, not metres.** You reach the box you are standing
in and two more in every direction, trimmed to a circle: two straight ahead, two
to either side, one diagonally — but not the far diagonals. Stand still and you
can floor the box under your feet, the one in front, and the one past that.
Walls are the exception in one direction only: a wall lives on the *edge*
between two boxes, so you can put one on the far side of the furthest floor you
can reach. Ramps are the exception the other way: your own box, or the one
directly in front, and no further. Walk one box forward and the whole radius
moves with you.

**You cannot build through things.** If a wall, a cone or a chunk of the map
stands between you and a box, that box is closed — you get the last one you can
actually see into. Break the wall and the range opens back up.

**Nothing floats — and you are never told "no".** A piece has to rest on the
ground, on the map, or against another piece that (however many pieces down the
chain) does. But if where you are pointing cannot hold a piece, the game does
not refuse: it **snaps to the nearest spot in your build radius that can**, so
building never stalls mid-fight. Break the piece holding a structure up and
everything it was carrying falls with it.

**A piece built on a player moves the player, not the piece.** A floor, ramp or
cone under you puts you on top of it — never underneath, and never through the
map. A wall placed on you shoves you along the wall's own facing, to whichever
side you were already heading; it never throws you sideways.

**The pickaxe** does 20 to anything it reaches — players, bots, dummies and
builds alike — at a swing every 0.7s. It is the free option when you are out of
ammo and the way to take a wall down without spending bullets; breaking a piece
you swung at refunds 5 materials.

**Build health.** Every piece has 150 HP and visibly deteriorates as it takes
damage — it splits, darkens and chips through five stages on its way to
breaking, the first of which shows after a single rifle round. Aim at any piece and its exact health appears under the crosshair.
Builds don't throw damage numbers; a number on screen always means you hurt
something that can shoot back.

**Editing.** Look at any piece *you* placed, press `F`, then hold left mouse and
sweep across the grid to pick tiles — releasing applies the edit. Right-click
cancels, and `F` on an already-edited piece resets it. The grid depends on the
piece:

| Piece | Grid | What a picked tile does |
|---|---|---|
| Wall | 3×3 | Cuts the tile out — window, door, doorway, half wall, side opening |
| Floor | 2×2 | Cuts that quadrant out, so you can drop through it |
| Cone | 2×2 | Cuts that quarter of the cone away |
| Ramp | 2×2 | *Raises* that quarter into a flat landing at half height |

Ramp edits are the interesting ones. Cut the low half and the ramp starts
halfway up with a landing in front of it; cut the high half and it climbs to the
middle and levels off; cut one side and you get a half-width ramp with a
platform running alongside.

**Only real edits go through.** Your picked tiles have to form a solid
rectangle, which is what every edit in this genre actually is — a scattered
diagonal pick is refused and drops back to an empty selection rather than
cutting a shape the game doesn't have. The prompt under the crosshair names what
you are about to make. You also cannot edit a piece away entirely; that's what
the pickaxe is for.

---

## Modes

- **Duel** — first to 5. Every kill wipes all builds, fully heals both players
  and respawns them at opposite ends. Needs exactly two participants; add a bot
  if your friend isn't around.
- **Deathmatch** — respawn after 3 seconds, builds persist, first to 15. Any
  number of players and bots.
- **Build** — sandbox. Infinite materials, no incoming damage, dummies to break.
- **Aim Trainer** — pop-up targets on a timer, with accuracy, reaction time and
  a running score.

The first person to connect is the host and picks the mode. Bots come in Easy,
Medium and Hard, which tune reaction time, accuracy, build reflex and how hard
they push.

---

## Tuning the game

Every number lives in the `CONFIG` dict at the top of `server.py` — movement,
weapon damage, build costs, health, bot difficulty. It is shipped to the browser
on connect, so the Python bot and the JavaScript player controller always read
the same values. **Edit that one dict and restart**; there is no second place to
keep in sync.

The map is the `ARENA` list right below it — boxes of
`[centreX, centreY, centreZ, sizeX, sizeY, sizeZ, tag]`. The server uses it for
collision and sends it to the browser to build the meshes from.

---

## Troubleshooting

**Friend can't connect.** Confirm they're on the same network — a phone hotspot
counts, but two different Wi-Fi networks never will. Then confirm the macOS
firewall prompt was allowed. If the page shows "Can't reach the server", the
page loaded but the game socket didn't, which is almost always the firewall.

**"Port 7777 is already in use."** An older copy of the server is probably still
running. `python3 server.py --port 8081`, or find it with
`lsof -nP -iTCP:7777 -sTCP:LISTEN`.

**Don't use `--port 8080` on this Mac.** Something on this machine intercepts
8080: the page loads fine over plain HTTP, but the WebSocket upgrade is silently
broken, so the game sits at "Connecting…" forever. 8080 is a common transparent
proxy port. 7777, 8081 and 8137 were all verified clean here.

**A red "SERVER IS RUNNING OLD CODE" banner.** Reloading the page updates the
browser but not `server.py`, and the server keeps its own copy of the building,
support and damage rules. Quit that terminal and double-click `start.command`
again — it stops the old process for you — then reload the page. Both stamps are
shown: the server prints its own on startup, the page shows its own on the main
menu, and `http://localhost:7777/whoami` reports what is actually running.

**Blank page.** A red panel should appear at the bottom with the exact file and
line. If it mentions three.js, run `start.command` once while online so it can
download `three.min.js` (633 KB) next to `index.html`.

**Everything is very slow.** Lower the FOV in the pause menu, and close other
GPU-heavy tabs. The renderer targets 60fps with a few hundred build pieces.

---

## What this is and isn't

The gameplay is reproduced faithfully: third-person shooting, four build pieces
on `Z X C V`, editing, structural support, the pickaxe/rifle/shotgun/sniper
loadout, 10 materials per piece, and duel rounds. All art, sound and UI are generated in code — procedural
textures, WebAudio-synthesized effects, blocky characters — so nothing is copied
from the original game, and there's no branding.

Deliberately not included: accounts, ranked play, matchmaking, cosmetics, a map
rotation, and touch controls (desktop only). The
server validates fire rate, ammo, range, line of sight and
movement plausibility, which is the right level for playing with a friend — it
is not hardened anti-cheat.
