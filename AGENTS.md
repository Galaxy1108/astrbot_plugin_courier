# AGENTS.md

AstrBot 插件（Star）：注册一个 LLM 工具，让机器人能向**同一个机器人接入的不同会话**投递主动消息。

## 参考源码（重要）

本机有 AstrBot 本体仓库：`/home/int_256t/Documents/Projects/AstrBot`（当前 v4.26.x）。
所有 API 以该仓库源码和 `docs/zh/dev/star/` 下的文档为准，不要凭猜测写 API。

- 插件开发指南：`docs/zh/dev/star/plugin-new.md`（新）、`plugin.md`（旧）
- Tool 定义与注册：`docs/zh/dev/star/guides/ai.md`
- 主动消息发送：`docs/zh/dev/star/guides/send-message.md`
- **本插件的现成参照实现**：`astrbot/core/tools/message_tools.py` 中的 `SendMessageToUserTool`（已内置"投递消息到其他会话"功能），以及 `astrbot/core/tools/registry.py` 中 `_evaluate_send_message_tool`（平台主动消息支持判定）

## 插件结构

```
metadata.yaml      # 必需；name/desc/version/author/repo 缺一不可（见 astrbot/cli/utils/plugin.py）
main.py            # 文件名必须叫 main.py；插件类继承 Star（from astrbot.api.star import Context, Star）
logo.png           # 可选，1:1，256x256
requirements.txt   # 可选，pip 依赖
```

- 插件目录/仓库名以 `astrbot_plugin_` 开头、全小写、无空格。
- `metadata.yaml` 可选字段：`display_name`、`short_desc`、`support_platforms`（`ADAPTER_NAME_2_TYPE` 的 key）、`astrbot_version`（PEP 440，如 `>=4.16,<5`）、`i18n`、`pages`。

## 注册 Tool（v4.5.1+ 现代方式）

```py
from pydantic import Field
from pydantic.dataclasses import dataclass
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

@dataclass
class CourierTool(FunctionTool[AstrAgentContext]):
    name: str = "xxx"                    # 工具名
    description: str = "..."             # LLM 看到的描述
    parameters: dict = Field(default_factory=lambda: {...})  # JSON Schema

    async def call(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        ...
```

- 在插件 `__init__` 中注册：`self.context.add_llm_tools(CourierTool())`。
- 弃用：`context.register_llm_tool()`（旧兼容，参数格式有坑）、旧式 `@filter.llm_tool` 装饰器（docstring 必须严格为 `参数名(类型): 描述` 的 `Args:` 格式，且不支持 `parameters=`，否则参数 schema 为空、参数被静默丢弃）。新插件用 dataclass + `add_llm_tools()` 方式。
- 工具内部拿 Star 的 `Context`：`context.context.context`；当前会话事件：`context.context.event`（`event.unified_msg_origin` 是会话标识）。

## 主动消息投递

```py
from astrbot.api.event import MessageChain
await self.context.send_message(unified_msg_origin, message_chain)  # 返回 bool，平台未匹配为 False
```

- 会话标识 `unified_msg_origin` 格式：`platform_id:message_type:session_id`（由 `MessageSession.__str__` 生成，`MessageSession.from_str()` 解析，见 `astrbot/core/platform/message_session.py`）。
- `send_message` 支持传字符串或 `MessageSession`；字符串非法会抛 `ValueError`；**qq_official 平台不支持主动消息**。
- `MessageChain` 组件：`import astrbot.api.message_components as Comp`（`Plain`/`At`/`Image` 等）。
- 跨会话投递要加管理员校验，参照 `SendMessageToUserTool.call()`（安全 issue #7822；`check_admin_permission` 在 `astrbot.core.tools.computer_tools.util`）。

## 区分不同机器人接入（必须处理）

一个 AstrBot 实例可能接入多台机器人（多个 platform 实例），**禁止把消息投递到其他机器人接入的会话**：

- `unified_msg_origin` 的第一段就是 platform 实例 ID（`event.get_platform_id()`，一个机器人接入 = 一个实例，ID 唯一；见 `astrbot/core/platform/astr_message_event.py` 中 `MessageSession(platform_name=platform_meta.id, ...)` 和 `PlatformMetadata.id`）。它**不包含**机器人自身账号信息。
- 机器人自身账号 ID：`event.get_self_id()`（`message_obj.self_id`，如 QQ 号）。
- 推荐做法：插件内维护"会话表"，在收到消息时记录 `(self_id, platform_id, umo)`；工具投递前校验目标 umo 的第一段 == 当前机器人的 platform_id（必要时再比对 self_id），不在表内/不属于当前机器人的会话一律拒绝。
- 注意 `send_message()` 只会在 `platform.meta().id == session.platform_name` 的实例上发送，找不到平台返回 `False`——这能兜底"投错平台"，但**不能**兜底同一实例 ID 前缀下拼错 session_id 的情况，校验逻辑不能省。

## 会话发现与命名（AI 侧设计）

UMO 字符串（`aiocqhttp:group_message:123456`）对 LLM 不可读，**不要要求 LLM 构造/记忆 UMO**。核心设计：

- 插件监听消息事件维护**会话表**，条目含友好名称：
  - 群消息：`message_obj.group.group_name`（`Group` 类，见 `astrbot/core/platform/astrbot_message.py`）+ group_id
  - 私聊：`event.get_sender_name()` / `message_obj.sender.nickname` + user_id
  - 每条记录 `(umo, platform_id, self_id, 名称, 最后活跃时间)`，持久化到 AstrBot `data/` 目录，重启不丢

**名称来源可靠性（已核实，因平台而异）**：
- aiocqhttp：昵称取 `sender.card or nickname`（缺省 `"N/A"`，`aiocqhttp_platform_adapter.py:211-219`）；群名是 OneBot 事件的扩展字段 `group_name`，缺省 `"N/A"`
- telegram 等适配器**不填充** `group_name`；line/slack/mattermost 有
- 适配器内部有 `bot.call_action("get_group_info"/"get_stranger_info")` 主动查名（处理 @ 段时用），但**未暴露给插件**，不要依赖
- **兜底策略**：名称为空/`"N/A"` 时显示 ID——私聊显示「用户 <user_id>」，**群聊只显示群号**（`session_id == group_id`，无"人"的概念）：「群 <group_id>」。群聊条目可附带"最近活跃的发送者昵称"作弱提示（「群 123456（张三最后发言）」），帮助 AI 区分群；`Group.owner/members` 字段适配器构造时为空，不要依赖成员列表。
- **别名**：会话表加**别名**字段，显示优先级 `别名 > 平台名 > ID`；别名由用户/管理员通过指令或工具设置并持久化（群号抽象，别名是唯一可让用户自己控制的显示方案）。
- 注册**三个工具**配合使用：
  1. `list_sessions`：返回人类可读的会话清单（序号/名称/最后活跃时间）
  2. 投递工具接收 `target`（人名/群名/序号）而非 UMO，插件内部解析为 UMO
  3. `rename_session`：给目标会话设置/清除别名，**仅管理员可用**（受 `require_admin` 开关约束）；与投递工具共享同一套 target 解析逻辑，同样校验目标属于当前机器人，写回会话表并持久化
- 解析歧义兜底：同名多个匹配 → 返回候选列表让 LLM 二次确认；`target` 直接传合法 UMO 字符串也兼容（管理员场景）。
- 解析出的 umo 第一段必须等于当前机器人 platform_id（会话表条目已绑定 self_id），双保险防投错机器人。

## 插件配置（管理员权限开关）

- 插件目录放 `_conf_schema.json`（JSON Schema），AstrBot 自动生成配置文件到 `data/config/<plugin_name>_config.json`，并通过 `__init__(self, context: Context, config: AstrBotConfig)` 传入（`AstrBotConfig` 继承 dict）。详见 `docs/zh/dev/star/guides/plugin-config.md`。
- "跨会话投递是否需要管理员"做成配置开关（如 `require_admin`，默认 `true`），工具内通过 `context.context.event.role != "admin"` 判断当前用户角色（参照 `_is_restricted_local_env`）。
- 工具类拿插件配置：在插件 `__init__` 里把 `config` 传给工具实例（构造参数），不要依赖模块级变量。

## 平台与消息注意

- 部分平台不支持主动消息（qq_official、wecom、weixin_official_account 等；wecom_ai_bot 需配置 webhook），按 `registry.py::_evaluate_send_message_tool` 的逻辑处理。
- aiocqhttp 适配器会对 `plain` 消息做 `strip()`，前后可加零宽空格 `\u200b`。
- 工具 `call()` 出错时返回错误字符串即可，不要让异常冒泡导致插件崩溃。

## 调试与开发

- AstrBot 运行时注入插件：把本插件 clone/放到 `/home/int_256t/Documents/Projects/AstrBot/data/plugins/`，用 `uv run main.py` 启动本体（API 默认 http://localhost:6185）。
- 改代码后在 WebUI 插件管理 → 插件 → `...` → `重载插件` 热重载。
- 代码规范（来自 AstrBot 插件开发原则）：提交前 `ruff format`；持久化数据放 AstrBot `data/` 目录而非插件目录；日志用 `from astrbot.api import logger`（或 `self.logger`）；网络请求用 aiohttp/httpx，**禁止 `requests`**。
- 本机代理：`http://127.0.0.1:7897`（需要联网拉取依赖/插件市场/测试外部 API 时使用）。
