from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.security import OAuth2PasswordRequestForm
from PIL import UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool

import fastapi_blog.models as models
from fastapi_blog.auth import (
    CurrentUser,
    create_access_token,
    hash_password,
    verify_password,
)
from fastapi_blog.config import settings
from fastapi_blog.database import get_db
from fastapi_blog.schemas import (
    PostResponse,
    Token,
    UserCreate,
    UserPrivate,
    UserPublic,
    UserUpdate,
)
from fastapi_blog.utils.image_utility import delete_profile_image, process_profile_image

router = APIRouter()


@router.post(
    "",
    response_model=UserPrivate,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    user: UserCreate, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserPrivate:
    # assert username is unique
    result = await db.execute(
        select(models.User).where(
            # case-insensitive comparison
            func.lower(models.User.username) == user.username.lower()
        )
    )
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # assert email is unique
    result = await db.execute(
        select(models.User).where(func.lower(models.User.email) == user.email.lower())
    )
    existing_email = result.scalars().first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists",
        )

    # construct a new user object manually
    new_user = models.User(
        username=user.username,
        email=user.email.lower(),
        password_hash=hash_password(user.password),
    )
    # stage the insert
    db.add(new_user)
    # execute the insert and save to the database
    await db.commit()
    # reload the object from database
    await db.refresh(new_user)
    return new_user  # pyright: ignore[reportReturnType]


## login_for_access_token
@router.post("/token", response_model=Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """
    Authenticate user after user logged in and return a JWT access token.
    """

    # Look up user by email (case-insensitive)
    # Note: OAuth2PasswordRequestForm uses "username" field, but we treat it as email
    result = await db.execute(
        select(models.User).where(
            func.lower(models.User.email) == form_data.username.lower(),
        ),
    )
    user = result.scalars().first()

    # Verify user exists and password is correct
    # Don't reveal which one failed (security best practice)
    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token with user id as subject
    access_token_expires = timedelta(
        minutes=settings.access_token_expire_minutes
    )  # when token is expired, the user need to log in again to get a new token
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )
    return Token(access_token=access_token, token_type="bearer")


## get_current_user
@router.get("/me", response_model=UserPrivate)
async def get_current_user(
    current_user: CurrentUser,
):
    return current_user  # pyright: ignore[reportReturnType]


@router.get("/{user_id}", response_model=UserPublic, name="get_user")
async def get_user(
    user_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserPublic:
    # assert user_id is valid
    if user_id < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is not valid",
        )

    result = await db.execute(
        select(models.User)
        .where(models.User.id == user_id)
        .options(selectinload(models.User.posts)),
    )
    user = result.scalars().first()
    if user:
        return user  # pyright: ignore[reportReturnType]
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )


@router.get(
    "/{user_id}/posts",
    response_model=list[PostResponse],
    name="get_user_posts",
)
async def get_user_posts(
    user_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[PostResponse]:
    # assert user_id is valid
    if user_id < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="user_id is not valid"
        )

    # assert user_id exists
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    result = await db.execute(
        select(models.Post)
        .where(models.Post.user_id == user_id)
        .options(selectinload(models.User.posts))
        .order_by(models.Post.date_posted.desc()),
    )
    posts = result.scalars().all()
    return [PostResponse.model_validate(post) for post in posts]


@router.patch("/{user_id}", response_model=UserPublic, name="Update User Partial")
async def update_user_partial(
    user_id: int,
    user_data: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> UserPublic:
    # assert current_user is the same user with user_id
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not allowed to update this user",
        )

    # assert new username is diffrent from old usernames in db
    if user_data.username is not None and user_data.username != current_user.username:
        result = await db.execute(
            select(models.User).where(
                func.lower(models.User.username) == user_data.username.lower()
            )
        )
        existing_username = result.scalars().first()
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )

    # assert new email is diffrent from old emails in db
    if user_data.email is not None and user_data.email != current_user.email:
        result = await db.execute(
            select(models.User).where(
                func.lower(models.User.email) == func.lower(user_data.email)
            )
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists",
            )

    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    update_user = user_data.model_dump(exclude_unset=True)
    for field, value in update_user.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user  # pyright: ignore[reportReturnType]


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, name="Delete User")
async def delete_user(
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> None:
    # assert current_user is the same user with user_id
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not allowed to delete this user",
        )

    # assert user_id exists
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    await db.delete(user)
    await db.commit()


@router.patch("/{user_id}/picture", response_model=UserPrivate)
async def upload_profile_picture(
    user_id: int,
    file: UploadFile,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this user's picture",
        )

    content: bytes = await file.read()

    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Maximum size is {settings.max_upload_size_bytes // (1024 * 1024)}MB",
        )

    try:
        new_filename = await run_in_threadpool(process_profile_image, content)
    except UnidentifiedImageError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image file. Please upload a valid image (JPEG, PNG, GIF, WebP).",
        ) from err

    old_filename = current_user.image_file

    current_user.image_file = new_filename
    await db.commit()
    await db.refresh(current_user)

    if old_filename:
        delete_profile_image(old_filename)

    return current_user


@router.delete("{user_id}/picture", response_model=UserPrivate)
async def delete_profile_picture(
    user_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No authroized to delete this user's picture",
        )

    old_filename = current_user.image_file
    if old_filename is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No profile picture to delete",
        )

    # remove the pointer to old image
    current_user.image_file = None
    await db.commit()
    await db.refresh(current_user)

    delete_profile_image(old_filename)
    return current_user
