import json
from collections.abc import AsyncIterator

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, messages_from_dict
from langchain_core.runnables import RunnableConfig

from fastapi_blog.agents.tools import build_agent
from fastapi_blog.agents.tools.base import AgentContext
from fastapi_blog.config import settings
from fastapi_blog.services.llm_services import build_chat_model


async def run_agent(
    *,
    message: str,
    history: list[dict],
    context: AgentContext,
) -> AsyncIterator[str]:
    """
    SSE stream: token + tool_result + done
    """
    llm: BaseChatModel = build_chat_model(
        base_url=context.llm_config.base_url,
        model=context.llm_config.model,
        encrypted_api_key=context.llm_config.encrypted_api_key,
    )

    agent = build_agent(llm=llm)

    # convert history stored by us before to langchain message
    lc_messages = messages_from_dict(history) if history else []

    config: RunnableConfig = {
        "context": context,  # pyright: ignore[reportAssignmentType]
        "recursion_limit": settings.ai_agent_max_iterations,
    }

    inputs = {"messages": lc_messages + [HumanMessage(content=message)]}

    try:
        async for event in agent.astream_events(inputs, config=config, version="v2"):
            kind = event["event"]
            data = event["data"]

            if kind == "on_chat_model_stream":
                chunk = data.get("chunk")  # pyright: ignore[reportTypedDictNotRequiredAccess]
                content = getattr(chunk, "content", "")
                if content:
                    yield _format_sse("token", {"content": content})

            elif kind == "on_tool_end":
                # tool execute finished, output ToolResult
                output = data.get("output")  # pyright: ignore[reportTypedDictNotRequiredAccess]
                yield _format_sse(
                    "tool_result",
                    output if isinstance(output, dict) else {"output": output},
                )

            elif kind == "on_tool_error":
                err = data.get("error")  # pyright: ignore[reportTypedDictNotRequiredAccess]
                yield _format_sse("tool_error", {"error": str(err)})

    except Exception as e:
        yield _format_sse("error", {"error": str(e)})
        return

    yield _format_sse("done", {})


def _format_sse(event: str, data: dict) -> str:
    """Format a Server-Sent Events message."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
