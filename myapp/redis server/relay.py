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
MAX_QUEUE_SIZE = 100                 # cap on queued messages per desktop
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

async def request_sync(desktop_id: str) -> Optional[list]:
    """
    Ask the desktop for everything new since last_sync. Waits on a Redis
    Pub/Sub channel (not a local Future) for the reply, so this works
    correctly even if you later split this into multiple server processes.
    Returns the NEW messages, or None if offline / no reply in time.
    """
    if not await is_online(desktop_id):
        return None
    ws = sockets.get(desktop_id)
    if not ws:
        return None

    session_id = str(uuid.uuid4())
    since_raw = await redis_client.get(f"last_sync:{desktop_id}")
    since = float(since_raw) if since_raw else 0

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(f"sync_result:{session_id}")

    try:
        await ws.send_json({
            "type": "sync_request",
            "session_id": session_id,
            "since": since,
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
    Desktop replied to a sync_request with new messages. Merge them into
    the permanent-ish Redis snapshot, then wake up whoever's waiting.
    """
    session_id = data.get("session_id")
    new_messages = data.get("messages", [])
    key = f"snapshot:{desktop_id}"

    if new_messages:
        pipe = redis_client.pipeline()
        for msg in new_messages:
            pipe.rpush(key, json.dumps(msg))
        pipe.ltrim(key, -SNAPSHOT_TRIM_SIZE, -1)
        await pipe.execute()

    # Refresh the 1-day TTL on every successful login sync, whether or not
    # there were new messages -- this is what makes the snapshot "expire a
    # day after the last login" rather than a day after it was first created.
    if await redis_client.exists(key):
        await redis_client.expire(key, SNAPSHOT_TTL)

    await redis_client.set(f"last_sync:{desktop_id}", str(time.time()))
    await redis_client.publish(f"sync_result:{session_id}", json.dumps(new_messages))


async def get_snapshot(desktop_id: str) -> list:
    """The full last-known chat, straight out of Redis."""
    raw_items = await redis_client.lrange(f"snapshot:{desktop_id}", 0, -1)
    return [json.loads(item) for item in raw_items]


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
async def login_sync(desktop_id: str):
    """
    Website calls this right after login. Tries to pull fresh messages
    from the desktop live; either way, returns the full snapshot from
    Redis so the website always gets "all messages."
    """
    status = "no_data"
    if await is_online(desktop_id):
        new_messages = await request_sync(desktop_id)
        status = "live" if new_messages is not None else "cached"
    else:
        status = "cached"

    snapshot = await get_snapshot(desktop_id)
    if snapshot:
        status = status if status != "no_data" else "cached"
    return {"status": status, "messages": snapshot}


@app.get("/api/status/{desktop_id}")
async def desktop_status(desktop_id: str):
    return {"desktop_id": desktop_id, "online": await is_online(desktop_id)}