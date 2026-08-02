# Changelog

## v0.1.1 - 2026-08-02

- 添加插件封面 `logo.png`
- 补齐插件市场发布元数据（`astrbot_version`、`social_link`、`tags`），`astrbot_version` 改为 `>=4.16`
- README 添加"代码由 AI 生成"声明

## v0.1.0 - 2026-08-02

首个可用版本。

- 会话表：自动记录当前机器人见过的会话（群聊/私聊），保存群名、昵称、最后活跃时间，持久化重启不丢
- LLM 工具：
  - `courier_list_sessions`：列出当前机器人接入的所有已见过会话（序号/名称/别名/最后活跃时间）
  - `courier_send_message`：向目标会话投递主动消息（目标可用序号、群名、昵称、群号、用户 ID、别名或 UMO 指定）
- 指令：`/courier_rename <目标> [别名]` 设置/清除会话别名
- 会话友好命名：显示优先级 `别名 > 平台名 > ID`，群聊附带最近发言者昵称作弱提示
- 安全：不同机器人接入互相隔离（platform_id + self_id 双重校验），跨会话投递与重命名默认仅限管理员
- 配置项：`require_admin`（默认 `true`）
