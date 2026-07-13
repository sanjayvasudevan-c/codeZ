# relay/routers/calls.py

from fastapi import APIRouter, Depends, HTTPException
from livekit import api as lk_api
import os
import secrets
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database import get_db
from models import CallSession
from websocket import manager

router = APIRouter(prefix="/groups/{group_id}/calls", tags=["Calls"])

LIVEKIT_API_KEY = os.environ.get("LIVEKIT_API_KEY", "devkey")
LIVEKIT_API_SECRET = os.environ.get("LIVEKIT_API_SECRET", "secret")
LIVEKIT_URL = os.environ.get("LIVEKIT_URL", "http://localhost:7880")


@router.post("/start")
async def start_call(group_id: str, user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CallSession).where(CallSession.group_id == group_id, CallSession.ended_at == None))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(400, "A call is already active in this group")

    room_name = f"group-{group_id}-{secrets.token_hex(6)}"
    lk_room_client = lk_api.RoomServiceClient(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        await lk_room_client.create_room(lk_api.CreateRoomRequest(name=room_name))
    except Exception as e:
        print(f"Failed to create LiveKit room (is LiveKit running?): {e}")

    call = CallSession(group_id=group_id, started_by=user_id, livekit_room_name=room_name)
    db.add(call)
    await db.commit()
    await db.refresh(call)

    await manager.broadcast(group_id, {"type": "call.invite", "call_id": call.id, "group_id": group_id})
    return {"call_id": call.id, "room_name": room_name}


@router.post("/{call_id}/join")
async def join_call(call_id: str, user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CallSession).where(CallSession.id == call_id, CallSession.ended_at == None))
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(404, "Call not found or already ended")

    token = lk_api.AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET) \
        .with_identity(str(user_id)) \
        .with_grants(lk_api.VideoGrants(room_join=True, room=call.livekit_room_name))

    return {"livekit_url": LIVEKIT_URL, "livekit_token": token.to_jwt(), "room_name": call.livekit_room_name}