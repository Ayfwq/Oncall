# Feishu Bot Setup

Two-step setup. The first step is a one-time click-through in the Feishu
developer console. The second step is one PowerShell command. After that,
the bot can chat, and proactive push works automatically once you message
the bot once.

## 1. Feishu developer console (one-time, ~3 minutes)

Open **https://open.feishu.cn/app** and sign in.

1. **Create app** → **企业自建应用** → name it (e.g. "Oncall AI SRE") →
   create.
2. Open the app → **凭证与基础信息** → copy the **App ID** (`cli_xxx`)
   and **App Secret** (you'll paste both into the script next).
3. **添加应用能力** → **机器人** → enable.
4. **权限管理** → add and request the following scopes (an admin may
   need to approve):
   - `im:message` (获取与发送单聊、群组消息)
   - `im:message:send_as_bot` (以应用的身份发消息)
   - `im:chat` (获取群组信息)
5. **事件与回调** → **事件订阅** → **订阅方式** = **使用长连接接收事件**
   (this project uses lark-oapi WebSocket, no public callback URL is
   needed) → add event **`接收消息 v2 1.0`** (`im.message.receive_v1`).
6. **版本管理与发布** → create a version → **申请发布**. For
   self-built apps in the same tenant, the bot becomes usable once the
   version is approved (often instant for the creator, otherwise after
   an admin approves).

You now have everything the script needs. Keep this tab open.

## 2. Run the setup script

From the repo root:

```powershell
.\scripts\setup-feishu.ps1
```

It will prompt for the App ID and App Secret (the secret input is
masked), then:

- validates the credentials against the Feishu auth endpoint (immediate
  feedback if anything is wrong),
- writes the Feishu block into `.env`,
- pre-warms `lark_oapi` (first Windows run can take 1-4 min while
  Windows Defender scans the package; subsequent runs are seconds),
- restarts `oncall-api` and `oncall-agent-worker`,
- polls until the Feishu WebSocket reports `connected to wss://`.

The official Feishu SDK reconnects after a dropped WebSocket connection. Retry
limits can be tuned with `ONCALL_FEISHU_WS_INITIAL_RETRY_SECONDS` and
`ONCALL_FEISHU_WS_MAX_RETRY_SECONDS`. Outbox delivery uses a database lease;
another agent worker can reclaim a stale notification after
`ONCALL_FEISHU_OUTBOX_CLAIM_SECONDS`.

If you want to pin proactive push to a specific group/user (instead of
using auto-bind), pass `-DefaultReceiveId`:

```powershell
.\scripts\setup-feishu.ps1 -DefaultReceiveId oc_xxxxxxxxxxxxxxxxxxxx
```

## What you do next in Feishu

1. Search for the bot by the app name (in step 1.1) and open a private
   chat.
2. Send any message, e.g. `hello`. The bot replies through the Agent
   (RAG / monitoring / etc.). This first message also **auto-binds
   proactive push** to that chat — no chat_id hunting required.
3. In a group, add the bot to the group, then **@-mention** it to
   trigger it. (The bot only receives messages that @-mention it in
   groups.)
4. Commands in chat: `/new` (new session), `/help`.

To switch the proactive push target later (e.g. you want cards in a
specific ops group), re-run the script with `-DefaultReceiveId` and the
group's `chat_id` (or a user's `open_id`).

## Troubleshooting

- **Script says "timed out"** and the log shows no `connected to wss://`
  line → the app is likely not yet published/visible. Re-check step 1.6
  and the app's **可用范围** (availability). The bot must be visible to
  at least the user who will message it.
- **Script says "credential validation failed"** → the App ID or App
  Secret is wrong. Re-copy from 凭证与基础信息.
- **Bot does not reply in a group** → you must @-mention it; it
  silently ignores other messages.
- **Active push (Incident cards) never arrive** → message the bot at
  least once first to trigger auto-bind, or set
  `ONCALL_FEISHU_DEFAULT_RECEIVE_ID` in `.env` and restart.
