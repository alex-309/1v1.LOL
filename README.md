# Build Fighter

A browser build-battle shooter — third-person movement and gunplay with instant
grid building, in the style of 1v1.LOL. You host the server on this Mac and your
friend joins from the same Wi-Fi or hotspot.

Zero installs to play on your own network. No Node, no npm, no `pip install`.
The server is one Python file using only the standard library, including a
hand-rolled WebSocket implementation. (Deploying to Vercel is the one
exception — see below.)

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

## Put it online (Vercel)

The LAN server above is still the better way to actually play. Deploy when you
want to send someone a link instead of asking them onto your Wi-Fi.

The repo is connected to Vercel through GitHub, so a push to `main` deploys:

```
git push
```

Three files drive this, and none of them touch the game:

| File | Role |
| --- | --- |
| `app.py` | Vercel's entrypoint. Swaps the transport; imports every rule from `server.py`. |
| `pyproject.toml` | Declares FastAPI, and names `app:app` as the entrypoint. |
| `vercel.json` | Framework preset, Fluid compute, and the function's max duration. |

`server.py` is unchanged and still runs standalone. It stays the single source
of truth for the arena, physics, build rules, bots and `CONFIG`; `app.py`
imports all of it and only replaces the socket underneath. Retuning the game is
still a matter of editing `CONFIG` in `server.py` and nothing else.

This is the one part of the project that is not install-free — Vercel needs
FastAPI to serve the game socket. To run the deployed app exactly as deployed:

```
uvicorn app:app --port 8080
```

### The two settings that fail quietly

`vercel.json` pins both of these in the repo, so neither needs a dashboard
visit and neither can drift:

- **`"framework": "fastapi"`** overrides the preset in Project Settings. A
  project imported as **Other** builds this repo as a static site and never
  builds the Python function. `index.html` still loads, so the symptom is the
  game reaching "Connection failed" — it looks like a broken game rather than an
  unbuilt backend.
- **`"fluid": true`** enables Fluid compute, which WebSockets require. It is
  default-on only for projects created after April 23, 2025, so an older project
  has it off.

Changing them here overrides the dashboard, so if you later flip a setting in
Project Settings and nothing happens, this file is why.

### What you give up

Vercel runs the game in a serverless function, and three consequences follow
that no amount of configuration removes:

**Matches are capped by the function's max duration** — 300 seconds on Hobby.
When it expires the socket closes mid-match and players get the existing
"Connection failed" screen; Retry reloads them into a fresh one. On Pro, raise
`maxDuration` in `vercel.json` to 800 (or 1800, in beta).

**Two players are only in the same match if they land on the same instance.**
A connection is pinned to one instance for its life, and one instance happily
holds several connections, so on a quiet deployment a friend joining does land
with you. It is not guaranteed. Under concurrency Vercel starts more instances,
and two players on different ones are in two different worlds — both playing,
neither able to see the other. The world lives in that instance's memory, so
there is no fix short of moving the whole 30Hz simulation behind shared storage,
which is a rewrite rather than a setting.

**The tick bills CPU the whole time anyone is connected.** A 30Hz simulation
does not idle. `app.py` starts the tick on the first join and cancels it when
the last player leaves, and wipes the world at the same time so nobody inherits
the previous match's builds — but while a game is live, that is continuous
Active CPU.

If you want a deployment that holds a real match to the end and reliably puts
everyone in the same world, the shape that fits is a single long-lived process —
`python3 server.py` on any host that gives you one (Fly, Railway, a VPS), with
no code changes at all.

---

## Controls

| | |
|---|---|
| `W A S D` | Move |
| `Space` | Jump |
| `Shift` | Crouch |
| `1` – `5` | Weapon slots, in the order the current mode hands them out |
| `G` | Quick-draw the pickaxe |
| `Z` `X` `C` `V` | Wall · Ramp · Floor · Cone |
| `F` | Edit a piece you built — wall, floor, ramp or cone (press again on an edited piece to reset it) |
| `R` | Reload |
| `T` | Rotate the ramp or cone you are holding &middot; free-cam toggle while spectating |
| Left mouse | Fire, or place a build piece (hold to turbo-build) |
| Right mouse | Aim down sights |
| `Tab` | Scoreboard · `Enter` chat · `Esc` pause |
| Mouse wheel | Cycle weapons |

Three switches live in the pause menu next to the binds, because they are
preference rather than balance:

| Option | Default | What it does |
|---|---|---|
| **Turbo build** | on | Hold left mouse to keep placing. Off means one click, one piece. |
| **Edit on release** | off | Hold the edit key, drag, let go to apply — one motion instead of three. Off keeps the press-to-enter / press-to-reset toggle. |
| **First person** | off | Camera at the eye with a viewmodel. Third person is easier to build in, which is why this is a switch and not a replacement. |
| **Instant replay** | on | On death, replay the last 2.6s from your killer's view. Click to skip, click again to rewatch. Off still leaves you the death-spot camera. |

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

**Look down and it goes under you.** Aim at your own feet — past about 40
degrees down — and a floor, ramp or cone lands in the box you are standing in
rather than the one in front. That is how a ramp climb starts: ramp under
yourself, ride it up, repeat. Walls are the exception, since a wall cannot go
where you are standing; looking down with one still targets the storey below.

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

**Walls reach as far as everything else.** A wall lives on the edge *between*
two boxes rather than in one, and it used to count as in reach when either of
those boxes was — a whole box more range than a floor. It now uses the same
circle, so the furthest wall you can place is one floor tile closer than it was.
Walls are the piece you throw out under pressure, and the extra box let you seal
off ground you had no business holding.

**On a phone or tablet** the game mounts a left stick, a look area and a button
pad — but only where touch is actually reported. They route through the same
`keys[]` table and the same press/release handlers the mouse and keyboard use,
so there is no second input path to keep in step.

**Rotation.** `T` turns a held ramp or cone by a quarter turn, so you can lay a
ramp across your path instead of along it — the one orientation you cannot get
by turning your own body, because turning your body also turns where the piece
lands. Walls and floors are deliberately not rotatable: a wall's facing is
*which edge of the box it occupies*, and a floor has no facing at all.

**Your own builds do not block your next build.** The line-of-sight rule exists
to stop you building through an *enemy's* wall, and only that. Counting your own
pieces made going vertical awkward, since inside your own box every cell worth
filling is behind something you just placed.

**The pickaxe farms.** Swing it at the ground, a crate, anything in the arena,
and you get 16 materials — more than a piece costs. Materials still regenerate,
but regen alone makes them a function of time and nothing else, so there is
never a reason to leave your box.

**Loadouts are per mode.** A duel is a gunfight — rifle, shotgun, sniper on
`1` `2` `3`, and that is all. No pickaxe to chip a wall down with and no
grenade to throw into a box fight. Deathmatch, Build and the Aim Trainer hand
out the full five. The hotbar, the number keys and the mouse wheel all follow
whatever the mode actually gave you, so there are never gaps on the bar where a
weapon you don't have used to be.

**Grenades** are thrown with left mouse while the grenade is in hand, like any
other weapon — they are a server-owned physics object rather than a hitscan,
so they arc, bounce, and go off on a 3s fuse whether or not they hit anything.

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
| Floor | 2×2 | Cuts that quadrant out, so you can drop through it — or climb a ramp up through it |
| Cone | 2×2 | Cuts that quarter of the cone away |
| Ramp | 2×2 | *Raises* that quarter into a flat landing at half height |

**Corner cuts.** A wall has one shape that isn't a rectangle, and it's the one
this genre leans on hardest. Pick three tiles around a corner — the corner tile,
the one beside it and the one above or below it — and the wall is cut along the
diagonal between the two corners you *didn't* touch. Half of it disappears and
what's left is a triangle, so a bottom corner cut opens a gap you can walk
straight through while the other half still covers you. You don't have to paint
the fourth tile of the corner block: it is already half inside the triangle that
survives.

A cut floor quadrant eats `FLOOR_HOLE_PAD` into the slabs beside it rather than
stopping dead at the quadrant line. That is not decoration — climbing a ramp up
through a hole means *stepping up* underneath it, and a step needs the player's
whole box clear of the slab, not just its leading edge. At exactly one quadrant
the hole is the same width as the player and nobody gets through.

Ramp edits are the interesting ones. Cut the low half and the ramp starts
halfway up with a landing in front of it; cut the high half and it climbs to the
middle and levels off; cut one side and you get a half-width ramp with a
platform running alongside.

**Only real edits go through.** Your picked tiles have to form a solid
rectangle, or one of the four wall corner cuts above — between them that is
every edit this genre actually has. A scattered diagonal pick is refused and
drops back to an empty selection rather than cutting a shape the game
doesn't have. The prompt under the crosshair names what
you are about to make. You also cannot edit a piece away entirely; that's what
the pickaxe is for.

**You fight by ear.** Footsteps are positional — distance attenuation and a
stereo pan taken from where the sound sits relative to your camera — and so are
other people's builds, breaks and edits. Crouching is quieter *and* duller, so
it actually buys you something. Your own steps play at half volume, because you
cannot judge how loud you are to someone else without hearing your own stride.
Low health is a heartbeat that quickens rather than a beep, so it registers
without competing with the footsteps you are straining to hear.

**Dropped connections are survivable.** A lost socket parks you rather than
removing you: your slot, score, builds, health, materials and magazine are all
held for `REJOIN_GRACE` seconds while the client retries with backoff. Come back
inside that window and you walk into the same slot mid-match. Down at the socket
a dropped connection and someone quitting look identical, and only one of them
should cost a match.

**Shots are lag-compensated.** You do not see the present: remote players are
drawn 100ms behind the newest snapshot, and that snapshot already cost a network
trip. So every shot carries the server timestamp the client was *rendering* when
you clicked, and the server rewinds everyone else to that instant before casting
the ray. Without it a strafing target feels bulletproof, because you really are
shooting behind them. The rewind is clamped to 260ms — enough for the
interpolation delay plus a round trip on a bad connection, and no more, since an
unbounded rewind is an invitation to ask to shoot at where someone stood a minute
ago. The shooter is never rewound; they see themselves in the present.

**Spectating** has two cameras, because the two things you want are different:
*follow* rides whoever is playing (click to switch), *free* is a flying camera
you steer yourself (`T` toggles). Follow is the replay rig pointed at a live
player — raise and all, so fixing how that camera frames someone fixes both
places it is used — and free is the local controller with gravity and collision
switched off.

**Stats.** Kills and deaths say who won; accuracy, damage, pieces built and
materials farmed say how. Accuracy is counted per *trigger pull*, not per
pellet — a shotgun that lands two pellets of nine hit its target, and scoring
that as 22% would be a lie about what happened. Damage is credited as what
actually landed, so overkill on someone with 3hp left is 3 damage. The
intermission between rounds shows the running numbers; the end card shows the
match. Your lifetime record lives in `localStorage` and is shown on the menu —
it is never sent anywhere.

**The death screen.** Dying is dead time, and there are two different things
worth doing with it. The **instant replay** answers *what just happened to me*:
your last 2.6 seconds from over the killer's shoulder. The **death spot**
answers *what is happening now*: a fixed camera where you went down, free to
look anywhere, running live until you respawn. **Click moves between them**, in
either direction, as often as the wait allows — a replay you skipped is never
gone, and watching it twice costs nothing. The panel counts your respawn down,
or says it is the round you are waiting on.

Neither costs anything on the wire. The snapshot buffer already holds every
player's position, yaw and pitch, so the replay is the same interpolation run
with the camera parented somewhere else. The one thing it could not do before
was play *twice*: the live buffer is a ring that drops frames older than a few
seconds, so by the time you asked for a second look the tape had been recorded
over — and asking again for "the last 2.6 seconds" would have replayed the
wrong ones, the seconds *after* you died. Death now cuts its window out into a
tape of its own, and the tape outlives the ring.

**The replay camera sits higher than the player it follows.** The old rig was
2.2m back at eye height, which put the camera inside the shoulders it was meant
to be looking over: the body took the middle of the frame and the sprites
floating above the head took what was left. It is now 3.5m back and a metre up,
and it stops copying the player's pitch — offset like that, their pitch aims the
middle of the screen somewhere over their target's head. It aims at a point down
their sight line instead, which keeps whatever they were shooting at centred no
matter where the camera is standing. The health bar over the head of the player
being filmed is hidden for the same reason: it draws with depth testing off, so
it goes over everything in front of it, and it is the sprite nearest the lens.

**Nobody wears their name over their head.** A nametag is the same kind of
sprite — pinned to a world position with depth testing off, so it draws over
everything in front of it, and the closer a camera gets the more of the screen
it takes. Two metres behind a player, which is exactly where the replay and
follow cameras sit, one plate was most of the view. The name was never carrying
much either: you are always blue and everyone shooting at you is always red,
which answers *can I shoot this* on its own.

**The combat report** is where a name gets attached to a number instead, down
the right of the screen. Take a hit and a row names who did it, with what, from
how far, and how much health you have left; land one and you get the same row
about them. Colour carries the direction and nothing else, on the rule the
player models already follow — **cyan is you, red is them**, so a cyan row is
damage you dealt and a red row is damage you took. A kill leaves the pair for
gold.

It is one row per player per exchange, not one per bullet: a rifle burst is a
single event to whoever is on either end of it, and three rows saying 22 tell
you less than one row saying 66 ×3. A row still on screen absorbs the next hit
from the same player and resets its own clock, so the number grows while the
fight is happening and settles when it stops. Rows freeze while you are dead —
the report of the fight that just killed you is the one you most want to read,
and it must not time out underneath the screen that exists to show it.

The two directions reach the client differently, and the difference is real.
Damage you took rides the `you` message you were already getting; damage you
dealt is a new `dealt` message, sent from `apply_damage` rather than from the
hitscan path. That is what puts **grenades** on the feed — an explosion never
casts a ray of yours, so it never sent a hitmarker — and what makes the numbers
match the scoreboard: both are credited as what actually landed, so overkill on
someone with 3hp left is 3 damage in both places.

---

## Modes

- **Duel** — first to 5, on the **box-fight arena**: 48 wide, walled 12 high,
  with a raised centre platform worth taking and almost nothing else. The open
  map is a deathmatch map — two duellists spend the first twenty seconds walking
  toward each other — so a duel gets the opposite: close enough that the fight
  starts at once, and walled, so the only way out of a bad position is up. The
  cover in a build fight is the cover you build. Rifle, shotgun and sniper only.
  Every kill wipes all builds, fully heals both players and respawns them at
  opposite ends. Needs exactly two participants; add a bot if your friend isn't
  around.
- **Team Fight** — 2v2 (up to 4v4) rounds on **Towers**, a vertical map where
  the useful ground is all above you and the only way onto it is to build. No
  friendly fire, and the round ends when a whole side is down rather than on
  the first kill — the 2-on-1 after a trade is the entire reason to play with a
  partner. First to 5 rounds. Needs 3+.
- **Deathmatch** — respawn after 3 seconds, builds persist, first to 15. Any
  number of players and bots.
- **Build Trainer** — timed courses of gates you can only reach by building,
  scored the way the aim trainer is: a clock, a best time, nothing else. Three
  courses (Ramp Rush, Tower, Bridge), infinite materials, switch course from the
  pause menu. Everyone runs the same course at once on their own clock.
- **Build** — sandbox. Infinite materials, no incoming damage, dummies to break.
- **Aim Trainer** — pop-up targets on a timer, with accuracy, reaction time and
  a running score.

The first person to connect is the host, picks the mode, and can override the
map — Open Yard, Box Fight or Towers — or leave it on Auto for whatever the mode
wants.

**Bots have a difficulty and a style, and they are separate questions.**
Difficulty (Easy / Medium / Hard) tunes reaction time, accuracy and build
reflex. Style changes what the bot is *trying* to do, which is the part you
actually practise against:

| Style | Plays like |
|---|---|
| **Balanced** | The old bot — trades at mid range, walls when hurt. |
| **Rusher** | Lives in your face and barely builds. Teaches you to hold an angle. |
| **Turtle** | Will not come off its wall and rebuilds constantly. Teaches you to break builds. |
| **Builder** | Takes height at every opportunity. Teaches you to fight someone above you. |

A hard Turtle and a hard Rusher are completely different opponents.

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

**Join limits** are the exception — they sit beside `JoinLimiter` rather than in
`CONFIG`, because they are the server protecting itself and no client needs to
know them:

| Constant | Default | Meaning |
| --- | --- | --- |
| `JOIN_BURST` | 5 | new players one address may create ... |
| `JOIN_WINDOW` | 10.0 | ... within this many seconds |
| `MAX_PLAYERS` | 16 | hard ceiling on one world, bots included |

A refused player is told why on the menu. **Rejoins are deliberately exempt**: a
reconnect carries a token for a slot that already exists, and rate-limiting
those would turn one dropped connection — or, on Vercel, a whole lobby dropping
together when the function hits its max duration — into everybody locked out of
their own match.

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

**The screen goes white.** Two different faults wore this face. A muzzle flash
is a bright sphere at the shooter's eye, and the third-person camera collapses
onto your eye when your back is against a wall — so your own flash, or one from
someone shooting you point-blank, used to fill the frame. Effects are now capped
at the on-screen size they were meant to be, whatever the range.

The other one was worse: effects arrive over the network but expire in the
render loop, and the render loop stops when the tab is hidden or the window is
covered. Left in the background through a firefight, the scene filled with tens
of thousands of meshes nothing was clearing, all of which were drawn at once the
moment you came back — the tab locked up, the browser killed the graphics
context, and you were left on a white canvas while still standing there on
everyone else's screen. Effects are no longer built while nothing is drawing
them, and the lists are hard-capped.

If a white screen ever comes back, look for a **"3D context lost"** row in the
red error panel. That means the browser took the graphics context away — a GPU
driver reset, waking from sleep, or too many 3D tabs — which is a machine
problem, not a game one. Reload the page.

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
