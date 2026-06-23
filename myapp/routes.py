from datetime import datetime, timedelta
import random

from fastapi import APIRouter, Depends, HTTPException, status

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db

from models import User

from schemas import (
CreateUser,
UserResponse,
SendOTP,
VerifyOTP,
LoginRequest
)

from auth import (
hash_password,
verify_password,
create_access_token,
get_current_user
)




router = APIRouter(
prefix="/auth",
tags=["Authentication"]
)

otp_store = {}
verified_emails = set()

@router.post("/send-otp")
async def send_otp(
data: SendOTP,
db: AsyncSession = Depends(get_db)
):


result = await db.execute(
    select(User).where(
        User.email == data.email
    )
)

existing_user = result.scalar_one_or_none()

if existing_user:
    raise HTTPException(
        status_code=400,
        detail="Email already registered"
    )

otp = str(
    random.randint(
        100000,
        999999
    )
)

otp_store[data.email] = {
    "otp": otp,
    "expires_at": datetime.utcnow() + timedelta(minutes=5)
}

print(f"OTP for {data.email}: {otp}")

return {
    "message": "OTP sent successfully"
}


@router.post("/verify-otp")
async def verify_otp(
data: VerifyOTP
):


stored_data = otp_store.get(
    data.email
)

if not stored_data:
    raise HTTPException(
        status_code=400,
        detail="OTP not found"
    )

if datetime.utcnow() > stored_data["expires_at"]:
    del otp_store[data.email]

    raise HTTPException(
        status_code=400,
        detail="OTP expired"
    )

if stored_data["otp"] != data.otp:
    raise HTTPException(
        status_code=400,
        detail="Invalid OTP"
    )

verified_emails.add(
    data.email
)

del otp_store[data.email]

return {
    "message": "Email verified successfully"
}


@router.post(
"/signup",
response_model=UserResponse
)
async def signup(
user: CreateUser,
db: AsyncSession = Depends(get_db)
):


if user.email not in verified_emails:
    raise HTTPException(
        status_code=403,
        detail="Verify email first"
    )

result = await db.execute(
    select(User).where(
        User.email == user.email
    )
)

if result.scalar_one_or_none():
    raise HTTPException(
        status_code=400,
        detail="Email already exists"
    )

result = await db.execute(
    select(User).where(
        User.username == user.username
    )
)

if result.scalar_one_or_none():
    raise HTTPException(
        status_code=400,
        detail="Username already exists"
    )

hashed_password = hash_password(
    user.password
)

new_user = User(
    username=user.username,
    email=user.email,
    phone_number=user.phone_number,
    hashed_password=hashed_password,
    is_verified=True
)

db.add(new_user)

await db.commit()

await db.refresh(new_user)

verified_emails.remove(
    user.email
)

return new_user


@router.post("/login")
async def login(
data: LoginRequest,
db: AsyncSession = Depends(get_db)
):


result = await db.execute(
    select(User).where(
        User.email == data.email
    )
)

user = result.scalar_one_or_none()

if not user:
    raise HTTPException(
        status_code=401,
        detail="Invalid credentials"
    )

if not verify_password(
    data.password,
    user.hashed_password
):
    raise HTTPException(
        status_code=401,
        detail="Invalid credentials"
    )

if not user.is_active:
    raise HTTPException(
        status_code=403,
        detail="Account disabled"
    )

token = create_access_token(
    {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role
    }
)

user.last_login_at = datetime.utcnow()

await db.commit()

return {
    "access_token": token,
    "token_type": "bearer"
}


@router.get("/me")
async def me(
current_user: User = Depends(
get_current_user
)
):
return current_user




# --------------------------------------------------------------------------------------------------------

