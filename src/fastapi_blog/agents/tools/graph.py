from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from fastapi_blog.agents.tools import ALL_TOOLS
from fastapi_blog.agents.tools.base import AgentContext

AGENT_SYSTEM_PROMPT = """You are a helpful blog site assistant.
You can create posts, list the user's posts, summarize posts, etc.
Use the provided tools when appropriate. Always respond concisely."""


def build_agent(
    llm: BaseChatModel, tools: Sequence[BaseTool] | None = None
) -> CompiledStateGraph:
    """Build a ReAct agent bound to the per-user LLM.

    Args:
        llm: per-user chat model (e.g. the ChatOpenAI returned by
            `build_chat_model`), already bound to the user's key/model.
        tools: tool subset to expose; defaults to `ALL_TOOLS`. Pass a
            different subset when orchestrating multiple agents.

    Returns:
        A compiled langgraph agent (CompiledStateGraph). Invoke via
        `.ainvoke()` / `.astream()` / `.astream_events()` at run time.
    """

    return create_agent(
        model=llm,
        tools=tools or ALL_TOOLS,
        system_prompt=AGENT_SYSTEM_PROMPT,
        context_schema=AgentContext,
    )  # pyright: ignore[reportReturnType]
