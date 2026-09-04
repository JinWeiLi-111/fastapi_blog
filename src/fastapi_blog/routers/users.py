from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import fastapi_blog.models as models
from fastapi_blog.database import get_db
from fastapi_blog.schemas import (
    PostResponse,
    UserCreate,
    UserPrivate,
    UserPublic,
    UserUpdate,
)

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
        select(models.User).where(models.User.username == user.username)
    )
    existing_user = result.scalars().first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    # assert email is unique
    result = await db.execute(
        select(models.User).where(models.User.email == user.email)
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
        email=user.email,
    )
    # stage the insert
    db.add(new_user)
    # execute the insert and save to the database
    await db.commit()
    # reload the object from database
    await db.refresh(new_user)
    return new_user  # pyright: ignore[reportReturnType]


@router.get("/{user_id}", response_model=UserResponse, name="get_user")
async def get_user(
    user_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> UserResponse:
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


@router.patch("/{user_id}", response_model=UserResponse, name="Update User Partial")
async def update_user_partial(
    user_id: int,
    user_data: UserUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserResponse:
    # assert user_id exists
    result = await db.execute(
        select(models.User)
        .where(models.User.id == user_id)
        .options(selectinload(models.User.posts)),
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # assert new username is diffrent from old usernames in db
    if user_data.username is not None and user_data.username != user.username:
        result = await db.execute(
            select(models.User).where(models.User.username == user_data.username)
        )
        existing_username = result.scalars().first()
        if existing_username:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )

    # assert new email is diffrent from old emails in db
    if user_data.email is not None and user_data.email != user.email:
        result = await db.execute(
            select(models.User).where(models.User.email == user_data.email)
        )
        existing_email = result.scalars().first()
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already exists",
            )

    update_user = user_data.model_dump(exclude_unset=True)
    for field, value in update_user.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return user  # pyright: ignore[reportReturnType]


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, name="Delete User")
async def delete_user(user_id: int, db: Annotated[AsyncSession, Depends(get_db)]):
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
