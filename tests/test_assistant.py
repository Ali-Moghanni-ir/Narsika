"""AI assistant contracts. The model provider is always mocked; no network or device I/O."""
import json

import pytest

from app.models import db, AuditEvent, AssistantMessage, Device, Setting, User
from app.services import assistant as A
from conftest import device, post, signin


def configure(client, **values):
    body = {'enabled': True, 'api_key': 'sk-test-secret-value', **values}
    response = post(client, '/api/assistant/config', body, method='PUT')
    assert response.status_code == 200, response.json
    return response.json['data']


class FakeProvider:
    """Scripted Chat Completions replies; records every request it receives."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, values, key, messages, tools):
        self.requests.append({'key': key, 'messages': json.loads(json.dumps(messages)), 'tools': tools})
        return self.replies.pop(0)


def tool_call(name, arguments=None, ident='call-1'):
    return {'role': 'assistant', 'content': '', 'tool_calls': [
        {'id': ident, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(arguments or {})}}]}


def answer(text):
    return {'role': 'assistant', 'content': text}


def test_disabled_by_default_and_page_renders(admin):
    config = admin.get('/api/assistant/config').json['data']
    assert config['enabled'] is False and config['ready'] is False
    assert config['model'] == 'stealth/ox-alpha' and config['provider'] == 'openrouter.ai'
    response = post(admin, '/api/assistant/ask', {'message': 'hello'})
    assert response.status_code == 409 and response.json['error']['code'] == 'AI_DISABLED'
    page = admin.get('/assistant.html').get_data(as_text=True)
    assert 'AI assistant' in page and 'assistant.js' in page


def test_api_key_is_encrypted_and_never_returned(admin, app):
    data = configure(admin)
    assert data['has_api_key'] is True and data['ready'] is True
    assert 'sk-test-secret-value' not in json.dumps(data)
    with app.app_context():
        stored = db.session.get(Setting, A.API_KEY_KEY).value
        assert 'sk-test-secret-value' not in json.dumps(stored)
        assert A.api_key() == 'sk-test-secret-value'
        details = ' '.join(e.detail for e in AuditEvent.query.all())
        assert 'sk-test-secret-value' not in details
    cleared = configure(admin, api_key='', clear_api_key=True)
    assert cleared['has_api_key'] is False and cleared['ready'] is False


@pytest.mark.parametrize('url', ['http://api.example.com/v1', 'ftp://example.com', 'https://user:pw@example.com/v1',
                                 'https://example.com/v1?x=1', 'not a url'])
def test_rejects_unsafe_base_urls(admin, url):
    response = post(admin, '/api/assistant/config', {'base_url': url}, method='PUT')
    assert response.status_code == 422


def test_plain_http_allowed_only_for_local_model_server(admin):
    data = configure(admin, base_url='http://127.0.0.1:11434/v1', model='qwen2.5:7b', api_key='', clear_api_key=True)
    assert data['local'] is True and data['ready'] is True


def test_only_admin_can_configure(admin, app):
    with app.app_context():
        viewer = User(username='viewer', name='Viewer', role='VIEWER', must_change_password=False)
        viewer.set_password('viewer-password-for-tests')
        db.session.add(viewer)
        db.session.commit()
    configure(admin)
    other = app.test_client()
    assert signin(other, 'viewer', 'viewer-password-for-tests').status_code == 200
    assert post(other, '/api/assistant/config', {'enabled': False}, method='PUT').status_code == 403
    assert post(other, '/api/assistant/test', {}).status_code == 403
    assert other.get('/api/assistant/config').status_code == 200


def test_tool_loop_reads_records_and_persists_encrypted(admin, app, monkeypatch):
    configure(admin)
    target = device(admin, ip='10.0.0.7')
    with app.app_context():
        row = db.session.get(Device, target['id'])
        row.health_json = {'status': 'degraded', 'cpu_percent': 91, 'memory_percent': None, 'sampled_at': '2026-09-28T10:00:00Z'}
        db.session.commit()
    provider = FakeProvider(tool_call('health_overview'), tool_call('device_health', {'device_id': target['id']}, 'call-2'),
                            answer('**Integration fixture** is degraded: CPU 91%, memory N/A.'))
    monkeypatch.setattr(A, 'chat_completion', provider)
    response = post(admin, '/api/assistant/ask', {'message': 'Which devices need attention?'})
    assert response.status_code == 200, response.json
    data = response.json['data']
    assert 'degraded' in data['message']['text']
    assert [t['tool'] for t in data['message']['tools']] == ['health_overview', 'device_health']
    assert provider.requests[0]['key'] == 'sk-test-secret-value'
    assert {t['function']['name'] for t in provider.requests[0]['tools']} == set(A.TOOLS)
    tool_result = provider.requests[1]['messages'][-1]
    assert tool_result['role'] == 'tool' and '"cpu_percent": 91' in tool_result['content']
    with app.app_context():
        stored = AssistantMessage.query.all()
        assert len(stored) == 2 and all('degraded' not in m.encrypted_content for m in stored)
        event = AuditEvent.query.filter_by(action='AI assistant question').one()
        assert 'Which devices' not in event.detail and 'health_overview' in event.detail

    # Follow-up turns carry the conversation history.
    ident = data['conversation']['id']
    provider = FakeProvider(answer('Still degraded.'))
    monkeypatch.setattr(A, 'chat_completion', provider)
    assert post(admin, '/api/assistant/ask', {'message': 'And now?', 'conversation_id': ident}).status_code == 200
    roles = [m['role'] for m in provider.requests[0]['messages']]
    assert roles == ['system', 'user', 'assistant', 'user']
    thread = admin.get(f'/api/assistant/conversations/{ident}').json['data']
    assert [m['role'] for m in thread['messages']] == ['user', 'assistant', 'user', 'assistant']


def test_tools_are_read_only_and_unknown_tools_are_refused(admin, monkeypatch):
    configure(admin)
    provider = FakeProvider(tool_call('apply_config', {'device_id': 1}), answer('I cannot change devices.'))
    monkeypatch.setattr(A, 'chat_completion', provider)
    response = post(admin, '/api/assistant/ask', {'message': 'Shut down every interface'})
    assert response.status_code == 200
    assert response.json['data']['message']['tools'] == [{'tool': 'apply_config', 'arguments': {'device_id': 1}, 'ok': False}]
    assert 'Unknown tool' in provider.requests[1]['messages'][-1]['content']
    # Every exposed tool is a read-only lookup.
    assert set(A.TOOLS) == {'list_devices', 'health_overview', 'device_health', 'recent_audit',
                            'operation_runs', 'backup_status', 'schedules'}


def test_conversations_are_private(admin, app, monkeypatch):
    configure(admin)
    monkeypatch.setattr(A, 'chat_completion', FakeProvider(answer('Private answer.')))
    ident = post(admin, '/api/assistant/ask', {'message': 'Private question'}).json['data']['conversation']['id']
    with app.app_context():
        operator = User(username='operator', name='Operator', role='OPERATOR', must_change_password=False)
        operator.set_password('operator-password-for-tests')
        db.session.add(operator)
        db.session.commit()
    other = app.test_client()
    assert signin(other, 'operator', 'operator-password-for-tests').status_code == 200
    assert other.get(f'/api/assistant/conversations/{ident}').status_code == 404
    assert other.get('/api/assistant/conversations').json['data']['items'] == []
    assert post(other, '/api/assistant/ask', {'message': 'x', 'conversation_id': ident}).status_code == 404
    assert post(other, f'/api/assistant/conversations/{ident}', {}, method='DELETE').status_code == 404
    assert post(admin, f'/api/assistant/conversations/{ident}', {}, method='DELETE').status_code == 200
    assert admin.get('/api/assistant/conversations').json['data']['items'] == []


def test_upstream_errors_are_mapped_and_audited(admin, app, monkeypatch):
    configure(admin)

    def failing(values, key, messages, tools):
        raise A.AssistantError('The AI provider rejected the API key.')

    monkeypatch.setattr(A, 'chat_completion', failing)
    response = post(admin, '/api/assistant/ask', {'message': 'hello'})
    assert response.status_code == 502 and 'API key' in response.json['error']['message']
    with app.app_context():
        assert AuditEvent.query.filter_by(action='AI assistant question', result='failed').count() == 1
        assert AssistantMessage.query.count() == 0


def test_question_validation(admin):
    configure(admin)
    assert post(admin, '/api/assistant/ask', {'message': ''}).status_code == 422
    assert post(admin, '/api/assistant/ask', {'message': 'x' * 4001}).status_code == 422
    assert post(admin, '/api/assistant/ask', {'message': 'x', 'extra': 1}).status_code == 422


def test_redaction_removes_secrets_but_keeps_audit_wording():
    masker = A.Masker(False)
    text = ('username admin privilege 15 secret 5 $1$abcd$efgh\n enable password 7 0822455D0A16\n'
            'snmp-server community n0tpublic RO\n/user add name=x password=hunter2\n'
            'Authorization: Bearer sk-live-123\n-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----\n'
            'Password changed; Password change required')
    result = masker(text)
    for secret in ('$1$abcd$efgh', '0822455D0A16', 'n0tpublic', 'hunter2', 'sk-live-123', 'MIIE'):
        assert secret not in result
    assert 'Password changed; Password change required' in result


def test_address_masking_is_consistent_and_restored():
    masker = A.Masker(True)
    masked = masker('10.0.0.1 talks to 10.0.0.12 and 10.0.0.1 again; version 15.2.4 stays')
    assert masked == 'ip-1 talks to ip-2 and ip-1 again; version 15.2.4 stays'
    assert masker.restore('Check ip-1 and ip-2, not ip-10.') == 'Check 10.0.0.1 and 10.0.0.12, not ip-10.'


def test_masked_addresses_do_not_reach_the_provider(admin, monkeypatch):
    configure(admin, mask_addresses=True)
    device(admin, ip='10.0.0.9')
    provider = FakeProvider(tool_call('list_devices'), answer('The device is ip-1.'))
    monkeypatch.setattr(A, 'chat_completion', provider)
    response = post(admin, '/api/assistant/ask', {'message': 'Is 10.0.0.9 in inventory?'})
    assert response.json['data']['message']['text'] == 'The device is 10.0.0.9.'
    assert '10.0.0.9' not in json.dumps(provider.requests)
