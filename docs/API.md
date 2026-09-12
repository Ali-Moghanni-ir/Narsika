# Narsika API

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
| `/healthz` | GET | Unauthenticated database liveness check |

Device fields: `name`, `ip_address`, `platform` (`cisco` or `mikrotik`), `ssh_port`, `snmp_port`, `group_id`, `credential_id`, `snmp_credential_id`, `model`, `notes`. Empty foreign-key fields use JSON null. Unknown fields are rejected.

SSH credential fields: `name`, `username`, `kind: "ssh"`, `password` or `private_key`, optional `enable_password`, `passphrase`. SNMP: `kind: "snmpv3"`, `auth_password`, `priv_password`; SHA-256 + AES-128 authPriv. Updating a secret requires supplying it; omitting it preserves its value. Passwords and private keys never appear in normal JSON responses.

Example operation shape (substitute actual IDs and reviewed values):

```json
{"kind":"vlan","device_id":1,"parameters":{"operation":"create","vlan_id":42,"vlan_name":"REVIEWED_NAME","save_config":false}}
```

`kind` is `playbook`, `vlan`, `acl` or `backup`. Playbooks require `playbook_id` and a `variables` JSON object. The server owns connection variables (`ansible_*`), `narsika_targets`, and `narsika_artifact_root`; do not pass these as user variables.

Run states: `PENDING → RUNNING → SUCCESS / FAILED / CANCELLED`. Interrupted active runs become `INTERRUPTED` after a process restart. Success means Ansible exited successfully and emitted executed task events; inspect changed/skipped events to distinguish planning from device changes. Cancellation cannot undo commands already accepted by equipment.

Legacy route URLs remain: `/`, `/login`, `/logout`, `/change-password`, `/add-group`, `/add-device`, `/logs`, `/health/{device_id}`, `/vlan`, `/acl`, `/playbooks`, `/playbook-runner`, `/upload-playbook`, `/run-playbook`. Old form field names are adapted; all mutations now require CSRF and server permissions. No public API token or unauthenticated device execution endpoint is provided.
