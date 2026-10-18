from langchain_core.tools import tool

from fastapi_blog.agents.tools.base import ToolResult, get_agent_context
from fastapi_blog.services.llm_services import generate_post_draft


@tool
async def create_post(topic: str, tone: str = "technical") -> dict:
    """Generate a blog post draft from the user's description.
    Returns a preview for confirmation before publishing.

    Args:
        topic: What the post should be about.
        tone: Writing style - "casual", "technical", or "formal".
    """
    ctx = get_agent_context()
    draft = await generate_post_draft(
        base_url=ctx.llm_config.base_url,
        model=ctx.llm_config.model,
        encrypted_api_key=ctx.llm_config.encrypted_api_key,
        user_prompt=f"Topic : {topic}. Tone: {tone}.",
        history=[],
    )

    return ToolResult(
        tool="create_post",
        action="",
        data=draft.model_dump(),
        confirm_endpoint="/api/create_post",
        confirm_method="POST",
        message=f'I\'ve drafted a post titled "{draft.title}". Review and publish when ready.',
    ).model_dump()
