(() => {
  const N = window.Narsika;
  const {$, $$, h, icon, toast, modal, error} = N;
  const md = window.NarsikaMarkdown;
  const state = {config: null, conversations: [], active: null, busy: false};
  const TOOL_LABELS = {
    list_devices: 'inventory', health_overview: 'health overview', device_health: 'device health',
    recent_audit: 'audit log', operation_runs: 'operation runs', backup_status: 'backups', schedules: 'schedules'
  };
  const thread = $('#ai-thread');
  const input = $('#ai-input');

  const relative = value => {
    const seconds = (Date.now() - new Date(value).getTime()) / 1000;
    if (!Number.isFinite(seconds)) return '';
    if (seconds < 60) return 'just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' min ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' h ago';
    return N.stamp(value);
  };

  function drawModel() {
    const c = state.config;
    const box = $('#ai-model');
    box.classList.toggle('ready', !!c?.ready);
    box.classList.toggle('off', !c?.ready);
    $('#ai-model-name').textContent = c ? c.model : 'Unavailable';
    $('#ai-model-state').textContent = !c ? 'Configuration unavailable'
      : c.ready ? `Connected via ${c.provider}${c.local ? ' · local' : ''}`
      : c.enabled ? 'API key missing' : 'Disabled';
    const banner = $('#ai-banner');
    if (c && !c.ready) {
      banner.hidden = false;
      banner.className = 'notice warning ai-banner';
      banner.textContent = N.allowed('admin')
        ? 'The assistant is not ready. Open Assistant settings to add the API key and enable it.'
        : 'The assistant is not enabled yet. Ask an administrator to configure it.';
    } else if (c && !c.local) {
      banner.hidden = false;
      banner.className = 'notice ai-banner';
      banner.textContent = `Questions and the records the assistant reads are sent to ${c.provider}. Secrets are redacted${c.mask_addresses ? ' and IP addresses are masked' : ''} before sending.`;
    } else {
      banner.hidden = true;
    }
    $('#ai-send').disabled = state.busy || !c?.ready;
    $$('.ai-prompt').forEach(b => { b.disabled = state.busy || !c?.ready; });
  }

  function drawConversations() {
    const box = $('#ai-conversations');
    if (!state.conversations.length) {
      box.innerHTML = '<p class="muted ai-side-empty">No conversations yet.</p>';
      return;
    }
    box.innerHTML = state.conversations.map(c => `<div class="ai-conv ${state.active?.id === c.id ? 'active' : ''}"><button class="ai-conv-open" data-open="${c.id}" dir="auto"><span>${h(c.title)}</span><small>${h(relative(c.updated_at))}</small></button><button class="ai-conv-archive" data-archive="${c.id}" aria-label="Archive conversation" title="Archive">${icon('trash')}</button></div>`).join('');
    $$('[data-open]', box).forEach(b => { b.onclick = () => open(+b.dataset.open); });
    $$('[data-archive]', box).forEach(b => { b.onclick = () => archive(+b.dataset.archive); });
  }

  function messageHtml(m) {
    if (m.role === 'user') {
      return `<div class="ai-msg user"><div class="ai-bubble" dir="auto">${h(m.text)}</div></div>`;
    }
    const tools = (m.tools || []).map(t => `<span class="ai-tool ${t.ok ? '' : 'failed'}" title="Read-only tool">${h(TOOL_LABELS[t.tool] || t.tool)}${t.arguments?.device_id ? ' #' + h(t.arguments.device_id) : ''}</span>`).join('');
    const timing = m.elapsed_ms ? ` · ${(m.elapsed_ms / 1000).toFixed(1)} s` : '';
    return `<div class="ai-msg assistant"><span class="ai-orb" aria-hidden="true"></span><div class="ai-bubble" dir="auto">${md.render(m.text)}<div class="ai-meta">${tools}<span>${h(m.model || '')}${timing}</span><button class="ai-copy" data-copy="${m.id}">Copy</button></div></div></div>`;
  }

  function drawThread() {
    const messages = state.active?.messages || [];
    $('#ai-welcome').hidden = messages.length > 0 || state.busy;
    $$('.ai-msg, .ai-pending', thread).forEach(e => e.remove());
    thread.insertAdjacentHTML('beforeend', messages.map(messageHtml).join(''));
    $$('[data-copy]', thread).forEach(b => {
      b.onclick = async () => {
        const item = messages.find(m => m.id === +b.dataset.copy);
        try {
          await navigator.clipboard.writeText(item?.text || '');
          toast('Answer copied.');
        } catch {
          toast('Copy is unavailable in this browser.');
        }
      };
    });
    thread.scrollTop = thread.scrollHeight;
  }

  function pending(question) {
    $('#ai-welcome').hidden = true;
    thread.insertAdjacentHTML('beforeend', `<div class="ai-pending">${messageHtml({role: 'user', text: question})}<div class="ai-msg assistant"><span class="ai-orb thinking" aria-hidden="true"></span><div class="ai-thinking">Reading Narsika records<span class="ai-dots"><span></span><span></span><span></span></span></div></div></div>`);
    thread.scrollTop = thread.scrollHeight;
  }

  async function loadConversations() {
    const {data} = await N.apiFetch('/api/assistant/conversations');
    state.conversations = data.items;
    drawConversations();
  }

  async function open(id) {
    if (state.busy) return;
    try {
      const {data} = await N.apiFetch('/api/assistant/conversations/' + id);
      state.active = data;
      history.replaceState(null, '', '#c' + id);
      drawConversations();
      drawThread();
    } catch (ex) {
      toast(ex.message);
    }
  }

  function reset() {
    if (state.busy) return;
    state.active = null;
    history.replaceState(null, '', location.pathname);
    drawConversations();
    drawThread();
    input.focus();
  }

  function archive(id) {
    N.confirm('Archive conversation', 'The conversation will be removed from your list.', async () => {
      await N.apiFetch('/api/assistant/conversations/' + id, {method: 'DELETE'});
      if (state.active?.id === id) reset();
      await loadConversations();
    });
  }

  async function send(question) {
    question = question.trim();
    if (!question || state.busy || !state.config?.ready) return;
    state.busy = true;
    drawModel();
    input.value = '';
    grow();
    pending(question);
    try {
      const {data} = await N.apiFetch('/api/assistant/ask', {method: 'POST', timeout: 180000,
        body: {message: question, ...(state.active ? {conversation_id: state.active.id} : {})}});
      const messages = state.active?.messages || [];
      messages.push({id: -Date.now(), role: 'user', text: question}, data.message);
      state.active = {...data.conversation, messages};
      history.replaceState(null, '', '#c' + data.conversation.id);
      await loadConversations().catch(() => {});
    } catch (ex) {
      input.value = question;
      grow();
      toast(ex.message);
    } finally {
      state.busy = false;
      drawModel();
      drawThread();
      input.focus();
    }
  }

  function grow() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }

  function settings() {
    if (!N.guard('admin')) return;
    const c = state.config || {};
    modal('Assistant settings', `<form id="ai-settings-form" class="ai-settings">
      <p class="notice ai-risk">Ox Alpha is a free preview model from an undisclosed vendor. Requests may be logged by the provider. Enable address masking, and do not use it with production data you cannot share.</p>
      <label class="check-line"><input type="checkbox" name="enabled" ${c.enabled ? 'checked' : ''}> Enable the assistant for all users</label>
      <label>API base URL (OpenAI-compatible)<input name="base_url" value="${h(c.base_url || '')}" required maxlength="300" spellcheck="false"></label>
      <label>Model<input name="model" value="${h(c.model || '')}" required maxlength="120" spellcheck="false"></label>
      <label><span>API key <span class="ai-key-state ${c.has_api_key ? 'set' : ''}">${c.has_api_key ? '· stored encrypted' : '· not set'}</span></span><input name="api_key" type="password" autocomplete="off" placeholder="${c.has_api_key ? 'Leave empty to keep the stored key' : 'sk-or-…'}" maxlength="400"></label>
      ${c.has_api_key ? '<label class="check-line"><input type="checkbox" name="clear_api_key"> Remove the stored key</label>' : ''}
      <label class="check-line"><input type="checkbox" name="mask_addresses" ${c.mask_addresses ? 'checked' : ''}> Mask IPv4 addresses before sending (answers show the real addresses)</label>
      <p class="field-note">Passwords, secrets, SNMP communities, keys and hashes are always redacted. The assistant has read-only tools only; it cannot reach devices. Plain http:// is accepted only for a local model server such as Ollama.</p>
      <div class="dialog-actions"><button class="btn" type="button" id="ai-test">Test connection</button><button class="btn primary" type="submit">Save settings</button></div>
    </form>`, el => {
      const form = $('#ai-settings-form', el);
      const values = () => {
        const f = form.elements;
        const body = {enabled: f.enabled.checked, base_url: f.base_url.value.trim(), model: f.model.value.trim(), mask_addresses: f.mask_addresses.checked};
        if (f.api_key.value) body.api_key = f.api_key.value;
        if (f.clear_api_key?.checked) body.clear_api_key = true;
        return body;
      };
      const save = async () => {
        const {data} = await N.apiFetch('/api/assistant/config', {method: 'PUT', body: values()});
        state.config = data;
        form.elements.api_key.value = '';
        drawModel();
      };
      form.onsubmit = async e => {
        e.preventDefault();
        try {
          await save();
          el.close();
          toast('Assistant settings saved.');
        } catch (ex) {
          error(form, ex.message);
        }
      };
      $('#ai-test', el).onclick = async ev => {
        const button = ev.currentTarget;
        button.disabled = true;
        button.textContent = 'Testing…';
        try {
          await save();
          const {data} = await N.apiFetch('/api/assistant/test', {method: 'POST', body: {}, timeout: 120000});
          toast(`Connected to ${data.model} in ${(data.elapsed_ms / 1000).toFixed(1)} s.`);
        } catch (ex) {
          error(form, ex.message);
        } finally {
          button.disabled = false;
          button.textContent = 'Test connection';
        }
      };
    });
  }

  $('#ai-form').onsubmit = e => {
    e.preventDefault();
    send(input.value);
  };
  input.addEventListener('input', grow);
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send(input.value);
    }
  });
  $$('.ai-prompt').forEach(b => { b.onclick = () => send(b.dataset.prompt); });
  $('#ai-new').onclick = reset;
  $('#ai-settings').onclick = settings;

  (async () => {
    try {
      state.config = (await N.apiFetch('/api/assistant/config')).data;
    } catch (ex) {
      toast(ex.message);
    }
    drawModel();
    try {
      await loadConversations();
    } catch (ex) {
      toast(ex.message);
    }
    const match = location.hash.match(/^#c(\d+)$/);
    if (match) await open(+match[1]);
  })();
})();
