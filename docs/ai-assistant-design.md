# AI Assistant 设计方案

> 在 FastAPI Blog 中增加一个独立的 AI 助手页面 `/assistant`，定位为**通用助手**：
> 用户通过对话驱动站内多种操作（创建/编辑 post、查询账号、总结内容等）。
> 每个用户用**自己的 LLM API key**，自选 provider 和 model。
>
> 本文档为唯一设计稿，按从上往下顺序组织，可直接依序开发。

---

## 1. 目标与定位

### 1.1 目标

- 新增 `/assistant` 顶层路由，作为 nav 栏与 Home 并列的一等公民
- 通过对话驱动站内操作（function calling 决定调用哪个 tool）
- 每个 capability 是一个 tool，注册即生效，新增功能不动 agent 主循环
- 每个用户配置自己的 LLM key/model，key 加密存储，永不回传明文

### 1.2 路由策略

- `/` 保持 blog 阅读入口（posts 列表），SEO / 分享 / 未登录访客不受影响
- `/assistant` 作为顶层入口，登录态突出；未登录引导先登录

> 可逆决策：若数据表明多数 session 从"创建"开始，未来可把 `/assistant` 提为默认落地页。

### 1.3 设计原则

1. **tool registry pattern**：capability 即 tool，声明 name / description / JSON schema / handler。新增功能只加 tool。
2. **agent 是草稿与建议者，不直接落库**：tool 返回结构化结果给前端预览，确认动作由用户点击触发走现有 REST 端点，不绕过校验。
3. **per-user LLM 配置**：agent 调用走当前用户的 key/model。
4. **key 加密落库**：Fernet 对称加密，加密 key 来自 `.env`，与 JWT `secret_key` 分离。
5. **永不回传明文 key**：所有响应只返回掩码 `sk-...ab12`。

---

## 2. 整体架构

```
/assistant 页面 (full-page, 对话区 + action 区)
  └─ 对话循环 (agent run)
       └─ POST /api/ai/agent/run (SSE 流式)
            └─ LLM (function calling, 用户自己的 key)
                 ├─ 决定调用哪个 tool
                 ├─ tool_handler 执行 → 返回 ToolResult
                 └─ LLM 据结果继续对话 or 结束
  └─ action 区渲染 tool 返回的结构化结果
       └─ 用户确认 → 走现有 REST 端点落库
```

### 2.1 tool 示例（通用助手覆盖范围）

| tool | 说明 | action | 是否需确认 | 确认端点 |
|---|---|---|---|---|
| `create_post` | 生成 post 草稿 | `preview_post_draft` | 是 | POST /api/posts |
| `update_post` | 生成更新草稿 | `preview_post_update` | 是 | PATCH /api/posts/{id} |
| `list_my_posts` | 查询我的 post 列表 | `render_post_list` | 否（只读） | — |
| `summarize_post` | 总结某篇 post | `render_summary` | 否 | — |
| `get_my_account` | 查询当前账号信息 | `render_account_info` | 否 | — |

---

## 3. 后端设计

### 3.1 目录结构（新增部分）

```
src/fastapi_blog/
├── config.py               # 新增 LLM/加密配置
├── models.py               # 新增 UserLLMConfig
├── schemas.py              # 新增 AI 相关 schema
├── main.py                 # 注册 router + /assistant 路由
├── utils/
│   └── crypto.py           # 新增：Fernet 加解密
├── services/
│   └── llm_service.py     # 新增：provider 预设 + per-user ChatOpenAI 构建
├── agents/                 # 新增目录（LangGraph 实现）
│   ├── __init__.py
│   ├── graph.py            # 用 langchain.agents.create_agent 构建 agent
│   ├── runner.py           # SSE 适配层：astream_events → 前端 SSE
│   ├── schemas.py          # ToolResult 等 agent 专用 schema
│   ├── state.py           # AgentState + context 依赖注入
│   └── tools/
│       ├── __init__.py     # 统一导出 + 注册
│       ├── base.py         # ToolResult + AgentContext（声明式）
│       ├── posts.py        # create_post / list_my_posts / ...
│       └── account.py      # get_my_account
└── routers/
    ├── ai.py               # 新增：agent run 端点
    └── llm_config.py      # 新增：per-user key 配置端点
```

> AI 调用属"外部服务"，放 `services/`；`utils/` 是纯工具函数（crypto）。

### 3.2 依赖 — `pyproject.toml`

```toml
"cryptography>=42.0",
"langgraph>=1.2.15",
"langchain>=1.4.0",         # langchain.agents.create_agent（langgraph 1.x 推荐入口）
"langchain-openai>=1.7.2",   # ChatOpenAI（兼容 OpenAI-compatible endpoint）
"langchain-core>=1.6.0",
```

> 不再直接依赖裸 `openai` SDK：`langchain.agents.create_agent` + `astream_events` 替代了手写 tool-calling 循环和 SSE 拼装。
> ⚠️ `langgraph.prebuilt.create_react_agent` 在 langgraph 1.0 起被标记弃用（V2.0 移除），官方建议改用 `from langchain.agents import create_agent`。本设计采用 `create_agent`。
> `langchain-openai` 的 `ChatOpenAI` 走 OpenAI-compatible 协议，DashScope / GLM / DeepSeek 等 provider 的 base_url 直接传入即可。
> 多 agent 编排时，通过 `langgraph.StateGraph` 的 subgraph / `add_node` / `add_edge` 组合各 agent，无需重写循环。

### 3.3 配置 — `config.py`

```python
# LLM / AI（per-user，不再有全局 key）
ai_encryption_key: SecretStr              # Fernet key，用于加密用户 key
ai_default_max_tokens: int = 1024
ai_default_temperature: float = 0.7
ai_agent_max_iterations: int = 8          # 运行时 recursion_limit，在 runner 的 RunnableConfig 里传
```

> 不再有 `ai_api_key` / `ai_base_url` / `ai_model`——这些改为 per-user 存库。

`.env` 新增（生成 Fernet key：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`）：

```env
AI_ENCRYPTION_KEY=<fernet_key>
```

> ⚠️ 加密 key 一旦设定不可更换，否则已存密文无法解密。部署前生成一次并妥善保管。

### 3.4 数据模型 — `models.py`

新增表 `user_llm_configs`（一对一，独立成表便于后续扩展）：

```python
class UserLLMConfig(Base):
    __tablename__ = "user_llm_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), unique=True, nullable=False, index=True,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)      # "openai"/"dashscope"/"zhipu"/"deepseek"/"custom"
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)     # 由 provider 决定，custom 时用户填
    model: Mapped[str] = mapped_column(String(100), nullable=False)        # 用户填，如 "qwen-plus"
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)   # Fernet 密文
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC),
    )

    user: Mapped[User] = relationship(back_populates="llm_config", uselist=False)
```

`User` 模型新增反向关系：

```python
llm_config: Mapped[UserLLMConfig | None] = relationship(
    back_populates="user",
    cascade="all, delete-orphan",
    uselist=False,
)
```

### 3.5 迁移 — alembic

```bash
alembic revision --autogenerate -m "add user_llm_configs"
alembic upgrade head
```

迁移核心（`down_revision` 指向当前最新 `da80b5ea4225`）：

```python
op.create_table(
    "user_llm_configs",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("user_id", sa.Integer(), nullable=False),
    sa.Column("provider", sa.String(length=50), nullable=False),
    sa.Column("base_url", sa.String(length=255), nullable=False),
    sa.Column("model", sa.String(length=100), nullable=False),
    sa.Column("encrypted_api_key", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("user_id"),
)
op.create_index(op.f("ix_user_llm_configs_id"), "user_llm_configs", ["id"])
op.create_index(op.f("ix_user_llm_configs_user_id"), "user_llm_configs", ["user_id"])
```

### 3.6 加密工具 — `utils/crypto.py`

```python
from cryptography.fernet import Fernet
from fastapi_blog.config import settings

_fernet = Fernet(settings.ai_encryption_key.get_secret_value())


def encrypt(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()
```

### 3.7 Schemas — `schemas.py`

```python
# --- LLM Key 配置 ---
class LLMConfigCreate(BaseModel):
    provider: str = Field(..., min_length=1, max_length=50)
    base_url: str | None = Field(default=None, max_length=255)  # custom 必填，其余由预设填
    model: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(..., min_length=1, max_length=500)


class LLMConfigUpdate(BaseModel):
    provider: str | None = Field(default=None, min_length=1, max_length=50)
    base_url: str | None = Field(default=None, max_length=255)
    model: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = None  # None 表示不改 key


class LLMConfigPublic(BaseModel):
    """对外暴露，永不回传明文 key。"""
    model_config = ConfigDict(from_attributes=True)
    provider: str
    base_url: str
    model: str
    has_api_key: bool
    api_key_hint: str  # 掩码，如 "sk-...ab12"


class ProviderInfo(BaseModel):
    key: str
    label: str
    base_url: str
    models: list[str]


# --- Agent 对话 ---
class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[ChatMessage] = []


class PostDraft(BaseModel):
    title: str
    content: str
```

### 3.8 LLM Service — `services/llm_service.py`

#### Provider 预设

```python
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


def resolve_base_url(provider: str, user_base_url: str | None) -> str:
    """非 custom 时取预设值（忽略用户传入）；custom 时必须用户提供。"""
    preset = LLM_PROVIDERS[provider]
    if provider == "custom":
        if not user_base_url:
            raise ValueError("base_url is required for custom provider")
        return user_base_url
    return preset["base_url"]
```

#### per-user ChatOpenAI 构建

```python
from langchain_openai import ChatOpenAI
from fastapi_blog.config import settings
from fastapi_blog.utils.crypto import decrypt

SYSTEM_PROMPT = """You are a blog post writing assistant.
Based on the user's request, write a blog post.
You MUST respond with ONLY a JSON object, no markdown, no explanation:
{"title": "<title under 100 chars>", "content": "<post body>"}
The title must be unique and descriptive. Content should be well-structured."""


def build_chat_model(*, base_url: str, model: str, encrypted_api_key: str) -> ChatOpenAI:
    """用用户自己的 key 构建 ChatOpenAI。agent 循环和 tool 都用这个。

    走 OpenAI-compatible 协议，DashScope / GLM / DeepSeek 等 provider
    只需 base_url + model + api_key 即可复用。
    """
    api_key = decrypt(encrypted_api_key)
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        max_tokens=settings.ai_default_max_tokens,
        temperature=settings.ai_default_temperature,
    )


async def generate_post_draft(
    *, base_url: str, model: str, encrypted_api_key: str,
    user_prompt: str, history: list[dict],
) -> dict:
    """单次生成 post draft，供 create_post tool 内部调用。

    与 agent 循环独立——这是 tool 内部的子调用，用结构化输出拿 JSON。
    """
    llm = build_chat_model(
        base_url=base_url, model=model, encrypted_api_key=encrypted_api_key,
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})
    # with_structured_output 走 function-calling 模式，强约束返回 dict
    structured_llm = llm.with_structured_output(PostDraft)
    result = await structured_llm.ainvoke(messages)
    return result.model_dump()
```

### 3.9 Tool 抽象 — `agents/tools/base.py`

LangGraph 用 `langchain_core.tools.@tool` 装饰器自动注册 tool（name/description/schema 从函数签名和 docstring 推导）。但有两个我们的定制需求要保留：

1. **`ToolResult` 确认协议**：tool 不直接落库，返回结构化结果让前端预览，确认走 REST。LangGraph 的 tool 可返回任意 JSON-serializable 对象，我们统一返回 `ToolResult` dict。
2. **上下文注入**（user/db/llm_config）：langgraph 1.x 用强类型的 `context_schema` + `Runtime.context` 注入 per-run 上下文，tool 内部用 `langgraph.runtime.get_runtime()` 取出，取代旧的 `RunnableConfig.configurable` 隐式字典。

```python
from dataclasses import dataclass

from langgraph.runtime import get_runtime
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_blog.models import User, UserLLMConfig


class ToolResult(BaseModel):
    """tool 执行结果，序列化后发给前端 action 区渲染。
    保持不变——这是 tool 与前端之间的协议，与框架无关。"""
    tool: str
    action: str            # 前端用此字段选 UI 组件，如 "preview_post_draft"
    data: dict             # 结构化数据，如 {title, content}
    confirm_endpoint: str | None = None  # 确认后调用的 REST 路径，如 "/api/posts"
    confirm_method: str = "POST"
    message: str | None = None  # 给对话区展示的说明文字


@dataclass
class AgentContext:
    """每个 agent run 的上下文，通过 langgraph 1.x 的 context_schema 注入。

    调用时传 ``config={"context": AgentContext(...)}``，tool 内用
    ``get_agent_context()`` 取出。强类型，不再依赖 RunnableConfig.configurable
    的隐式字典，也无需 ``# pyright: ignore[reportTypedDictNotRequiredAccess]``。
    """
    user: User
    db: AsyncSession
    llm_config: UserLLMConfig


def get_agent_context() -> AgentContext:
    """tool 函数内部调用，从当前 graph run 的 Runtime 取上下文。"""
    return get_runtime(AgentContext).context
```

> 迁移要点：原 `Tool` protocol 废弃，改用 langchain `@tool` 装饰器；
> `AgentContext` 改为 `@dataclass` 并作为 `create_agent(..., context_schema=AgentContext)` 声明，
> 取上下文从 `ensure_config()` 改为 `langgraph.runtime.get_runtime(AgentContext).context`。
> 旧的 `RunnableConfig.configurable` 隐式注入 + `pyright: ignore` 写法一并废弃。

### 3.10 第一个 tool — `agents/tools/posts.py`

用 `langchain_core.tools.@tool` 装饰器：name/description/schema 从函数签名 + docstring 自动推导，无需手写 JSON schema。

```python
from langchain_core.tools import tool
from fastapi_blog.agents.tools.base import ToolResult, get_agent_context
from fastapi_blog.services.llm_service import generate_post_draft


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
        user_prompt=f"Topic: {topic}. Tone: {tone}.",
        history=[],  # 单次生成，agent 层管整体 history
    )
    return ToolResult(
        tool="create_post",
        action="preview_post_draft",
        data=draft,
        confirm_endpoint="/api/posts",
        confirm_method="POST",
        message=f'I\'ve drafted a post titled "{draft["title"]}". Review and publish when ready.',
    ).model_dump()
```

> tool 不落库——只返回 `ToolResult` dict + 确认端点。用户点"发布"时前端带 token 调 `/api/posts`，复用全部校验。
> `@tool` 自动把函数注册为 langchain Tool，schema 从类型注解推导；多 agent 编排时各 agent 可共享或选择性绑定 tool 集合。

### 3.11 Tool 注册与导出 — `agents/tools/__init__.py`

LangGraph 模式下不再需要手写 registry 字典——`@tool` 装饰器已把函数转成 langchain `BaseTool` 对象，agent 构建时直接传入 tool 列表即可。这里只做集中导出，便于 agent 按需挑选子集：

```python
from fastapi_blog.agents.tools.posts import create_post
# 后续新增 tool 在此导出

# 主 agent 可用的全部 tool（多 agent 编排时各 agent 可选不同子集）
ALL_TOOLS = [create_post]
```

> 原 `registry.py` 的 `register/get_tool/all_tools` 废弃——tool 列表在 graph 构建时静态绑定，调度交给 LangGraph。

### 3.12 Agent Graph 与 SSE 适配 — `agents/graph.py` + `agents/runner.py`

#### Graph 构建 — `agents/graph.py`

`create_agent`（来自 `langchain.agents`，langgraph 1.x 的推荐入口，替代已弃用的 `langgraph.prebuilt.create_react_agent`）一行构建 ReAct agent（LLM + tool-calling 循环），无需手写循环：

```python
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
    llm: BaseChatModel,
    tools: Sequence[BaseTool] | None = None,
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
        context_schema=AgentContext,   # 让 tool 内可经 get_agent_context() 取 ctx
    )
```

> ⚠️ 不要在构建时传 `recursion_limit`——`create_agent` 的签名里没有这个参数（会报
> `got unexpected keyword arguments`）。`recursion_limit` 是运行时配置，在 runner 调
> `astream_events` 时放进 `RunnableConfig`（见下节）。
> 多 agent 编排扩展点：未来用 `langgraph.StateGraph` + `add_node` 把多个 `build_agent(...)` 作为 subgraph 连起来（如 researcher → writer → reviewer），`create_agent` 作为单 agent 节点复用，循环逻辑零改动。

#### SSE 适配 — `agents/runner.py`

LangGraph 的 `astream_events(version="v2")` 给出结构化事件（`on_chat_model_stream` / `on_tool_start` / `on_tool_end`），适配成前端 SSE 格式：

```python
import json
from collections.abc import AsyncIterator
from langchain_core.runnables import RunnableConfig
from fastapi_blog.agents.graph import build_agent
from fastapi_blog.agents.tools.base import AgentContext
from fastapi_blog.services.llm_service import build_chat_model
from fastapi_blog.config import settings


async def run_agent(
    *, message: str, history: list[dict], context: AgentContext
) -> AsyncIterator[str]:
    """SSE 流：token + tool_result + done。"""
    llm = build_chat_model(
        base_url=context.llm_config.base_url,
        model=context.llm_config.model,
        encrypted_api_key=context.llm_config.encrypted_api_key,
    )
    agent = build_agent(llm)

    # 把 history 转成 langchain messages
    from langchain_core.messages import messages_from_dict
    lc_messages = messages_from_dict(history) if history else []

    # context 经 langgraph 1.x 的 context_schema 通道注入（不再是 configurable 字典）；
    # recursion_limit 在这里作为运行时配置传入
    config: RunnableConfig = {
        "context": context,
        "recursion_limit": settings.ai_agent_max_iterations,
    }

    inputs = {"messages": lc_messages + [("user", message)]}

    async for event in agent.astream_events(inputs, config=config, version="v2"):
        kind = event["event"]
        if kind == "on_chat_model_stream":
            # LLM 输出 token
            chunk = event["data"]["chunk"]
            if chunk.content:
                yield _sse("token", {"content": chunk.content})
        elif kind == "on_tool_end":
            # tool 执行完，输出 ToolResult（create_post 返回的 dict）
            output = event["data"]["output"]
            yield _sse("tool_result", output)
    yield _sse("done", {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"
```

SSE 事件格式（前端据此分流渲染）：

```
event: token
data: {"content": "I'll draft"}

event: tool_result
data: {"tool":"create_post","action":"preview_post_draft","data":{...},"confirm_endpoint":"/api/posts"}

event: done
data: {}
```

> 对比自建：runner 不再有 `for _ in range(...)` 循环、不再手拼 OpenAI tool schema、不再手动把 tool message 追加回 messages——这些全由 `create_agent` + `astream_events` 接管。我们只写"事件 → SSE"的薄适配层。

### 3.13 Routers

#### `routers/llm_config.py` — per-user key 配置端点

挂载到 `/api/users/me/llm-config`（归属 `me` 下，强调只管当前用户，无需校验 user_id 一致性）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/users/me/llm-config` | 当前用户配置（`LLMConfigPublic`），无则返回 null |
| PUT | `/api/users/me/llm-config` | 新建或覆盖（upsert） |
| PATCH | `/api/users/me/llm-config` | 部分更新（不传 `api_key` 则不改 key） |
| DELETE | `/api/users/me/llm-config` | 清除配置 |
| GET | `/api/users/me/llm-config/providers` | provider 预设列表 |

```python
@router.put("", response_model=LLMConfigPublic)
async def upsert_llm_config(
    data: LLMConfigCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LLMConfigPublic:
    if data.provider not in LLM_PROVIDERS:
        raise HTTPException(400, "Unknown provider")
    base_url = resolve_base_url(data.provider, data.base_url)

    result = await db.execute(
        select(models.UserLLMConfig).where(
            models.UserLLMConfig.user_id == current_user.id
        )
    )
    cfg = result.scalars().first()
    if cfg:
        cfg.provider = data.provider
        cfg.base_url = base_url
        cfg.model = data.model
        cfg.encrypted_api_key = encrypt(data.api_key)
        cfg.updated_at = datetime.now(UTC)
    else:
        cfg = models.UserLLMConfig(
            user_id=current_user.id,
            provider=data.provider,
            base_url=base_url,
            model=data.model,
            encrypted_api_key=encrypt(data.api_key),
        )
        db.add(cfg)
    await db.commit()
    await db.refresh(cfg)
    return to_public(cfg)


@router.get("", response_model=LLMConfigPublic | None)
async def get_llm_config(current_user: CurrentUser, db):
    # 返回当前用户配置，无则 null（前端据此显示"未配置"）
    ...


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers(current_user: CurrentUser):
    return [ProviderInfo(key=k, **v) for k, v in LLM_PROVIDERS.items()]
```

`to_public()`：解密后取末 4 位生成 `api_key_hint = f"{key[:3]}...{key[-4:]}"`，`has_api_key=True`。

#### `routers/ai.py` — agent run 端点

```python
from fastapi.responses import StreamingResponse
from fastapi_blog.agents.runner import run_agent
from fastapi_blog.agents.tools.base import AgentContext

@router.post("/agent/run")
async def agent_run(
    req: ChatRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    cfg = await get_user_llm_config(db, current_user.id)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LLM not configured. Set up your API key in Account settings.",
        )
    context = AgentContext(user=current_user, db=db, llm_config=cfg)
    # AgentContext 经 RunnableConfig 注入 tool；agent 循环由 LangGraph 接管
    return StreamingResponse(
        run_agent(
            message=req.message,
            history=[m.model_dump() for m in req.history],
            context=context,
        ),
        media_type="text/event-stream",
    )
```

> router 层不变：仍只是"取用户配置 → 构建 context → 调 runner 返回 SSE"。框架切换的复杂度被 runner.py 吸收。

### 3.14 注册 — `main.py`

```python
from fastapi_blog.routers import ai, llm_config, posts, users

app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(llm_config.router, prefix="/api/users/me/llm-config", tags=["llm-config"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])

# tool 由 @tool 装饰器自动成为 langchain BaseTool，无需手动 register。
# tools/__init__.py 的 ALL_TOOLS 在 build_agent 时引用即可。
```

> 对比自建：不再需要 `register(CreatePostTool())` 启动注册；tool 列表在 `agents/tools/__init__.py` 静态导出，agent 构建时绑定。

---

## 4. 前端设计

### 4.1 页面路由 — `main.py`

```python
@app.get("/assistant", include_in_schema=False, name="assistant_page")
async def assistant_page(request: Request):
    return templates.TemplateResponse(request, "assistant.html", {"title": "AI Assistant"})
```

### 4.2 Nav 入口 — `layout.html`

`loggedInNav` 内、New Post 按钮旁加：

```html
<a class="btn btn-outline-light mb-2 mb-md-0 me-md-2"
   href="{{ url_for('assistant_page') }}">Assistant</a>
```

> 在 `loggedInNav` 内，已有 `updateAuthUI()` 控制显隐，无需额外处理。

### 4.3 Assistant 页面 — 新建 `templates/assistant.html`

两栏（桌面）/ 单栏堆叠（移动）：

```
┌─────────────────────────────────────────────┐
│  对话区 (chat)              │  action 区      │
│  ┌─────────────────────┐   │  ┌────────────┐ │
│  │ user: 帮我写篇...   │   │  │ Post Draft │ │
│  │ assistant: 好的...  │   │  │ [title]    │ │
│  │   [token 流式]      │   │  │ [content]  │ │
│  └─────────────────────┘   │  │ [发布][弃] │ │
│  ┌─────────────────────┐   │  └────────────┘ │
│  │ [输入框] [发送]      │   │  (tool 结果按   │
│  └─────────────────────┘   │   action 切换 UI)│
└─────────────────────────────────────────────┘
```

```html
{% extends "layout.html" %}
{% block content %}
  <div class="row g-3">
    <div class="col-md-7">
      <div class="content-section p-3" id="chatPanel"
           style="height:70vh;display:flex;flex-direction:column;">
        <div id="chatHistory" class="flex-grow-1 overflow-auto mb-3"></div>
        <div class="input-group">
          <input type="text" id="agentInput" class="form-control"
                 placeholder="Ask the assistant...">
          <button class="btn btn-primary" id="agentSendBtn">Send</button>
        </div>
      </div>
    </div>
    <div class="col-md-5">
      <div class="content-section p-3" id="actionPanel"
           style="height:70vh;overflow-y:auto;">
        <p class="text-body-secondary">Action results appear here.</p>
      </div>
    </div>
  </div>
{% endblock content %}
{% block scripts %}
  <script type="module" src="/static/js/assistant.js"></script>
{% endblock scripts %}
```

### 4.4 Assistant 前端逻辑 — `static/js/assistant.js`

核心：解析 SSE 流，按 event 类型分发——`token` 追加到对话区，`tool_result` 按 `action` 渲染对应 UI 组件。

```javascript
import { getToken } from "/static/js/auth.js";
import { getErrorMessage, escapeHtml, showModal } from "/static/js/utils.js";

let history = [];

document.getElementById("agentSendBtn").addEventListener("click", send);

async function send() {
  const input = document.getElementById("agentInput");
  const message = input.value.trim();
  if (!message) return;
  appendChat("user", message);
  input.value = "";

  const token = getToken();
  if (!token) { window.location.href = "/login"; return; }

  const resp = await fetch("/api/ai/agent/run", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ message, history }),
  });
  if (resp.status === 400) {
    const err = await resp.json();
    if (err.detail.includes("not configured")) {
      window.location.href = "/account";  // 引导去配 key
      return;
    }
  }

  // 解析 SSE 流
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        const evt = line.slice(7).trim();
        // 读下一行 data: {...}
        // token → appendChat("assistant", content, streaming)
        // tool_result → renderAction(data)
        // done → break
      }
    }
  }
}

// action 分发：每个 action 对应一个渲染函数
function renderAction(result) {
  const panel = document.getElementById("actionPanel");
  const renderers = {
    preview_post_draft: renderPostDraft,
    render_post_list: renderPostList,
    render_summary: renderSummary,
    render_account_info: renderAccountInfo,
  };
  const fn = renderers[result.action] || renderUnknown;
  panel.innerHTML = "";
  fn(result, panel);
}

function renderPostDraft(result, panel) {
  const { title, content } = result.data;
  panel.innerHTML = `
    <h6>Post Draft</h6>
    <input type="text" id="draftTitle" class="form-control mb-2" value="${escapeHtml(title)}">
    <textarea id="draftContent" class="form-control mb-2" rows="8">${escapeHtml(content)}</textarea>
    <button class="btn btn-success" id="draftPublishBtn">Publish</button>
    <button class="btn btn-outline-secondary ms-2" id="draftDiscardBtn">Discard</button>`;
  document.getElementById("draftPublishBtn").onclick = () =>
    confirmAction(result.confirm_endpoint, result.confirm_method, {
      title: document.getElementById("draftTitle").value,
      content: document.getElementById("draftContent").value,
    });
}

function confirmAction(endpoint, method, body) {
  const token = getToken();
  fetch(endpoint, {
    method,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(body),
  }).then(r => r.ok ? window.location.reload()
                  : r.json().then(err => {
                      document.getElementById("errorMessage").textContent = getErrorMessage(err);
                      showModal("errorModal");
                    }));
}

function appendChat(role, text) {
  history.push({ role, content: text });
  const div = document.createElement("div");
  div.textContent = `${role}: ${text}`;
  document.getElementById("chatHistory").appendChild(div);
}
```

### 4.5 Account 页 AI 设置区块 — `account.html`

在 Change Password 区块下方、Logout 上方插入：

```html
<hr>
<div class="mb-4">
  <h5>AI Assistant Settings</h5>
  <p class="text-body-secondary">
    Configure your own LLM API key. Stored encrypted, never displayed in full.
  </p>
  <form id="llmConfigForm">
    <div class="mb-3">
      <label for="llmProvider" class="form-label">Provider</label>
      <select class="form-select" id="llmProvider" required></select>
    </div>
    <div class="mb-3 d-none" id="customBaseUrlGroup">
      <label for="llmBaseUrl" class="form-label">Base URL (custom provider)</label>
      <input type="url" class="form-control" id="llmBaseUrl">
    </div>
    <div class="mb-3">
      <label for="llmModel" class="form-label">Model</label>
      <input type="text" class="form-control" id="llmModel" required
             placeholder="e.g. qwen-plus">
      <div class="form-text" id="llmModelHints"></div>
    </div>
    <div class="mb-3">
      <label for="llmApiKey" class="form-label">API Key</label>
      <input type="password" class="form-control" id="llmApiKey">
      <div class="form-text" id="llmApiKeyHint"></div>
    </div>
    <button type="submit" class="btn btn-primary" id="saveLlmConfigBtn">Save Settings</button>
    <button type="button" class="btn btn-outline-danger" id="deleteLlmConfigBtn">Clear</button>
  </form>
</div>
```

### 4.6 Account AI 设置逻辑 — 新增 `static/js/llm_config.js`

```javascript
import { getToken } from "/static/js/auth.js";
import { getErrorMessage, showModal } from "/static/js/utils.js";

const PROVIDER_MODELS = {};

async function init() {
  const token = getToken();
  const headers = { Authorization: `Bearer ${token}` };

  // 1. 拉 provider 预设
  const providers = await fetch("/api/users/me/llm-config/providers", { headers })
    .then(r => r.json());
  const sel = document.getElementById("llmProvider");
  providers.forEach(p => {
    sel.insertAdjacentHTML("beforeend",
      `<option value="${p.key}">${p.label}</option>`);
    PROVIDER_MODELS[p.key] = p.models;
  });

  // 2. 拉当前用户配置
  const resp = await fetch("/api/users/me/llm-config", { headers });
  if (resp.ok) {
    const cfg = await resp.json();
    sel.value = cfg.provider;
    document.getElementById("llmModel").value = cfg.model;
    document.getElementById("llmApiKeyHint").textContent = `Current key: ${cfg.api_key_hint}`;
  }
  renderModelHints(sel.value);
  toggleCustomUrl(sel.value);
}

document.getElementById("llmProvider").addEventListener("change", (e) => {
  toggleCustomUrl(e.target.value);
  renderModelHints(e.target.value);
});

document.getElementById("llmConfigForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const apiKey = document.getElementById("llmApiKey").value;
  const body = {
    provider: document.getElementById("llmProvider").value,
    model: document.getElementById("llmModel").value,
    ...(apiKey ? { api_key: apiKey } : {}),  // 留空=不改 key
    ...(document.getElementById("customBaseUrlGroup").classList.contains("d-none")
        ? {} : { base_url: document.getElementById("llmBaseUrl").value }),
  };
  const resp = await fetch("/api/users/me/llm-config", {
    method: "PUT",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${getToken()}` },
    body: JSON.stringify(body),
  });
  if (resp.ok) {
    document.getElementById("successMessage").textContent = "AI settings saved.";
    showModal("successModal");
    init();
  } else {
    const err = await resp.json();
    document.getElementById("errorMessage").textContent = getErrorMessage(err);
    showModal("errorModal");
  }
});

function toggleCustomUrl(provider) {
  document.getElementById("customBaseUrlGroup").classList.toggle("d-none", provider !== "custom");
}
function renderModelHints(provider) {
  const models = PROVIDER_MODELS[provider] || [];
  document.getElementById("llmModelHints").textContent = models.length ? `Suggested: ${models.join(", ")}` : "";
}

init();
```

在 `account.html` 的 scripts 块引入：

```html
<script type="module" src="/static/js/llm_config.js"></script>
```

---

## 5. 数据流

```
浏览器                          FastAPI                     LLM (用户自选 provider)
  │                              │                              │
  │  Account: PUT /api/users/me/llm-config                    │
  │  {provider, model, api_key}  │                              │
  │─────────────────────────────>│  encrypt(api_key)            │
  │                              │  存 user_llm_configs          │
  │  200 (hint only)             │                              │
  │<─────────────────────────────│                              │
  │                              │                              │
  │  /assistant: POST /api/ai/agent/run                       │
  │  Authorization: Bearer <jwt>  │                              │
  │─────────────────────────────>│  get_current_user → User      │
  │                              │  load UserLLMConfig            │
  │                              │  build per-user ChatOpenAI     │
  │                              │  create_agent(llm, tools)      │
  │                              │  LLM(tool calling) ───────────>│
  │                              │<── tool_calls: create_post     │
  │                              │  astream_events → tool 执行   │
  │                              │    → ToolResult(draft)         │
  │                              │  LLM(继续) ───────────────────>│
  │  SSE: token + tool_result    │<── final answer               │
  │<─────────────────────────────│  (明文 key 用后即弃，不缓存)     │
  │  (action 区渲染 draft)        │                              │
  │  用户点发布 → POST /api/posts │  (复用现有校验+落库)            │
  │─────────────────────────────>│                              │
```

---

## 6. 安全设计

| 风险 | 措施 |
|---|---|
| API key 明文落库 | Fernet 对称加密存储 `encrypted_api_key` |
| key 泄露给前端 | 所有响应只返回 `api_key_hint` 掩码，永不回传明文 |
| 跨用户读取配置 | 端点挂 `/api/users/me/...`，用 `CurrentUser`，只能操作自己的配置 |
| 加密 key 泄露 | `ai_encryption_key` 存 `.env`，与 `secret_key` 分离，不进仓库 |
| tool 越权 | tool handler 接收 `context.user`，db 查询带 `user_id` 过滤，不信任 tool 参数里的 id |
| tool 循环失控 | `ai_agent_max_iterations` 上限，超过则中断 |
| 确认绕过 | 落库只走 REST 端点，agent 不直接写库，schema 校验不被绕过 |
| per-user key 串号 | client 每次从 `context.llm_config` 构建，用后即弃 |
| 只读 tool 误写 | 只读 tool 不带 `confirm_endpoint`，前端无确认按钮 |
| 输入超长 | `ChatRequest.message` 限制 `max_length=2000` |
| 内部错误泄露 | router 捕获异常，对外只返回通用 502/400 文案 |

---

## 7. 落地步骤（从上往下开发）

按依赖顺序，每步可独立验证：

### 阶段 1：基础设施
1. **依赖**：`pyproject.toml` 加 `cryptography>=42.0`、`langgraph>=0.2`、`langchain-openai>=0.2`、`langchain-core>=0.3`，`uv lock`
2. **配置**：`.env` 加 `AI_ENCRYPTION_KEY`（生成一次），`config.py` 加字段
3. **加密工具**：新建 `utils/crypto.py`
4. **模型**：`models.py` 加 `UserLLMConfig` + User 反向关系
5. **迁移**：`alembic revision --autogenerate` + `alembic upgrade head`

### 阶段 2：Key 管理
6. **Schemas**：`schemas.py` 加 `LLMConfigCreate/Update/Public`、`ProviderInfo`
7. **LLM service**：新建 `services/llm_service.py`（provider 预设 + `build_chat_model` + `generate_post_draft`）
8. **LLM config router**：新建 `routers/llm_config.py`，`main.py` 注册
9. **后端验证**：`curl PUT /api/users/me/llm-config` 带 token，确认返回 hint 无明文
10. **前端**：`account.html` 加 AI 设置区块 + `static/js/llm_config.js`

### 阶段 3：Agent 骨架（LangGraph）
11. **Tool 抽象**：新建 `agents/tools/base.py`（`ToolResult` + `AgentContext` + `get_agent_context`）
12. **第一个 tool**：`agents/tools/posts.py` 用 `@tool` 装饰 `create_post`
13. **Tool 导出**：`agents/tools/__init__.py` 集中导出 `ALL_TOOLS`
14. **Agent graph**：`agents/graph.py` 用 `create_agent`（`from langchain.agents import create_agent`）构建 agent
15. **SSE 适配**：`agents/runner.py` 用 `astream_events` 适配前端 SSE
16. **AI router**：新建 `routers/ai.py` 的 `agent/run` 端点，`main.py` 注册
17. **前端页面**：新建 `/assistant` 路由 + `assistant.html` + `static/js/assistant.js`
18. **nav 入口**：`layout.html` 加 Assistant 按钮
19. **联调**：登录 → Account 配 key → /assistant → 输入 → draft → 发布 → 首页刷新见新 post

### 阶段 4：扩展 tool 生态（通用助手）
20. `list_my_posts`（只读，验证 `@tool` 扩展性）
21. `get_my_account`（只读，验证跨域能力）
22. `summarize_post`（只读 + LLM 二次调用）
23. `update_post`（需确认）

### 阶段 5：多 agent 编排与体验增强
24. **多 agent**：用 `langgraph.StateGraph` 把多个 `build_agent(...)` 作为 subgraph 串联（如 researcher → writer）
25. 流式打字机效果打磨
26. 对话历史持久化（可选，LangGraph checkpointer）
27. 移动端响应式优化

---

## 8. 验收清单

### Key 管理
- [ ] Account 页有 "AI Assistant Settings" 区块，provider 下拉来自后端预设
- [ ] 选 `custom` 才显示 Base URL 输入；选其他 provider 隐藏（用内置 base_url）
- [ ] 保存后 key 以掩码显示（如 `sk-...ab12`），输入框留空再次保存不覆盖 key
- [ ] 数据库 `user_llm_configs.encrypted_api_key` 为密文，非明文
- [ ] 任何 API 响应都不包含明文 key
- [ ] A 用户的 key 不会被 B 用户的请求使用（`CurrentUser` 隔离）

### Agent
- [ ] nav 栏有 Assistant 入口，仅登录可见
- [ ] `/assistant` 两栏布局，对话区 + action 区
- [ ] 未配置 key 时发送 → 400 引导去 Account
- [ ] 输入"写一篇关于 X 的文章" → 对话区流式输出 + action 区出现 draft
- [ ] draft 可编辑 title/content，点发布走 `/api/posts` 落库
- [ ] 重复标题 → 走 posts 校验返回 400，不绕过
- [ ] `list_my_posts` tool 返回列表渲染在 action 区（只读无确认按钮）
- [ ] 新增一个 tool 只需：`@tool` 函数 + 导出到 `ALL_TOOLS` + 前端加 renderer
- [ ] agent 循环不超过 `ai_agent_max_iterations`（LangGraph recursion_limit）上限
- [ ] 多 agent 编排：用 `StateGraph` 串联多个 `build_agent` subgraph，单 agent 循环逻辑零改动
