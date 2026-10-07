# 飞书接入

平台通过飞书 WebSocket 接收消息，Agent 经 MCP 查询证据，Notification Worker 发送回复和告警。

## 配置应用

在[飞书开发平台](https://open.feishu.cn/app)创建企业自建应用并启用机器人，配置消息读取/发送权限与 `im.message.receive_v1` 事件，选择长连接接收事件并发布应用。用户须在应用可用范围内。

在仓库根目录运行：

```powershell
.\scripts\setup-feishu.ps1
```

脚本提示输入 App ID/Secret，验证凭证、写入 `.env`，重启 API 和 Agent Worker，并等待 WebSocket 连接。通知发送还需要 Notification Worker；完整本地启动使用 `.\scripts\start-all.ps1`。

也可以直接配置 `.env`：

```dotenv
ONCALL_FEISHU_ENABLED=true
ONCALL_FEISHU_APP_ID=<App ID>
ONCALL_FEISHU_APP_SECRET=<App Secret>
ONCALL_FEISHU_DEFAULT_RECEIVE_ID=<可选 chat_id 或 open_id>
ONCALL_FEISHU_DEFAULT_RECEIVE_ID_TYPE=chat_id
```

指定用户 `open_id` 时将类型改为 `open_id`。生产部署修改环境后运行 `sudo bash deploy/update.sh`，见[部署说明](../deploy/README.md)。

## 使用

- 私聊机器人发送运维问题；群内将机器人加入群并 @ 它。
- `/new` 新建会话，`/help` 查看帮助。
- 回复告警消息继续调查同一事件。
- 主动通知优先使用配置的接收目标；未配置时使用最早绑定的飞书聊天，后续聊天不会自动改换目标。

## 排查

| 现象 | 检查 |
| --- | --- |
| WebSocket 未连接 | 应用发布、可用范围、长连接事件设置；查看 API 日志 |
| 凭证校验失败 | App ID / Secret |
| 群聊无回复 | 机器人是否入群、是否被 @ |
| 告警未发送 | 接收目标、发送权限、Notification Worker 与通知 outbox |

常规启动日志位于 `logs/local`；配置脚本连接检查使用 `logs/api.err.log`。WebSocket 重试由 `ONCALL_FEISHU_WS_INITIAL_RETRY_SECONDS` 和 `ONCALL_FEISHU_WS_MAX_RETRY_SECONDS` 控制；通知租约由 `ONCALL_FEISHU_OUTBOX_CLAIM_SECONDS` 控制。验证边界见 [VALIDATION.md](VALIDATION.md)。
