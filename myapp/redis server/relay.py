"""
FastAPI Relay Server: Browser <-> Desktop bridge.

Everything lives in REAL Redis: online status, the offline queue, the full
chat snapshot, and even the "wait for the desktop's reply" mechanism (via
Redis Pub/Sub instead of an in-memory Future).

The ONE exception, and it's not a choice: an open WebSocket is a live TCP
socket object. It cannot be serialized or stored in Redis -- only the
process that physically accepted the connection can use it. So we keep a
single local dict of {desktop_id: socket}, and nothing else.

Run:  uvicorn relay_server:app --reload
Deps: pip install fastapi uvicorn "redis>=5"
"""
import asyncio
import json
import logging
import time
import uuid
from typing import Optional

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse

logger = logging.getLogger("relay")
logging.basicConfig(level=logging.INFO)

app = FastAPI()

REDIS_URL = "redis://localhost:6379"

HEARTBEAT_TIMEOUT = 45              # seconds of silence before desktop is considered dead
OFFLINE_QUEUE_TTL = 7 * 24 * 3600   # 1 week -- messages waiting for the desktop survive this long
MAX_QUEUE_SIZE = 250                 # cap on queued messages per desktop
SYNC_REQUEST_TIMEOUT = 10            # seconds the website waits for a LIVE pull
SNAPSHOT_TTL = 24 * 3600             # 1 day -- login-sync snapshot expires this long after
                                       # its last write (each new login sync resets the clock)
SNAPSHOT_TRIM_SIZE = 5000            # also keep the snapshot list from growing forever
                                       # (this is a cache, not permanent storage --
                                       #  put real chat history in a proper DB)

redis_client: Optional[redis.Redis] = None

# The ONLY local, non-Redis state in this whole server: raw sockets.
# desktop_id -> WebSocket
sockets: dict[str, WebSocket] = {}


# ---------------------------------------------------------------------------
# Redis key design
#
#   online:{desktop_id}     STRING, value=1, TTL=HEARTBEAT_TIMEOUT
#                            -> presence flag. Expires on its own if nobody
#                               refreshes it (heartbeat), so "offline" is
#                               detected even after a hard crash.
#
#   queue:{desktop_id}      LIST of JSON messages, TTL = 1 week
#                            -> messages waiting to reach the desktop while
#                               it's offline. Drained + deleted on connect.
#                               If the desktop stays offline longer than a
#                               week, the whole queue silently expires --
#                               that's the deliberate "give up" point.
#
#   snapshot:{desktop_id}   LIST of JSON messages, TTL = 1 day (refreshed
#                            on every login sync)
#                            -> the "last known chat" cache. Grows as the
#                               desktop syncs new messages, and the website
#                               reads it on login. If nobody logs in for a
#                               full day, it's deleted -- next login just
#                               starts a fresh pull from the desktop.
#
#   last_sync:{desktop_id}  STRING, unix timestamp
#                            -> so we only ask the desktop for messages
#                               NEWER than this, instead of everything again.
#
#   sync_result:{session_id} Pub/Sub channel (not a stored key)
#                            -> the desktop publishes its sync_batch reply
#                               here; the HTTP handler waiting on the login
#                               request subscribes to it.
# ---------------------------------------------------------------------------


async def connect_desktop(desktop_id: str, ws: WebSocket):
    await ws.accept()
    old = sockets.get(desktop_id)
    if old:
        try:
            await old.close(code=4000, reason="replaced by new connection")
        except Exception:
            pass
    sockets[desktop_id] = ws
    await redis_client.set(f"online:{desktop_id}", "1", ex=HEARTBEAT_TIMEOUT)
    logger.info(f"desktop {desktop_id} connected")
    await flush_offline_queue(desktop_id)


async def heartbeat(desktop_id: str):
    await redis_client.expire(f"online:{desktop_id}", HEARTBEAT_TIMEOUT)


async def disconnect_desktop(desktop_id: str):
    sockets.pop(desktop_id, None)
    await redis_client.delete(f"online:{desktop_id}")
    logger.info(f"desktop {desktop_id} disconnected")


async def is_online(desktop_id: str) -> bool:
    return await redis_client.exists(f"online:{desktop_id}") == 1


# ---------- Flow 1: offline queue (Website -> Desktop) ----------

async def send_to_desktop(desktop_id: str, message: dict) -> bool:
    if not await is_online(desktop_id):
        await enqueue(desktop_id, message)
        return False
    ws = sockets.get(desktop_id)
    if not ws:
        await enqueue(desktop_id, message)
        return False
    try:
        await ws.send_json(message)
        return True
    except Exception:
        await disconnect_desktop(desktop_id)
        await enqueue(desktop_id, message)
        return False


async def enqueue(desktop_id: str, message: dict):
    message["_queued_at"] = time.time()
    key = f"queue:{desktop_id}"
    await redis_client.rpush(key, json.dumps(message))
    await redis_client.ltrim(key, -MAX_QUEUE_SIZE, -1)
    await redis_client.expire(key, OFFLINE_QUEUE_TTL)


async def flush_offline_queue(desktop_id: str):
    key = f"queue:{desktop_id}"
    ws = sockets.get(desktop_id)
    if not ws:
        return
    now = time.time()
    while True:
        raw = await redis_client.lpop(key)
        if raw is None:
            break
        msg = json.loads(raw)
        if now - msg.get("_queued_at", now) > OFFLINE_QUEUE_TTL:
            continue  # stale, drop it
        await ws.send_json(msg)


# ---------- Flow 2: login sync (Desktop -> Website), snapshot in real Redis ----------

async def request_sync(desktop_id: str) -> Optional[dict]:
    """
    Ask the desktop for everything new since last_sync -- tracked
    SEPARATELY for chat and files, same as everything else in this design.
    Waits on a Redis Pub/Sub channel (not a local Future) for the reply,
    so this still works correctly if you later split this into multiple
    server processes.
    Returns {"messages": [...], "files": [...]}, or None if offline / no
    reply in time.
    """
    if not await is_online(desktop_id):
        return None
    ws = sockets.get(desktop_id)
    if not ws:
        return None

    session_id = str(uuid.uuid4())

    since_chat_raw = await redis_client.get(f"last_sync:{desktop_id}:chat")
    since_files_raw = await redis_client.get(f"last_sync:{desktop_id}:files")
    since_chat = float(since_chat_raw) if since_chat_raw else 0
    since_files = float(since_files_raw) if since_files_raw else 0

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"sync_result:{session_id}")

    try:
        await ws.send_json({
            "type": "sync_request",
            "session_id": session_id,
            "since_messages": since_chat,   # desktop sends chat newer than this
            "since_files": since_files,     # desktop sends code/file changes newer than this
        })
    except Exception:
        await disconnect_desktop(desktop_id)
        await pubsub.unsubscribe(f"sync_result:{session_id}")
        return None

    try:
        async def wait_for_reply():
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    return json.loads(raw["data"])
        return await asyncio.wait_for(wait_for_reply(), timeout=SYNC_REQUEST_TIMEOUT)
    except asyncio.TimeoutError:
        return None
    finally:
        await pubsub.unsubscribe(f"sync_result:{session_id}")


async def handle_sync_batch(desktop_id: str, data: dict):
    """
    Desktop replied to a sync_request with new messages AND/OR new/changed
    files. Each type gets merged into its own Redis snapshot list, and each
    gets its OWN last_sync timestamp updated -- so next time, we only ask
    the desktop for what's genuinely new in each category independently.
    """
    session_id = data.get("session_id")
    new_messages = data.get("messages", [])
    new_files = data.get("files", [])

    await _append_to_snapshot(desktop_id, "chat", new_messages)
    await _append_to_snapshot(desktop_id, "files", new_files)

    now = str(time.time())
    await redis_client.set(f"last_sync:{desktop_id}:chat", now)
    await redis_client.set(f"last_sync:{desktop_id}:files", now)

    await redis_client.publish(f"sync_result:{session_id}", json.dumps({
        "messages": new_messages,
        "files": new_files,
    }))


async def _append_to_snapshot(desktop_id: str, kind: str, items: list):
    """kind is 'chat' or 'files' -- each gets its own Redis list."""
    key = f"snapshot:{desktop_id}:{kind}"
    if items:
        pipe = redis_client.pipeline()
        for item in items:
            pipe.rpush(key, json.dumps(item))
        pipe.ltrim(key, -SNAPSHOT_TRIM_SIZE, -1)
        await pipe.execute()
    # refresh TTL on every sync, whether or not this particular type had
    # new items -- one login should keep both caches alive together
    if await redis_client.exists(key):
        await redis_client.expire(key, SNAPSHOT_TTL)


async def get_snapshot_delta(desktop_id: str, kind: str, since_count: int) -> tuple[list, int]:
    """
    Returns (new_items, total_count). since_count is how many items of
    this kind the BROWSER already has locally (in IndexedDB) -- we only
    return the ones after that index, so a repeat login doesn't re-send
    data the browser already cached.
    """
    key = f"snapshot:{desktop_id}:{kind}"
    total = await redis_client.llen(key)
    if since_count >= total:
        return [], total
    raw_items = await redis_client.lrange(key, since_count, -1)
    return [json.loads(item) for item in raw_items], total


# ---------------------------------------------------------------------------

@app.on_event("startup")
async def startup():
    global redis_client
    redis_client = redis.from_url(REDIS_URL)


def verify_token(token: str) -> Optional[str]:
    """Stub -- replace with real JWT verification + Origin header check."""
    return token or None


@app.websocket("/ws/desktop")
async def desktop_ws(ws: WebSocket, token: str = Query(...)):
    desktop_id = verify_token(token)
    if not desktop_id:
        await ws.close(code=4401, reason="unauthorized")
        return

    await connect_desktop(desktop_id, ws)
    try:
        while True:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=HEARTBEAT_TIMEOUT)
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "pong":
                await heartbeat(desktop_id)
                continue

            if msg_type == "sync_batch":
                await handle_sync_batch(desktop_id, data)
                continue

    except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
        pass
    finally:
        await disconnect_desktop(desktop_id)


@app.post("/api/submit/{desktop_id}")
async def submit_task(desktop_id: str, payload: dict):
    session_id = str(uuid.uuid4())
    payload["session_id"] = session_id
    delivered = await send_to_desktop(desktop_id, payload)
    return JSONResponse({
        "session_id": session_id,
        "status": "delivered" if delivered else "queued_offline",
    })


@app.post("/api/login/{desktop_id}")
async def login_sync(desktop_id: str, since_messages: int = 0, since_files: int = 0):
    """
    Website calls this on login, passing how many chat messages and how
    many files it ALREADY has cached locally (in IndexedDB). Returns only
    what's new past those counts, plus the new totals so the browser knows
    what cursor to send next time.
    """
    if await is_online(desktop_id):
        await request_sync(desktop_id)  # triggers a fresh sync_batch from desktop, if it has anything new
        status = "live"
    else:
        status = "cached"

    new_messages, total_messages = await get_snapshot_delta(desktop_id, "chat", since_messages)
    new_files, total_files = await get_snapshot_delta(desktop_id, "files", since_files)

    if total_messages == 0 and total_files == 0:
        status = "no_data"

    return {
        "status": status,
        "messages": new_messages,
        "files": new_files,
        "total_messages": total_messages,
        "total_files": total_files,
    }


@app.get("/api/status/{desktop_id}")
async def desktop_status(desktop_id: str):
    return {"desktop_id": desktop_id, "online": await is_online(desktop_id)}