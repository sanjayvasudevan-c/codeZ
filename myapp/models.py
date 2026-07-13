from sqlalchemy.orm import Mapped, DeclarativeBase, mapped_column
from datetime import datetime

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "user"
    id : Mapped[int]= mapped_column(primary_key=True)
    username : Mapped[str] = mapped_column(nullable=False)
    email : Mapped[str] = mapped_column(unique=True, nullable=False)
    phone_number : Mapped[str] = mapped_column(nullable=False,unique=True)
    hashed_password : Mapped[str] = mapped_column(nullable=False)
    is_verified : Mapped[bool] = mapped_column(default=False)
    role : Mapped[str] = mapped_column(default="user")
    is_active : Mapped[bool] = mapped_column(default=True)
    created_at : Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at : Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login : Mapped[datetime] = mapped_column(default=None)
    identity_public_key : Mapped[str] = mapped_column(nullable=True)

import uuid
from sqlalchemy import String, Integer, DateTime, func

def gen_uuid():
    return str(uuid.uuid4())

class CallSession(Base):
    __tablename__ = "call_sessions"
    id : Mapped[str] = mapped_column(String, primary_key=True, default=gen_uuid)
    group_id : Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_by : Mapped[int] = mapped_column(Integer, nullable=False)
    livekit_room_name : Mapped[str] = mapped_column(String, nullable=False)
    started_at : Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    ended_at : Mapped[datetime] = mapped_column(DateTime, nullable=True)

from pydantic import BaseModel, Field
from typing import Optional, List

class GroupDescription(BaseModel):
    group_id : str = Field(Alias="_id")
    group_name : str
    admin_id : str
    admin_email : str
    github_repo_link : str
    created_at : datetime
    members : List[str] = []
    invite_token : str

class MessageDescription(BaseModel):
    id : Optional[str] = Field(None, alias="_id")
    member_id : str
    group_id : str
    message : str
    sender : str
    timestamp : datetime
    

