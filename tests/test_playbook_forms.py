"""Playbook forms: every field belongs to its playbook, and input is checked before a run is queued."""
from pathlib import Path
from types import SimpleNamespace
import re
import pytest
from app.services import catalog

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', sorted(catalog.FORMS))
def test_form_fields_are_variables_the_playbook_uses(name):
    source = (ROOT / 'Playbooks' / name).read_text()
    for field in catalog.FORMS[name]:
        assert re.search(r'\b' + field['name'] + r'\b', source), (name, field['name'])


def check(name, variables):
    return catalog.form_variables(SimpleNamespace(name=name, builtin=True), variables)


def test_form_input_is_typed_and_unknown_keys_are_dropped():
    result = check('Cisco/03_ensure_vlan.yml', {'vlan_id': '40', 'vlan_name': 'Eng', 'narsika_apply': True, 'extra': 'x'})
    assert result == {'vlan_id': 40, 'vlan_name': 'Eng', 'save_config': False, 'narsika_apply': True}
    assert check('Linux/04_manage_service.yml', {'service_name': 'cron', 'service_state': 'started', 'service_enabled': None})['service_enabled'] is None


@pytest.mark.parametrize('name,variables,message', [
    ('Cisco/03_ensure_vlan.yml', {'vlan_name': 'Eng'}, 'VLAN ID is required'),
    ('Cisco/03_ensure_vlan.yml', {'vlan_id': 5000, 'vlan_name': 'Eng'}, 'from 2 to 4094'),
    ('Original/cisco_acl.yml', {'acl_name': 'A', 'action': 'permit', 'protocol': 'ip', 'src_ip': 'any', 'dst_ip': '10.0.0.1\nexit'}, 'one line'),
    ('Original/cisco_acl.yml', {'acl_name': 'A', 'action': 'allow', 'protocol': 'ip', 'src_ip': 'any', 'dst_ip': 'any'}, 'choose permit, deny'),
    ('MikroTik/04_dhcp_reservation.yml', {'dhcp_server': 'd', 'lease_address': '10.0.0.300', 'lease_mac': '02:00:00:00:00:50', 'lease_comment': 'c'}, 'invalid format'),
])
def test_bad_form_input_is_refused(name, variables, message):
    with pytest.raises(Exception, match=message):
        check(name, variables)


def test_uploaded_playbooks_keep_free_variables():
    book = SimpleNamespace(name='Cisco/03_ensure_vlan.yml', builtin=False)
    assert catalog.form_variables(book, {'anything': 1}) == {'anything': 1}


@pytest.mark.parametrize('value', ['²', '٤٠', '4e1', '40.5', True, -40, '9999999'])
def test_number_fields_accept_only_plain_numbers(value):
    # Characters such as '²' pass str.isdigit() but make int() fail; they must give a clear error.
    with pytest.raises(Exception, match='enter a number from 2 to 4094'):
        check('Cisco/03_ensure_vlan.yml', {'vlan_id': value, 'vlan_name': 'Eng'})


def test_api_refuses_bad_form_input_before_queueing(admin, app):
    from app.models import OperationRun, Playbook
    from conftest import credential, device, post
    target = device(admin, credential(admin))
    with app.app_context():
        book = Playbook.query.filter_by(name='Cisco/03_ensure_vlan.yml').one().id
    response = post(admin, '/api/automation/runs', {'kind': 'playbook', 'device_id': target['id'], 'playbook_id': book,
                                                    'variables': {'vlan_id': '²', 'vlan_name': 'Eng'}})
    assert response.status_code == 422 and 'enter a number' in response.json['error']['message']
    with app.app_context():
        assert OperationRun.query.count() == 0


def test_api_lists_form_fields_only_for_packaged_playbooks(admin):
    items = {item['name']: item for item in admin.get('/api/playbooks').json['data']['items']}
    assert [field['name'] for field in items['Linux/03_ensure_package.yml']['fields']] == ['package_name', 'package_state']
    assert items['Linux/01_health_report.yml']['fields'] is None
