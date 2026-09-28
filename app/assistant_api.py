"""AI assistant endpoints: configuration (admin) and private conversations (any role)."""
import time

from flask import Blueprint
from flask_login import current_user

from .api import boolean, get, ok
from .models import db, AssistantConversation, AssistantMessage, now
from .security import APIError, audit, decrypt, encrypt, fail, integer, payload, require
from .services import assistant as A

assistant_api = Blueprint('assistant_api', __name__)
MAX_QUESTION = 4000
MAX_CONVERSATIONS = 200


def owned(ident: int) -> AssistantConversation:
    row = get(AssistantConversation, ident)
    # Conversations are private; another user's record is reported as missing.
    if row.owner_id != current_user.id:
        fail('Record not found.', 'NOT_FOUND', 404)
    return row


def message_public(row: AssistantMessage) -> dict:
    content = decrypt(row.encrypted_content)
    if not isinstance(content, dict):
        content = {'text': ''}
    return {'id': row.id, 'role': row.role, 'text': content.get('text', ''), 'tools': content.get('tools', []),
            'model': row.model or None, 'elapsed_ms': row.elapsed_ms, 'created_at': row.created_at}


@assistant_api.get('/config')
@require()
def read_config():
    return ok(A.public_config())


@assistant_api.put('/config')
@require('admin')
def write_config():
    data = payload({'enabled', 'base_url', 'model', 'mask_addresses', 'api_key', 'clear_api_key'})
    values = {}
    if 'enabled' in data:
        values['enabled'] = boolean(data['enabled'], 'enabled')
    if 'mask_addresses' in data:
        values['mask_addresses'] = boolean(data['mask_addresses'], 'mask_addresses')
    if 'base_url' in data:
        values['base_url'] = A.validate_base_url(data['base_url'])
    if 'model' in data:
        values['model'] = A.validate_model(data['model'])
    new_key = data.get('api_key')
    if new_key is not None:
        if not isinstance(new_key, str) or len(new_key) > 400 or any(ord(c) < 33 for c in new_key):
            fail('Enter a valid API key.')
        new_key = new_key or None
    clear_key = boolean(data.get('clear_api_key', False), 'clear_api_key')
    A.save_config(values, new_key, clear_key)
    changes = sorted(values) + (['api_key'] if new_key or clear_key else [])
    # Record which fields changed, never the key itself.
    audit('AI assistant settings updated', 'Assistant', detail=', '.join(changes))
    db.session.commit()
    return ok(A.public_config())


@assistant_api.post('/test')
@require('admin')
def test_connection():
    payload(set())
    started = time.monotonic()
    try:
        result = A.ask([], 'Reply with the single word: ready', completion=_no_tools_completion)
    except A.AssistantError as ex:
        audit('AI assistant connection tested', 'Assistant', 'failed', ex.code)
        db.session.commit()
        raise APIError(ex.message, ex.code, ex.status) from None
    audit('AI assistant connection tested', 'Assistant')
    db.session.commit()
    return ok({'model': result['model'], 'elapsed_ms': int((time.monotonic() - started) * 1000),
               'reply': result['answer'][:200]})


def _no_tools_completion(values, key, messages, tools):
    return A.chat_completion(values, key, messages, None)


@assistant_api.get('/conversations')
@require()
def conversations():
    rows = (AssistantConversation.query.filter_by(owner_id=current_user.id, archived_at=None)
            .order_by(AssistantConversation.updated_at.desc()).limit(MAX_CONVERSATIONS).all())
    return ok({'items': [row.public() for row in rows]})


@assistant_api.get('/conversations/<int:ident>')
@require()
def conversation(ident):
    row = owned(ident)
    messages = AssistantMessage.query.filter_by(conversation_id=row.id).order_by(AssistantMessage.id).all()
    return ok({**row.public(), 'messages': [message_public(m) for m in messages]})


@assistant_api.delete('/conversations/<int:ident>')
@require()
def archive_conversation(ident):
    row = owned(ident)
    row.archived_at = now()
    db.session.commit()
    return ok(row.public())


@assistant_api.post('/ask')
@require()
def ask():
    data = payload({'conversation_id', 'message'}, ('message',))
    question = data['message']
    if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION:
        fail(f'Write a question of up to {MAX_QUESTION} characters.')
    question = question.strip()
    if data.get('conversation_id') is not None:
        row = owned(integer(data['conversation_id'], 'conversation', 1, 2**63 - 1))
        history = [message_public(m) for m in
                   AssistantMessage.query.filter_by(conversation_id=row.id).order_by(AssistantMessage.id).all()]
    else:
        row = None
        history = []
    try:
        result = A.ask([{'role': m['role'], 'content': m['text']} for m in history], question)
    except A.AssistantError as ex:
        audit('AI assistant question', str(row.id) if row else '', 'failed', ex.code)
        db.session.commit()
        raise APIError(ex.message, ex.code, ex.status) from None
    if row is None:
        title = ' '.join(question.split())[:80]
        row = AssistantConversation(owner_id=current_user.id, title=title)
        db.session.add(row)
        db.session.flush()
    stamp = now()
    row.updated_at = stamp
    db.session.add(AssistantMessage(conversation_id=row.id, role='user', created_at=stamp,
                                    encrypted_content=encrypt({'text': question})))
    answer = AssistantMessage(conversation_id=row.id, role='assistant', model=result['model'][:120],
                              elapsed_ms=result['elapsed_ms'], created_at=stamp,
                              encrypted_content=encrypt({'text': result['answer'], 'tools': result['tools']}))
    db.session.add(answer)
    db.session.flush()
    # Metadata only: the question and answer stay in the encrypted conversation.
    tools = ', '.join(sorted({t['tool'] for t in result['tools']})) or 'none'
    audit('AI assistant question', str(row.id), detail=f"model={result['model']}; tools={tools}")
    db.session.commit()
    return ok({'conversation': row.public(), 'message': message_public(answer)})
