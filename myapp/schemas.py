from pydantic import BaseModel, EmailStr

class CreateUser(BaseModel):
    username: str
    email: EmailStr
    phone_number: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    phone_number: str

class SendOTP(BaseModel):
    email: EmailStr

class VerifyOTP(BaseModel):
    email: EmailStr
    otp: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    class Config:
        from_attributes = True