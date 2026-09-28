# Scheduled tasks

**Schedules** runs configuration backups and existing Playbooks automatically on selected Cisco and MikroTik devices. Nothing is scheduled by default, and a new installation starts with an empty list.

## Create a task

Open **Schedules → New task**:

1. **Operation** — name the task and choose **Backup** or **Playbook**. For a Playbook, review the active version and its JSON variables. Variables such as `narsika_apply` decide whether the Playbook previews or applies; the scheduler does not change them.
2. **Targets** — select up to 32 devices. Only devices of a compatible platform are listed; search by name, IP or group. Targets are fixed: a device later added to a group is not added to the task.
3. **Timing** — choose the schedule type and an explicit time zone, for example `Asia/Tehran` or `UTC`. You can save the task paused.
4. **Review** — check the operation, variables, target IPs, time zone and the next three run times, then confirm that future runs may change device configuration without asking again.

The review is valid for 10 minutes. If a target, credential or the Playbook changes in the meantime, saving is refused and you review again. Task definitions and variables are encrypted at rest.

## Schedule types

| Type | Behaviour |
|---|---|
| **One time** | Runs once at a future date and time, then disables itself. |
| **Every few hours** | Every 1–720 hours of real elapsed time, counted in UTC from the start time. |
| **Daily** | Every day at the chosen local time, from the start date. |
| **Weekly** | On the chosen weekdays at the chosen local time. |

Daylight saving time: if the start time itself does not exist the form reports an error; a daily or weekly run whose local time does not exist that day is skipped; a repeated local time runs only on its first occurrence. Hourly intervals always keep their real duration.

## Controls and history

- **Run now** — requests one run immediately without moving the regular schedule. Works on paused tasks too, if permissions and targets are still valid.
- **Pause** — stops future runs only. Work already queued or running is not cancelled.
- **Resume** — continues from the next future time. Missed runs are not replayed.
- **Edit** — goes through the review again and creates a new revision. Runs still queued from the old revision are refused before they reach the network.
- **History** — click the task name. Each occurrence shows its time, status and per-device results; **Open execution** shows the device log and cancel control.

## Statuses

| Status | Meaning |
|---|---|
| **QUEUED** / **RUNNING** | At least one target is queued / running. |
| **SUCCESS** | Every target run reported success. |
| **PARTIAL** | Some targets succeeded and others failed or were cancelled. |
| **FAILED** | The occurrence finished and no target succeeded. |
| **CANCELLED** | Every target run was cancelled. Cancelling is not a rollback. |
| **MISSED** | The run window passed while the service was unavailable. Nothing was queued to catch up. |
| **SKIPPED_OVERLAP** | The previous occurrence of this task was still queued or running. |
| **SKIPPED_CAPACITY** | The queue had no room for all targets, so none were submitted. |
| **BLOCKED** | The owner, a target, a credential or the Playbook is no longer valid. The task is paused until it is reviewed. |

A run is dispatched within a 60-second grace window. After downtime, one MISSED record is written for the missed period and the task moves to its next future time — there is no burst of catch-up runs. Failed or ambiguous runs are never retried automatically.

## Permissions

| Role | Can |
|---|---|
| **ADMIN** | Create, edit, pause and run all tasks and see their parameters |
| **OPERATOR** | Create tasks for operations they are allowed to run and manage their own tasks |
| **VIEWER** | See task summaries and history, without private variables |

The owner's permission is checked before every run. Disabling the owner, demoting them to VIEWER or requiring a password change stops their tasks from running. A normal password change does not invalidate a task.

A task needs review when a target's IP, platform, SSH port, credential profile or secret changes, or when the Playbook version or content changes. Renaming a credential profile alone does not.

Legacy access-list Playbooks (`Original/cisco_acl.yml`, `Original/mikrotik_acl.yml`) cannot be scheduled; use [Firewall](FIREWALL.md) for filter changes.

## Limits

- Up to **200** tasks per installation and **32** devices per task.
- The shared operation queue holds up to **32** queued or running jobs in total.
- History is paginated and never deleted automatically.

Browsers do not need to stay open. The Narsika service must be running; the scheduler is part of it and needs no cron, Redis or other service.

## Upgrades and downgrades

- Finish or cancel running work before upgrading.
- The scheduler adds three tables. Before creating them in an existing database, Narsika writes and verifies an encrypted snapshot, `narsika.db.before-schedules.sqlite3.enc`.
- On Docker, rebuild the image when upgrading (run the launcher again); `--no-build` alone does not pick up new code.
- To go back to a version without schedules: pause all tasks, drain or cancel the queue, take a full platform backup, then restore using the documented procedure. Older versions do not run or understand schedules. Never drop the new tables.

## A first lab test

Create a one-time **Backup** task for a single lab device a few minutes ahead, close the browser, and check the result after the run time. Then try Pause/Resume, a restart shortly before a run, two targets with different results, and a credential change.
