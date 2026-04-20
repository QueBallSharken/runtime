const state = {
  health: null,
  turn: null,
  proposals: null,
  maintenance: null,
  tools: null,
  errors: []
};

const $ = (id) => document.getElementById(id);

function raw(value) {
  return JSON.stringify(value, null, 2);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch (error) {
    payload = { parse_error: String(error), raw: text };
  }
  if (!response.ok) {
    const error = { status: response.status, payload };
    state.errors.push(error);
    renderError(error);
    throw error;
  }
  return payload;
}

function renderError(error) {
  const output = $('chat-output');
  output.classList.add('error');
  output.textContent = raw(error);
}

function setText(id, value) {
  $(id).textContent = value === undefined || value === null || value === '' ? 'none' : String(value);
}

function renderHealth(data) {
  state.health = data;
  setText('health-runtime', `${data.runtime || 'unknown'} ${data.runtime_version || ''}`.trim());
  setText('health-spec', data.spec_version);
  setText('health-db', data.database_path);
  setText('health-model', `${data.model_base_url || 'unknown'} (${data.model || 'unknown'})`);
  setText('health-mode', data.governance_mode);
  setText('health-pending', data.pending_proposals);
  setText('health-event', data.latest_event_index);
}

function artifactBlock(title, artifact) {
  const div = document.createElement('div');
  div.className = 'artifact';
  const h = document.createElement('strong');
  h.textContent = title;
  div.appendChild(h);
  const pre = document.createElement('pre');
  pre.textContent = raw(artifact);
  div.appendChild(pre);
  return div;
}

function renderTurn(data) {
  state.turn = data;
  state.tools = data.tool_results || [];
  $('chat-output').classList.remove('error');
  $('chat-output').textContent = data.turn?.reply || 'No reply.';
  setText('turn-status', data.status);
  setText('binding-status', data.interpretation?.status);
  setText('claim-status', data.final_claim?.classification);
  setText('user-event-index', data.turn?.user_event?.index);
  setText('model-event-index', data.turn?.model_event?.index);
  setText('claim-event-index', data.turn?.claim_event?.index);

  $('raw-turn').textContent = raw(data.turn);
  $('raw-interpretation').textContent = raw(data.interpretation);
  $('raw-claim').textContent = raw(data.final_claim);
  $('raw-tools').textContent = raw(data.tool_results || []);

  const interp = $('interpretation-summary');
  interp.textContent = '';
  interp.appendChild(artifactBlock('interpretation artifact', data.interpretation || {}));

  const claim = $('claim-summary');
  claim.textContent = '';
  claim.appendChild(artifactBlock('final claim', data.final_claim || {}));

  const tools = $('tool-results');
  tools.textContent = '';
  if (!data.tool_results || data.tool_results.length === 0) {
    tools.textContent = 'No tool results.';
  } else {
    data.tool_results.forEach((result, index) => tools.appendChild(artifactBlock(`tool result ${index + 1}`, result)));
  }

  renderRawAll();
}

function renderProposals(data) {
  state.proposals = data;
  const list = $('proposal-list');
  list.textContent = '';
  const proposals = data.proposals || [];
  if (proposals.length === 0) {
    list.textContent = 'No proposals.';
  }
  proposals.forEach((proposal) => {
    const div = document.createElement('div');
    div.className = 'artifact';
    div.appendChild(line('proposal_id', proposal.proposal_id));
    div.appendChild(line('event_index', proposal.event_index));
    div.appendChild(line('status', proposal.status));
    div.appendChild(line('content', proposal.content));
    div.appendChild(line('reason', proposal.reason));
    if (proposal.metadata?.salience) {
      div.appendChild(line('salience', raw(proposal.metadata.salience)));
    }
    if (proposal.status === 'pending') {
      const approve = document.createElement('button');
      approve.textContent = 'approve';
      approve.addEventListener('click', () => decideProposal(proposal.proposal_id, 'approve'));
      const reject = document.createElement('button');
      reject.textContent = 'reject';
      reject.className = 'danger';
      reject.addEventListener('click', () => decideProposal(proposal.proposal_id, 'reject'));
      div.appendChild(approve);
      div.appendChild(reject);
    }
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'view raw JSON';
    const pre = document.createElement('pre');
    pre.textContent = raw(proposal);
    details.appendChild(summary);
    details.appendChild(pre);
    div.appendChild(details);
    list.appendChild(div);
  });
  $('raw-proposals').textContent = raw(data);
  renderRawAll();
}

function line(label, value) {
  const p = document.createElement('p');
  const code = document.createElement('code');
  code.textContent = value === undefined || value === null ? 'none' : String(value);
  p.textContent = `${label}: `;
  p.appendChild(code);
  return p;
}

function renderMaintenance(pulseData, reportData) {
  state.maintenance = { pulse: pulseData, report: reportData };
  const pulse = $('pulse-summary');
  pulse.textContent = '';
  pulse.appendChild(artifactBlock('pulse', pulseData.pulse || {}));

  const findings = $('finding-list');
  findings.textContent = '';
  const list = reportData.report?.findings || [];
  if (list.length === 0) {
    findings.textContent = 'No findings.';
  } else {
    list.forEach((finding, index) => findings.appendChild(artifactBlock(`finding ${index + 1}`, finding)));
  }
  $('raw-maintenance').textContent = raw(state.maintenance);
  renderRawAll();
}

function renderRawAll() {
  $('raw-all').textContent = raw(state);
}

async function refreshHealth() {
  renderHealth(await requestJson('/health'));
}

async function refreshProposals() {
  renderProposals(await requestJson('/memory/proposals'));
}

async function refreshMaintenance() {
  const pulse = await requestJson('/maintenance/pulse');
  const report = await requestJson('/maintenance/report');
  renderMaintenance(pulse, report);
}

async function refreshAll() {
  await refreshHealth();
  await refreshProposals();
  await refreshMaintenance();
}

async function sendChat(event) {
  event.preventDefault();
  const message = $('chat-input').value;
  const data = await requestJson('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  });
  renderTurn(data);
  await refreshAll();
}

async function decideProposal(proposalId, action) {
  await requestJson(`/memory/proposals/${proposalId}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decided_by: 'hud', reason: `hud ${action}` })
  });
  await refreshProposals();
  await refreshHealth();
  await refreshMaintenance();
}

$('chat-form').addEventListener('submit', sendChat);
refreshAll().catch(renderError);
