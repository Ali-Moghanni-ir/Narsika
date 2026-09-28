# Narsika API

## Scheduled Tasks

All mutations require the existing authenticated session, CSRF header and Operator/Admin role. Operators manage their own tasks; administrators manage all. Viewers can read summaries and history. No schedule is created by installation.

| Method | Endpoint | Contract |
|---|---|---|
| GET | `/api/schedules` | Items, server UTC time, actual worker availability, grace seconds and definition limit |
| POST | `/api/schedules/preview` | Validate the proposed definition and return an encrypted review token, fixed target summary and next three execution windows |
| POST | `/api/schedules` | Save `{review_token}`; create or edit according to the reviewed definition |
| GET | `/api/schedules/{id}` | Definition metadata; `variables` only for owner/admin |
| POST | `/api/schedules/{id}/state` | `{enabled: true/false, revision}`; pause/resume future occurrences |
| POST | `/api/schedules/{id}/run` | `{request_key, revision}`; one manual occurrence without changing regular due time |
| GET | `/api/schedules/{id}/history?page=1` | 25 occurrences per page, total count, status and individual device-run references |

Preview body example (substitute a real selected device ID and future date):

```json
{"name":"Nightly configuration backup","kind":"backup","device_ids":[1],"enabled":true,"rule":{"frequency":"daily","timezone":"Asia/Tehran","start_at":"2090-01-01T02:00"}}
```

`kind` is `backup` or `playbook`. Playbooks add `playbook_id` and an object-valued `variables`; ordinary operation validation and vendor compatibility apply. Editing adds `task_id` and current `revision`. Target IDs are unique, with 1–32 fixed devices. The installation limit is 200 definitions. No raw cron input or dynamic group membership is accepted.

Frequency is `once`, `interval`, `daily` or `weekly`. `start_at` is a minute-resolution local ISO datetime interpreted in the explicit IANA `timezone`. Interval rules require `interval_hours` from 1 to 720. Weekly rules require `weekdays`, Monday=0 through Sunday=6. Start is the earliest execution time. Once rules must be in the future when reviewed/saved. Preview includes UTC and offset-bearing local times. DST gaps are skipped; repeated wall times use the first occurrence.

Review tokens expire after 600 seconds and bind the actor/session, operation, target identities, source checksum and definition revision. Save revalidates them. Repeating a successful creation token returns the same task; repeating a successful edit token returns the saved task while that review remains current. A different stale revision returns 409. Run now requires a unique caller-generated key of 16–64 characters, retained when retrying that same request. Repeated manual keys return the same occurrence; changing the key requests a new occurrence. Never automatically retry a timed-out request with a new key.

Occurrence status is `QUEUED`, `RUNNING`, `SUCCESS`, `FAILED`, `PARTIAL`, `CANCELLED`, `MISSED`, `SKIPPED_OVERLAP`, `SKIPPED_CAPACITY` or `BLOCKED`. Execution states derive from child runs. `MISSED` records the first overdue due time and advances over the missed period; it does not create a job or row per elapsed interval. Dispatch grace is 60 seconds. Queue admission is all-or-none for the targets. Validation failure disables future dispatch and stores `attention`; ordinary execution failure stays in history without automatic retry.

Run now can execute a paused task after explicit confirmation, with current authorization checks. Pause does not cancel already submitted work. Edit changes revision, so still-queued runs from an older revision fail preflight; already applied commands are not undone. Direct Firewall review/apply is outside this API. `/api/bootstrap?view=schedules` is available for the new workspace.

## Firewall Control Preview

All endpoints below use the existing authenticated JSON envelope and CSRF header on POST. This is an additive internal API. Access-list changes are made only through these endpoints.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/firewall/capabilities` | Presets, scope, role, 20-item limit and 600-second review validity |
| POST | `/api/firewall/devices/:id/refresh` | Empty object; read real configuration over SSH |
| POST | `/api/firewall/devices/:id/reviews` | `{source_ip, changes}`; fresh state read and encrypted immutable plan |
| GET | `/api/firewall/reviews/:id` | Owned review (or Admin access), associated run and current/expired status |
| POST | `/api/firewall/reviews/:id/apply` | Atomically consume an owned receipt; repeated request returns the same run |
| GET | `/api/firewall/history?device=:id` | Latest 40 owned reviews; Admin can inspect all |

Each change accepts `service`, `action` (`allow`/`block`), `protocol` (`tcp`/`udp`/`icmp`/`ip`), `source`, `destination`, and a single `port` for TCP/UDP. RouterOS adds `chain` and `position`; Cisco adds `acl` and `sequence`, plus optional `create_acl: true` to create a new named extended ACL (the name must not match any existing IPv4 ACL; every entry for that ACL must set the flag). Addresses are validated IPv4 CIDRs or `any`. Extra fields, raw commands/YAML, duplicate/conflicting intents and unsupported selectors are rejected. The general `/api/automation/runs` endpoint cannot submit the `firewall` job kind.

Apply requires `checksum` and literal JSON `true` for `target_confirmed`, `source_confirmed`, `risk_ack`, `recovery_saved` and `no_rollback_ack`. LOCKOUT additionally requires `oob_confirmed: true`, `device_name` matching the target and `phrase: "DISCONNECT"`. Viewer execution is rejected. Operator custom/high-risk changes are rejected. Receipts bind the actor, actor session version, target connection identity, reviewed configuration fingerprint and expiry. Validation/authorization is repeated by the worker.

Review returns `{id, checksum, expires_at, status, created_at, run_id, plan}` inside `data`; detail also contains `run`. Apply returns `{review_id, run, reused}` with HTTP 202 for first acceptance or 200 for an already-queued receipt. Common errors include `CONFIRMATION_REQUIRED`, `STALE_STATE`, `REVIEW_EXPIRED`, `TARGET_CHANGED`, `INVALID_REVIEW`, `FORBIDDEN`, `BUSY` and `CAPABILITY_UNAVAILABLE`. See [the review guide](FIREWALL-REVIEW.md) for status meanings and non-atomic execution/recovery boundaries.

## Existing API

### Compact workspace reads and worker state

The default `GET /api/bootstrap` response is unchanged. An optional `view` accepts a current page ID (`inventory`, `health`, `interfaces`, `discovery`, `playbooks`, `vlan`, `acl`, `backups`, `audit`, `settings` `firewall` or `schedules` as registered by the server). Page-specific responses retain inventory and metadata but leave `audit` empty; audit history is still available from `/api/audit`. Administrators receive the full account list only for the settings view; other page views include only the current account. Unknown views return validation error 422. These compact reads do not change authorization.

`GET /api/automation/runs?summary=true` omits the `output` field and avoids loading retained output/encrypted parameters for history rows. The default history response and `GET /api/automation/runs/{id}` retain their existing full contract. Nothing is removed from storage.

`GET /api/system` reports `worker_available` from the live dispatcher and adds `queue: {pending, running, capacity}` with actual persisted state counts and the 32-run capacity. `/healthz` checks SQLite and, when `START_WORKER` is enabled, the dispatcher thread; a stopped required dispatcher returns 503 and a database error follows the normal non-2xx error path. Deployment job submissions with a stopped required worker return `CAPABILITY_UNAVAILABLE` (503), rather than silently accumulating pending work.

Native release additions: POST /api/users may omit password to receive temporary_password exactly once in the creation response. PATCH /api/users/:id accepts reset_password: true and returns a new temporary_password once. Explicit password input remains for internal compatibility; it cannot be combined with reset_password. Both flows force next-login password change. Subsequent user lists never return passwords.

Requests outside NARSIKA_WEB_NETWORKS receive SOURCE_NOT_ALLOWED (403). Forwarded source headers are ignored unless proxy trust is explicitly configured. X-Request-ID matches the error envelope's request_id. Health/interface requests may share an actual sample for up to four seconds; sampled_at identifies its collection time. ARTIFACT_LIMIT means collection was incomplete while accepted artifacts were retained.

The browser and server use same-origin cookie authentication. Obtain the CSRF token from the rendered page's `csrf-token` meta element. Every POST, PATCH and DELETE requires `X-CSRFToken`. Login also requires CSRF. Requests and responses use JSON except YAML uploads, source downloads, and backup/artifact downloads.

Success: `{"data": {...}, "meta": {}}`. Collections use `data.items`. Accepted runs return HTTP 202 with `data.run_id`, `data.status`, `data.poll_url`. Errors use `{"error":{"code":"VALIDATION_ERROR","message":"...","fields":{},"request_id":"..."}}` and an appropriate non-2xx status. The request ID identifies the response; it is not an external tracing integration.

| Endpoint | Methods | Access / purpose |
|---|---|---|
| `/login`, `/change-password`, `/logout` | GET, POST | Session and password management; GET logout renders confirmation |
| `/api/session` | GET | Current authenticated user, or null |
| `/api/bootstrap` | GET | Real inventory, metadata, account and preferences |
| `/api/devices` | GET, POST | Read / administrator create |
| `/api/devices/{id}` | GET, PATCH, DELETE | Read / administrator update or archive |
| `/api/devices/{id}/restore` | POST | Administrator restores archived inventory entry |
| `/api/groups`, `/api/groups/{id}` | GET, POST / PATCH, DELETE | Administrator manages groups; archive requires no active devices |
| `/api/credentials`, `/api/credentials/{id}` | GET, POST / PATCH | Administrator; metadata only in responses |
| `/api/users`, `/api/users/{id}` | GET, POST / PATCH | Administrator; disable or change role, no deletion |
| `/api/settings` | GET, PATCH | Shared preferences; administrator writes |
| `/api/system` | GET | Capabilities and allowed target scope |
| `/api/devices/{id}/health` | GET | Actual on-demand SSH/ICMP collection |
| `/api/devices/{id}/interfaces` | GET | Actual SNMPv3 or SSH interface collection |
| `/api/devices/{id}/vlans` | GET | Cisco IOS VLAN table |
| `/api/devices/{id}/host-key` | GET, POST | Administrator fetches or trusts an explicitly verified fingerprint |
| `/api/discovery/scans` | GET, POST | Operator/admin scan history and bounded SSH discovery |
| `/api/discovery/scans/{id}` | GET | Run status and observed candidates |
| `/api/discovery/scans/{id}/candidates` | GET | Observed candidate list |
| `/api/discovery/candidates/{id}/host-key` | GET, POST | Administrator SSH fingerprint verification |
| `/api/discovery/candidates/{id}/verify` | POST | Operator/admin authenticates and verifies expected platform |
| `/api/discovery/candidates/import` | POST | Administrator imports recently verified candidate IDs |
| `/api/playbooks` | GET, POST | Catalog / administrator multipart upload (`file`, `vendor`, `replace`) |
| `/api/playbooks/{id}/source` | GET | Operator/admin downloads actual YAML |
| `/api/automation/runs` | GET, POST | History / operator/admin execution |
| `/api/automation/runs/{id}` | GET | Persisted state, safe task events, output artifacts |
| `/api/automation/runs/{id}/cancel` | POST | Run owner or administrator requests cancellation |
| `/api/backups` | GET, POST | Metadata / operator/admin captures a configuration |
| `/api/backups/{id}` | GET, DELETE | Operator/admin content / administrator archives |
| `/api/backups/{id}/download` | GET | Operator/admin decrypted configuration attachment |
| `/api/artifacts/{id}/download` | GET | Operator/admin decrypted run attachment |
| `/api/audit` | GET | Server-recorded current and legacy events |
| `/healthz` | GET | Unauthenticated database and required-dispatcher health check |

Device fields: `name`, `ip_address`, `platform` (`cisco` or `mikrotik`), `ssh_port`, `snmp_port`, `group_id`, `credential_id`, `snmp_credential_id`, `model`, `notes`. Empty foreign-key fields use JSON null. Unknown fields are rejected.

SSH credential fields: `name`, `username`, `kind: "ssh"`, `password` or `private_key`, optional `enable_password`, `passphrase`. SNMP: `kind: "snmpv3"`, `auth_password`, `priv_password`; SHA-256 + AES-128 authPriv. Updating a secret requires supplying it; omitting it preserves its value. Passwords and private keys never appear in normal JSON responses.

Example operation shape (substitute actual IDs and reviewed values):

```json
{"kind":"vlan","device_id":1,"parameters":{"operation":"create","vlan_id":42,"vlan_name":"REVIEWED_NAME","save_config":false}}
```

`kind` is `playbook`, `vlan` or `backup`. `acl` is retired: new submissions, including the legacy `POST /acl` form, return HTTP 410 with code `MOVED`; use `/api/firewall`. Runs queued before the upgrade still execute. The legacy `Original/cisco_acl.yml` and `Original/mikrotik_acl.yml` playbooks can be run only by an administrator and cannot be scheduled. Playbooks require `playbook_id` and a `variables` JSON object. The server owns connection variables (`ansible_*`), `narsika_targets`, and `narsika_artifact_root`; do not pass these as user variables.

Run states: `PENDING → RUNNING → SUCCESS / FAILED / CANCELLED`. Interrupted active runs become `INTERRUPTED` after a process restart. Success means Ansible exited successfully and emitted executed task events; inspect changed/skipped events to distinguish planning from device changes. Cancellation cannot undo commands already accepted by equipment.

Work for the same device is dispatched in submission order; different targets can use the available workers concurrently. An operation may return to `PENDING` if a monitoring sample already holds its connection lock, but only before any network work in that attempt. This is not a retry of failed device commands. Cancelled pending work is settled even when all executor slots are occupied.

Legacy route URLs remain: `/`, `/login`, `/logout`, `/change-password`, `/add-group`, `/add-device`, `/logs`, `/health/{device_id}`, `/vlan`, `/acl` (GET redirects to `/firewall.html`), `/playbooks`, `/playbook-runner`, `/upload-playbook`, `/run-playbook`. Old form field names are adapted; all mutations now require CSRF and server permissions. No public API token or unauthenticated device execution endpoint is provided.
