import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, status
from database import group_collection, message_collection

router = APIRouter(tags=["WebSockets"])

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, group_id: str, websocket: WebSocket):
        await websocket.accept()
        if group_id not in self.active_connections:
            self.active_connections[group_id] = []
        self.active_connections[group_id].append(websocket)

    def disconnect(self, group_id: str, websocket: WebSocket):
        if group_id in self.active_connections:
            self.active_connections[group_id].remove(websocket)
            if not self.active_connections[group_id]:
                del self.active_connections[group_id]

    async def broadcast(self, group_id: str, message: dict):
        if group_id in self.active_connections:
            for connection in self.active_connections[group_id]:
                await connection.send_json(message)

manager = ConnectionManager()

@router.websocket("/ws/{group_id}")
async def websocket_endpoint(websocket: WebSocket, group_id: str):
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        from jose import jwt, JWTError
        from auth import SECRET_KEY, ALGORITHM
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        user_email = payload.get("email")
    except JWTError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    group = await group_collection.find_one({"_id": group_id})
    if not group or user_id not in group.get("members", []):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(group_id, websocket)

    try:
        while True:
            data = await websocket.receive_text()
            
            message_document = {
                "_id": str(uuid.uuid4()),
                "group_id": group_id,
                "sender_id": user_id,
                "sender_email": user_email,
                "content": data,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            await message_collection.insert_one(message_document)
            await manager.broadcast(group_id, message_document)

    except WebSocketDisconnect:
        manager.disconnect(group_id, websocket)

@router.get("/groups/{group_id}/messages")
async def get_chat_history(
    group_id: str,
    limit: int = 50
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    cursor = message_collection.find({"group_id": group_id}).sort("timestamp", -1).limit(limit)
    messages = await cursor.to_list(length=limit)
    
    return messages[::-1]