from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import fastapi_blog.models as models
from fastapi_blog.config import settings
from fastapi_blog.database import get_db
from fastapi_blog.templating import templates

router = APIRouter()


@router.get("/", include_in_schema=False, name="home")
@router.get("/posts", include_in_schema=False, name="posts")
async def home(request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    count_result = await db.execute(
        select(func.count()).select_from(models.Post),
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .order_by(models.Post.date_posted.desc())
        .limit(settings.posts_per_page),
    )
    posts = result.scalars().all()

    has_more = len(posts) < total
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "posts": posts,
            "title": "Home",
            "total": total,
            "has_more": has_more,
            "limit": settings.posts_per_page,
        },
    )


@router.get("/posts/{post_id}", include_in_schema=False, name="post_page")
async def post_page(
    request: Request, post_id: int, db: Annotated[AsyncSession, Depends(get_db)]
):
    # assert post_id is valid
    if post_id < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="post_id is not valid"
        )

    # assert post exists
    result = await db.execute(
        select(models.Post)
        .where(models.Post.id == post_id)
        .options(selectinload(models.Post.author)),
    )
    post: models.Post | None = result.scalars().first()
    if post:
        title = post.title[:50]
        return templates.TemplateResponse(
            request=request,
            name="post.html",
            context={"post": post, "title": title},
        )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")


@router.get("/users/{user_id}/posts", include_in_schema=False, name="user_posts")
async def user_posts_page(
    request: Request,
    user_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(models.User).where(models.User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    count_result = await db.execute(
        select(func.count())
        .select_from(models.Post)
        .where(models.Post.user_id == user_id),
    )
    total = count_result.scalar() or 0

    result = await db.execute(
        select(models.Post)
        .options(selectinload(models.Post.author))
        .where(models.Post.user_id == user_id)
        .order_by(models.Post.date_posted.desc())
        .limit(settings.posts_per_page),
    )
    posts = result.scalars().all()

    has_more = len(posts) < total

    return templates.TemplateResponse(
        request,
        "user_posts.html",
        {
            "posts": posts,
            "user": user,
            "title": f"{user.username}'s Posts",
            "limit": settings.posts_per_page,
            "has_more": has_more,
        },
    )
