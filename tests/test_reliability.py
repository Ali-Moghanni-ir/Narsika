"""Runtime reliability regressions. Devices are fixtures; no network I/O."""
import time
from types import SimpleNamespace

import pytest

from app.models import db, AuditEvent, Device, OperationRun, ScheduledTask, ScheduleOccurrence
from app.security import APIError
from app.services import scheduling as S
from app.services.discovery import vendor_hint
from app.services.jobs import JobManager, enqueue
from app.services.network import command
from app.services.telemetry import calculate_rates
from conftest import credential, device, post


def client_returning(output):
    return SimpleNamespace(send_command=lambda cmd: output)


def test_configuration_text_is_not_mistaken_for_a_device_error():
    running = ('interface GigabitEthernet0/1\n description syntax error lab\n'
               'banner motd ^C bad command name in a banner ^C\n'
               '/interface ethernet set [ find default-name=ether1 ] comment="failure: syntax error"\n')
    assert command(client_returning(running), 'show running-config') == running


@pytest.mark.parametrize('output', [
    '% Invalid input detected at \'^\' marker.',
    'bad command name foo (line 1 column 1)',
    'syntax error (line 1 column 8)',
    'failure: already have such address',
])
def test_device_error_lines_still_fail(output):
    with pytest.raises(APIError) as caught:
        command(client_returning('\n' + output + '\n'), 'show something')
    assert caught.value.code == 'UNSUPPORTED'


@pytest.mark.parametrize('banner,expected', [
    ('SSH-2.0-ROSSSH', 'mikrotik'),
    ('ssh-2.0-mikrotik', 'mikrotik'),
    ('SSH-2.0-Cisco-1.25', 'cisco'),
    ('SSH-2.0-OpenSSH_9.6', 'unknown'),
])
def test_discovery_recognises_routeros_banner(banner, expected):
    assert vendor_hint(banner) == expected


def test_rates_work_when_discontinuity_counter_is_unsupported():
    first = {'index': 1, 'rx_bytes': 1000, 'tx_bytes': 2000, 'discontinuity': None, 'oper_status': 1}
    current = {**first, 'rx_bytes': 2000, 'tx_bytes': 4000}
    row = calculate_rates([dict(current)], [first], 10, 110, 100)[0]
    assert row['rx_bps'] == 800 and row['tx_bps'] == 1600
    # Appearing or changing still means the counters may have been reset.
    assert calculate_rates([{**current, 'discontinuity': 5}], [first], 10, 110, 100)[0]['rx_bps'] is None


def test_dispatcher_cancellation_is_recorded_in_output_and_audit(admin, app):
    target = device(admin)
    manager = SimpleNamespace(app=app, futures={100: None, 101: None})
    with app.app_context():
        row = enqueue('backup', target['id'], 1, {})
        row.cancel_requested = True
        db.session.commit()
        JobManager.dispatch_pending(manager)
        row = db.session.get(OperationRun, row.id)
        assert row.status == 'CANCELLED' and 'No commands were sent' in row.output
        event = AuditEvent.query.filter_by(action='Run backup', result='cancelled').one()
        assert event.target == str(target['id'])


def test_run_returns_to_queue_when_worker_pool_is_shutting_down(admin, app):
    target = device(admin)

    def closed(fn, ident):
        raise RuntimeError('cannot schedule new futures after shutdown')

    manager = SimpleNamespace(app=app, futures={}, pool=SimpleNamespace(submit=closed), execute=lambda ident: None)
    with app.app_context():
        row = enqueue('backup', target['id'], 1, {})
        db.session.commit()
        JobManager.dispatch_pending(manager)
        row = db.session.get(OperationRun, row.id)
        assert row.status == 'PENDING' and row.started_at is None


def schedule(admin, device_ids):
    data = {'name': 'Reliability backup', 'kind': 'backup', 'device_ids': device_ids, 'enabled': True,
            'rule': {'frequency': 'daily', 'timezone': 'UTC', 'start_at': '2090-01-01T02:00'}}
    review = post(admin, '/api/schedules/preview', data).json['data']
    response = post(admin, '/api/schedules', {'review_token': review['review_token']})
    assert response.status_code == 201, response.json
    return response.json['data']


def test_one_failing_schedule_does_not_block_the_others(admin, app, monkeypatch):
    profile = credential(admin)
    first = schedule(admin, [device(admin, profile)['id']])
    second = schedule(admin, [device(admin, profile, ip='10.0.0.2')['id']])
    moment = time.time()
    with app.app_context():
        for ident in (first['id'], second['id']):
            db.session.get(ScheduledTask, ident).next_due = moment - 5
        db.session.commit()
    original = S.dispatch

    def broken(task, *args, **kwargs):
        if task.id == first['id']:
            raise PermissionError('playbook unreadable')
        return original(task, *args, **kwargs)

    monkeypatch.setattr(S, 'dispatch', broken)
    with app.app_context():
        # Temporary errors are retried; the task is disabled only when the error persists.
        for _ in range(S.MAX_TICK_FAILURES - 1):
            with pytest.raises(PermissionError):
                S.tick(moment)
            assert db.session.get(ScheduledTask, first['id']).enabled
        S.tick(moment)
        S.tick(moment)
        failed = db.session.get(ScheduledTask, first['id'])
        assert not failed.enabled and 'internal error' in failed.attention
        assert AuditEvent.query.filter_by(action='Scheduled task blocked', target=str(first['id'])).count() == 1
        assert ScheduleOccurrence.query.filter_by(task_id=second['id']).count() == 1
        assert ScheduleOccurrence.query.filter_by(task_id=first['id']).count() == 0


def test_changed_target_fails_only_its_own_scheduled_run(admin, app, monkeypatch):
    profile = credential(admin)
    a = device(admin, profile)
    b = device(admin, profile, ip='10.0.0.2')
    task = schedule(admin, [a['id'], b['id']])
    assert post(admin, f"/api/schedules/{task['id']}/run",
                {'request_key': 'reliability-target-scope', 'revision': 1}).status_code == 202
    assert post(admin, f"/api/devices/{b['id']}", {'ssh_port': 2222}, method='PATCH').status_code == 200
    from app.services import backups

    def captured(device, run_id):
        raise APIError('Fixture stops before any device connection.', 'FIXTURE_STOP', 502)

    monkeypatch.setattr(backups, 'capture', captured)
    with app.app_context():
        runs = {row.device_id: row.id for row in OperationRun.query.all()}
    for ident in runs.values():
        JobManager.execute(SimpleNamespace(app=app), ident)
    with app.app_context():
        assert db.session.get(OperationRun, runs[a['id']]).error_code == 'FIXTURE_STOP'
        assert db.session.get(OperationRun, runs[b['id']]).error_code == 'TARGET_CHANGED'
