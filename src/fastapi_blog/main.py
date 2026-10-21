from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from fastapi_blog.database import engine
from fastapi_blog.pages import router as pages_router
from fastapi_blog.routers import ai, llm_config, posts, users
from fastapi_blog.templating import BASE_DIR, templates


# async create all the database tables
@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    # Shut down
    await engine.dispose()


app = FastAPI(lifespan=lifespan)

# Anchor static/media resources to the package directory so the app works
# regardless of the working directory.
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(
    llm_config.router, prefix="/api/users/me/llm-config", tags=["llm-config"]
)
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])

# Server-rendered page endpoints (HTML responses, excluded from the OpenAPI schema).
app.include_router(pages_router, tags=["pages"])


@app.exception_handler(StarletteHTTPException)
async def general_http_exception_handler(
    request: Request, exception: StarletteHTTPException
):
    message = (
        exception.detail
        if exception.detail
        else "An error occurred. Please check your request and try again."
    )

    if request.url.path.startswith("/api"):
        return await http_exception_handler(request, exception)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": exception.status_code,
            "title": exception.status_code,
            "message": message,
        },
        status_code=exception.status_code,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exception: RequestValidationError
):
    if request.url.path.startswith("/api"):
        return await request_validation_exception_handler(request, exception)
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "title": status.HTTP_422_UNPROCESSABLE_CONTENT,
            "message": "Invalid request. Please check your input and try again.",
        },
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
    )
