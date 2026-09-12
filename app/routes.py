"""Server-rendered pages and compatibility endpoints."""
import hashlib
import time
from functools import wraps
from flask import Blueprint, render_template, request, redirect, session, jsonify
from flask_login import current_user, login_user, logout_user
from sqlalchemy import text as sql
from werkzeug.security import generate_password_hash, check_password_hash
from .models import db, User, LoginAttempt, Credential, Device, Playbook
from .security import csrf_token, password_policy, audit, fail, require, APIError

web=Blueprint('web',__name__)
PAGES={'inventory':'Device inventory','discovery':'Discovery','health':'Device health','interfaces':'Interfaces','playbooks':'Playbooks','vlan':'VLAN management','acl':'Access lists','backups':'Backups','audit':'Audit log','settings':'Settings'}
DUMMY_PASSWORD_HASH=generate_password_hash('unused-password-verification',method='scrypt')

def signed_in(fn):
    @wraps(fn)
    def wrapped(*args,**kwargs):
        if not current_user.is_authenticated:return redirect('/login')
        return fn(*args,**kwargs)
    return wrapped

def render_page(page):
    from .api import bootstrap
    return render_template('pages/'+page+'.html',page_id=page,page_title=PAGES.get(page,page.replace('-',' ').title()),bootstrap=bootstrap())

@web.route('/login',methods=['GET','POST'])
@web.route('/login.html',methods=['GET','POST'])
def login():
    if request.method=='GET':
        if current_user.is_authenticated:return redirect('/')
        return render_page('login')
    data=request.get_json(silent=True) if request.is_json else request.form
    if not isinstance(data,dict) and not hasattr(data,'get'):fail('Invalid sign-in request.',status=400)
    username=data.get('username','');password=data.get('password','')
    if not isinstance(username,str) or not isinstance(password,str) or len(username)>64 or len(password)>256:fail('Invalid username or password.','INVALID_CREDENTIALS',401)
    # Reserve attempts atomically before expensive password verification.
    keys=[hashlib.sha256(('ip:'+str(request.remote_addr)).encode()).hexdigest(),hashlib.sha256(('user:'+username.lower()).encode()).hexdigest()]
    moment=time.time()
    db.session.execute(sql('DELETE FROM login_attempt WHERE reset_at <= :now'),{'now':moment})
    reservations=[]
    for key,limit in zip(keys,(50,10)):
        count,reset=db.session.execute(sql('INSERT INTO login_attempt (key,count,reset_at) VALUES (:key,1,:reset) ON CONFLICT(key) DO UPDATE SET count=count+1 RETURNING count,reset_at'),{'key':key,'reset':moment+900}).one()
        reservations.append((key,reset))
        if count>limit:
            db.session.rollback()
            fail('Too many sign-in attempts. Retry after the 15-minute window.','RATE_LIMITED',429)
    db.session.commit()
    user=User.query.filter_by(username=username).first()
    valid=user.check_password(password) if user else check_password_hash(DUMMY_PASSWORD_HASH,password) and False
    if not valid or not user.is_active:
        audit('Sign in','','failed','Invalid credentials.');db.session.commit()
        fail('Invalid username or password.','INVALID_CREDENTIALS',401)
    # Successful logins do not consume the IP failure budget; reset account failures.
    for index,(key,reset) in enumerate(reservations):
        db.session.execute(sql('UPDATE login_attempt SET count='+('0' if index else 'MAX(0,count-1)')+' WHERE key=:key AND reset_at=:reset'),{'key':key,'reset':reset})
    session.clear();login_user(user);session['version']=user.session_version;session.permanent=True;csrf_token()
    audit('Sign in');db.session.commit()
    url='/change-password' if user.must_change_password else '/'
    return jsonify(data={'redirect':url},meta={}) if request.is_json else redirect(url)

@web.route('/logout',methods=['GET','POST'])
@signed_in
def logout():
    if request.method=='GET':return render_template('logout.html',page_id='logout',page_title='Sign out',bootstrap={'user':current_user.public()})
    audit('Sign out');db.session.commit();logout_user();session.clear()
    return redirect('/login')

@web.route('/change-password',methods=['GET','POST'])
@web.route('/change-password.html',methods=['GET','POST'])
@signed_in
def password():
    if request.method=='GET':return render_page('change-password')
    data=request.get_json(silent=True) if request.is_json else request.form
    if not hasattr(data,'get'):fail('Send a password change form or JSON object.',status=400)
    current=data.get('current',data.get('current_password'))
    if not isinstance(current,str) or not current_user.check_password(current):fail('Current password is incorrect.','INVALID_CREDENTIALS',422)
    value=password_policy(data.get('password',data.get('new_password')))
    if value!=data.get('confirm',data.get('confirm_password')):fail('Password confirmation does not match.')
    if current_user.check_password(value):fail('Choose a password different from the current password.')
    current_user.set_password(value);current_user.must_change_password=False;session['version']=current_user.session_version
    audit('Password changed');db.session.commit()
    return jsonify(data={'redirect':'/'},meta={}) if request.is_json else redirect('/')

@web.get('/')
@web.get('/index.html')
@signed_in
def inventory():return render_page('inventory')

for slug in PAGES:
    if slug=='inventory':continue
    def view(slug=slug):return render_page(slug)
    view=signed_in(view)
    web.add_url_rule('/'+slug,endpoint=slug,view_func=view)
    web.add_url_rule('/'+slug+'.html',endpoint=slug,view_func=view)

@web.get('/health/<int:device_id>')
@signed_in
def health_detail(device_id):return redirect('/health.html?device='+str(device_id))

@web.get('/logs')
@signed_in
def logs():return render_page('audit')

@web.get('/playbook-runner')
@signed_in
def runner():return render_page('playbooks')

@web.post('/add-group')
@require('admin')
def add_group():
    from .api import create_group
    create_group({'name':request.form.get('name') or request.form.get('group_name')})
    return redirect('/')

@web.post('/add-device')
@require('admin')
def add_device():
    from .api import create_device, credential_values
    data=request.form
    credential=Credential(**credential_values({'name':'Device '+str(time.time_ns()),'username':data.get('username'),'kind':'ssh','password':data.get('password','')}))
    db.session.add(credential);db.session.flush()
    create_device({'name':data.get('name') or data.get('device_name'),'ip_address':data.get('ip_address') or data.get('ip'),'platform':'mikrotik' if 'mikrotik' in data.get('os_type','').lower() else 'cisco', 'credential_id':credential.id,'group_id':data.get('group_id') or None})
    return redirect('/')

@web.post('/upload-playbook')
@require('admin')
def upload_playbook():
    from .api import upload_playbook as upload
    upload();return redirect('/playbooks')

@web.post('/run-playbook')
@require('operate')
def run_playbook():
    from .api import create_run
    name=request.form.get('playbook') or request.form.get('playbook_name','')
    book=Playbook.query.filter_by(name=name,active=True).first() or Playbook.query.filter_by(name='Original/'+name,active=True).first()
    if not book:fail('Choose a registered playbook.','NOT_FOUND',404)
    return create_run({'kind':'playbook','device_id':request.form.get('device_id'),'playbook_id':book.id,'variables':{}})

@web.post('/vlan')
@require('operate')
def legacy_vlan():
    from .api import create_run
    return create_run({'kind':'vlan','device_id':request.form.get('device_id'),'parameters':{'vlan_id':request.form.get('vlan_id'),'vlan_name':request.form.get('vlan_name',''),'operation':{'present':'create','absent':'remove'}.get(request.form.get('action'),request.form.get('action','create'))}})

@web.post('/acl')
@require('operate')
def legacy_acl():
    from .api import create_run
    data=request.form
    return create_run({'kind':'acl','device_id':data.get('device_id'),'parameters':{'name':data.get('acl_name') or data.get('name'),'protocol':data.get('protocol','ip'),'action':data.get('action','permit'),'source':data.get('source') or data.get('src_ip','any'),'destination':data.get('destination') or data.get('dst_ip','any'),'port':data.get('port'),'chain':data.get('chain','forward')}})
