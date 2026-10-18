from dataclasses import dataclass

from langgraph.runtime import get_runtime
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_blog.models import User, UserLLMConfig


class ToolResult(BaseModel):
    """Structured result returned by a tool, including action, payload and optional confirmation endpoint."""

    tool: str
    action: str
    data: dict
    confirm_endpoint: str | None = None
    confirm_method: str = "POST"
    message: str | None = None  # display in context area


@dataclass
class AgentContext:
    """Per-run context injected into the agent via langgraph 1.x `context_schema`.

    Passed at invocation time as ``config={"context": AgentContext(...)}`` and
    retrieved inside tools with ``get_agent_context()`` (which wraps
    ``langgraph.runtime.get_runtime``). Unlike the old
    ``RunnableConfig.configurable`` approach, the context is strongly typed and
    no longer needs the ``reportTypedDictNotRequiredAccess`` pyright ignore.
    """

    user: User
    db: AsyncSession
    llm_config: UserLLMConfig


def get_agent_context() -> AgentContext:
    """Return the AgentContext bound to the current graph run.

    Must be called from within a tool invoked by the agent (i.e. inside a
    graph run that received ``config={"context": ...}``).
    """
    return get_runtime(AgentContext).context
