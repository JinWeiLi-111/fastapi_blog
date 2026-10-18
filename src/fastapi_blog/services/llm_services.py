from typing import cast

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from fastapi_blog.config import settings
from fastapi_blog.schemas import AiPostDraft
from fastapi_blog.utils.crypto import decrypt

SYSTEM_PROMPT = """You are a blog post writing assistant.
Based on the user's request, write a blog post.
You MUST respond with ONLY a JSON object, no markdown, no explanation:
{"title": "<title under 100 chars>", "content": "<post body>"}
The title must be unique and descriptive. Content should be well-structured."""

LLM_PROVIDERS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1"],
    },
    "dashscope": {
        "label": "阿里云 DashScope (通义千问)",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
    },
    "zhipu": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash", "glm-4-air"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
    },
    "custom": {
        "label": "自定义 (OpenAI 兼容)",
        "base_url": "",
        "models": [],
    },
}


def build_chat_model(
    *, base_url: str, model: str, encrypted_api_key: str
) -> ChatOpenAI:
    """
    generate ChatOpenAI model for per-chat
    """
    api_key = decrypt(encrypted_api_key)
    return ChatOpenAI(
        base_url=base_url,
        model=model,
        api_key=SecretStr(api_key),
        max_completion_tokens=settings.ai_default_max_tokens,
        temperature=settings.ai_default_temperature,
    )


async def generate_post_draft(
    *,
    base_url: str,
    model: str,
    encrypted_api_key: str,
    user_prompt: str,
    history: list[dict],
) -> AiPostDraft:
    llm = build_chat_model(
        base_url=base_url,
        model=model,
        encrypted_api_key=encrypted_api_key,
    )

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    # with_structured_output 走 function-calling 模型，强约束返回dict
    structured_llm = llm.with_structured_output(AiPostDraft)
    result = await structured_llm.ainvoke(messages)
    return cast(AiPostDraft, result)


def resolve_base_url(provider: str, user_base_url: str | None) -> str:
    preset = LLM_PROVIDERS[provider]
    if provider == "custom":
        if not user_base_url:
            raise ValueError("base url is required fot custom provider")

    return preset["base_url"]
