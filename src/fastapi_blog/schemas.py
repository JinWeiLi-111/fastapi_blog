"""
Schemas define the shape of API request and response bodies.

1. Auto-validate incoming request JSON.
2. Auto-serialize outgoing response JSON.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    email: EmailStr = Field(..., max_length=120)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=200)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=120)


class Token(BaseModel):
    access_token: str
    token_type: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str = Field(..., min_length=1, max_length=50)
    image_file: str | None = None
    image_path: str


class UserPrivate(UserPublic):
    email: EmailStr = Field(..., max_length=120)


class PostBase(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1)


class PostCreate(PostBase):
    # server side judge user_id from token, so no need to pass user_id in request body
    pass


class PostUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    content: str | None = Field(default=None, min_length=1)


class PostResponse(PostBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    date_posted: datetime
    author: UserPublic


class PaginatedPostsResponse(BaseModel):
    posts: list[PostResponse]
    total: int
    skip: int
    limit: int
    has_more: bool


class ForgotPasswordRequest(BaseModel):
    email: EmailStr = Field(max_length=120)


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class LLMConfigCreate(BaseModel):
    provider: str = Field(..., min_length=1, max_length=50)
    base_url: str | None = Field(default=None, max_length=255)
    model: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(..., min_length=1, max_length=128)


class LLMConfigUpdate(BaseModel):
    provider: str | None = Field(..., min_length=1, max_length=50)
    base_url: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, min_length=1, max_length=255)


class LLMConfigPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    provider: str
    base_url: str
    model: str
    has_api_key: bool
    api_key_hints: str


class ProviderInfo(BaseModel):
    key: str
    label: str
    base_url: str
    models: list[str]


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = []


class AiPostDraft(BaseModel):
    title: str
    content: str
