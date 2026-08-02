import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext
from astrbot.core.config import AstrBotConfig
from astrbot.core.platform.message_type import MessageType
from pydantic import Field
from pydantic.dataclasses import dataclass

_SESSIONS_KEY = "courier_sessions_v1"
"""会话表在 KV store 中的键名。"""


class SessionTable:
    """会话表：记录当前 AstrBot 实例下所有机器人见过的会话。

    条目结构（key 为 unified_msg_origin 字符串）:
    {
        "self_id": str,                 # 机器人自身账号 ID
        "platform_id": str,             # 平台实例 ID（umo 第一段）
        "session_type": "group|private",
        "name": str,                    # 群名 / 昵称，可能为 "N/A"
        "alias": str | None,            # 用户设置的别名
        "group_id": str,                # 群聊专用
        "user_id": str,                 # 私聊专用
        "last_sender_name": str,        # 群聊最近发言者昵称（弱提示）
        "last_active": float,           # 最后活跃时间戳
    }
    """

    def __init__(self, star: Star) -> None:
        self._star = star
        self._sessions: dict[str, dict] = {}

    async def load(self) -> None:
        data = await self._star.get_kv_data(_SESSIONS_KEY, {})
        if isinstance(data, dict):
            self._sessions = data

    async def save(self) -> None:
        await self._star.put_kv_data(_SESSIONS_KEY, self._sessions)

    def record_event(self, event: AstrMessageEvent) -> None:
        """根据收到的事件更新会话表条目（仅内存，调用方负责 save）。"""
        message_obj = event.message_obj
        if message_obj is None:
            return
        self_id = event.get_self_id()
        umo = event.unified_msg_origin
        if not self_id or not umo:
            return
        message_type = event.get_message_type()
        sender_name = event.get_sender_name()
        existing = self._sessions.get(umo)
        alias = existing.get("alias") if existing else None

        if message_type == MessageType.GROUP_MESSAGE:
            group = getattr(message_obj, "group", None)
            group_id = (
                str(group.group_id)
                if group and group.group_id
                else event.get_group_id()
            )
            group_name = str(group.group_name) if group and group.group_name else ""
            entry = {
                "self_id": self_id,
                "platform_id": event.get_platform_id(),
                "session_type": "group",
                "name": group_name or "N/A",
                "alias": alias,
                "group_id": group_id,
                "last_sender_name": sender_name or "",
                "last_active": time.time(),
            }
        elif message_type == MessageType.FRIEND_MESSAGE:
            entry = {
                "self_id": self_id,
                "platform_id": event.get_platform_id(),
                "session_type": "private",
                "name": sender_name or "N/A",
                "alias": alias,
                "user_id": event.get_sender_id(),
                "last_active": time.time(),
            }
        else:
            return
        self._sessions[umo] = entry

    def display_name(self, entry: dict) -> str:
        """会话的展示名：别名 > 平台名 > ID 兜底。"""
        if entry.get("alias"):
            label = f"「{entry['alias']}」"
            return f"群{label}" if entry["session_type"] == "group" else f"用户{label}"
        name = entry.get("name") or ""
        if name and name != "N/A":
            if entry["session_type"] == "group":
                last_sender = entry.get("last_sender_name") or ""
                suffix = f"（{last_sender}最后发言）" if last_sender else ""
                return f"群「{name}」({entry['group_id']}){suffix}"
            return f"用户「{name}」({entry['user_id']})"
        if entry["session_type"] == "group":
            last_sender = entry.get("last_sender_name") or ""
            suffix = f"（{last_sender}最后发言）" if last_sender else ""
            return f"群 {entry['group_id']}{suffix}"
        return f"用户 {entry['user_id']}"

    def list_for_event(self, event: AstrMessageEvent) -> list[tuple[str, dict]]:
        """返回 (umo, entry) 列表，仅包含当前机器人的会话，按最后活跃时间降序。"""
        platform_id = event.get_platform_id()
        self_id = event.get_self_id()
        entries = [
            (umo, entry)
            for umo, entry in self._sessions.items()
            if entry.get("platform_id") == platform_id
            and entry.get("self_id") == self_id
        ]
        entries.sort(key=lambda item: item[1].get("last_active", 0), reverse=True)
        return entries

    def resolve_target(
        self, event: AstrMessageEvent, target: str
    ) -> tuple[str | None, dict | None, str]:
        """把 LLM 传的 target（名称/别名/序号/ID/UMO）解析为 (umo, entry)。

        Returns:
            (umo, entry, "") 解析成功
            (None, None, 错误信息) 解析失败，可能附带候选列表
        """
        target = (target or "").strip()
        if not target:
            return None, None, "error: target 不能为空。"
        sessions = self.list_for_event(event)

        # 1. 完整 UMO 字符串（管理员场景）
        if ":" in target:
            for umo, entry in sessions:
                if umo == target:
                    return umo, entry, ""
            return (
                None,
                None,
                f"error: 未找到会话 {target}（不属于当前机器人或从未见过）。",
            )

        # 2. 精确匹配：别名/名称/群号/用户ID，或「群 123」「用户 123」写法
        t = target.casefold()
        compact_t = t.replace(" ", "")
        matches = []
        for umo, entry in sessions:
            alias = (entry.get("alias") or "").casefold()
            name = (entry.get("name") or "").casefold()
            group_id = str(entry.get("group_id") or "")
            user_id = str(entry.get("user_id") or "")
            if (
                alias == t
                or name == t
                or group_id == target
                or user_id == target
                or compact_t == "群" + group_id.casefold()
                or compact_t == "用户" + user_id.casefold()
            ):
                matches.append((umo, entry))

        if len(matches) == 1:
            return matches[0][0], matches[0][1], ""
        if len(matches) > 1:
            return None, None, self._format_candidates(matches)

        # 3. 序号（courier_list_sessions 返回清单中的序号）。
        #    放精确匹配之后：纯数字可能是群号/用户ID，序号优先级最低。
        if target.isdigit():
            idx = int(target) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx][0], sessions[idx][1], ""

        # 4. 包含匹配（容错：名称/别名包含 target）
        contains = [
            (umo, entry)
            for umo, entry in sessions
            if (
                t in (entry.get("alias") or "").casefold()
                or t in (entry.get("name") or "").casefold()
                or t in str(entry.get("group_id") or "")
                or t in str(entry.get("user_id") or "")
            )
        ]
        if len(contains) == 1:
            return contains[0][0], contains[0][1], ""
        if len(contains) > 1:
            return None, None, self._format_candidates(contains)
        return (
            None,
            None,
            (
                f"error: 未找到会话「{target}」。请先调用 courier_list_sessions 查看可投递的会话清单，"
                "或用更精确的名称/群号/序号重新指定。"
            ),
        )

    def _format_candidates(self, sessions: list[tuple[str, dict]]) -> str:
        lines = [f"匹配到 {len(sessions)} 个会话，请指定具体序号："]
        for i, (_, entry) in enumerate(sessions, start=1):
            lines.append(f"{i}. {self.display_name(entry)}")
        return "error: " + "\n".join(lines)


def _check_admin(
    config: AstrBotConfig, context: ContextWrapper[AstrAgentContext], operation: str
) -> str | None:
    """插件 require_admin 开关下的管理员校验。返回错误字符串表示拒绝。"""
    if not config.get("require_admin", True):
        return None
    if context.context.event.role != "admin":
        return f"error: 权限不足，{operation}仅限管理员使用。"


@dataclass
class ListSessionsTool(FunctionTool[AstrAgentContext]):
    name: str = "courier_list_sessions"
    description: str = (
        "列出当前机器人接入的所有已见过的会话（群聊和私聊），返回带序号的清单，"
        "包含名称/别名/群号/最后活跃时间。投递消息前请先调用本工具查看目标会话。"
    )
    parameters: dict = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    table: Any = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            sessions = self.table.list_for_event(context.context.event)
            if not sessions:
                return (
                    "当前机器人还没有任何已见过的会话。"
                    "用户与机器人在任意会话中说过话后即可投递。"
                )
            lines = []
            for i, (_, entry) in enumerate(sessions, start=1):
                last = time.strftime(
                    "%m-%d %H:%M", time.localtime(entry.get("last_active", 0))
                )
                lines.append(f"{i}. {self.table.display_name(entry)}  最后活跃: {last}")
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"courier_list_sessions error: {exc}")
            return f"error: 列出会话失败: {exc}"


@dataclass
class SendMessageTool(FunctionTool[AstrAgentContext]):
    name: str = "courier_send_message"
    description: str = (
        "向当前机器人接入的另一个会话投递主动消息。"
        "target 可以是 courier_list_sessions 清单中的序号，也可以是群名/用户昵称/群号/用户ID/别名。"
        "投递目标必须属于当前机器人，无法投递到其他机器人的会话。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "目标会话：序号（如 1）、群名、用户昵称、群号、用户ID 或别名。",
                },
                "message": {
                    "type": "string",
                    "description": "要投递的文本消息内容。",
                },
            },
            "required": ["target", "message"],
        }
    )
    table: Any = None
    config: Any = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            target = str(kwargs.get("target", "") or "").strip()
            message = str(kwargs.get("message", "") or "").strip()
            if not target:
                return "error: target 不能为空。"
            if not message:
                return "error: message 不能为空。"
            event = context.context.event

            target_umo, entry, err = self.table.resolve_target(event, target)
            if entry is None:
                return err
            if target_umo != event.unified_msg_origin and (
                perm := _check_admin(self.config, context, "跨会话投递消息")
            ):
                return perm

            ok = await context.context.context.send_message(
                target_umo, MessageChain(chain=[Plain(message)])
            )
            if not ok:
                return "error: 发送失败，未找到对应平台（该平台可能不支持主动消息）。"
            return f"消息已投递到 {self.table.display_name(entry)}。"
        except Exception as exc:  # noqa: BLE001
            logger.error(f"courier_send_message error: {exc}")
            return f"error: 投递失败: {exc}"


@dataclass
class RenameSessionTool(FunctionTool[AstrAgentContext]):
    name: str = "courier_rename_session"
    description: str = (
        "给某个会话设置或清除别名（仅管理员）。设置后 courier_list_sessions 将优先显示别名，"
        "方便后续用别名指代该会话。alias 传空字符串表示清除别名。"
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "目标会话：序号、群名、用户昵称、群号、用户ID 或别名。",
                },
                "alias": {
                    "type": "string",
                    "description": "新别名；传空字符串清除别名。",
                },
            },
            "required": ["target", "alias"],
        }
    )
    table: Any = None
    config: Any = None

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            event = context.context.event
            if perm := _check_admin(self.config, context, "重命名会话"):
                return perm
            target = str(kwargs.get("target", "") or "").strip()
            alias = str(kwargs.get("alias", "") or "").strip()
            if not target:
                return "error: target 不能为空。"
            _, entry, err = self.table.resolve_target(event, target)
            if entry is None:
                return err
            old_name = self.table.display_name(entry)
            entry["alias"] = alias or None
            await self.table.save()
            if alias:
                return f"已将 {old_name} 的别名设为「{alias}」。"
            return f"已清除 {old_name} 的别名。"
        except Exception as exc:  # noqa: BLE001
            logger.error(f"courier_rename_session error: {exc}")
            return f"error: 重命名失败: {exc}"


class CourierPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config: AstrBotConfig = config or AstrBotConfig({})
        self.table = SessionTable(self)
        self.context.add_llm_tools(
            ListSessionsTool(table=self.table),
            SendMessageTool(table=self.table, config=self.config),
            RenameSessionTool(table=self.table, config=self.config),
        )

    async def initialize(self) -> None:
        await self.table.load()
        logger.info(
            "Courier plugin initialized, sessions loaded: %d",
            len(self.table._sessions),
        )

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        try:
            self.table.record_event(event)
            await self.table.save()
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Courier record session error: {exc}")
