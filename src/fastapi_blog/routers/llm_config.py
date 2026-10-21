from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_blog import models
from fastapi_blog.auth import CurrentUser
from fastapi_blog.database import get_db
from fastapi_blog.schemas import (
    LLMConfigCreate,
    LLMConfigPublic,
    ProviderInfo,
)
from fastapi_blog.services.llm_services import LLM_PROVIDERS, resolve_base_url
from fastapi_blog.utils.crypto import decrypt, encrypt

router = APIRouter()


@router.put("", response_model=LLMConfigPublic)
async def upsert_llm_config(
    data: LLMConfigCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LLMConfigPublic:
    """create a new llm config"""

    if data.provider not in LLM_PROVIDERS:
        raise HTTPException(status_code=400, detail="Unknown model provider")

    base_url = resolve_base_url(data.provider, data.base_url)

    result = await db.execute(
        select(models.UserLLMConfig).where(
            models.UserLLMConfig.user_id == current_user.id
        )
    )

    user_cfg = result.scalars().first()

    if user_cfg:
        user_cfg.provider = data.provider
        user_cfg.base_url = base_url
        user_cfg.model = data.model
        user_cfg.encrypted_api_key = encrypt(data.api_key)
    else:
        user_cfg = models.UserLLMConfig(
            user_id=current_user.id,
            provider=data.provider,
            base_url=base_url,
            model=data.model,
            encrypted_api_key=encrypt(data.api_key),
        )
        db.add(user_cfg)

    await db.commit()
    await db.refresh(user_cfg)
    return _to_public(user_cfg)


@router.get("", response_model=LLMConfigPublic | None)
async def get_llm_config(
    current_user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]
) -> LLMConfigPublic | None:
    # return current_user's llm config
    result = await db.execute(
        select(models.UserLLMConfig).where(
            models.UserLLMConfig.user_id == current_user.id
        )
    )

    user_cfg = result.scalars().first()

    if user_cfg is None:
        return None
    else:
        return _to_public(user_cfg)


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers(current_user: CurrentUser) -> list:
    return [ProviderInfo(key=k, **v) for k, v in LLM_PROVIDERS.items()]


def _to_public(config: models.UserLLMConfig) -> LLMConfigPublic:
    # transfer UserLLMConfig model to LLMConfigPublic schema
    api_key = decrypt(config.encrypted_api_key)

    return LLMConfigPublic(
        provider=config.provider,
        base_url=config.base_url,
        model=config.model,
        has_api_key=True if config.encrypted_api_key is not None else False,
        api_key_hints=f"{api_key[:3]}...{api_key[-4:]}",
    )
