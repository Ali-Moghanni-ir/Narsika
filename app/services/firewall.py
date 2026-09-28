"""Firewall intent compiler: bounded input, immutable reviews and observed results.

IPv4 filter additions only. Existing rules remain read-only. Cisco edits target
existing extended ACLs; bindings, NAT, services and routing are never rewritten.
"""
import hashlib
import ipaddress
import json
import re
import shlex
import socket
import time
import uuid
from flask import current_app
from ..models import db, FirewallReview, now
from ..security import APIError, audit, decrypt, encrypt, fail, integer
from . import network as net
from .jobs import target_identity

MAX_ITEMS = 20
TTL = 600
RANK = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2, 'LOCKOUT': 3}
PRESETS = [
    dict(id='ssh', name='SSH', protocol='tcp', port=22, icon='terminal', note='Remote management'),
    dict(id='https', name='HTTPS', protocol='tcp', port=443, icon='shield', note='Secure web access'),
    dict(id='http', name='HTTP', protocol='tcp', port=80, icon='grid', note='Web access'),
    dict(id='dns', name='DNS', protocol='udp', port=53, icon='search', note='UDP name resolution'),
    dict(id='snmp', name='SNMP', protocol='udp', port=161, icon='pulse', note='Device monitoring'),
    dict(id='ping', name='Ping', protocol='icmp', port=None, icon='ports', note='IPv4 echo requests only'),
    dict(id='winbox', name='Winbox', protocol='tcp', port=8291, icon='settings', note='RouterOS management'),
    dict(id='telnet', name='Telnet', protocol='tcp', port=23, icon='terminal', note='Legacy remote access'),
]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_ip(device):
    """Local route hint only, not the source observed through NAT or a jump host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect((device.ip_address, device.ssh_port))
            return sock.getsockname()[0]
    except OSError:
        return None


def parse_routeros(raw):
    rows = []
    # Export terse excludes runtime counters. Wrapped exports are joined first.
    raw = re.sub(r'\\\r?\n\s*', '', raw)
    for line in raw.splitlines():
        if not line.startswith('/ip firewall filter add '):
            if line.strip() and not line.lstrip().startswith('#') and line.strip()!='/ip firewall filter':
                fail('Unexpected RouterOS export format. No writable snapshot was created.', 'UNSUPPORTED', 422)
            continue
        try:
            fields = dict(token.split('=', 1) for token in shlex.split(line)[4:] if '=' in token)
        except ValueError:
            fail('The RouterOS filter export could not be parsed.', 'UNSUPPORTED', 422)
        rows.append(dict(index=len(rows), chain=fields.get('chain', ''), action=fields.get('action', ''),
                         protocol=fields.get('protocol', 'ip'), source=fields.get('src-address', 'any'),
                         destination=fields.get('dst-address', 'any'), port=fields.get('dst-port', ''),
                         comment=fields.get('comment', ''), disabled=fields.get('disabled')=='yes',
                         fields=fields, raw=line))
    if '#error' in raw.lower():
        fail('RouterOS returned an incomplete export.', 'PARTIAL_EXPORT', 502)
    return rows


def parse_cisco(raw):
    acls = {}
    name = None
    for line in raw.splitlines():
        header = re.match(r'^Extended IP access list ([A-Za-z][A-Za-z0-9_-]{0,63})\s*$', line)
        if header:
            name = header[1]; acls[name] = []
            continue
        if line and not line[0].isspace():
            name = None
        if name and line.strip():
            clean = re.sub(r'\s+\(\d+ match(?:es)?\).*$', '', line.strip())
            m = re.match(r'^(\d+) (.+)$', clean)
            if not m:
                fail('ACL output lacks stable sequence numbers.', 'UNSUPPORTED', 422)
            acls[name].append(dict(sequence=int(m[1]), raw=clean, body=m[2]))
    return acls


def read_state(device):
    """Caller owns device_lock. No configuration writes or demo fallback."""
    with net.connection(device) as client:
        if device.platform == 'mikrotik':
            raw = net.command(client, '/ip firewall filter export terse')
            rows = parse_routeros(raw)
            state = dict(rules=rows, acls={}, bindings=[], raw='\n'.join(r['raw'] for r in rows))
        elif device.platform == 'cisco':
            raw = net.command(client, 'show ip access-lists')
            acls = parse_cisco(raw)
            config = net.command(client, 'show running-config')
            bindings = []
            context = ''
            acl_context = None
            remark_acls = set()
            for line in config.splitlines():
                if line and not line[0].isspace():
                    context = line if line.startswith(('interface ', 'line vty ')) else ''
                    match = re.match(r'^ip access-list extended (\S+)$', line)
                    acl_context = match[1] if match else None
                if acl_context and re.match(r'^\s+(?:\d+\s+)?remark\b', line):
                    remark_acls.add(acl_context)
                if context and re.match(r'^\s+(ip access-group|access-class) ', line):
                    bindings.append(context+' / '+line.strip())
            rows = [dict(acl=name, **row) for name, entries in acls.items() for row in entries]
            state = dict(rules=rows, acls=acls, bindings=bindings,
                         raw='\n'.join(name+': '+r['raw'] for name, entries in acls.items() for r in entries))
            # show ip access-lists may hide remarks and their occupied sequence.
            state['unsupported_acls'] = sorted(remark_acls)
        else:
            fail('This platform has no firewall adapter.', 'UNSUPPORTED', 422)
    # Exclude time and route hints from the configuration fingerprint.
    state['fingerprint'] = digest({k:state.get(k) for k in ('rules', 'acls', 'bindings', 'unsupported_acls')})
    state['source_ip_hint'] = source_ip(device)
    state['captured_at'] = now()
    return state


def cidr(value):
    if value == 'any':
        return 'any'
    if not isinstance(value, str):
        fail('Use an IPv4 address, CIDR, or any.')
    try:
        network = ipaddress.IPv4Network(value, strict=False)
        return 'any' if network.prefixlen==0 else str(network)
    except ValueError:
        fail('Use an IPv4 address, CIDR, or any.')


def contains(network, address):
    return network == 'any' or ipaddress.IPv4Address(address) in ipaddress.IPv4Network(network)


def ios_address(value):
    if value == 'any':
        return 'any'
    network = ipaddress.IPv4Network(value)
    if network.prefixlen == 32:
        return 'host '+str(network.network_address)
    return str(network.network_address)+' '+str(network.hostmask)


def normalize(data, device):
    if not isinstance(data, dict) or set(data)-{'source_ip', 'changes'}:
        fail('Send source_ip and changes only.')
    source = data.get('source_ip')
    if not isinstance(source, str):
        fail('Confirm the Narsika source IPv4 address as seen by this device.')
    try:
        source = str(ipaddress.IPv4Address(source))
        if ipaddress.ip_address(source).is_unspecified or ipaddress.ip_address(source).is_multicast:
            raise ValueError()
    except (ValueError, TypeError):
        fail('Confirm the Narsika source IPv4 address as seen by this device.')
    changes = data.get('changes')
    if not isinstance(changes, list) or not 1 <= len(changes) <= MAX_ITEMS:
        fail('Review between 1 and 20 changes at a time.')
    result = []
    for i, row in enumerate(changes):
        allowed = {'service', 'action', 'protocol', 'source', 'destination', 'port', 'chain', 'acl', 'sequence', 'position'}
        if not isinstance(row, dict) or set(row)-allowed:
            fail('Unknown firewall rule fields.')
        protocol = row.get('protocol')
        action = row.get('action')
        if protocol not in ('tcp', 'udp', 'icmp', 'ip') or action not in ('allow', 'block'):
            fail('Select a supported protocol and Allow or Block.')
        service = row.get('service', 'custom')
        if service not in {p['id'] for p in PRESETS} | {'custom'}:
            fail('Unknown service.')
        if service != 'custom':
            preset = next(p for p in PRESETS if p['id'] == service)
            if protocol != preset['protocol']:
                fail('Service protocol does not match the preset.')
            if service == 'winbox' and device.platform != 'mikrotik':
                fail('Winbox is available for RouterOS only.')
        port = integer(row.get('port'), 'Destination port') if protocol in ('tcp', 'udp') else None
        if port is None and row.get('port') not in (None, ''):
            fail('ICMP and Any IP do not have a destination port.')
        item = dict(service=service, protocol=protocol, action=action, port=port,
                    source=cidr(row.get('source', 'any')), destination=cidr(row.get('destination', 'any')))
        if device.platform == 'mikrotik':
            chain = row.get('chain', 'input')
            if chain not in ('input', 'forward', 'output'):
                fail('Select input, forward or output.')
            position = row.get('position', 'first')
            if position not in ('first', 'last'):
                fail('Select first or last rule position.')
            item.update(chain=chain, position=position)
        else:
            acl = row.get('acl', '')
            if not isinstance(acl, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', acl):
                fail('Choose an existing named extended IPv4 ACL.')
            item.update(acl=acl, sequence=integer(row.get('sequence'), 'Sequence', 1, 2147483646))
        if item in result:
            fail('The change set contains duplicate rules.')
        result.append(item)
    return source, result


def compile_plan(data, device, state, role):
    source, changes = normalize(data, device)
    items, warnings, occupied = [], [], set()
    for index, row in enumerate(changes):
        risk = 'MEDIUM'
        inbound = contains(row['source'], source) and contains(row['destination'], device.ip_address)
        outbound = contains(row['destination'], source) and contains(row['source'], device.ip_address)
        # Return traffic uses the client ephemeral destination port, not ssh_port.
        # RouterOS chains narrow direction; Cisco bindings are deliberately conservative.
        chain = row.get('chain')
        inbound = inbound and chain != 'output'
        outbound = outbound and chain != 'input'
        possible = row['action']=='block' and (
            inbound and (row['protocol']=='ip' or row['protocol']=='tcp' and row['port']==device.ssh_port)
            or outbound and row['protocol'] in ('tcp', 'ip'))
        if possible:
            risk = 'LOCKOUT'
        elif row['action']=='block' or row['source']=='any':
            risk = 'HIGH'
        preset_port=device.ssh_port if row['service']=='ssh' else next((p['port'] for p in PRESETS if p['id']==row['service']),None)
        if row['service']!='custom' and row['port']!=preset_port:
            risk = max((risk, 'HIGH'), key=RANK.get)
        if device.platform == 'cisco':
            acl = row['acl']
            if acl not in state['acls']:
                fail('The selected extended ACL was not observed on the device.', 'STALE_STATE', 409)
            if acl in state.get('unsupported_acls', []):
                fail('This ACL contains remarks with potentially hidden sequence numbers. It is read-only in this adapter.', 'UNSUPPORTED', 422)
            seq = row['sequence']
            used = {x['sequence'] for x in state['acls'][acl]}
            for value in (seq,):
                if value in used or (acl, value) in occupied:
                    fail('The selected ACL sequence is already occupied.')
                occupied.add((acl, value))
            body = ('permit' if row['action']=='allow' else 'deny')+' '+row['protocol']+' '+ios_address(row['source'])+' '+ios_address(row['destination'])
            if row['port'] is not None:
                body += ' eq '+str(row['port'])
            if row['protocol']=='icmp':
                body += ' echo'
            tag = 'NARSIKA_FW_'+uuid.uuid4().hex[:16]
            commands = [f'{seq} {body}']
            rollback = [f'ip access-list extended {acl}', f' no {seq}', ' exit']
            risk = max((risk, 'HIGH'), key=RANK.get)
            parents = ['ip access-list extended '+acl]
            expected = dict(acl=acl, sequence=seq, body=body, tag=tag)
        else:
            tag = 'NARSIKA_FW_'+uuid.uuid4().hex[:16]
            fields = dict(chain=row['chain'], action='accept' if row['action']=='allow' else 'drop', comment=tag)
            if row['protocol']!='ip':
                fields['protocol'] = row['protocol']
            if row['protocol']=='icmp':
                fields['icmp-options'] = '8:0'
            for param, key in [('source', 'src-address'), ('destination', 'dst-address')]:
                if row[param]!='any':
                    fields[key] = row[param]
            if row['port'] is not None:
                fields['dst-port'] = str(row['port'])
            add = '/ip firewall filter add '+' '.join(k+'="'+v+'"' for k,v in fields.items())
            move = ('; :if ([:len [/ip firewall filter find]] > 1) do={ /ip firewall filter move $new destination=0 }' if row['position']=='first' else '')
            # Tag is generated by the server, never interpolated from free-form input.
            cmd = '{ :if ([:len [/ip firewall filter find where comment="'+tag+'"]] != 0) do={ :error "Review already applied" }; '+add+'; :local new [/ip firewall filter find where comment="'+tag+'"]; :if ([:len $new] != 1) do={ :error "Ambiguous inserted rule" }'+move+'; :put "NARSIKA_APPLIED" }'
            commands = [cmd]; parents = []
            rollback = ['/ip firewall filter remove [find where comment="'+tag+'"]']
            expected = dict(fields=fields, tag=tag)
        label = f'{index+1}. {row["action"].title()} {row["service"] if row["service"]!="custom" else row["protocol"].upper()}'+(f' :{row["port"]}' if row['port'] else '')
        items.append(dict(label=label, rule=row, risk=risk, parents=parents, commands=commands, rollback=rollback, expected=expected))
    # Do not silently reorder first-match policies to move lockout last.
    # Any earlier lockout item must be moved by the user and reviewed again.
    if any(i['risk']=='LOCKOUT' for i in items[:-1]):
        fail('Move the management-disrupting change to the final position, or use a separate change set.')
    if device.platform == 'mikrotik':
        # Moving each new rule to zero reverses insertion order; show final order explicitly.
        first = [i for i in items if i['rule']['position']=='first']
        last = [i for i in items if i['rule']['position']=='last']
        final_order = [i['label'] for i in reversed(first)]+['Existing rules (unchanged)']+[i['label'] for i in last]
        warnings.append('First-position rules run before existing rules, including established/related rules. Multiple first-position additions appear in reverse execution order.')
        warnings.append('The snapshot contains exported IPv4 filter configuration, not every dynamic runtime rule. FastTrack, existing connections and other chains may change the observed traffic effect.')
    else:
        final_order = [f'{i["rule"]["acl"]} / {i["rule"]["sequence"]}: {i["label"]}' for i in sorted(items, key=lambda i:(i['rule']['acl'], i['rule']['sequence']))]
        warnings.append('Existing ACL bindings and implicit deny remain unchanged. All attachment points may be affected. Cisco running configuration is not saved to startup automatically.')
        warnings.append('Cisco traceability uses the immutable receipt, ACL name, sequence and observed rule body. No ownership comment is written on the device; a receipt match is not proof of exclusive ownership.')
    risk = max((i['risk'] for i in items), key=RANK.get)
    if role=='OPERATOR' and (risk in ('HIGH', 'LOCKOUT') or any(i['rule']['service']=='custom' for i in items)):
        fail('An administrator must review custom or high-risk firewall changes.', 'FORBIDDEN', 403)
    warnings.extend([
        'Allow adds a matching filter rule; it does not start a service, change its listening port, or prove end-to-end reachability.',
        'Unmodelled rules, NAT, routing and other administrators can change the effect. The risk estimate is not a guarantee.',
        'Automatic timed rollback is unavailable in this review build. Download the recovery commands before applying. An encrypted backup is mandatory.',
    ])
    if risk=='LOCKOUT':
        warnings.insert(0, 'This change may disconnect Narsika. Monitoring, backups and future operations may stop. Console or out-of-band recovery may be required.')
    for index, a in enumerate(items):
        for b in items[index+1:]:
            fields = ('protocol', 'port', 'source', 'destination', 'chain', 'acl')
            if all(a['rule'].get(k)==b['rule'].get(k) for k in fields) and a['rule']['action']!=b['rule']['action']:
                fail('Conflicting Allow and Block changes match the same traffic. Keep one intent and review again.')
    return dict(version=1, device=device.public(), target=target_identity(device), source_ip=source,
                source_ip_hint=state['source_ip_hint'], baseline=state, items=items, risk=risk,
                final_order=final_order, warnings=warnings, automatic_rollback=False,
                recovery=[cmd for item in reversed(items) for cmd in item['rollback']])


def public_review(row):
    plan = decrypt(row.encrypted_plan)
    return dict(id=row.id, checksum=row.checksum, expires_at=row.expires_at, status=row.status,
                created_at=row.created_at, run_id=row.run_id, plan=plan)


def annotate_receipt_matches(state, device):
    """Add presentation-only provenance without changing the configuration fingerprint."""
    if device.platform != 'cisco':
        return state
    matches = {}
    for row in FirewallReview.query.filter_by(device_id=device.id, status='SUCCESS').order_by(FirewallReview.created_at.desc()).limit(200):
        plan = decrypt(row.encrypted_plan)
        if digest(plan) != row.checksum or plan['target'] != target_identity(device):
            continue
        for item in plan['items']:
            e = item['expected']
            if 'acl' in e:
                matches.setdefault((e['acl'], e['sequence'], canonical_ios(e['body'])), row.id)
    # UI-only fields must never be passed into a subsequent review baseline.
    for rule in state['rules']:
        key = (rule['acl'], rule['sequence'], canonical_ios(rule['body']))
        if key in matches:
            rule['receipt_id'] = matches[key]
    return state


def canonical_ios(body):
    aliases = {'ssh':'22', 'telnet':'23', 'www':'80', 'http':'80', 'https':'443', 'domain':'53', 'snmp':'161'}
    return re.sub(r'\beq (\S+)', lambda m: 'eq '+aliases.get(m[1], m[1]), body)


def verify(plan, after):
    """Configuration verification only; never claims a packet-flow simulation."""
    outcomes = []
    for item in plan['items']:
        expected = item['expected']
        if plan['device']['platform']=='mikrotik':
            matching = [r for r in after['rules'] if r['comment']==expected['tag']]
            def equal(key, value):
                actual = matching[0]['fields'].get(key)
                if key in ('src-address', 'dst-address') and actual:
                    return cidr(actual)==cidr(value)
                return actual==value
            outcomes.append(len(matching)==1 and not matching[0]['disabled'] and
                            all(equal(k,v) for k,v in expected['fields'].items()))
        else:
            entries = after['acls'].get(expected['acl'], [])
            # IOS renders well-known numeric ports as service names.
            outcomes.append(any(r['sequence']==expected['sequence'] and canonical_ios(r['body'])==canonical_ios(expected['body']) for r in entries))
    if plan['device']['platform']=='mikrotik':
        actual = [r['comment'] for r in after['rules']]
        baseline = [r['comment'] for r in plan['baseline']['rules']]
        first = [i['expected']['tag'] for i in reversed(plan['items']) if i['rule']['position']=='first']
        last = [i['expected']['tag'] for i in plan['items'] if i['rule']['position']=='last']
        if actual != first+baseline+last:
            return False, outcomes
        unchanged = after['rules'][len(first):len(first)+len(baseline)]
        if [r['fields'] for r in unchanged] != [r['fields'] for r in plan['baseline']['rules']]:
            return False, outcomes
    else:
        if after['bindings'] != plan['baseline']['bindings']:
            return False, outcomes
        if sum(map(len,after['acls'].values()))!=sum(map(len,plan['baseline']['acls'].values()))+len(plan['items']):
            return False, outcomes
        if after.get('unsupported_acls',[])!=plan['baseline'].get('unsupported_acls',[]):
            return False, outcomes
        for acl, entries in plan['baseline']['acls'].items():
            for entry in entries:
                if not any(r['sequence']==entry['sequence'] and r['body']==entry['body'] for r in after['acls'].get(acl, [])):
                    return False, outcomes
    return all(outcomes), outcomes


def execute(run, params, device, user):
    from .automation import run_ansible
    from .backups import capture
    row = db.session.get(FirewallReview, params.get('review_id'))
    if not row or row.run_id!=run.id:
        fail('Firewall receipt is missing.', 'INVALID_REVIEW', 409)
    plan = decrypt(row.encrypted_plan)
    if digest(plan)!=row.checksum:
        fail('Review integrity check failed.', 'INVALID_REVIEW', 409)
    if row.expires_at<time.time() or user.session_version!=params.get('session_version'):
        fail('Review expired or account changed while queued. Review again.', 'REVIEW_EXPIRED', 409)
    if plan['risk'] in ('HIGH', 'LOCKOUT') and user.role!='ADMIN':
        fail('Administrator permission is required.', 'FORBIDDEN', 403)
    with net.device_lock(device.id):
        before = read_state(device)
        if before['fingerprint']!=plan['baseline']['fingerprint']:
            fail('Firewall changed since review. No commands sent. Refresh and review again.', 'STALE_STATE', 409)
        backup = capture(device, run.id)
        audit('Firewall apply starting', device.ip_address, 'running', 'Review '+row.id+'; backup '+str(backup.id), actor_id=user.id)
        run.output=f'Backup {backup.id} saved. Executing review {row.checksum[:12]}.\n'
        db.session.commit()
        # Recheck after backup closes the second read window as far as possible.
        if read_state(device)['fingerprint']!=before['fingerprint']:
            fail('Firewall changed during backup. No commands sent.', 'STALE_STATE', 409)
        db.session.refresh(user);db.session.refresh(device);db.session.refresh(run)
        if not user.is_active or user.role not in ('ADMIN','OPERATOR') or user.session_version!=params.get('session_version'):
            fail('Account authorization changed during preflight. No commands sent.', 'FORBIDDEN', 403)
        if target_identity(device)!=plan['target'] or device.archived_at:
            fail('Target changed during preflight. No commands sent.', 'TARGET_CHANGED', 409)
        if run.cancel_requested:
            run.status='CANCELLED';row.status='CANCELLED'
            return 'Cancelled before firewall execution. No change commands were sent.'
        row.status='APPLYING'; db.session.commit()
        execution_error = None
        try:
            run_ansible(run, {'items':plan['items']}, device, lock_held=True)
        except APIError as ex:
            execution_error = ex.code
        except Exception:
            execution_error = 'EXECUTION_ERROR'
        db.session.refresh(run)
        row.status='VERIFYING'; db.session.commit()
        try:
            after = read_state(device)
        except APIError:
            run.status='APPLIED_UNVERIFIED'
            row.status=run.status
            return 'Execution was attempted, but a fresh SSH connection could not verify the result. This does not prove any command was applied. Use the saved backup and recovery commands through console/OOB.'
        complete, outcomes = verify(plan, after)
        if complete:
            run.status='SUCCESS'
            detail='Fresh SSH verified all requested configuration entries and managed order. End-to-end traffic is not tested.'
        else:
            run.status='PARTIAL' if any(outcomes) or after['fingerprint']!=before['fingerprint'] else ('CANCELLED' if run.cancel_requested else 'FAILED')
            detail=f'Configuration verification matched {sum(outcomes)}/{len(outcomes)} requested entries. Inspect the target before retrying.'
        if execution_error:
            run.error_code=execution_error
            detail+=' The executor reported '+execution_error+'.'
        row.status=run.status
        return detail
