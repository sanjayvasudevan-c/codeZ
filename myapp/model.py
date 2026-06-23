from sqlachemy import DeclarativeBase , mapped_column, Mapped

class Base(DelarativeBase):
    pass

class user(base):
    __tablename__ = user
    id : mapped[int]= mapped_column(primary_key=True)
    name : mapped[str] = mapped_column(nullable=False)
    email : mapped[str] = mapped_column(unique=True, nullable=False)
    phone_no : mapped[int] = mapped_column(nullable=False,unique=True)
    hash_password : mapped[str] = mapped_column(nullable=False)
    is_verified : mapped[bool] = mapped_column(default=False)
    role : mapped[str] = mapped_column(default="user")
    is_active : mapped[bool] = mapped_column(default=True)
    is_verified : mapped[bool] = mapped_column(default=False)
    created_at : mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at : mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow
    last_login : mapped[datetime] = mapped_column(default=None)


from pydantic import BaseModel, Field
from typing import Optional,List
from datetime import datetime

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
    

