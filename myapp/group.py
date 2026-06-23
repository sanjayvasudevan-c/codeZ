import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from database import group_collection, message_collection
from model import Group, Message
from auth import get_current_user

router = APIRouter(prefix="/groups", tags=["Group Management"])

@router.post("/create")
async def create_group(
    group_name: str,
    github_repo_link: str,
    creator: User = Depends(get_current_user)
):
    group_id = str(uuid.uuid4())
    invite_token = str(uuid.uuid4()).replace("-", "")[:12] 

    group = Group(
        group_id=group_id,
        group_name=group_name,
        admin_id=creator.id,
        admin_email=creator.email,
        github_repo_link=github_repo_link,
        created_at=datetime.now(timezone.utc),
        members=[creator.id],
        invite_token=invite_token 
    )

    await group_collection.insert_one(group.dict(by_alias=True))
    
    invite_link = f"http://localhost:8000/groups/join/{invite_token}"
    
    return {
        "group": group,
        "invite_link": invite_link
    }

@router.post("/join/{invite_token}")
async def join_group(
    invite_token: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"invite_token": invite_token})
    if not group:
        raise HTTPException(status_code=404, detail="Invalid invite token")

    created_time = group["created_at"].replace(tzinfo=timezone.utc) if group["created_at"].tzinfo is None else group["created_at"]
    if datetime.now(timezone.utc) - created_time > timedelta(minutes=15):
        raise HTTPException(status_code=410, detail="This invite link has expired (15 min limit exceeded).")

    if user.id in group["members"]:
        raise HTTPException(status_code=400, detail="Already a member of the group")

    await group_collection.update_one(
        {"_id": group["_id"]},
        {"$push": {"members": user.id}}
    )

    return {"message": f"User {user.email} has joined the group {group['group_name']}"}

@router.post("/leave/{group_id}")
async def leave_group(
    group_id: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if user.id not in group["members"]:
        raise HTTPException(status_code=400, detail="You are not a member of this group")

    if group["admin_id"] == user.id:
        raise HTTPException(
            status_code=400, 
            detail="Admins cannot leave without transferring ownership first."
        )

    await group_collection.update_one(
        {"_id": group_id},
        {"$pull": {"members": user.id}}
    )
    return {"message": "You have successfully left the group"}

@router.post("/{group_id}/kick/{member_id}")
async def kick_member(
    group_id: str,
    member_id: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group["admin_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only admins can kick members")

    if member_id not in group["members"]:
        raise HTTPException(status_code=400, detail="User is not a member of this group")

    await group_collection.update_one(
        {"_id": group_id},
        {"$pull": {"members": member_id}}
    )
    return {"message": f"Member {member_id} has been removed"}

@router.post("/change_name/{group_id}")
async def change_group_name(
    group_id: str,
    new_name: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group["admin_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only the admin can change the group name")

    await group_collection.update_one(
        {"_id": group_id},
        {"$set": {"group_name": new_name}}
    )
    return {"message": f"Group name changed to {new_name}"}

@router.post("/changegroup_admin/{group_id}")
async def change_group_admin(
    group_id: str,
    new_admin_id: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group["admin_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only the current admin can change the admin")

    if new_admin_id not in group["members"]:
        raise HTTPException(status_code=400, detail="New admin must be a member of the group")

    await group_collection.update_one(
        {"_id": group_id},
        {"$set": {"admin_id": new_admin_id}}
    )
    return {"message": f"Admin changed to user with ID {new_admin_id}"}

@router.post("/{group_id}/reset-invite")
async def reset_invite_token(
    group_id: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group["admin_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only the admin can reset the invite link")

    new_token = str(uuid.uuid4()).replace("-", "")[:12]

    await group_collection.update_one(
        {"_id": group_id},
        {"$set": {"invite_token": new_token, "created_at": datetime.now(timezone.utc)}}
    )

    return {
        "message": "Invite link updated successfully",
        "invite_link": f"http://localhost:8000/groups/join/{new_token}"
    }

@router.post("/delete/{group_id}")
async def delete_group(
    group_id: str,
    user: User = Depends(get_current_user)
):
    group = await group_collection.find_one({"_id": group_id})
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")

    if group["admin_id"] != user.id:
        raise HTTPException(status_code=403, detail="Only the admin can delete the group")

    await group_collection.delete_one({"_id": group_id})
    return {"message": f"Group {group['group_name']} has been deleted"}

