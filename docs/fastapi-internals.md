# FastAPI 内部原理：从 app 创建到请求处理

本文以本仓库（`fastapi_blog`）为样本，从零讲清 FastAPI 的框架分层、app 对象如何注册、注册完成后发生什么、以及一个请求如何被处理完。

所有结论都对照过本机实际安装的源码，版本如下：

```
fastapi    0.141.1
starlette  1.5.0
pydantic   2.13.4
python     3.11
```

> 文中引用的源码行号基于上述版本。升级依赖后行号可能漂移，但机制不变。

---

## 目录

1. [总览：谁负责什么](#1-总览谁负责什么)
2. [ASGI：一切的起点](#2-asgi一切的起点)
3. [阶段 A：app 对象的创建](#3-阶段-aapp-对象的创建)
4. [阶段 B：注册——逐行拆解](#4-阶段-b注册逐行拆解)
5. [注册完成后的注册表全貌](#5-注册完成后的注册表全貌)
6. [阶段 C：启动——lifespan 与中间件栈](#6-阶段-clifespan-与中间件栈)
7. [阶段 D：一个请求的完整生命周期](#7-阶段-d一个请求的完整生命周期)
8. [依赖注入系统](#8-依赖注入系统)
9. [序列化与校验：入站 422 vs 出站 500](#9-序列化与校验入站-422-vs-出站-500)
10. [并发模型](#10-并发模型)
11. [数据库连接：正式环境 vs 测试环境](#11-数据库连接正式环境-vs-测试环境)
12. [阶段 E：关闭](#12-阶段-e关闭)
13. [完整时间线示例](#13-完整时间线示例)
14. [本仓库的实际问题清单](#14-本仓库的实际问题清单)

---

## 1. 总览：谁负责什么

FastAPI 不是从零写的框架。它站在三层之上，自己只加了三样东西。

```
┌─ ASGI server ───────────────────────────────────────────────┐
│  uvicorn                                                     │
│  监听 socket · 解析 HTTP · worker 管理 · lifespan 握手        │
│  ★ 不认识 FastAPI，只认识一个 ASGI callable                   │
└──────────────────────────┬──────────────────────────────────┘
                           │ scope · receive · send
┌──────────────────────────▼──────────────────────────────────┐
│  Starlette   (通用 ASGI toolkit，不依赖 Pydantic)            │
│  Router · Request/Response · Middleware 链 · Mount            │
│  StaticFiles · Jinja2Templates · BackgroundTask · WebSocket   │
└──────────────────────────┬──────────────────────────────────┘
┌──────────────────────────▼──────────────────────────────────┐
│  FastAPI   (继承 Starlette，只加三件事)                       │
│  ① Pydantic 感知的 APIRoute                                   │
│  ② 依赖注入 Depends / Security                                │
│  ③ OpenAPI 自动生成 + /docs /redoc                            │
└──────────────────────────┬──────────────────────────────────┘
┌──────────────────────────▼──────────────────────────────────┐
│  Pydantic       类型注解 → JSON Schema → 校验 / 序列化        │
├─────────────────────────────────────────────────────────────┤
│  SQLAlchemy + Alembic   ORM / connection pool / migration     │
├─────────────────────────────────────────────────────────────┤
│  用户代码   routers · models · schemas · templates            │
└─────────────────────────────────────────────────────────────┘
```

**关键认知**：`fastapi.Request` 实际就是 `starlette.requests.Request` 的重导出。这就是为什么本仓库能同时提供 REST API（`/api/posts`）和 HTML 页面（`/login`）而不用两套代码——它们在同一个 Router 里，只是返回不同的 Response 类型。

---

## 2. ASGI：一切的起点

### 2.1 ASGI callable 的签名

ASGI 应用就是一个可调用对象：

```python
async def __call__(self, scope, receive, send) -> None:
    ...
```

三个参数的含义：

| 参数 | 类型 | 作用 |
|---|---|---|
| `scope` | `dict` | 本次连接/请求的元数据。`type` 字段决定这是哪种连接 |
| `receive` | `async callable` | 从 server 读取事件（请求体分片、lifespan 信号） |
| `send` | `async callable` | 向 server 发送事件（响应头、响应体） |

### 2.2 三种 scope type

```
scope["type"] == "lifespan"    → 进程启停信号（uvicorn 发的第一个 scope）
scope["type"] == "http"        → HTTP 请求
scope["type"] == "websocket"   → WebSocket 连接
```

**顺序很重要**：uvicorn 拿到 app 后，发的第一个 scope 是 `lifespan`，然后才开始 accept HTTP 连接。这直接决定了中间件栈在什么时候构建（见[第 6 节](#6-阶段-clifespan-与中间件栈)）。

### 2.3 一次 HTTP 请求的 scope 长什么样

```python
{
    "type": "http",
    "method": "POST",
    "path": "/api/posts",
    "raw_path": b"/api/posts",
    "query_string": b"",
    "headers": [(b"host", b"localhost:8000"), (b"authorization", b"Bearer xxx")],
    "scheme": "http",
    "server": ("127.0.0.1", 8000),
    "client": ("127.0.0.1", 54321),
    "root_path": "",
    "state": {...},          # 每个请求一份 copy，来自 uvicorn 的 app_state
}
```

`Starlette.__call__` 会额外塞进去 `scope["app"] = self`，这就是 `request.app` 和 `request.url_for()` 能工作的原因。

---

## 3. 阶段 A：app 对象的创建

对应代码：[src/fastapi_blog/main.py:32](../src/fastapi_blog/main.py#L32)

```python
app = FastAPI(lifespan=lifespan)
```

`FastAPI.__init__`（`fastapi/applications.py:924-1018`）按顺序做这些事：

```python
self.openapi_schema = None                 # OpenAPI 缓存，此刻为空
self.webhooks = APIRouter()                # 只用于生成 webhook 文档
self.root_path = root_path
self.state = State()                       # app.state，进程级共享容器

self.dependency_overrides = {}             # ★ 测试覆写就是写这个 dict

self.router = APIRouter(                   # ★ 真正存放路由的对象
    routes=routes,
    redirect_slashes=redirect_slashes,
    dependency_overrides_provider=self,    # router 反持有 app，用来查 overrides
    lifespan=lifespan,                     # ★ 你传的 lifespan 存在 router 上，不在 app 上
    default_response_class=default_response_class,
    dependencies=dependencies,
    ...
)

self.exception_handlers = {} if exception_handlers is None else dict(exception_handlers)
self.exception_handlers.setdefault(HTTPException, http_exception_handler)
self.exception_handlers.setdefault(RequestValidationError, request_validation_exception_handler)
self.exception_handlers.setdefault(
    WebSocketRequestValidationError, websocket_request_validation_exception_handler
)

self.user_middleware = [] if middleware is None else list(middleware)
self.middleware_stack = None               # ★ 还没构建，留到启动时

self.setup()                               # 注册文档相关路由
```

### 3.1 `lifespan` 为什么存在 `router` 上

因为 `include_router` 需要把子 router 的 lifespan 合并进来：

```python
# fastapi/routing.py:3317-3320
self.lifespan_context = _merge_lifespan_context(
    self.lifespan_context,
    router.lifespan_context,
)
```

如果 lifespan 挂在 app 上就做不到了。

### 3.2 `setup()` 注册了什么

`FastAPI.setup()`（`fastapi/applications.py:1105-1158`）用 Starlette 的 `add_route` 注册 4 条普通 `Route`，全部带 `include_in_schema=False`：

| 路径 | 处理函数 | 作用 |
|---|---|---|
| `/openapi.json` | `openapi()` | 返回生成的 OpenAPI schema |
| `/docs` | `swagger_ui_html()` | Swagger UI 页面 |
| `/docs/oauth2-redirect` | `swagger_ui_redirect()` | Swagger 登录回调 |
| `/redoc` | `redoc_html()` | ReDoc 页面 |

**所以你的业务代码还一行没跑，`app.router.routes` 里已经有 4 条了。**

---

## 4. 阶段 B：注册——逐行拆解

`import fastapi_blog.main` 就是执行注册。下面按 [main.py](../src/fastapi_blog/main.py) 从上到下的顺序拆。

### 4.1 `app.mount("/static", ...)`

对应代码：[src/fastapi_blog/main.py:38](../src/fastapi_blog/main.py#L38)

```python
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
```

调用链极短：

```
app.mount()          → starlette/applications.py:92
  self.router.mount()→ starlette/routing.py:715
    routes.append(Mount("/static", app=StaticFiles, name="static"))
```

`Mount` 是**前缀匹配**：路径以 `/static` 开头就整个交给 `StaticFiles` 这个子 ASGI app。

三个特点：

- 不经过 FastAPI 的依赖注入和 Pydantic 校验
- 不出现在 OpenAPI 里
- `name="static"` 供模板里 `url_for('static', path='...')` 反向生成 URL

> 注意 [main.py:36](../src/fastapi_blog/main.py#L36) 的 `BASE_DIR = Path(__file__).resolve().parent`：把静态资源和模板锚定到**文件位置**而不是当前工作目录。这样从任何 cwd 启动都能找到资源。这是很实用的一招。

### 4.2 `Jinja2Templates(...)` 不注册任何东西

对应代码：[src/fastapi_blog/main.py:40](../src/fastapi_blog/main.py#L40)

```python
templates = Jinja2Templates(directory=BASE_DIR / "templates")
```

这只是一个普通对象，和 `app` **没有任何关联**。它只是被 endpoint 函数引用。

### 4.3 装饰器注册的完整调用链

以 `@app.get("/", name="home")` 为例（[main.py:46](../src/fastapi_blog/main.py#L46)）：

```
@app.get("/", include_in_schema=False, name="home")
  │
  └─ FastAPI.get()                        applications.py:1646
       │
       └─ self.router.get()               routing.py:3322
            │
            └─ self.api_route(...)        routing.py:2968  → 返回 decorator
                 │
                 └─ decorator(func)       Python 在函数定义完成时自动调用
                      │
                      └─ self.add_api_route()        routing.py:2889
                           ├─ 合并 router 级配置：
                           │    tags / dependencies / responses / response_class
                           ├─ route = APIRoute(self.prefix + path, endpoint=func, ...)
                           │    ★ 执行 §4.4 的全部计算
                           ├─ self.routes.append(route)
                           └─ self._mark_routes_changed()   # 版本号 +1，缓存失效
                      │
                      └─ return func       ★ 原函数原样返回，没有被包装
```

最后一行 `return func` 很重要：**装饰器只是一个注册动作，`home` 仍然是一个普通函数**，测试里可以直接调用它。

### 4.4 `APIRoute` 在构造时就算好了一切

这是理解 FastAPI 性能的关键：**签名分析、正则编译、Pydantic field 生成全部发生在 import 期，请求期不再重复。**

`APIRoute.__init__`（`routing.py:1193`）委托给 `_populate_api_route_state`（`routing.py:995-1117`）：

```python
route.path            = path
route.name            = get_name(endpoint) if name is None else name
route.path_regex, route.path_format, route.param_convertors = compile_path(path)
route.methods         = {method.upper() for method in methods}
route.unique_id       = route.operation_id or current_generate_unique_id(route)
route.status_code     = int(status_code) if isinstance(status_code, IntEnum) else status_code
route.description     = description or inspect.cleandoc(route.endpoint.__doc__ or "")
route.description     = route.description.split("\f")[0].strip()

route.dependant       = ...           # ★ 分析函数签名得到的依赖树
route.body_field      = ...           # ★ 由 body 参数合成的 Pydantic field
route.response_field  = ...           # ★ 由 response_model 生成的 field

self.app = request_response(self.get_route_handler())   # ★ 最终 ASGI handler
```

`compile_path` 把 `"/posts/{post_id}"` 编译成正则，并记录参数转换器（`post_id: int` → int 转换）。

**一个直接推论**：如果 endpoint 的类型注解有错，import 时就会报错，而不是等第一个请求。

### 4.5 `include_router` 做了什么

对应代码：[src/fastapi_blog/main.py:42-43](../src/fastapi_blog/main.py#L42-L43)

```python
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
```

> ⚠️ **重要更正**：网上大量教程说 `include_router` 会"把子 router 的 route 复制一份、加上 prefix、追加进去"。**这是旧版本的行为，0.141 已经不这么做了。**

现代实现（`routing.py:3268-3320`）：

```python
assert self is not router                          # 不能 include 自己
assert not router._contains_router(self)           # 不能形成循环
if prefix:
    assert prefix.startswith("/")                  # 否则 AssertionError
    assert not prefix.endswith("/")                # 所以 "/api/posts/" 会直接报错
else:
    # prefix 为空时，逐条检查子路由 path 不能为空
    ...

include_context = _RouterIncludeContext.for_include(
    parent_router=self, included_router=router,
    prefix=prefix, tags=tags, dependencies=dependencies, ...
)

self.routes.append(
    _IncludedRouter(original_router=router, include_context=include_context)
)                                                  # ★ 只追加 1 个包装对象
self._mark_routes_changed()

for handler in router.on_startup:
    self.add_event_handler("startup", handler)
for handler in router.on_shutdown:
    self.add_event_handler("shutdown", handler)
self.lifespan_context = _merge_lifespan_context(self.lifespan_context, router.lifespan_context)
```

### 4.6 `_IncludedRouter`：延迟展开的包装体

这是 0.141 引入的设计，`routing.py:1586`：

```python
class _IncludedRouter(BaseRoute):
    original_router: "APIRouter"              # 持有原 router 的引用
    include_context: _RouterIncludeContext    # prefix / tags / dependencies 等配置
    _effective_candidates: list[...]          # 展开结果缓存
    _effective_candidates_version: int | None # 缓存版本号
```

展开发生在 `effective_candidates()`（`routing.py:1601-1624`）：

```python
def effective_candidates(self):
    routes_version = self.original_router._get_routes_version()
    if routes_version == self._effective_candidates_version:
        return self._effective_candidates           # 版本没变 → 直接用缓存
    with self._effective_routes_lock:
        # 双重检查 + 重建
        for route in self.original_router.routes:
            if isinstance(route, _IncludedRouter):
                # 嵌套 include → 递归合并 context
                child_context = self.include_context.combine(route.include_context)
                effective_candidates.append(_IncludedRouter(..., child_context))
                continue
            route_context = self._build_effective_context(route)
            if route_context is not None:
                effective_candidates.append(route_context)
        self._effective_candidates = effective_candidates
        self._effective_candidates_version = routes_version
        return effective_candidates
```

**这个设计带来三个好处**：

1. **子 router 保持"活"的**。`_IncludedRouter` 持有引用而非副本，所以 `include` 之后再往 `posts.router` 加路由，app 也能看到。
2. **prefix 在展开时才拼接**。`_RouterIncludeContext.path_for()`（`routing.py:1369-1370`）就是：
   ```python
   def path_for(self, route):
       return self.prefix + route.path
   ```
   **纯字符串拼接，不会自动补 `/`**。这一点直接导致了[第 14 节](#14-本仓库的实际问题清单)的 bug。
3. **支持嵌套 include**。`combine()`（`routing.py:1343`）把父子 context 层层叠加。

展开的产物是 `_EffectiveRouteContext`（`routing.py:1374`），它包装原始 `APIRoute` 并叠加 prefix、tags 等配置。

### 4.7 `@app.exception_handler(...)`

对应代码：[src/fastapi_blog/main.py:196](../src/fastapi_blog/main.py#L196)、[main.py:220](../src/fastapi_blog/main.py#L220)

```python
@app.exception_handler(StarletteHTTPException)
async def general_http_exception_handler(request, exception): ...

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exception): ...
```

调用链（`applications.py:4770-4774`）：

```python
def decorator(func):
    self.add_exception_handler(exc_class_or_status_code, func)
    return func

def add_exception_handler(self, exc_class_or_status_code, handler):
    self.exception_handlers[exc_class_or_status_code] = handler    # 字典赋值
```

**注意 `setdefault` vs 直接赋值**：阶段 A 用 `setdefault` 写入默认 handler，这里用直接赋值，所以**会覆盖默认值**。

本仓库实际情况（实测 dump）：

```
HTTPException                   -> general_http_exception_handler     ← 已被覆盖
RequestValidationError          -> validation_exception_handler       ← 已被覆盖
WebSocketRequestValidationError -> websocket_request_validation_...  ← 默认值保留
```

此时 `exception_handlers` 只是一个 dict，**还没接进任何 middleware**，要到启动时才生效。

### 4.8 装饰器叠加的顺序

对应代码：[src/fastapi_blog/main.py:46-47](../src/fastapi_blog/main.py#L46-L47)

```python
@app.get("/", include_in_schema=False, name="home")
@app.get("/posts", include_in_schema=False, name="posts")
async def home(request, db): ...
```

**装饰器从下往上执行**，所以先注册 `/posts`，再注册 `/`。结果是**两个独立的 `APIRoute` 指向同一个函数**。

实测路由表中 `/posts`（index 7）排在 `/`（index 8）前面，印证了这个顺序。

---

## 5. 注册完成后的注册表全貌

`import` 结束时，`app.router.routes` 的实际内容（实测 dump）：

```
 #  类型              路径 / 前缀                    name                 进 OpenAPI   来源
 0  Route            /openapi.json                openapi                ✗          setup()
 1  Route            /docs                        swagger_ui_html        ✗          setup()
 2  Route            /docs/oauth2-redirect        swagger_ui_redirect    ✗          setup()
 3  Route            /redoc                       redoc_html             ✗          setup()
 4  Mount            /static                      static                 -          main.py:38
 5  _IncludedRouter  prefix=/api/users  tags=[users]   → 展开为 12 条            main.py:42
 6  _IncludedRouter  prefix=/api/posts  tags=[posts]   → 展开为 6 条             main.py:43
 7  APIRoute         /posts                       posts                  ✗          main.py:47
 8  APIRoute         /                           home                   ✗          main.py:46
 9  APIRoute         /posts/{post_id}             post_page              ✗          main.py:76
10  APIRoute         /users/{user_id}/posts       user_posts             ✗          main.py:103
11  APIRoute         /login                       login_page             ✗
12  APIRoute         /register                    register_page          ✗
13  APIRoute         /account                     account_page           ✗
14  APIRoute         /forgot-password             forgot_password_page   ✗
15  APIRoute         /reset-password              reset_password_page    ✗
```

顶层 16 项，展开后 32 条路由。

其他注册表的状态：

| 属性 | 内容 |
|---|---|
| `app.exception_handlers` | 3 项（见 §4.7） |
| `app.user_middleware` | `[]`（空，本仓库没加 middleware） |
| `app.dependency_overrides` | `{}` |
| `app.middleware_stack` | `None` ← **还没构建** |
| `app.openapi_schema` | `None` ← **还没生成** |
| `app.state` | 空 `State()` |

**列表顺序就是匹配优先级。** Starlette 的 `Router.app`（`starlette/routing.py:674-691`）从上往下遍历，遇到第一个 `Match.FULL` 就交给它：

```python
partial = None
for route in self.routes:
    match, child_scope = route.matches(scope)
    if match == Match.FULL:
        scope.update(child_scope)
        await route.handle(scope, receive, send)
        return
    elif match == Match.PARTIAL and partial is None:
        partial = route              # 记下第一个部分匹配
        partial_scope = child_scope

if partial is not None:
    scope.update(partial_scope)
    await partial.handle(scope, receive, send)   # → 通常返回 405
```

`Match.PARTIAL` 表示路径匹配但 method 不对——这就是 405 Method Not Allowed 的来源。

---

## 6. 阶段 C：lifespan 与中间件栈

### 6.1 uvicorn 加载 app

启动命令形如 `uvicorn fastapi_blog.main:app`。uvicorn 用 `import_from_string` 解析（`uvicorn/importer.py`）：

```python
module_str, _, attrs_str = import_str.partition(":")   # "fastapi_blog.main", "app"
module = importlib.import_module(module_str)           # ← 触发阶段 A + B 的全部注册
instance = module
for attr_str in attrs_str.split("."):
    instance = getattr(instance, attr_str)
return instance
```

**import 这个模块就是执行注册。** 这也解释了为什么 `.env` 和测试里的环境变量必须在 import 之前设好——`Settings()` 在这一刻求值。

uvicorn 不做任何 FastAPI 相关的检查，只要求这是个 ASGI callable。

### 6.2 第一个 scope 是 lifespan

uvicorn 起好 event loop、绑好端口后，第一件事是发起 lifespan 握手（`uvicorn/lifespan/on.py:78-86`）：

```python
scope = {
    "type": "lifespan",
    "asgi": {"version": ..., "spec_version": "2.0"},
    "state": self.state,          # uvicorn 持有的空 dict
}
await app(scope, self.receive, self.send)
```

**这是 `app.__call__` 的第一次调用**（`starlette/applications.py:86-90`）：

```python
async def __call__(self, scope, receive, send):
    scope["app"] = self
    if self.middleware_stack is None:
        self.middleware_stack = self.build_middleware_stack()   # ★ 在这里构建
    await self.middleware_stack(scope, receive, send)
```

> **重要**：中间件栈是在 **lifespan 启动时**构建的，不是第一个 HTTP 请求时。因为 uvicorn 发的第一个 scope 就是 lifespan。

### 6.3 中间件栈的构建

`FastAPI.build_middleware_stack`（`fastapi/applications.py:1020-1068`）：

```python
debug = self.debug
error_handler = None
exception_handlers = {}

for key, value in self.exception_handlers.items():
    if key in (500, Exception):
        error_handler = value          # 单独挑出 500/Exception 交给 ServerErrorMiddleware
    else:
        exception_handlers[key] = value

middleware = (
    [Middleware(ServerErrorMiddleware, handler=error_handler, debug=debug)]
    + self.user_middleware
    + [
        Middleware(ExceptionMiddleware, handlers=exception_handlers, debug=debug),
        Middleware(AsyncExitStackMiddleware),
    ]
)

app = self.router
for cls, args, kwargs in reversed(middleware):
    app = cls(app, *args, **kwargs)    # 从内往外一层层包装
return app
```

**注意 500 的处理是分出去的**：`ServerErrorMiddleware` 在整个链的**最外层**，只有它能看到未捕获的 `Exception`。

构建完成的中间件栈：

```
ServerErrorMiddleware          ← 最外层，只兜 500 / 未捕获 Exception
  user_middleware              ← 本仓库：空
    ExceptionMiddleware        ← 查 scope["starlette.exception_handlers"]
      AsyncExitStackMiddleware ← 关闭文件等资源
        Router (app.router)    ← 路由匹配
```

对非 HTTP scope 的处理（三个 middleware 都会放行）：

| middleware | 判断条件 | 行为 |
|---|---|---|
| `ServerErrorMiddleware` | `if scope["type"] != "http"` | 直接 `await self.app(...)` |
| `ExceptionMiddleware` | `if scope["type"] not in ("http", "websocket")` | 直接放行 |
| `AsyncExitStackMiddleware` | 同上 | 直接放行 |

所以 lifespan scope 一路裸奔到最内层。

### 6.4 硬约束：middleware 只能在启动前加

```python
# starlette/applications.py:98-100
def add_middleware(self, middleware_class, *args, **kwargs):
    if self.middleware_stack is not None:
        raise RuntimeError("Cannot add middleware after an application has started")
```

一旦启动，再加 middleware 直接抛异常。想动态加只能重建 app。

### 6.5 你的 lifespan 在这里执行

进入 `Router.app`（`starlette/routing.py:662-670`）：

```python
if scope["type"] == "lifespan":
    await self.lifespan(scope, receive, send)
    return
```

`Router.lifespan`（`starlette/routing.py:629-654`）是握手实现：

```python
async def lifespan(self, scope, receive, send):
    started = False
    app = scope.get("app")
    await receive()                                    # 收到 {"type": "lifespan.startup"}
    try:
        async with self.lifespan_context(app) as maybe_state:
            if maybe_state is not None:
                if "state" not in scope:
                    raise RuntimeError('The server does not support "state" in the lifespan scope.')
                scope["state"].update(maybe_state)     # 把 yield 出的值注入 state
            await send({"type": "lifespan.startup.complete"})
            started = True
            await receive()                            # ★ 阻塞在这里等 shutdown 信号
    except BaseException:
        exc_text = traceback.format_exc()
        if started:
            await send({"type": "lifespan.shutdown.failed", "message": exc_text})
        else:
            await send({"type": "lifespan.startup.failed", "message": exc_text})
        raise
    else:
        await send({"type": "lifespan.shutdown.complete"})
```

对照本仓库 [main.py:25-29](../src/fastapi_blog/main.py#L25-L29)：

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield                    # startup 阶段什么都不做
    await engine.dispose()   # shutdown 阶段，关闭连接池
```

三个推论：

1. **本仓库 startup 阶段是空的**。建表、预热连接池都没做。注意 [main.py:24](../src/fastapi_blog/main.py#L24) 那句 `# async create all the database tables` 注释**已经和实现脱节**——建表归 [alembic/](../alembic/) 管。
2. `yield` 没有产出值 → `maybe_state` 是 `None` → `scope["state"]` 保持 uvicorn 给的 `{}`。
3. **`await engine.dispose()` 只在正常关闭时执行**。这也解释了为什么测试不会误伤——httpx 的 `ASGITransport` 根本不发 lifespan scope。

### 6.6 state 的三层流转

```
uvicorn LifespanOn.state = {}                         # uvicorn/lifespan/on.py:45
      │  lifespan scope["state"]
      ▼
Router.lifespan: scope["state"].update(maybe_state)   # 本仓库无产出，保持 {}
      │  uvicorn/server.py:119  app_state=self.lifespan.state
      ▼
每个 HTTP scope: "state": self.app_state.copy()       # h11_impl.py:218 —— 注意 copy
      │
      ▼
request.state → State(scope["state"])                 # starlette/requests.py:189-196
```

> ⚠️ **常见混淆**：`app.state` 和 `request.state` 是两回事。
>
> - `app.state`：`FastAPI.__init__` 里创建的 `State()`（`applications.py:950`），**进程级共享**，不经过 scope。
> - `request.state`：来自 `scope["state"]`，**每个请求一份 copy**（`starlette/requests.py:189-196` 里 `State(self.scope["state"])`）。
>
> 想让启动时准备好的对象出现在 `request.state`，得让 lifespan `yield` 一个 dict。

---

## 7. 阶段 D：一个请求的完整生命周期

### 7.1 从 socket 到 handler

```
uvicorn 构造 scope
  └─ await app(scope, receive, send)          # Starlette.__call__
       ├─ scope["app"] = self
       ├─ middleware_stack 已就绪（lifespan 时构建过）
       └─ ServerErrorMiddleware
            └─ ExceptionMiddleware            # 写入 scope["starlette.exception_handlers"]
                 └─ AsyncExitStackMiddleware
                      └─ Router.app           # starlette/routing.py:662
                           ├─ 遍历 self.routes 找 Match.FULL
                           │    routes[6] = _IncludedRouter("/api/posts")
                           │      → effective_candidates() → FULL
                           ├─ scope.update(child_scope)   # 注入 path_params
                           └─ await route.handle(scope, receive, send)
                                └─ APIRoute.handle → self.app(scope, receive, send)
                                     ↑ 注册期就设好了：
                                       self.app = request_response(self.get_route_handler())
                                       (fastapi/routing.py:1223)
```

### 7.2 handler 内部：六个步骤

`get_request_handler` 返回的 `app(request)` 函数（`fastapi/routing.py:406-761`）：

```
1. 读 body                                    routing.py:427-473
   body_field 存在 → await request.json() / request.form()
   JSON 解析失败 → 400 "There was an error parsing the body"

2. await solve_dependencies(...)              routing.py:481
   递归解析 Depends 树，收集 errors
   ★ get_db 在这里创建 AsyncSession

3. await run_endpoint_function(...)           routing.py:706 → 344-354
   is_coroutine ? await dependant.call(**values)
                : await run_in_threadpool(dependant.call, **values)

4. raw_response.background = solved_result.background_tasks
   合并 BackgroundTask

5. await serialize_response(...)              routing.py:727
   response_field.validate() + jsonable_encoder()
   校验失败 → ResponseValidationError → 500

6. Response 反向穿过中间件链 → uvicorn 写回 socket
   AsyncExitStackMiddleware 和各 yield 依赖在此收尾
```

### 7.3 异常在这条链上怎么走

```
handler 内抛出异常
  │
  ├─ HTTPException              → ExceptionMiddleware 查表
  │                                → general_http_exception_handler (main.py:197)
  │                                  → path 以 /api 开头？ JSON : error.html
  │
  ├─ RequestValidationError     → ExceptionMiddleware 查表
  │                                → validation_exception_handler (main.py:221)
  │                                  → 同上分流
  │
  └─ 未捕获的 Exception         → 穿过 ExceptionMiddleware
                                   → ServerErrorMiddleware (最外层) → 500
```

本仓库的 `ExceptionMiddleware` 实际持有的 handlers（实测）：

```
HTTPException                   -> general_http_exception_handler
RequestValidationError          -> validation_exception_handler
```

### 7.4 双轨设计

本仓库一个值得注意的架构选择：**同一个中间件栈里，REST API 和 HTML 页面共用异常出口**。

```python
# main.py:206
if request.url.path.startswith("/api"):
    return await http_exception_handler(request, exception)   # 结构化 JSON
return templates.TemplateResponse(request, "error.html", {...})  # HTML 页面
```

切分点是**路径前缀**。很直接，代价是 API 前缀一旦变动要同步改这两处。

---

## 8. 依赖注入系统

### 8.1 `Dependant` 树在注册期就建好了

`_populate_api_route_state` 里通过 `inspect.signature` 读取函数类型注解，构建 `dependant`。

实测本仓库 `POST /api/posts` 的依赖树：

```
create_post
├ body_params:  post : PostCreate          ← Pydantic model，自动识别为 body
├ query_params: []
└ dependencies:
    ├ get_db                               (use_cache=True)
    └ get_current_user                     ← 来自 CurrentUser = Annotated[User, Depends(...)]
        ├ OAuth2PasswordBearer             ← 从 Authorization header 取 token
        └ get_db                           ← 第二次出现，命中 cache
```

对应用户代码：

```python
# routers/posts.py:26-34
async def create_post(
    post: PostCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: CurrentUser,
) -> PostResponse:
```

```python
# auth.py:72-75
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],   # ★ 同一个 get_db
) -> models.User:
```

### 8.2 三条关键机制

**① `use_cache=True`（默认）** —— 同一个 dependency 在一次请求内只实例化一次。

这就是 `get_current_user` 和 endpoint **共享同一个 `AsyncSession`** 的原因。如果没有这个缓存，一个请求会开两个 session、两条连接、两套事务，`db.commit()` 只提交一半。

**② `dependency_overrides` 查表** —— `solve_dependencies` 拿 `dependency_overrides_provider`（就是 app）去查 key 是否命中：

```python
# conftest.py:98-101
async def override_get_db():
    yield db_session

app.dependency_overrides[get_db] = override_get_db
```

key 必须是**同一个函数对象**。这能成立是因为全仓库都从 `fastapi_blog.database` 导入同一个 `get_db`。

**③ `yield` 形式的 resource** —— generator dependency 的 `yield` 之后是"出口"逻辑，在 Response 生成后执行。

```python
# database.py:25-27
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

`async with` 退出 → `session.close()`。

> ⚠️ **`close()` 不是 `commit()`。** 未提交的事务在这里 ROLLBACK，连接归还 pool。

### 8.3 解析顺序

递归深度优先后序：先解析叶子的 `get_db`，再解析父节点。

---

## 9. 序列化与校验：入站 422 vs 出站 500

这是 FastAPI 里最容易搞混的一对：

| 方向 | 校验对象 | 失败结果 | 由谁处理 |
|---|---|---|---|
| 入站 | 请求体 / query / path 参数 | **422** | `RequestValidationError` → `validation_exception_handler` |
| 出站 | `response_model` / 返回类型注解 | **500** | `ResponseValidationError` → `ServerErrorMiddleware` |

出站校验发生在 `serialize_response`（`routing.py:727`），内部用 `jsonable_encoder()` 把复杂对象转成 JSON-safe：

```
datetime   → ISO 格式字符串
Enum       → 其 value
Decimal    → float
set        → list
ORM object → dict（通过 __dict__，Pydantic model 走 model_dump）
```

**出站校验意义**：即使你的数据库里存了脏数据（比如 `Optional` 字段实际是 `None` 但 `response_model` 声明为 `str`），FastAPI 也会在返回前发现，而不是把不一致的数据发给客户端。

### 9.1 本仓库的一个相关细节

```python
# routers/posts.py:52
await db.refresh(new_post, attribute_names=["author"])
```

`db.refresh()` 是显式再查一次，把 `author` 一起 eager load。注释里写明了原因：

> reload the object from database, eager for author so serialization doesn't trigger lazy loading (MissingGreenlet)

背景是 [database.py:17](../src/fastapi_blog/database.py#L17) 的 `expire_on_commit=False`。默认行为是 commit 后把对象所有属性标为 expired，下次访问属性会触发隐式 SELECT——在 async 语境下这个隐式 IO 会在错误的 greenlet 里执行，抛 `MissingGreenlet`。设了 `expire_on_commit=False` 后属性保持已加载状态，但 `author` 这个 relationship 需要显式补上。

---

## 10. 并发模型

```
                                   ┌─ async def endpoint ──────────────────┐
                                   │ 直接在 event loop 上 await            │
                                   │ 任何阻塞调用 = 卡死整个进程 ⚠          │
   event loop ─────────────────────┤ 本仓库走这条：psycopg3 async driver     │
                                   │   await db.execute(...) 是真让出       │
                                   └───────────────────────────────────────┘

                                   ┌─ def endpoint / def dependency ───────┐
   thread pool ◄───────────────────┤ run_in_threadpool → anyio.to_thread   │
   （默认 40 个并发 token）         │ 阻塞式同步 driver（psycopg2 等）走这条  │
                                   └───────────────────────────────────────┘
```

判定逻辑（`routing.py:394`）：

```python
is_coroutine = _is_coroutine_callable(dependant.call)
```

**在注册期就算好了**，请求期只读这个布尔值：

```python
# routing.py:344-354
async def run_endpoint_function(*, dependant, values, is_coroutine):
    if is_coroutine:
        return await dependant.call(**values)
    else:
        return await run_in_threadpool(dependant.call, **values)
```

**规则**：I/O 若是阻塞的，endpoint 就别用 `async def`。混用会让单个慢查询拖垮整个 loop。

本仓库选了 `+psycopg`（async driver）+ `async def`，是同一条路上的一致选择。

---

## 11. 数据库连接：正式环境 vs 测试环境

### 11.1 正式环境

**配置链**：

```
.env   DATABASE_URL=postgresql+psycopg://bloguser:blogpass@localhost/blog
  │
  ▼  pydantic-settings 读取（优先级：init kwargs > 环境变量 > .env > 默认值）
config.py:37   settings = Settings()          ← import 期求值
  │
  ▼
database.py:10  engine = create_async_engine(SQLALCHEMY_DATABASE_URL)
```

URL 里的 `+psycopg` 指定 driver——psycopg3 的 async 实现。

**`create_async_engine()` 不建立任何 TCP 连接**，只是构造 Engine 对象，里面持有未填充的 connection pool。真正的连接发生在第一次执行 SQL 时（lazy）。所以数据库没起来，进程照样能启动，第一个请求才报错。

**每请求一个 session**（`database.py:25-27`）：

```python
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

`AsyncSessionLocal` 是 `async_sessionmaker`，session 工厂。这个 async generator 被 FastAPI 当 dependency 用，每请求解析一次。

**一次 `POST /api/posts` 的数据流**：

```
solve_dependencies
  └─ get_db 执行到 yield，创建 session（尚未 checkout 连接）
  └─ get_current_user 内部 await db.execute(select(User)...)
       └─ ★ 此刻才从 pool checkout 一条连接，psycopg3 通过 socket 发 SQL

create_post 函数体
  ├─ await db.execute(...)      查 title 重复
  ├─ db.add(new_post)           仅 stage 到 session 的 unit of work，不发 SQL
  ├─ await db.commit()          真正 flush + COMMIT（INSERT 在此刻发出）
  └─ await db.refresh(...)      显式再查一次，把 author 一起 eager load

返回 → session 关闭
  └─ async with 退出 → session.close()
       └─ 有未提交事务则 ROLLBACK，连接 return 回 pool（不关闭）
```

**default pool 参数**（`AsyncAdaptedQueuePool`）：

```
pool_size      = 5
max_overflow   = 10
pool_timeout   = 30
pool_recycle   = -1          # 永不回收
pool_pre_ping  = False       # 不预先探活
```

两个需要注意的后果：

- 多 worker 部署时连接数是 **workers × 15**。4 个 uvicorn worker 对 Postgres 最坏 60 条连接，容易撞上默认 `max_connections=100`。
- `pool_pre_ping=False` + `pool_recycle=-1` 意味着连接可以永久存活。如果中间有 RDS / PgBouncer / 云代理会静默掐掉空闲连接，第一个复用到死连接的请求会报错。加 `pool_pre_ping=True` 是最省事的修法。

**Alembic 走另一条路**（`alembic/env.py:16`）：

```python
config.set_main_option("sqlalchemy.url", settings.database_url)
```

用 `.env` 里同一个 URL 喂给 Alembic（`alembic.ini:89` 的占位符被覆盖），并用 `NullPool` + `asyncio.run()` 起临时 engine，迁移完就 dispose。

### 11.2 测试环境

测试**没有**换成 SQLite 或 mock，而是连真实但独立的数据库（`tests/conftest.py:4`）：

```python
os.environ["DATABASE_URL"] = "postgresql+psycopg://bloguser:blogpass@localhost/test_blog"
```

三件事按顺序发生：

**① 环境变量必须在 import app 之前设好。**

因为 `Settings()` 是 import 期求值的，等你 `import app` 再改就晚了。conftest 把 `os.environ[...]` 全写在最前面、`from fastapi_blog.main import app` 放在第 27 行——**这个顺序是强制的**。环境变量优先级高于 `.env`，所以测试打到 `test_blog`，开发库 `blog` 不受影响。

**② 建表/拆表走 session scope。**

```python
async with test_engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)   # 整个 test session 开始
...
    await conn.run_sync(Base.metadata.drop_all)     # 结束时拆掉
```

用 `Base.metadata.create_all` 而非跑 Alembic——测试要的是"当前 models 定义的 schema"。

**③ 每个 test function 包在一个会回滚的外层事务里。**

```python
# conftest.py:59-80
conn = await test_engine.connect()
trans = await conn.begin()                      # 外层事务，全程不提交

test_async_session = async_sessionmaker(
    bind=conn,                                  # session 绑到这条连接
    join_transaction_mode="create_savepoint",   # ★ commit 退化为 savepoint release
)

async with test_async_session() as session:
    yield session
finally:
    await trans.rollback()                      # ★ 一切归零
    await conn.close()
```

`join_transaction_mode="create_savepoint"` 让 endpoint 里那句 `await db.commit()` 在 SQL 层退化成 `RELEASE SAVEPOINT`，**不会真的提交外层事务**。测试跑完 `trans.rollback()`，这个测试写入的所有行凭空消失。

**④ 用 dependency override 把 endpoint 接到这个 session 上。**

```python
async def override_get_db():
    yield db_session

app.dependency_overrides[get_db] = override_get_db
```

必要性：endpoint 里的 `Depends(get_db)` 用的是生产版 `get_db`，它会从生产 engine 建 session、连到 `blog` 库、**并且自己提交**——下面那层 rollback 就白设计了。override 让 endpoint 和测试拿到**同一个 session 对象**，共享同一个事务。

**⑤ `ASGITransport(app=app)` 不跑 lifespan。**

httpx 把请求直接喂给 ASGI callable，没有真实网络、没有 uvicorn，lifespan 的 startup/shutdown 都不会触发。

### 11.3 对比表

| | 正式环境 | 测试环境 |
|---|---|---|
| database | `.env` → `blog` | conftest 设的环境变量 → `test_blog` |
| engine | `database.py` module-level，AsyncAdaptedQueuePool | 测试自己的，NullPool |
| session 来源 | 每请求新建，来自 pool | 每测试新建，绑在外层事务的连接上 |
| endpoint 拿到哪个 session | 自己的 | 测试的那一个（override） |
| commit 语义 | 真提交 | savepoint release，最后统一 rollback |
| 数据清理 | 不需要 | 自动，靠事务回滚 |
| schema 来源 | Alembic migration | `Base.metadata.create_all` |

---

## 12. 阶段 E：关闭

uvicorn 收到 SIGINT/SIGTERM 时，往 lifespan 的 receive 队列放 `lifespan.shutdown`。`Router.lifespan` 里那句 `await receive()` 返回，`async with` 退出 → **你的 shutdown 代码执行**：

```python
# main.py:29
await engine.dispose()    # 关闭连接池里所有连接
```

然后 `send({"type": "lifespan.shutdown.complete"})`，uvicorn 退出。

---

## 13. 完整时间线示例

`POST /api/posts`，带 `Authorization: Bearer xxx`：

```
[import 期]
  settings = Settings()                    → 读 .env
  engine = create_async_engine(...)        → 不连接
  app = FastAPI(lifespan=...)              → 建注册表 + setup() 注册 4 条文档路由
  mount /static                            → routes[4]
  include_router(users)                    → routes[5] = _IncludedRouter
  include_router(posts)                    → routes[6] = _IncludedRouter
  @app.get × 9                             → routes[7..15]
  @app.exception_handler × 2               → exception_handlers 覆盖默认值

[uvicorn 启动]
  import_from_string("fastapi_blog.main:app")  → 触发上面全部
  发 lifespan scope
    → app.__call__ 第一次调用
      → build_middleware_stack()          ← ★ 中间件栈在此刻定稿
      → 穿过 3 个 middleware（都放行非 http）
      → Router.app → Router.lifespan
        → 你的 lifespan 跑到 yield，startup 什么都不做
        → send lifespan.startup.complete  → uvicorn 打印 "Application startup complete."
    → 阻塞在 await receive()

[请求到达]
  scope = {type:"http", method:"POST", path:"/api/posts", state:{}.copy(), ...}
  → app.__call__ → scope["app"] = app → middleware_stack（已就绪）
    → ServerErrorMiddleware
      → ExceptionMiddleware（写入 scope["starlette.exception_handlers"]）
        → AsyncExitStackMiddleware
          → Router.app → 遍历 routes → routes[6].matches() → 展开 → FULL
            → _IncludedRouter.handle → _EffectiveRouteContext
              → APIRoute.handle → self.app = request_response(handler)
                → 读 body → PostCreate 校验
                → solve_dependencies
                    → get_db 建 session
                    → get_current_user 查 User（复用同一个 session）
                → run_endpoint_function → create_post()
                     await db.execute(...)    ← ★ 此刻才从 pool checkout 真连接
                     await db.commit()        ← INSERT 落盘
                     await db.refresh(...)    ← 补 author，避免 MissingGreenlet
                → serialize_response → PostResponse → 201 JSON
              → 出站穿过中间件链 → uvicorn 写 socket
            → get_db 的 async with 退出 → session.close() → 连接归还 pool

[收到 SIGTERM]
  发 lifespan.shutdown → lifespan 在 yield 之后恢复
    → await engine.dispose()               ← 连接池关闭
    → send lifespan.shutdown.complete
```

---

## 14. 本仓库的实际问题清单

### 14.1 `delete_profile_picture` 路由路径缺 `/`

`src/fastapi_blog/routers/users.py:505`：

```python
@router.delete("{user_id}/picture", response_model=UserPrivate)
```

路径开头少了 `/`。`_RouterIncludeContext.path_for()` 用**纯字符串拼接**（`routing.py:1370`），所以展开后得到：

```
/api/users{user_id}/picture        ← 错误的路径
```

实测请求结果：

```
DELETE /api/users/5/picture   -> 405 Method Not Allowed   ← 前端调用会失败
DELETE /api/users5/picture    -> 401 Not authenticated    ← 这个错误的地址反而能匹配上
```

这个 405 就是 §5 讲的 PARTIAL 匹配：`PATCH /{user_id}/picture` 的路径能匹配，但 method 不对。

**修法**：改成 `"/{user_id}/picture"`。

### 14.2 `tests/conftest.py` 三个问题

**① 参数名拼错**（`conftest.py:41`）：

```python
engine = create_async_engine(url=..., pullclass=NullPool)   # 应为 poolclass
```

实测会抛 `TypeError: Invalid argument(s) 'pullclass' sent to create_engine()`。这个 fixture 是 session scope、按需执行，所以任何测试真的用到 `client` 就会炸。

**② 环境变量大小写不一致**（`conftest.py:88`）：

```python
s3.create_bucket(Bucket=os.environ["s3_bucket_name"])   # 应为 S3_BUCKET_NAME
```

第 7 行设的是大写 `S3_BUCKET_NAME`。会抛 `KeyError`。

**③ 测试函数位置错误**：

`test_create_post_success` 定义在 `conftest.py:152`——**pytest 不收集 conftest.py 里的 test 函数**。而 `tests/test_posts.py` 和 `tests/test_user.py` 都是 0 字节空文件。

所以 `uv run pytest tests/ --co` 返回 `no tests collected`，这是真实的 0，不是配置问题（`plugins: anyio-4.14.2` 已正常加载）。

**修法**：把测试函数挪到 `test_posts.py`。

### 14.3 无害的冗余

`conftest.py:29` 的 `pytest_plugins = ["anyio"]` 是多余的——anyio 的 pytest plugin 通过 entry point 注册，这一行只是重复 import 了一遍 `anyio` 模块，不报错但没作用。

---

## 附：快速参考

### 注册期 vs 运行期

| 在注册期做（import 时） | 在运行期做（每请求） |
|---|---|
| 分析函数签名 → `dependant` 树 | 解析 `dependant` 树，实参化 |
| 编译 path 正则 | 匹配 path |
| 生成 Pydantic field（body / response） | 校验请求体、序列化响应 |
| 判定 `is_coroutine` | 选择 `await` 或 threadpool |
| 构建 ASGI handler `self.app` | 调用 handler |
| 注册路由到 `routes` 列表 | 遍历 `routes` 匹配 |

### 关键对象一览

| 对象 | 位置 | 作用 |
|---|---|---|
| `app.router` | `FastAPI.__init__` | 实际存放 routes 的对象 |
| `app.router.routes` | list | 路由表，顺序即优先级 |
| `app.exception_handlers` | dict | 异常类型 → handler 函数 |
| `app.user_middleware` | list | 用户 middleware 定义 |
| `app.middleware_stack` | ASGI app | 启动时才构建 |
| `app.dependency_overrides` | dict | 测试覆写 |
| `app.state` | `State()` | 进程级共享 |
| `app.openapi_schema` | dict \| None | 懒生成后缓存 |
| `route.dependant` | `Dependant` | 依赖树，注册期构建 |
| `route.app` | ASGI app | 最终 handler，注册期构建 |

### 官方文档

- FastAPI: https://fastapi.tiangolo.com/
- Starlette: https://www.starlette.io/
- ASGI 规范: https://asgi.readthedocs.io/
- Pydantic: https://docs.pydantic.dev/
