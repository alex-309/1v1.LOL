#!/usr/bin/env python3
"""
Vercel entrypoint for Build-Fighter.

`server.py` remains the authority on the game itself: the arena, the physics,
the build rules, the bots and CONFIG all live there and are imported here
untouched. This module swaps only the *transport* underneath them. server.py
accepts a raw TCP socket and speaks WebSocket frames it framed by hand; on
Vercel the platform hands us an already-upgraded ASGI connection instead.

The seam that makes this cheap is `Client`. Game only ever calls two methods on
it -- `send(msg, droppable)` and `kill()` -- via broadcast()/send_to(), so
`SocketClient` below reimplements exactly those two against an ASGI WebSocket
and the other 2500 lines are none the wiser.

Two structural differences from server.py, both forced by the platform:

  * Everything runs in one asyncio loop, instead of a thread per connection
    plus a tick thread. No task ever awaits while holding GAME.lock, so the
    lock is uncontended and no client can observe a half-applied tick.
  * The 30Hz tick is started by the first player to connect and cancelled when
    the last one leaves. Vercel bills active CPU time, and a tick loop spinning
    in an idle instance is a bill for a game nobody is playing.

Run locally exactly as it runs deployed:  uvicorn app:app --port 8080
(`python3 server.py` still works too, and is still the better LAN option --
see README.md for why.)
"""

import asyncio
import json
import mimetypes
import os
import traceback

from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect

import server as bf

app = FastAPI()

# The tick task, and the game it is stepping. Module-level because a Vercel
# instance may serve many connections over its life and they must all land in
# the same world -- that is the whole reason two players can see each other.
_tick_task = None


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
class SocketClient(object):
    """One connected browser, with server.Client's interface over an ASGI socket.

    Same backpressure contract as the original: a slow client must never stall
    the tick, so senders only enqueue. Under pressure stale `state` snapshots
    are dropped (the next one supersedes them) while events are never dropped,
    and a client that falls MAX_Q behind is cut loose rather than allowed to
    grow the queue without bound.
    """

    MAX_Q = 512

    def __init__(self, pid):
        self.pid = pid
        self.alive = True
        self.q = []
        self.wake = asyncio.Event()

    def send(self, msg, droppable=False):
        if not self.alive:
            return
        if droppable:
            # keep only the freshest state snapshot
            self.q = [m for m in self.q if not m[1]]
        if len(self.q) >= self.MAX_Q:
            self.alive = False
            self.wake.set()
            return
        self.q.append((msg, droppable))
        self.wake.set()

    def kill(self):
        self.alive = False
        self.wake.set()

    async def pump(self, ws):
        """Drain the queue onto the wire until killed or the socket breaks.

        The clear/re-check below is not racy despite looking like it: send() is
        only ever called from this same event loop, and there is no await
        between the clear and the re-check, so no other task can slip a message
        in and have its wake-up dropped.
        """
        while True:
            if not self.q:
                self.wake.clear()
                if not self.q and self.alive:
                    await self.wake.wait()
                if not self.q and not self.alive:
                    return
                continue
            msg, _ = self.q.pop(0)
            try:
                await ws.send_text(json.dumps(msg, separators=(",", ":")))
            except Exception:
                self.alive = False
                return


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------
async def _tick_loop():
    """server.tick_loop(), rewritten as a coroutine.

    asyncio.sleep replaces stop.wait(); the dt clamp and the drift correction
    are carried over unchanged so the simulation behaves identically.
    """
    loop = asyncio.get_event_loop()
    last = loop.time()
    while True:
        now = loop.time()
        dt = now - last
        last = now
        if dt > 0.5:
            dt = 0.5
        try:
            with bf.GAME.lock:
                bf.GAME.step(dt)
        except Exception:
            traceback.print_exc()
        slp = bf.TICK_DT - (loop.time() - now)
        await asyncio.sleep(slp if slp > 0 else 0)


def _start_tick():
    global _tick_task
    if _tick_task is None or _tick_task.done():
        _tick_task = asyncio.ensure_future(_tick_loop())


def _stop_tick_if_idle():
    """Last player out turns off the lights -- and wipes the world.

    An instance is reused for connections that have nothing to do with each
    other. Without this reset the next player to arrive would walk into the
    previous match's builds, bots and scores.
    """
    global _tick_task
    if bf.GAME.clients:
        return
    if _tick_task is not None:
        _tick_task.cancel()
        _tick_task = None
    # Safe to re-run __init__ here: no clients remain and the tick is cancelled,
    # so nothing holds the lock we are about to replace.
    bf.GAME.__init__()


# ---------------------------------------------------------------------------
# Game socket
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def game_socket(ws: WebSocket):
    await ws.accept()
    GAME = bf.GAME
    pid = None
    client = None
    try:
        # -- handshake: ignore anything that isn't `hello`, as server.py does --
        while pid is None:
            msg = await _recv_json(ws)
            if msg is None or msg.get("t") != "hello":
                continue
            name = str(msg.get("name", "Player"))[:16].strip() or "Player"
            with GAME.lock:
                p = GAME.new_player(name)
                pid = p["id"]
                client = SocketClient(pid)
                GAME.clients[pid] = client
                if GAME.host is None:
                    GAME.host = pid
                client.send({
                    "t": "welcome", "id": pid, "host": GAME.host,
                    "build": bf.BUILD_ID,
                    "config": bf.CONFIG, "arena": bf.ARENA, "spawns": bf.SPAWNS,
                    "mode": GAME.mode, "phase": GAME.phase,
                    "players": [GAME.public_player(q) for q in GAME.players.values()],
                    "pieces": [GAME.wire_piece(pc) for pc in GAME.pieces.values()],
                    "dummies": GAME.wire_dummies(),
                })
                GAME.broadcast({"t": "join", "p": GAME.public_player(p)}, exclude=pid)
            print("  + %s joined" % name)
            _start_tick()

        # -- steady state: reader and writer race; either ending ends both ----
        writer = asyncio.ensure_future(client.pump(ws))
        reader = asyncio.ensure_future(_read_loop(ws, GAME, pid))
        done, pending = await asyncio.wait(
            [reader, writer], return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            exc = t.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                traceback.print_exc()
    except WebSocketDisconnect:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        with bf.GAME.lock:
            if pid is not None:
                bf.GAME.clients.pop(pid, None)
                nm = bf.GAME.players.get(pid, {}).get("name", "?")
                bf.GAME.remove_player(pid)
                print("  - %s left" % nm)
        if client:
            client.kill()
        _stop_tick_if_idle()


async def _recv_json(ws):
    """One decoded message, or None for anything the game should ignore.

    Mirrors server.py: non-text frames and undecodable payloads are skipped
    rather than treated as a protocol error, so a stray binary frame cannot
    drop a player out of a match.
    """
    ev = await ws.receive()
    if ev["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(ev.get("code", 1000))
    raw = ev.get("text")
    if raw is None:
        data = ev.get("bytes")
        if data is None:
            return None
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError:
            return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


async def _read_loop(ws, GAME, pid):
    while True:
        msg = await _recv_json(ws)
        if msg is None:
            continue
        with GAME.lock:
            try:
                GAME.handle(pid, msg)
            except Exception:
                traceback.print_exc()


# ---------------------------------------------------------------------------
# Static files -- same rules as server.py._static()
# ---------------------------------------------------------------------------
@app.api_route("/whoami", methods=["GET", "HEAD"])
def whoami():
    return Response(
        content=json.dumps({"app": "build-fighter", "build": bf.BUILD_ID,
                            "pid": os.getpid()}),
        media_type="application/json")


@app.api_route("/{rel:path}", methods=["GET", "HEAD"])
def static_file(rel: str, request: Request):
    head_only = request.method == "HEAD"

    if rel in ("", "/"):
        rel = "index.html"
    if rel == "favicon.ico":
        return _body(bf.FAVICON_BYTES, "image/svg+xml", head_only)

    # Traversal defense: resolve, then require the result to still be inside
    # HERE. A naive `".." in path` check misses encoded and symlinked forms.
    target = os.path.realpath(os.path.join(bf.HERE, rel))
    if target != bf.HERE and not target.startswith(bf.HERE + os.sep):
        return Response(content=b"forbidden", status_code=403,
                        media_type="text/plain")
    ext = os.path.splitext(target)[1].lower()
    if ext not in bf.STATIC_OK or not os.path.isfile(target):
        return Response(content=b"not found", status_code=404,
                        media_type="text/plain")

    ctype = mimetypes.guess_type(target)[0] or "application/octet-stream"
    if ext == ".js":
        ctype = "application/javascript"
    try:
        with open(target, "rb") as f:
            body = f.read()
    except OSError:
        return Response(content=b"not found", status_code=404,
                        media_type="text/plain")

    # index.html must never be served stale, or a deploy is invisible to
    # whoever already loaded the page once.
    cache = "no-store" if ext == ".html" else "public, max-age=86400"
    return _body(body, ctype, head_only, {"Cache-Control": cache})


def _body(body, ctype, head_only, headers=None):
    hdrs = dict(headers or {})
    if head_only:
        hdrs["Content-Length"] = str(len(body))
        return Response(content=b"", media_type=ctype, headers=hdrs)
    return Response(content=body, media_type=ctype, headers=hdrs)
