# AI assistant (phase 1: read-only)

Status: written and covered by automated tests with a mocked provider. It has **not** been verified against the live Ox Alpha endpoint or real devices yet.

## What it does

The **AI assistant** page (`/assistant.html`) answers questions about Narsika's own records: inventory, the latest stored health sample per device, operation runs (with the tail of failed run logs), configuration backups, scheduled tasks and the audit log. The model gets these through read-only tools. Answers render as safe Markdown, and each answer shows which tools were used.

It cannot contact devices, queue jobs or change settings. Health data is the latest stored sample only, not a time series. Trend analysis needs a telemetry history table in a later phase.

## Provider

Any OpenAI-compatible Chat Completions endpoint works. The defaults are:

| Setting | Default |
|---|---|
| Base URL | `https://openrouter.ai/api/v1` |
| Model | `stealth/ox-alpha` |
| Enabled | off |

An administrator enables it under **AI assistant → Assistant settings** and stores the API key there. The key is encrypted with the installation Fernet key and is never returned by the API or written to the audit log. `http://` is accepted only for a loopback or private-address model server (for example Ollama at `http://127.0.0.1:11434/v1`, which needs no key).

Ox Alpha is a free preview model from an undisclosed vendor, and the provider may log requests. Do not send production data you cannot share.

## Data protection

- The Narsika server calls the provider. The browser never talks to the provider, so the CSP is unchanged.
- Before anything is sent, device-style secrets (`password`, `secret`, `community`, keys, `$n$` hashes, private-key blocks, bearer tokens) are redacted.
- Optional **address masking** replaces each IPv4 address with a stable alias (`ip-1`, `ip-2`, …) for the provider. The answer shown to the user has the real addresses restored.
- Conversations are private to their owner. Message content is encrypted at rest (`assistant_conversation`, `assistant_message` tables; additive).
- Each question writes an audit event with metadata only (model and tools used), never the question or answer.
- At most two assistant requests run at once, so the web workers stay available. Each request allows up to six tool rounds and a 90-second upstream timeout.

## API

| Method | Endpoint | Role | Contract |
|---|---|---|---|
| GET | `/api/assistant/config` | read | Public configuration: `enabled`, `base_url`, `model`, `mask_addresses`, `provider`, `has_api_key`, `local`, `ready` |
| PUT | `/api/assistant/config` | admin | Any of `enabled`, `base_url`, `model`, `mask_addresses`, `api_key`, `clear_api_key` |
| POST | `/api/assistant/test` | admin | Sends one short prompt without tools; returns model and latency |
| GET | `/api/assistant/conversations` | read | Caller's conversations |
| GET | `/api/assistant/conversations/{id}` | read | Caller's conversation with decrypted messages |
| DELETE | `/api/assistant/conversations/{id}` | read | Archives the caller's conversation |
| POST | `/api/assistant/ask` | read | `{message, conversation_id?}`; returns the conversation and the assistant message |

Error codes: `AI_DISABLED`, `AI_NOT_CONFIGURED` (409), `AI_BUSY` (429), `AI_UPSTREAM_ERROR`, `AI_UNREACHABLE`, `AI_TOO_MANY_STEPS` (502), `AI_TIMEOUT` (504).

## Road to actions (phase 2, not implemented)

Device changes will stay on the existing path: validation → job queue → audit. The assistant will only **propose** a change, for example a firewall rule, VLAN or playbook run. The proposal opens the existing review screen, and a user with the right role approves and applies it there. The model will never hold credentials or run commands directly.
