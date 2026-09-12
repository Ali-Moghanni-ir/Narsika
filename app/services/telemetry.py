import asyncio
import re
import math
import time
import threading
import hashlib
import copy
from functools import wraps
from datetime import datetime, timezone
from flask import current_app
from ..models import db, now
from ..security import APIError, decrypt, fail
from .network import device_lock, connection, command, ping

COUNTERS={}
SAMPLE_LOCK=threading.Lock()


def coalesced_sample(function):
    """Coalesce concurrent readers and reuse actual samples for at most four seconds."""
    @wraps(function)
    def wrapped(device):
        identity=tuple(getattr(device,key) for key in ('id','ip_address','platform','ssh_port','snmp_port','credential_id','snmp_credential_id'))
        profiles=tuple(hashlib.sha256(p.encrypted_secret.encode()).digest() if p else b''
                       for p in (device.credential,device.snmp_credential))
        key=(function.__name__,identity,profiles)
        with SAMPLE_LOCK:
            cache=current_app.extensions.setdefault('telemetry_samples',{})
            entry=cache.get(key)
            if entry and time.monotonic()-entry[0]<4:
                return copy.deepcopy(entry[1])
            pending=current_app.extensions.setdefault('telemetry_pending',{})
            signal=pending.get(key)
            owner=signal is None
            if owner:
                # A failed refresh must not release an expired sample to waiters.
                cache.pop(key,None)
                signal=threading.Event();pending[key]=signal
        if not owner:
            if not signal.wait(30):fail('A device sample is still in progress. Retry shortly.','BUSY',429)
            with SAMPLE_LOCK:
                entry=cache.get(key)
                if entry and time.monotonic()-entry[0]<4:return copy.deepcopy(entry[1])
            fail('The concurrent device sample failed. Retry to obtain a fresh result.','SAMPLE_FAILED',502)
        try:
            result=function(device)
            with SAMPLE_LOCK:
                if len(cache)>=256:cache.pop(next(iter(cache)))
                cache[key]=(time.monotonic(),copy.deepcopy(result))
            return result
        finally:
            with SAMPLE_LOCK:
                pending.pop(key,None);signal.set()
    return wrapped

def number(value):
    try:
        result=float(str(value).strip().rstrip('%'))
        return result if math.isfinite(result) else None
    except (ValueError,TypeError):return None

def speed_mbps(value):
    match=re.search(r'(\d+(?:\.\d+)?)\s*([KMG])(?:b|bit)',str(value),re.I)
    if not match:return None
    return float(match[1])*{'K':.001,'M':1,'G':1000}[match[2].upper()]

def memory_bytes(value):
    match=re.fullmatch(r'([\d.]+)\s*([KMGT]?)(?:i?B)?',str(value).strip(),re.I)
    if not match:return None
    try:return int(float(match[1])*1024**('KMGT'.find(match[2].upper())+1 if match[2] else 0))
    except (ValueError,OverflowError):return None

def parse_cisco(version,cpu,memory,temperature=''):
    result=dict(cpu_percent=None,memory_percent=None,memory_used_bytes=None,memory_free_bytes=None,
        uptime=None,temperature_celsius=None,model=None,os_version=None)
    match=re.search(r'five seconds:\s*(\d+)',cpu,re.I)
    if match:result['cpu_percent']=int(match[1])
    match=re.search(r'Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)\s+Free:\s*(\d+)',memory,re.I)
    if not match:match=re.search(r'^Processor\s+\S+\s+(\d+)\s+(\d+)\s+(\d+)',memory,re.M)
    if match:
        total,used,free=map(int,match.groups())
        result.update(memory_used_bytes=used,memory_free_bytes=free,memory_percent=round(used/total*100,1) if total else None)
    match=re.search(r'uptime is (.+)',version)
    if match:result['uptime']=match[1].strip()
    match=re.search(r'\bVersion\s+([^,\s]+)',version)
    if match:result['os_version']=match[1]
    match=re.search(r'(?im)^cisco\s+(\S+)\s+\(',version)
    if match:result['model']=match[1]
    match=re.search(r'(?:Temperature Value|SYSTEM TEMPERATURE|Temperature)\s*[:=]\s*([\d.]+)',temperature,re.I)
    if match:result['temperature_celsius']=float(match[1])
    return result

def parse_routeros(resource):
    values=dict(re.findall(r'^\s*([\w-]+):\s*(.*?)\s*$',resource,re.M))
    total=memory_bytes(values.get('total-memory'));free=memory_bytes(values.get('free-memory'))
    used=total-free if total is not None and free is not None else None
    return dict(cpu_percent=number(values.get('cpu-load')),memory_percent=round(used/total*100,1) if total and used is not None else None,
        memory_used_bytes=used,memory_free_bytes=free,uptime=values.get('uptime'),temperature_celsius=None,
        model=values.get('board-name'),os_version=values.get('version'))

@coalesced_sample
def health(device):
    from ..security import address
    address(device.ip_address)
    with device_lock(device.id):
        reachable=ping(device.ip_address)
        result=dict(cpu_percent=None,memory_percent=None,memory_used_bytes=None,memory_free_bytes=None,uptime=None,
            temperature_celsius=None,model=None,os_version=None,reachability=reachable,connection_status='UNKNOWN',
            status='unknown',sampled_at=now(),error=None)
        try:
            with connection(device) as client:
                if device.platform=='cisco':
                    version=command(client,'show version')
                    def optional(cmd):
                        try:return command(client,cmd)
                        except APIError:return ''
                    result.update(parse_cisco(version,optional('show processes cpu'),optional('show memory statistics'),optional('show environment temperature status')))
                else:
                    result.update(parse_routeros(command(client,'/system resource print')))
                result.update(status='online' if reachable is not False else 'degraded',connection_status='CONNECTED')
        except APIError as ex:
            failed=ex.code in ('UNREACHABLE','TIMEOUT','DEVICE_ERROR')
            result.update(connection_status=ex.code,status='degraded' if reachable or ex.code=='AUTH_FAILED' else 'offline' if reachable is False and failed else 'unknown',error={'code':ex.code,'message':ex.message})
        result['support']={key:('AVAILABLE' if result[key] is not None else 'UNAVAILABLE') for key in ('cpu_percent','memory_percent','temperature_celsius','uptime')}
        device.health_json=result
        if result.get('model'):device.model=result['model'][:100]
        db.session.commit()
        return result

async def snmp_snapshot(ip,port,username,values):
    from pysnmp.hlapi.v3arch.asyncio import (SnmpEngine,UsmUserData,UdpTransportTarget,ContextData,ObjectType,ObjectIdentity,
        bulk_walk_cmd,get_cmd,usmHMAC192SHA256AuthProtocol,usmAesCfb128Protocol)
    engine=SnmpEngine()
    auth=UsmUserData(username,values['auth_password'],values['priv_password'],authProtocol=usmHMAC192SHA256AuthProtocol,privProtocol=usmAesCfb128Protocol)
    target=await UdpTransportTarget.create((ip,port),timeout=1,retries=0)
    columns={'name':'1.3.6.1.2.1.31.1.1.1.1','oper_status':'1.3.6.1.2.1.2.2.1.8','admin_status':'1.3.6.1.2.1.2.2.1.7',
        'speed_mbps':'1.3.6.1.2.1.31.1.1.1.15','rx_bytes':'1.3.6.1.2.1.31.1.1.1.6','tx_bytes':'1.3.6.1.2.1.31.1.1.1.10',
        'rx_errors':'1.3.6.1.2.1.2.2.1.14','tx_errors':'1.3.6.1.2.1.2.2.1.20','discontinuity':'1.3.6.1.2.1.31.1.1.1.19'}
    rows={}
    try:
        indication,status,index,bindings=await get_cmd(engine,auth,target,ContextData(),ObjectType(ObjectIdentity('1.3.6.1.2.1.1.3.0')),lookupMib=False)
        if indication or status:raise ValueError('SNMP failure')
        uptime=int(bindings[0][1])
        for field,oid in columns.items():
            async for indication,status,index,bindings in bulk_walk_cmd(engine,auth,target,ContextData(),0,25,ObjectType(ObjectIdentity(oid)),lexicographicMode=False,lookupMib=False,maxRows=512,maxCalls=24):
                if indication or status:raise ValueError('SNMP failure')
                for key,value in bindings:
                    key=str(key)
                    if not key.startswith(oid+'.'):continue
                    ident=int(key.split('.')[-1])
                    try:parsed=value.prettyPrint() if field=='name' else int(value)
                    except (ValueError,TypeError):parsed=None
                    rows.setdefault(ident,{'index':ident})[field]=parsed
        return list(rows.values()),uptime
    finally:engine.close_dispatcher()

def calculate_rates(rows,old,elapsed,uptime,old_uptime):
    previous={r['index']:r for r in old}
    for row in rows:
        row['rx_bps']=row['tx_bps']=None
        before=previous.get(row['index'])
        if not before or not 0<elapsed<=120 or uptime<old_uptime or row.get('discontinuity') is None or row.get('discontinuity')!=before.get('discontinuity'):continue
        if row.get('oper_status')!=1:continue
        for field,target in (('rx_bytes','rx_bps'),('tx_bytes','tx_bps')):
            a,b=row.get(field),before.get(field)
            if a is not None and b is not None and a>=b:row[target]=round((a-b)*8/elapsed,1)
    return rows

@coalesced_sample
def interfaces(device):
    from ..security import address
    address(device.ip_address)
    with device_lock(device.id):
        sampled=now();rows=[];transport='SSH'
        if device.snmp_credential:
            transport='SNMPv3'
            values=decrypt(device.snmp_credential.encrypted_secret)
            try:
                rows,uptime=asyncio.run(asyncio.wait_for(snmp_snapshot(device.ip_address,device.snmp_port,device.snmp_credential.username,values),timeout=25))
            except Exception:fail('SNMPv3 collection failed. Check reachability, username, SHA-256/AES keys and device support.','SNMP_ERROR',502)
            identity=(device.ip_address,device.snmp_port,device.snmp_credential_id)
            counter_key=(current_app.config['DATA_DIR'],device.id)
            tick=time.monotonic()
            with SAMPLE_LOCK:old=COUNTERS.get(counter_key,([],tick,uptime,identity))
            if old[3]!=identity:old=([],tick,uptime,identity)
            rows=calculate_rates(rows,old[0],tick-old[1],uptime,old[2])
            with SAMPLE_LOCK:
                if len(COUNTERS)>=512:COUNTERS.pop(next(iter(COUNTERS)))
                COUNTERS[counter_key]=(rows,tick,uptime,identity)
            for row in rows:row['status']='online' if row.get('oper_status')==1 else 'offline' if row.get('oper_status')==2 else 'unknown'
        elif device.platform=='cisco':
            with connection(device) as client:
                parsed=client.send_command('show interfaces',use_textfsm=True)
                if not isinstance(parsed,list):fail('Interface parser is unavailable for this device output. Configure SNMPv3.','UNSUPPORTED',502)
                for i,r in enumerate(parsed):
                    rate=lambda field: number(r.get(field))
                    state=r.get('link_status','').lower()
                    rows.append(dict(index=i+1,name=r.get('interface',''),status='online' if state=='up' and r.get('protocol_status','').lower()=='up' else 'offline',speed_mbps=speed_mbps(r.get('speed')),
                        rx_bps=rate('input_rate'),tx_bps=rate('output_rate'),rx_errors=rate('input_errors'),tx_errors=rate('output_errors')))
        else:
            with connection(device) as client:
                # The CLI prints read-only interface state; counters/rates require SNMPv3.
                output=command(client,':foreach i in=[/interface find] do={ :put ([/interface get $i name]."|".[/interface get $i running]."|".[/interface get $i disabled]) }')
                for line in output.splitlines():
                    parts=line.strip().split('|')
                    if len(parts)==3 and parts[1] in ('true','false'):
                        rows.append(dict(index=len(rows)+1,name=parts[0],status='online' if parts[1]=='true' else 'offline',rx_bps=None,tx_bps=None,speed_mbps=None,rx_errors=None,tx_errors=None))
                if not rows:fail('Interface output is unsupported. Configure SNMPv3 for IF-MIB collection.','UNSUPPORTED',502)
        return dict(items=rows,sampled_at=sampled,transport=transport,rate_note='Rates require two consecutive valid counter samples.' if transport=='SNMPv3' else 'SSH rates, when present, are the device-reported averaging window.')
