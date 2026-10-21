from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_blog import models
from fastapi_blog.agents.runner import run_agent
from fastapi_blog.agents.tools.base import AgentContext
from fastapi_blog.auth import CurrentUser
from fastapi_blog.database import get_db
from fastapi_blog.schemas import ChatMessage, ChatRequest

from .llm_config import get_llm_config

router = APIRouter()


@router.post("/agent/run")
async def agent_run(
    req: ChatRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:

    # get LLM conifg from ORM
    result = await db.execute(
        select(models.UserLLMConfig).where(
            models.UserLLMConfig.user_id == current_user.id
        )
    )
    cfg = result.scalars().first()

    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LLM not configured, Set up your API key in Account settings",
        )

    context = AgentContext(user=current_user, db=db, llm_config=cfg)

    return StreamingResponse(
        run_agent(
            message=req.message,
            history=[m.model_dump() for m in req.history],
            context=context,
        )
    )
