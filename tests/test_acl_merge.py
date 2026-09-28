"""Access lists merged into Firewall control. Isolated fixtures only; no device I/O."""
import copy

import pytest

from app.models import User, db
from app.security import APIError
from app.services import firewall as fw
from conftest import credential, device, post, signin, token
from test_firewall import fake_device, intent, snapshot


def cisco_snapshot(names=None):
    state = snapshot('cisco')
    state['acl_names'] = names if names is not None else ['TEST']
    return state


def new_acl_intent(*sequences, name='NEW_ACL'):
    changes = [dict(service='https', protocol='tcp', port=443, action='allow', source='10.0.0.100/32',
                    destination='any', acl=name, sequence=seq, create_acl=True) for seq in sequences]
    return dict(source_ip='10.0.0.100', changes=changes)


def signed_in_user(app, role):
    with app.app_context():
        user = User(username='fixture-' + role.lower(), name='Fixture', role=role, must_change_password=False)
        user.set_password('fixture-role-password')
        db.session.add(user)
        db.session.commit()
    client = app.test_client()
    assert signin(client, 'fixture-' + role.lower(), 'fixture-role-password').status_code == 200
    return client


# Page and API retirement

def test_acl_page_redirects_to_firewall_and_leaves_navigation(admin):
    for path in ('/acl', '/acl.html'):
        response = admin.get(path)
        assert response.status_code == 302
        assert response.headers['Location'].endswith('/firewall.html')
    page = admin.get('/firewall.html').get_data(as_text=True)
    assert 'Access lists' not in page and 'href="/acl.html"' not in page


def test_new_acl_runs_are_rejected_with_a_pointer_to_firewall(admin):
    target = device(admin, credential(admin))
    parameters = dict(name='TEST', protocol='tcp', action='permit', source='any', destination='any', port=443)
    response = post(admin, '/api/automation/runs', dict(kind='acl', device_id=target['id'], parameters=parameters))
    assert response.status_code == 410
    assert response.json['error']['code'] == 'MOVED'
    assert '/firewall.html' in response.json['error']['message']
    legacy = admin.post('/acl', data=dict(device_id=target['id'], acl_name='TEST', protocol='tcp', port='443'),
                        headers={'X-CSRFToken': token(admin)})
    assert legacy.status_code == 410


def test_legacy_acl_playbooks_are_admin_only_and_not_schedulable(admin, app):
    target = device(admin, credential(admin))
    books = {b['name']: b['id'] for b in admin.get('/api/playbooks').json['data']['items']}
    book = books['Original/cisco_acl.yml']
    variables = dict(acl_name='TEST', protocol='tcp', action='permit', src_ip='any', dst_ip='any', port=443)
    request = dict(kind='playbook', device_id=target['id'], playbook_id=book, variables=variables)
    operator = signed_in_user(app, 'OPERATOR')
    denied = post(operator, '/api/automation/runs', request)
    assert denied.status_code == 403
    with app.app_context():
        from app.api import prepare_run
        from app.models import Device
        target_row = db.session.get(Device, target['id'])
        with pytest.raises(APIError) as scheduled:
            prepare_run('playbook', target_row, dict(playbook_id=book, variables=variables))
        assert scheduled.value.status == 403
        allowed = prepare_run('playbook', target_row, dict(playbook_id=book, variables=variables), actor_role='ADMIN')
        assert allowed['variables']['acl_name'] == 'TEST'


# Operator permissions on Cisco

def test_operator_can_add_scoped_preset_entry_to_existing_cisco_acl():
    plan = fw.compile_plan(intent(acl='TEST', sequence=10), fake_device('cisco'), cisco_snapshot(), 'OPERATOR')
    assert plan['risk'] == 'MEDIUM'


@pytest.mark.parametrize('change', [dict(action='block'), dict(source='any'), dict(service='custom', port=8443)])
def test_operator_still_needs_admin_for_high_risk_or_custom_cisco_changes(change):
    data = intent(acl='TEST', sequence=10)
    data['changes'][0].update(change)
    with pytest.raises(APIError) as err:
        fw.compile_plan(data, fake_device('cisco'), cisco_snapshot(), 'OPERATOR')
    assert err.value.status == 403


def test_cisco_management_lockout_is_still_detected():
    data = intent(acl='TEST', sequence=10)
    data['changes'][0].update(service='ssh', port=22, action='block', source='10.0.0.100/32', destination='10.0.0.1')
    assert fw.compile_plan(data, fake_device('cisco'), cisco_snapshot(), 'ADMIN')['risk'] == 'LOCKOUT'


# Creating a new Cisco ACL

def test_create_new_acl_is_low_risk_unbound_and_recoverable():
    plan = fw.compile_plan(new_acl_intent(10, 20), fake_device('cisco'), cisco_snapshot(), 'OPERATOR')
    assert plan['risk'] == 'LOW'
    assert [item['parents'] for item in plan['items']] == [['ip access-list extended NEW_ACL']] * 2
    assert [item['commands'] for item in plan['items']] == [['10 permit tcp host 10.0.0.100 any eq 443'],
                                                          ['20 permit tcp host 10.0.0.100 any eq 443']]
    assert plan['recovery'] == ['no ip access-list extended NEW_ACL']
    assert any('NEW_ACL is not attached' in warning for warning in plan['warnings'])


@pytest.mark.parametrize('name,names', [('TEST', ['TEST']), ('LEGACY_STD', ['LEGACY_STD', 'TEST'])])
def test_create_rejects_names_already_used_by_any_acl(name, names):
    with pytest.raises(APIError) as err:
        fw.compile_plan(new_acl_intent(10, name=name), fake_device('cisco'), cisco_snapshot(names), 'ADMIN')
    assert err.value.status == 409


def test_entries_for_a_new_acl_must_all_be_marked_new():
    data = new_acl_intent(10, 20)
    data['changes'][1]['create_acl'] = False
    with pytest.raises(APIError):
        fw.compile_plan(data, fake_device('cisco'), cisco_snapshot(), 'ADMIN')


@pytest.mark.parametrize('platform,value', [('mikrotik', True), ('cisco', 'yes')])
def test_create_acl_flag_is_validated(platform, value):
    data = intent(**({'acl': 'NEW_ACL', 'sequence': 10} if platform == 'cisco' else {}))
    data['changes'][0]['create_acl'] = value
    baseline = cisco_snapshot() if platform == 'cisco' else snapshot()
    with pytest.raises(APIError) as err:
        fw.compile_plan(data, fake_device(platform), baseline, 'ADMIN')
    assert err.value.status == 422


def test_verification_of_a_created_acl():
    plan = fw.compile_plan(new_acl_intent(10), fake_device('cisco'), cisco_snapshot(), 'ADMIN')
    after = copy.deepcopy(plan['baseline'])
    after['acls']['NEW_ACL'] = [dict(sequence=10, body='permit tcp host 10.0.0.100 any eq 443', raw='')]
    after['acl_names'] = ['NEW_ACL', 'TEST']
    assert fw.verify(plan, after)[0]
    after['acl_names'] = ['NEW_ACL', 'OTHER', 'TEST']
    assert not fw.verify(plan, after)[0]


def test_acl_names_include_standard_numbered_and_extended_lists():
    raw = ('Standard IP access list 10\n    10 permit 10.0.0.0, wildcard bits 0.0.0.255\n'
           'Standard IP access list MGMT_STD\n    10 permit any\n'
           'Extended IP access list 101\n    10 permit ip any any\n'
           'Extended IP access list EDGE_IN\n    10 permit tcp any any eq www\n')
    assert fw.cisco_acl_names(raw) == ['10', '101', 'EDGE_IN', 'MGMT_STD']
    assert list(fw.parse_cisco(raw)) == ['EDGE_IN']
