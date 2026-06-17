# Email agent — topologies & the credential model

This is the "why is config split in two places?" doc. Read it before touching
`/agent/config`, the OAuth flows, or anything notifier-related.

## The two topologies

The email agent runs in one of two places, and **where it runs determines where its
config and secrets come from.**

| | **Local / self-host** | **Cloud / hosted** |
|---|---|---|
| Runs | on the user's own machine, *outside* the cluster | as an in-cluster k8s CronJob |
| Why it exists | Proton Mail Bridge is local-only — the mailbox is only reachable from that machine | serves everyone else (Gmail users) without them running anything |
| Mailbox | Proton Bridge, creds in local `.env` | Gmail OAuth, refresh token in `email_credentials` |
| LLM key | local `.env` | the user's active key from `user_api_keys`, served via `/agent/config` |
| Slack | **local `.env`** (`SLACK_BOT_TOKEN` + channel) | **OAuth install** → `slack_connections`, served via `/agent/config` |
| Folder/label config | local `.env` | `email_credentials.folder_*`, served via `/agent/config` |
| Enable/disable | n/a (you just don't run it) | `email_credentials.enabled` |
| Reaches Job Radar via | REST `/agent/*` over public nginx, `X-Agent-Key` | in-cluster MCP/REST; reads config via `X-Internal-Token` |
| Config source | **its own `.env`** | **Job Radar DB**, via `/agent/config` / `/agent/cloud/config` |

## The one rule that creates the split (H6a)

> **Decrypted secrets never leave the cluster.**

`/agent/config` and `/agent/cloud/config/{user_id}` return *decrypted* LLM keys,
mailbox creds, and Slack tokens. They are **in-cluster only** — blocked at nginx
(`/agent/config` exact, `^~ /api/agent/cloud/`) and behind the tracker-api
NetworkPolicy.

The local agent runs **outside** that boundary (on a user's machine, over the public
internet), so Job Radar will **not** hand it decrypted secrets. That's why the local
agent carries its own secrets in `.env` instead of fetching them.

This is a deliberate security property, not an oversight: Job Radar is **not** a
single internet-reachable endpoint that, if compromised, leaks every user's plaintext
LLM keys + mailbox creds + Slack tokens. Don't "simplify" it by serving decrypted
secrets to external agents.

## Local agent's coupling to Job Radar is thin

All the local agent needs from Job Radar is **one agent key** (minted in Settings →
Email Agent), which it uses to write results back over REST. Everything else —
mailbox, LLM, Slack, folders — lives in its `.env`. It never calls `/agent/config`.

## Slack, specifically

- **Local/self-host:** set `SLACK_BOT_TOKEN` and the channel in the agent's `.env`
  (see the `job-radar-agent` repo's `.env.example`). No OAuth, no UI, no
  `/agent/config` — it's your machine and your workspace, so you control the file.
  This is the *simple* case.
- **Cloud:** a Slack bot token is **per-workspace**, so each user installs the shared
  Slack app into their own workspace via OAuth ("Add to Slack" in Settings → Email
  Agent → Notifications). The per-workspace bot token + chosen channel are stored
  encrypted in `slack_connections` and served to the in-cluster agent via the
  `slack {bot_token, channel_id}` block in `/agent/config`. The OAuth machinery
  exists **only** to solve cloud multi-tenancy — it is not needed (and not used) by
  the local path.
- Separately, the cluster's shared `SLACK_BOT_TOKEN` + `SLACK_ADMIN_CHANNEL`
  (`email-agent-config`) is the **ops/admin** sink for the cloud CronJob's run
  summaries — distinct from any user's per-user Slack.

## For a future self-host (e.g. another Proton user)

They run the agent locally and configure **everything in `.env`** — including Slack
(`SLACK_BOT_TOKEN` + channel). They do **not** use the Add-to-Slack OAuth flow; that's
cloud-only. The only thing they get from the Job Radar UI is an **agent key** for
write-back. Configuring a `.env` is the expected cost of self-hosting; the OAuth/UI
flows are the convenience layer for cloud users who can't share a token or edit a
server-side file.

## Could we make the double-config less ponderous?

A user who runs **both** topologies (local Proton + cloud Gmail) configures some
things twice (Slack in `.env` for local, via OAuth for cloud). The only safe lever to
reduce that is to let the local agent *also* pull **non-secret** config (folder names,
channel name, enabled flag) from Job Radar via its agent key, keeping true secrets in
`.env`. It's a hybrid (more code/surface) and currently judged not worth it for a
single dual-topology user. The unsafe lever — serving decrypted secrets to external
agents — is off the table (H6a).

See also: [[project_email_agent_topology]] (memory), `CLAUDE.md` (email-agent
sections), and the `job-radar-agent` repo's `INTEGRATION_SPEC.md`.
