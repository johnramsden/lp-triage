// ─── State ───────────────────────────────────────────────────────────────────
let currentRunId = null;
let runMode = 'auto';
let reviewQueue = [];    // [{bug_id, title, result, comment_body, run_id}]
let bugTitles = {};      // bug_id -> title
let stats = { bugs: 0, posted: 0, errors: 0, tin: 0, tout: 0 };
let approvedBugs = new Set();
let _lpWebRoot = 'https://launchpad.net/';

// ─── Panel routing ────────────────────────────────────────────────────────────
function showPanel(name) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  document.querySelector(`[data-panel="${name}"]`).classList.add('active');
  if (name === 'config') loadConfig();
  if (name === 'review') renderReviewQueue();
}

// ─── Run ─────────────────────────────────────────────────────────────────────
function startRun() {
  runMode = document.getElementById('run-mode').value;
  const provider = document.getElementById('run-provider').value;
  const limit = document.getElementById('run-limit').value;
  const maxTurns = document.getElementById('run-max-turns').value;
  const dryRun = document.getElementById('run-dry-run').checked;
  const postComment = document.getElementById('run-post').checked;
  const allowRepost = document.getElementById('run-allow-repost').checked;

  const body = { provider, dry_run: dryRun, post_comment: postComment, allow_repost: allowRepost };
  if (limit) body.limit = parseInt(limit);
  if (maxTurns) body.max_turns = parseInt(maxTurns);

  stats = { bugs: 0, posted: 0, errors: 0, tin: 0, tout: 0 };
  document.getElementById('event-log').textContent = '';
  document.getElementById('summary-tbody').innerHTML = '';
  document.getElementById('summary-section').style.display = 'none';
  document.getElementById('stats-bar').style.display = 'flex';
  document.getElementById('btn-post-all').style.display = 'none';
  bugTitles = {};

  setRunStatus('<span class="spinner"></span> Running…');
  document.getElementById('btn-start-run').disabled = true;
  document.getElementById('btn-stop-run').style.display = 'inline-block';

  fetch('/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(r => r.json())
    .then(({ run_id }) => {
      currentRunId = run_id;
      localStorage.setItem('lp-triage-run-id', run_id);
      subscribeSSE(run_id);
    });
}

function subscribeSSE(runId) {
  const es = new EventSource(`/run/${runId}/stream`);
  es.onmessage = e => {
    const ev = JSON.parse(e.data);
    handleEvent(ev, runId);
    if (ev.t === 'run_done') {
      es.close();
      onRunDone(ev);
    } else if (ev.t === 'run_stopped') {
      es.close();
    }
  };
  es.onerror = () => { setRunStatus('Stream error'); es.close(); };
}

function handleEvent(ev, runId) {
  appendLog(ev);
  if (ev.t === 'bug_start') bugTitles[ev.bug_id] = ev.title;
  if (ev.t === 'classification') {
    updateStats('bugs');
    addSummaryRow(ev.bug_id, bugTitles[ev.bug_id] || '?', ev.result);
    if (runMode === 'review') {
      addToReviewQueue(ev.bug_id, bugTitles[ev.bug_id] || '?', ev.result, runId);
    }
  }
  if (ev.t === 'bug_error') updateStats('errors');
  if (ev.t === 'comment_posted') updateStats('posted');
  if (ev.t === 'token_usage') {
    stats.tin += ev.input; stats.tout += ev.output;
    document.getElementById('stat-in').textContent = stats.tin.toLocaleString();
    document.getElementById('stat-out').textContent = stats.tout.toLocaleString();
  }
}

function updateStats(field) {
  stats[field]++;
  document.getElementById('stat-' + field).textContent = stats[field];
}

function onRunDone(ev) {
  setRunStatus('Done');
  localStorage.removeItem('lp-triage-run-id');
  document.getElementById('btn-start-run').disabled = false;
  document.getElementById('btn-stop-run').style.display = 'none';
  document.getElementById('summary-section').style.display = 'block';
  if (ev.stats?.posts_skipped_cap) {
    document.getElementById('stat-skipped').textContent = ev.stats.posts_skipped_cap;
  }
  if (approvedBugs.size > 0) document.getElementById('btn-post-all').style.display = 'inline-block';
}

function stopRun() {
  if (!currentRunId) return;
  fetch(`/run/${currentRunId}/stop`, { method: 'POST' })
    .then(r => r.json())
    .then(() => {
      setRunStatus('Stopped');
      document.getElementById('btn-start-run').disabled = false;
      document.getElementById('btn-stop-run').style.display = 'none';
      if (document.getElementById('summary-tbody').rows.length > 0) {
        document.getElementById('summary-section').style.display = 'block';
      }
    });
}

function setRunStatus(html) {
  document.getElementById('run-status').innerHTML = html;
}

// ─── Event log ───────────────────────────────────────────────────────────────
function appendLog(ev) {
  const el = document.getElementById('event-log');
  const line = document.createElement('div');
  line.className = 'log-' + (ev.t || 'unknown');
  if (ev.t === 'classification') {
    line.textContent = `[${ev.t}] bug=${ev.bug_id} cat=${ev.result?.category} evidence=${(ev.result?.evidence||[]).length}`;
  } else {
    const { t, ts, ...rest } = ev;
    const summary = Object.entries(rest).map(([k,v]) => `${k}=${typeof v==='object'?JSON.stringify(v):v}`).join(' ');
    line.textContent = `[${t}] ${summary}`;
  }
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

// ─── Summary table ────────────────────────────────────────────────────────────
function addSummaryRow(bugId, title, result) {
  const tbody = document.getElementById('summary-tbody');
  const tr = document.createElement('tr');
  const cat = result.category || 'unknown';
  const repostBadge = result.already_posted
    ? ' <span class="badge" style="background:#3a2800;color:#ffc107">repost</span>'
    : '';
  tr.innerHTML = `
    <td><a href="${_lpWebRoot}bugs/${bugId}" target="_blank" style="color:var(--ubuntu-orange)">#${bugId}</a></td>
    <td>${escHtml(title)}</td>
    <td><span class="badge badge-${cat}">${cat}</span>${repostBadge}</td>
    <td>${escHtml(result.importance||'')}</td>
    <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escHtml(result.summary||'')}</td>
    <td>${escHtml(result.recommended_action||'')}</td>
  `;
  tbody.appendChild(tr);
}

// ─── Review queue ─────────────────────────────────────────────────────────────
function addToReviewQueue(bugId, title, result, runId) {
  const body = buildCommentBody(result, bugId);
  reviewQueue.push({ bug_id: bugId, title, result, comment_body: body, run_id: runId });
  if (document.getElementById('panel-review').classList.contains('active')) {
    renderReviewQueue();
  }
}

function _cleanText(text, projectUrl) {
  // Markdown links: [text](url) → text (url)
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1 ($2)');
  // Bare 40-char SHA not already in a URL path → full commit URL
  if (projectUrl) {
    const base = projectUrl.replace(/\/+$/, '');
    text = text.replace(/(?<![\/\w])([0-9a-f]{40})(?![\/\w])/g,
      (sha) => `${base}/commit/${sha}`);
  }
  return text;
}

function buildCommentBody(result, bugId) {
  const projectUrl = result._project_url || '';
  function clean(t) { return _cleanText(t || '', projectUrl); }
  const disclaimer = '[lp-triage AI report — informational only; a human must decide final actions]';
  let body = `${disclaimer}\n\n`;
  body += `Category: ${result.category||'unknown'}\n\n`;
  body += `Summary: ${clean(result.summary||'')}\n\n`;
  body += `Recommended action: ${clean(result.recommended_action||'')}\n\n`;
  if (result.potential_resolution_detail) body += `Potential resolution detail:\n${clean(result.potential_resolution_detail)}\n\n`;
  if (result.fix_reference) body += `Fix reference: ${clean(result.fix_reference)}\n\n`;
  if ((result.evidence||[]).length) {
    body += `Evidence:\n` + result.evidence.map(u => `- ${clean(u)}`).join('\n') + '\n';
  }
  return body.trim();
}

function _updateQueueMeta() {
  document.getElementById('queue-count').textContent = `${reviewQueue.length} item${reviewQueue.length===1?'':'s'}`;
  document.getElementById('review-empty').style.display = reviewQueue.length === 0 ? 'block' : 'none';
}

function renderReviewQueue() {
  const container = document.getElementById('review-queue');
  _updateQueueMeta();

  // Only append cards that don't already exist — never touch existing ones so
  // focus and in-progress edits are never disturbed.
  reviewQueue.forEach(item => {
    if (document.getElementById(`review-${item.bug_id}`)) return;
    const cat = item.result.category || 'unknown';
    const div = document.createElement('div');
    div.className = 'review-card';
    div.id = `review-${item.bug_id}`;
    const alreadyPostedNotice = item.result.already_posted
      ? `<div class="already-posted-notice">⚠ This bug already has an lp-triage comment — posting again will add a duplicate.</div>`
      : '';
    div.innerHTML = `
      <h3><a href="${_lpWebRoot}bugs/${item.bug_id}" target="_blank" style="color:var(--ubuntu-orange)">#${item.bug_id}</a> — ${escHtml(item.title)}</h3>
      <div class="meta"><span class="badge badge-${cat}">${cat}</span> &nbsp; ${escHtml(item.result.summary||'')}</div>
      ${alreadyPostedNotice}
      <textarea id="ta-${item.bug_id}">${escHtml(item.comment_body)}</textarea>
      <div class="review-actions">
        <button class="btn btn-primary" onclick="approveAndPost(${item.bug_id})">Approve &amp; post</button>
        <button class="btn btn-outline" onclick="skipReview(${item.bug_id})">Skip</button>
      </div>
    `;
    container.appendChild(div);
  });
}

function _removeCard(bugId) {
  const idx = reviewQueue.findIndex(i => i.bug_id === bugId);
  if (idx !== -1) reviewQueue.splice(idx, 1);
  document.getElementById(`review-${bugId}`)?.remove();
  _updateQueueMeta();
}

function approveAndPost(bugId) {
  const item = reviewQueue.find(i => i.bug_id === bugId);
  if (!item) return;
  const body = document.getElementById(`ta-${bugId}`).value;
  postComment(item.run_id, bugId, body);
}

function skipReview(bugId) {
  _removeCard(bugId);
}

function postComment(runId, bugId, body) {
  fetch(`/run/${runId}/bugs/${bugId}/post`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment_body: body, dry_run: document.getElementById('run-dry-run').checked }),
  }).then(r => {
    if (!r.ok) return r.json().then(d => Promise.reject(new Error(d.error || d.detail || 'Post failed')));
    return r.json();
  }).then(() => {
    _removeCard(bugId);
  }).catch(err => alert('Post failed: ' + err));
}

function postAll() {
  reviewQueue.slice().forEach(item => approveAndPost(item.bug_id));
}

// ─── Status / instance badge ──────────────────────────────────────────────────
const _lpWebRoots = {
  production: 'https://launchpad.net/',
  qastaging:  'https://qastaging.launchpad.net/',
  staging:    'https://staging.launchpad.net/',
};

function loadStatus() {
  fetch('/status').then(r => r.json()).then(({ lp_instance, lp_connected }) => {
    setInstanceBadge(lp_instance || 'production');
    setLPConnected(!!lp_connected);
  });
}

function setLPConnected(connected) {
  const status = document.getElementById('lp-status');
  const btn = document.getElementById('btn-lp-connect');
  if (connected) {
    status.textContent = 'Launchpad: connected';
    status.style.color = '#0e8420';
    btn.textContent = 'Reconnect';
  } else {
    status.textContent = 'Launchpad: not connected';
    status.style.color = '';
    btn.textContent = 'Connect Launchpad';
  }
  btn.style.display = '';
  btn.disabled = false;
}

function setInstanceBadge(instance) {
  _lpWebRoot = _lpWebRoots[instance] || `https://${instance}.launchpad.net/`;
  const el = document.getElementById('lp-instance-badge');
  el.textContent = instance;
  el.className = 'instance-badge ' + (instance === 'production' ? 'instance-badge-prod' : 'instance-badge-nonprod');
}

// ─── Config ───────────────────────────────────────────────────────────────────
let _projectsData = [];

function loadConfig() {
  fetch('/config').then(r => r.json()).then(({ user, project }) => {
    document.getElementById('cfg-or-key').value = user?.auth?.openrouter_api_key || '';
    document.getElementById('cfg-gemini-key').value = user?.auth?.gemini_api_key || '';
    document.getElementById('cfg-lp-instance').value = user?.defaults?.lp_instance || 'production';
    document.getElementById('cfg-provider').value = user?.defaults?.provider || 'openrouter';
    document.getElementById('cfg-or-model').value = user?.openrouter?.model || '';
    document.getElementById('cfg-gemini-model').value = user?.gemini?.model || '';
    _projectsData = project?.projects || [];
    // Show configured/not-configured status; inputs left blank = "no change"
    const orStatus = document.getElementById('cfg-or-key-status');
    const gmStatus = document.getElementById('cfg-gemini-key-status');
    if (orStatus) orStatus.textContent = user?.auth?.openrouter_api_key === '__unchanged__' ? 'configured' : 'not configured';
    if (gmStatus) gmStatus.textContent = user?.auth?.gemini_api_key === '__unchanged__' ? 'configured' : 'not configured';
    document.getElementById('cfg-or-key').value = '';
    document.getElementById('cfg-gemini-key').value = '';
    renderProjects();
  });
}

function renderProjects() {
  const container = document.getElementById('projects-list');
  container.innerHTML = '';
  _projectsData.forEach((p, i) => {
    const url = p.url || '';
    const linkBtn = url
      ? `<a href="${escHtml(url)}" target="_blank" rel="noopener" title="Open repository" style="color:var(--warm-grey);text-decoration:none;font-size:1rem;line-height:1;flex-shrink:0" onmouseover="this.style.color='white'" onmouseout="this.style.color='var(--warm-grey)'">↗</a>`
      : '';
    const div = document.createElement('div');
    div.className = 'card';
    div.style.marginBottom = '0.5rem';
    div.innerHTML = `
      <div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap">
        <input type="text" value="${escHtml(p.lp_project)}" placeholder="lp_project" style="flex:1;min-width:140px;background:#111;border:1px solid #444;color:white;border-radius:4px;padding:.3rem .5rem" oninput="_projectsData[${i}].lp_project=this.value" />
        <div style="display:flex;align-items:center;gap:0.4rem;flex:2;min-width:200px">
          <input type="url" value="${escHtml(url)}" placeholder="https://github.com/org/repo" style="flex:1;background:#111;border:1px solid #444;color:white;border-radius:4px;padding:.3rem .5rem" oninput="_projectsData[${i}].url=this.value;renderProjects()" />
          ${linkBtn}
        </div>
        <input type="text" value="${escHtml(p.branch)}" placeholder="branch" style="flex:1;min-width:100px;background:#111;border:1px solid #444;color:white;border-radius:4px;padding:.3rem .5rem" oninput="_projectsData[${i}].branch=this.value" />
        <input type="text" value="${escHtml(p.subdir)}" placeholder="subdir (optional)" style="flex:1;min-width:100px;background:#111;border:1px solid #444;color:white;border-radius:4px;padding:.3rem .5rem" oninput="_projectsData[${i}].subdir=this.value" />
        <button class="btn btn-outline" style="padding:.25rem .6rem;font-size:.8rem" onclick="removeProject(${i})">✕</button>
      </div>
    `;
    container.appendChild(div);
  });
}

function addProject() {
  _projectsData.push({ lp_project: '', url: '', branch: 'main', subdir: '' });
  renderProjects();
}

function removeProject(i) {
  _projectsData.splice(i, 1);
  renderProjects();
}

function saveConfig() {
  const btn = document.getElementById('btn-save-config');
  // Blank input = "no change" (send sentinel); non-blank = replace with new value
  const orKey = document.getElementById('cfg-or-key').value;
  const gmKey = document.getElementById('cfg-gemini-key').value;
  const user = {
    auth: {
      openrouter_api_key: orKey || '__unchanged__',
      gemini_api_key: gmKey || '__unchanged__',
    },
    defaults: {
      provider: document.getElementById('cfg-provider').value,
      lp_instance: document.getElementById('cfg-lp-instance').value,
    },
    openrouter: { model: document.getElementById('cfg-or-model').value },
    gemini: { model: document.getElementById('cfg-gemini-model').value },
  };
  const project = { projects: _projectsData };
  fetch('/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, project }),
  }).then(() => {
    btn.textContent = 'Saved!';
    setTimeout(() => { btn.textContent = 'Save'; }, 1500);
    setInstanceBadge(user.defaults.lp_instance);
  });
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─── Launchpad OAuth (OOB desktop flow) ──────────────────────────────────────
let _lpTokenKey = null;

async function startLPAuth() {
  const btn = document.getElementById('btn-lp-connect');
  btn.disabled = true;
  btn.textContent = 'Connecting…';
  try {
    const resp = await fetch('/auth/lp');
    if (!resp.ok) throw new Error(await resp.text());
    const { auth_url, token_key } = await resp.json();
    _lpTokenKey = token_key;
    window.open(auth_url, '_blank');
    document.getElementById('lp-pending').style.display = 'block';
    btn.style.display = 'none';
  } catch (e) {
    btn.disabled = false;
    btn.textContent = 'Connect Launchpad';
    alert('Failed to start LP auth: ' + e.message);
  }
}

async function completeLPAuth() {
  if (!_lpTokenKey) return;
  const completeBtn = document.querySelector('#lp-pending .btn-positive');
  completeBtn.disabled = true;
  completeBtn.textContent = 'Connecting…';
  try {
    const resp = await fetch('/auth/lp/complete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token_key: _lpTokenKey }),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || 'Unknown error');
    _lpTokenKey = null;
    document.getElementById('lp-pending').style.display = 'none';
    loadStatus();
  } catch (e) {
    completeBtn.disabled = false;
    completeBtn.textContent = 'Complete authorization';
    alert('LP authorization failed: ' + e.message);
  }
}

function cancelLPAuth() {
  _lpTokenKey = null;
  document.getElementById('lp-pending').style.display = 'none';
  const btn = document.getElementById('btn-lp-connect');
  btn.style.display = '';
  btn.disabled = false;
  btn.textContent = 'Connect Launchpad';
}

// ─── Reload recovery ─────────────────────────────────────────────────────────
// On load, check localStorage for an in-progress or completed run to recover
(function init() {
  loadStatus();
  const savedRunId = localStorage.getItem('lp-triage-run-id');
  if (!savedRunId) return;

  let runStatus = 'running';
  fetch(`/run/${savedRunId}/results`)
    .then(r => r.ok ? r.json() : null)
    .then(data => {
      if (!data) { localStorage.removeItem('lp-triage-run-id'); return null; }
      runStatus = data.status || 'running';
      return fetch(`/run/${savedRunId}/replay`).then(r => r.json());
    })
    .then(data => {
      if (!data) return;
      currentRunId = savedRunId;
      let runFinished = false;
      data.events.forEach(ev => {
        handleEvent(ev, savedRunId);
        if (ev.t === 'run_done' || ev.t === 'run_stopped') {
          onRunDone(ev);
          runFinished = true;
        }
      });
      // Use server status as fallback in case terminal event is absent from replay
      if (!runFinished && runStatus !== 'running') {
        onRunDone({});
        runFinished = true;
      }
      // If the run is still in progress, reattach to the SSE stream
      if (!runFinished) {
        document.getElementById('btn-stop-run').style.display = 'inline-block';
        document.getElementById('btn-start-run').disabled = true;
        subscribeSSE(savedRunId);
      }
    });
})();
