# Courier · 快递员

AstrBot 插件：注册一组 LLM 工具，让机器人可以向**同一个机器人接入的不同会话**投递主动消息。

## 功能

- **自动记录会话**：机器人收到消息时自动记录会话（群聊/私聊），保存群名、昵称、最后活跃时间等，重启不丢。
- **三个 LLM 工具**：
  - `courier_list_sessions`：列出当前机器人接入的所有已见过会话（带序号、名称/别名、最后活跃时间）。
  - `courier_send_message`：向目标会话投递主动消息。目标可以用序号、群名、昵称、群号、用户 ID、别名或 UMO 字符串指定。
  - `courier_rename_session`：给会话设置/清除别名（仅管理员），方便用别名指代目标会话。
- **会话友好命名**：显示优先级 `别名 > 平台名 > ID`。平台拿不到名称时兜底显示「群 123456」/「用户 78901」，群聊附带最近发言者昵称作弱提示。

## 安装

### 从 GitHub 安装

在 AstrBot WebUI → 插件管理 → 安装插件中填入：

```
https://github.com/Galaxy1108/astrbot_plugin_courier
```

### 本地开发

将本仓库放入 AstrBot 的 `data/plugins/` 目录（或 clone 到该目录），然后在 WebUI 插件管理 → 插件 → `...` → 重载插件。

## 配置

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `require_admin` | bool | `true` | 跨会话投递消息、重命名会话是否仅限管理员 |

## 使用示例

对机器人说「把『明天记得买牛奶』发给群『摸鱼群』」，机器人会：

1. 调用 `courier_list_sessions` 查看可用会话；
2. 调用 `courier_send_message`，`target="摸鱼群"`，`message="明天记得买牛奶"`。

## 安全说明

- **不同机器人隔离**：一个 AstrBot 实例可能接入多台机器人。插件记录的每个会话都绑定机器人的 `platform_id` 与 `self_id`，投递前会校验目标会话属于当前机器人，**不会把消息投递到其他机器人的会话**。
- **管理员限制**：跨会话投递与重命名默认仅限管理员（可在插件配置中关闭）。
- **平台限制**：部分平台（如 QQ 官方接口）不支持主动消息投递。

## 开发

- 参考实现：AstrBot 内置 `SendMessageToUserTool`（`astrbot/core/tools/message_tools.py`）。
- API 以 AstrBot 本体仓库源码与 `docs/zh/dev/star/` 文档为准。
