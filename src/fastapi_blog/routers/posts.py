from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import fastapi_blog.models as models
from fastapi_blog.auth import CurrentUser
from fastapi_blog.database import get_db
from fastapi_blog.schemas import (
    PostCreate,
    PostResponse,
    PostUpdate,
)

router = APIRouter()


@router.post(
    "",
    response_model=PostResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_post(
    post: PostCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> PostResponse:
    # allow post title to be same in different posts, but be not same in a user
    result = await db.execute(
        select(models.Post.title).where(
            models.Post.user_id == current_user.id,
            models.Post.title == post.title,
        ),
    )
    existing_title = result.scalars().first()
    if existing_title:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Post title is duplicated",
        )

    new_post = models.Post(
        title=post.title,
        content=post.content,
        user_id=current_user.id,
    )
    # stage the insert
    db.add(new_post)
    # execute the insert and save to the database
    await db.commit()
    # reload the object from database, eager for author so
    # serialization doesn't trigger lazy loading (MissingGreenlet)
    await db.refresh(new_post, attribute_names=["author"])
    return new_post  # pyright: ignore[reportReturnType]


@router.get("", response_model=list[PostResponse], name="get_posts")
async def get_posts(db: Annotated[AsyncSession, Depends(get_db)]) -> list[PostResponse]:
    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .order_by(models.Post.date_posted.desc()),
    )
    posts = result.scalars().all()
    return [PostResponse.model_validate(post) for post in posts]


@router.get("/{post_id}", response_model=PostResponse, name="get_post")
async def get_post(
    post_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> PostResponse:
    result = await db.execute(
        select(models.Post)
        .where(models.Post.id == post_id)
        .options(selectinload(models.Post.author))
        .order_by(models.Post.date_posted.desc()),
    )
    post = result.scalars().first()
    if post:
        return post  # pyright: ignore[reportReturnType]
    else:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )


@router.put("/{post_id}", response_model=PostResponse, name="Update Post Full")
async def update_post_full(
    post_id: int,
    post_data: PostCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> PostResponse:
    # assert post_id exists
    result = await db.execute(
        select(models.Post)
        .where(models.Post.id == post_id)
        .options(selectinload(models.Post.author)),
    )
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    # assert user is the owner of the post
    if current_user.id != post.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the owner of this post, not authorized to update it",
        )

    post.title = post_data.title
    post.content = post_data.content
    post.user_id = current_user.id
    await db.commit()
    await db.refresh(post, attribute_names=["author"])
    return post  # pyright: ignore[reportReturnType]


@router.patch("/{post_id}", response_model=PostResponse, name="Update Post Partial")
async def update_post_partial(
    post_id: int,
    post_data: PostUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> PostResponse:
    # assert post_id exists
    result = await db.execute(
        select(models.Post)
        .where(models.Post.id == post_id)
        .options(selectinload(models.Post.author)),
    )
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    # assert user is the owner of the post
    if current_user.id != post.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the owner of this post, not authorized to update it",
        )

    update_data = post_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(post, field, value)

    await db.commit()
    await db.refresh(post, attribute_names=["author"])
    return post  # pyright: ignore[reportReturnType]


@router.delete("/{post_id}", status_code=status.HTTP_204_NO_CONTENT, name="Delete Post")
async def delete_post(
    post_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> None:
    # assert post_id exists
    result = await db.execute(select(models.Post).where(models.Post.id == post_id))
    post = result.scalars().first()
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found",
        )

    # assert user is the owner of the post
    if current_user.id != post.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the owner of this post, not authorized to delete it",
        )

    await db.delete(post)
    await db.commit()
