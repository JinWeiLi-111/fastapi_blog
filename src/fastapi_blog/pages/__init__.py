from fastapi import APIRouter

from fastapi_blog.pages import auth, posts

router = APIRouter()
router.include_router(posts.router)
router.include_router(auth.router)
