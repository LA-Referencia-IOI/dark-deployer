'use strict';

/* web-wizard canvas — the operator workbench.
 *
 * Machines are boxes; services are cards nested inside their logical groups.
 * Only edges that cross a host boundary are drawn.
 *
 * Two gestures:
 *   - drag a machine *header*   -> move the box (view state only)
 *   - drag a *group* onto another machine -> re-place it (a real edit)
 *
 * Every edit is a typed operation sent to the server, which validates it with
 * the deployer's resolver before committing. A rejected change leaves the draft
 * untouched and reports why. Nothing is ever written to the inventory file.
 */

const FAMILY_COLOR = {
  blockchain: '#2f5d8c',
  storage: '#8c6b2f',
  applications: '#3b6b57',
  web: '#6b4e7a',
  other: '#6b6e66',
};

const FAMILY_LABEL = {
  blockchain: 'blockchain',
  storage: 'storage',
  applications: 'applications',
  web: 'web edge',
  other: 'other',
};

const FAMILY_ORDER = ['blockchain', 'storage', 'applications', 'web', 'other'];
const ROUTING_LABEL = {
  blockchain_p2p: 'chain P2P',
  storage_p2p: 'storage P2P',
  storage_api: 'storage API',
  application_api: 'application API',
};
const ROUTING_ROLES = ['blockchain_p2p', 'storage_p2p', 'storage_api', 'application_api'];
const PROFILES = ['local', 'lab', 'production'];

/* Networks are structure, not decoration: a low-saturation tint per network,
 * deliberately quieter than the service-family hues so the two systems never
 * compete. */
const NETWORK_TINT = ['#5b7c99', '#4f8a7b', '#8a7a4f', '#7c6f96', '#6f8a8a', '#8a5f6b'];

const SVG_NS = 'http://www.w3.org/2000/svg';
const STORE_KEY = 'webwizard.layout.v2';
const OLD_STORE_KEY = 'webwizard.layout.v1';

// Geometry (px).
const PAD = 44;
const MGAP = 104;
const MW = 336;
const MIP = 14;
const MHEAD = 58;
const GGAP = 14;
const GHEAD = 22;
const GPADY = 10;
const GIP = 12;
const CGAP = 8;
const CH = 50;
const ROWGAP = 64;
const BAND_HEAD = 30;
const BAND_GAP = 36;
const groupW = MW - 2 * MIP;
const nodeW = groupW - 2 * GIP;

// The fabric layer drawn above the machines.
const FBAND_H = 26;
const FBAND_GAP = 15;
const FABRIC_GAP = 38;
const ROUTE_GUTTER = 190;
const ADDRESS_ROW = 22;

const $ = (sel) => document.querySelector(sel);

/* -- helpers --------------------------------------------------------- */

function svgEl(tag, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  if (parent) parent.appendChild(node);
  return node;
}

function svgText(parent, x, y, text, cls, extra = {}) {
  const node = svgEl('text', { x, y, class: cls, ...extra }, parent);
  node.textContent = text;
  return node;
}

function truncate(text, maxChars) {
  if (!text) return '';
  return text.length > maxChars ? text.slice(0, maxChars - 1) + '…' : text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  let body = {};
  try { body = await response.json(); } catch (_) { /* ignore */ }
  if (!response.ok) throw new Error(body.detail || body.error || `HTTP ${response.status}`);
  return body;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body || {}),
  });
  let payload = {};
  try { payload = await response.json(); } catch (_) { /* ignore */ }
  if (!response.ok) throw new Error(payload.detail || payload.error || `HTTP ${response.status}`);
  return payload;
}

/* -- remembered layout (view state only) ----------------------------- */

let memoryStore = {};
const readStore = () => {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || localStorage.getItem(OLD_STORE_KEY)) || {};
  } catch (_) { return memoryStore; }
};
const writeStore = (data) => {
  memoryStore = data;
  try { localStorage.setItem(STORE_KEY, JSON.stringify(data)); } catch (_) { /* ignore */ }
};

/* Per inventory: which view is showing, and a layout per view (the two box
 * models have different geometry, so their arrangements are kept apart). */
function readView(path) {
  const entry = (path && readStore()[path]) || {};
  if (entry.view || entry.services || entry.fabric) return entry;
  if (Object.keys(entry).length) return { view: 'services', services: entry };  // old shape
  return { view: 'services' };
}

function writeView(path, next) {
  if (!path) return;
  const data = readStore();
  data[path] = next;
  writeStore(data);
}

/* The canvas is the primary surface. Keeping every long form open made the
 * rail hard to scan and pushed useful controls far below the fold. */
function wireCollapsiblePanels() {
  for (const panel of document.querySelectorAll('.panel.collapsible')) {
    const heading = panel.querySelector('h2');
    if (!heading || heading.querySelector('button')) continue;
    const title = heading.textContent.trim();
    const button = el('button', 'panel-toggle', title);
    button.type = 'button';
    button.setAttribute('aria-expanded', 'false');
    heading.textContent = '';
    heading.appendChild(button);
    panel.classList.add('collapsed');
    button.addEventListener('click', () => {
      const collapsed = panel.classList.toggle('collapsed');
      button.setAttribute('aria-expanded', String(!collapsed));
    });
  }
}

const layoutOf = (view, name) => (view[name] || { mode: 'columns', positions: {} });

/* -- module state ---------------------------------------------------- */

let currentPath = null;
let currentGraph = null;
let currentLayout = null;
let currentEntries = [];
let machineGroups = new Map();
let selected = null;         // {kind: 'machine'|'group', id}
let suppressClick = false;   // set when a drag ends, so it does not also select
let currentView = 'services';  // 'services' | 'fabric'
let currentZoom = 1;
let filterQuery = '';
const session = {
  id: null, readOnly: true, changed: false, sections: [], canUndo: false,
  error: null, savedPath: null, suggestedPath: null,
};

const drag = { kind: null, id: null, box: null, element: null, startX: 0, startY: 0, origX: 0, origY: 0, dx: 0, dy: 0, serviceIds: new Set(), target: null };

const clampZoom = (value) => Math.max(0.35, Math.min(1.6, value));

function applyCanvasScale() {
  if (!currentLayout) return;
  const canvas = $('#canvas');
  canvas.setAttribute('width', Math.round(currentLayout.width * currentZoom));
  canvas.setAttribute('height', Math.round(currentLayout.height * currentZoom));
  $('#zoom-value').textContent = `${Math.round(currentZoom * 100)}%`;
}

function setZoom(value) {
  currentZoom = clampZoom(value);
  if (currentPath) {
    const stored = readView(currentPath);
    writeView(currentPath, { ...stored, zoom: currentZoom });
  }
  applyCanvasScale();
}

function fitTopology() {
  if (!currentLayout) return;
  const viewport = $('#canvas-scroll');
  const availableWidth = Math.max(1, viewport.clientWidth - 24);
  const availableHeight = Math.max(1, viewport.clientHeight - 24);
  setZoom(Math.min(1, availableWidth / currentLayout.width, availableHeight / currentLayout.height));
  viewport.scrollTo({ left: 0, top: 0, behavior: 'smooth' });
}

function applyTopologyFilter() {
  const query = filterQuery.trim().toLowerCase();
  const graph = currentGraph;
  if (!graph) return;

  const matchingMachines = new Set(graph.machines.filter((machine) => {
    const addresses = Object.values(machine.addresses || {}).join(' ');
    return `${machine.id} ${machine.execution} ${machine.management_address} ${addresses}`.toLowerCase().includes(query);
  }).map((machine) => machine.id));
  const matchingServices = new Set(graph.services.filter((service) => (
    `${service.id} ${service.type} ${service.family} ${service.group_id} ${service.machine_id} ${service.subtitle || ''}`
      .toLowerCase().includes(query)
  )).map((service) => service.id));

  for (const node of document.querySelectorAll('#canvas g.node')) {
    const service = graph.services.find((item) => item.id === node.dataset.id);
    const matched = !query || matchingServices.has(node.dataset.id)
      || (service && matchingMachines.has(service.machine_id));
    node.classList.toggle('search-miss', !matched);
    node.classList.toggle('search-hit', Boolean(query && matched));
  }
  for (const machine of document.querySelectorAll('#canvas g.machine')) {
    const matched = !query || matchingMachines.has(machine.dataset.machine)
      || graph.services.some((service) => (
        service.machine_id === machine.dataset.machine && matchingServices.has(service.id)
      ));
    machine.classList.toggle('search-miss', !matched);
  }
  for (const entry of currentEntries) {
    const matched = !query || matchingServices.has(entry.edge.consumer)
      || matchingServices.has(entry.edge.provider);
    entry.group.classList.toggle('search-miss', !matched);
  }

  const matches = new Set([...matchingMachines, ...matchingServices]).size;
  $('#filter-count').textContent = query ? `${matches} match${matches === 1 ? '' : 'es'}` : '';
}

/* -- loading --------------------------------------------------------- */

async function loadInventoryList() {
  const list = $('#examples');
  list.innerHTML = '';
  let payload;
  try { payload = await api('/api/inventories'); } catch (_) { return; }
  for (const item of payload.inventories) {
    const li = document.createElement('li');
    const button = el('button', null, item.name);
    button.type = 'button';
    button.dataset.path = item.path;
    button.addEventListener('click', () => {
      $('#path').value = item.path;
      openInventory(item.path);
    });
    li.appendChild(button);
    list.appendChild(li);
  }
}

function markCurrent(path) {
  for (const button of document.querySelectorAll('.examples button')) {
    button.setAttribute('aria-current', button.dataset.path === path ? 'true' : 'false');
  }
}

async function openInventory(path) {
  showOverlay('Reading the inventory…', path);
  try {
    const state = await post('/api/session', { path });
    currentPath = path;
    session.id = state.session_id;
    session.readOnly = false;
    hideOverlay();
    markCurrent(path);
    adopt(state);
    return;
  } catch (_) {
    /* not editable — fall through to a read-only view */
  }
  try {
    const graph = await api('/api/graph?path=' + encodeURIComponent(path));
    currentPath = path;
    session.id = null;
    session.readOnly = true;
    session.changed = false;
    session.sections = [];
    session.canUndo = false;
    session.error = null;
    selected = null;
    hideOverlay();
    markCurrent(path);
    render(graph);
    renderDraftBar();
  } catch (error) {
    showOverlay('Could not read this inventory', error.message, true);
    $('#deployment').innerHTML = '<span class="hint">No inventory loaded</span>';
    currentGraph = null;
    renderDraftBar();
  }
}

/* -- the draft ------------------------------------------------------- */

function absorbDraft(state) {
  session.changed = state.draft.changed;
  session.sections = state.draft.sections;
  session.canUndo = state.draft.can_undo;
  session.savedPath = state.draft.saved_path || null;
  session.suggestedPath = state.draft.suggested_path || null;
}

function adopt(state) {
  absorbDraft(state);
  session.error = state.error || null;
  currentGraph = state.graph;
  render(state.graph);
  renderDraftBar();
}

async function applyOp(kind, params) {
  if (!session.id) return;
  try {
    const state = await post(`/api/session/${session.id}/op`, { kind, params });
    absorbDraft(state);
    session.error = state.ok ? null : state.error;
    if (state.ok) {
      currentGraph = state.graph;
      render(state.graph);
    }
    renderDraftBar();
  } catch (error) {
    session.error = error.message;
    renderDraftBar();
  }
}

async function saveCopy(destination) {
  if (!session.id) return;
  try {
    const state = await post(`/api/session/${session.id}/save`, { path: destination });
    absorbDraft(state);
    session.error = state.ok ? null : state.error;
    if (state.ok) {
      currentGraph = state.graph;
      session.savedPath = state.saved || session.savedPath;
      render(state.graph);
    }
    renderDraftBar();
  } catch (error) {
    session.error = error.message;
    renderDraftBar();
  }
}

async function undo() {
  if (!session.id) return;
  try {
    const state = await post(`/api/session/${session.id}/undo`, {});
    if (state.ok) { adopt(state); } else { session.error = state.error; renderDraftBar(); }
  } catch (error) { session.error = error.message; renderDraftBar(); }
}

async function resetDraft() {
  if (!session.id) return;
  try { adopt(await post(`/api/session/${session.id}/reset`, {})); }
  catch (error) { session.error = error.message; renderDraftBar(); }
}

function renderDraftBar() {
  const state = $('#draft-state');
  if (session.readOnly) {
    state.textContent = currentGraph
      ? 'Read-only view — not a compact operator inventory.'
      : 'No inventory loaded';
    state.className = 'draft-state readonly';
  } else if (session.changed) {
    state.textContent = `Draft — ${session.sections.join(', ')} changed. Nothing written.`;
    state.className = 'draft-state changed';
  } else {
    state.textContent = 'Draft matches the file. Nothing written.';
    state.className = 'draft-state';
  }
  $('#draft-error').textContent = session.error || '';
  $('#undo').disabled = !session.canUndo;
  $('#reset').disabled = !session.changed;
  $('#layout-hint').textContent = session.readOnly
    ? 'Drag a machine by its header to move it. Positions are view-only.'
    : 'Drag a machine header to move it; drag a group onto another machine to re-place it.';
}

/* -- overlays -------------------------------------------------------- */

function showOverlay(title, body, isError = false) {
  const overlay = $('#overlay');
  overlay.querySelector('.overlay-title').textContent = title;
  overlay.querySelector('.overlay-body').textContent = body || '';
  overlay.classList.toggle('error', isError);
  overlay.style.display = 'flex';
}

function hideOverlay() {
  $('#overlay').style.display = 'none';
}

/* -- side panels ----------------------------------------------------- */

function renderMasthead(graph) {
  const node = $('#deployment');
  node.innerHTML = '';
  node.appendChild(el('span', 'id', graph.deployment_id));
  if (graph.profile) node.appendChild(el('span', 'chip', graph.profile));
  node.appendChild(el('span', 'chip', graph.inventory_format === 'operator' ? 'operator inventory' : 'v3 inventory'));
  // "REPLACE: …" is planning residue in some example inventories, not a
  // useful deployment label. Keeping it in the masthead makes a finished
  // workbench look unfinished, so surface only a real operator label.
  if (graph.label && !/^replace\s*:/i.test(graph.label.trim())) {
    node.appendChild(el('span', 'deployment-label', graph.label));
  }
}

function setStat(id, value) {
  $(id).textContent = value === null || value === undefined ? '—' : String(value);
}

function renderAvailability(graph) {
  const a = graph.availability;
  setStat('#stat-validators', a.validators);
  setStat('#stat-quorum', a.quorum);
  setStat('#stat-tolerated', a.validator_failures_tolerated);
  setStat('#stat-copies', a.blockchain_full_copies);
  setStat('#stat-peers', a.storage_peers);
  setStat('#stat-primary', a.primary_rpc);

  const warnings = $('#warnings');
  warnings.innerHTML = '';
  for (const message of graph.warnings) warnings.appendChild(el('li', null, message));
}

function renderNetworks(graph) {
  const list = $('#networks');
  list.innerHTML = '';
  for (const network of graph.networks) {
    const li = document.createElement('li');
    const head = el('div', 'net-head');
    head.append(
      el('span', 'net-id', network.id),
      el('span', 'net-kind', network.kind),
      el('span', 'net-cidr', network.cidr),
    );
    li.appendChild(head);
    li.appendChild(el('div', 'net-members', network.machine_ids.length ? network.machine_ids.join('  ') : 'no machines'));
    list.appendChild(li);
  }

  const routing = $('#routing');
  routing.innerHTML = '';
  for (const [key, value] of Object.entries(graph.routing)) {
    const row = el('div', 'route');
    row.append(el('span', null, ROUTING_LABEL[key] || key), el('span', null, value));
    routing.appendChild(row);
  }

  // Reachability between networks: the one thing a route can carry that the
  // routing policy above cannot.
  const routes = $('#routes');
  routes.innerHTML = '';
  routes.appendChild(el('div', 'field-label', 'Routes between networks'));
  if (!graph.routes.length) {
    routes.appendChild(el('div', 'net-members', 'none declared'));
  }
  for (const route of graph.routes) {
    const row = el('div', 'route');
    row.appendChild(el('span', null,
      `${route.from} → ${route.to}${route.via ? `  via ${route.via}` : ''}`));
    if (session.id) {
      row.appendChild(miniButton('Remove', () => applyOp('remove_route', {
        from: route.from, to: route.to,
      }), true));
    }
    routes.appendChild(row);
  }
  if (session.id && graph.operator && graph.operator.networks.length > 1) {
    const networks = graph.operator.networks;
    const from = selectControl(networks.map((id) => [id, id]), networks[0], () => {});
    const to = selectControl(networks.map((id) => [id, id]), networks[1], () => {});
    const row = el('div', 'inline');
    row.append(from, el('span', null, '→'), to,
      miniButton('Add route', () => applyOp('add_route', { from: from.value, to: to.value })));
    routes.appendChild(row);
  }
}

function renderLegend(graph) {
  const list = $('#legend');
  list.innerHTML = '';
  for (const family of graph.families) {
    const li = document.createElement('li');
    const swatch = el('span', 'swatch');
    swatch.style.background = FAMILY_COLOR[family] || FAMILY_COLOR.other;
    li.append(swatch, el('span', null, FAMILY_LABEL[family] || family));
    list.appendChild(li);
  }
  if (graph.machines.some((machine) => machineRisk(machine))) {
    const li = document.createElement('li');
    const dot = el('span', 'swatch risk-dot');
    li.append(dot, el('span', null, 'losing this machine breaks consensus or the primary RPC'));
    list.appendChild(li);
  }
  if (localLinks(graph).size) {
    const li = document.createElement('li');
    li.append(el('span', 'legend-badge', 'n'),
      el('span', null, 'links kept inside a machine are counted, not drawn'));
    list.appendChild(li);
  }
}

function machineRisk(machine) {
  const fate = machine.if_lost;
  if (!fate) return null;
  if (fate.consensus === 'quorum_lost') {
    return { kind: 'risk-quorum', text: 'Losing this machine removes QBFT quorum.' };
  }
  if (fate.applications_rpc === 'primary_lost') {
    return { kind: 'risk-rpc', text: 'Losing this machine removes the primary application RPC.' };
  }
  if (fate.storage === 'publication_unavailable') {
    return { kind: 'risk-storage', text: 'Losing this machine stops publication to storage.' };
  }
  return null;
}

/* -- saving ---------------------------------------------------------- */

function renderSavePanel(graph) {
  const panel = $('#save-panel');
  if (!session.id) { panel.hidden = true; return; }
  panel.hidden = false;

  const body = $('#save-body');
  body.innerHTML = '';
  body.appendChild(el('p', 'local-note',
    'Always writes a new file. The inventory you opened is never modified or overwritten.'));

  const input = document.createElement('input');
  input.type = 'text';
  input.value = session.savedPath || session.suggestedPath || '';
  body.appendChild(field('New file', input));

  const row = el('div', 'mini-row');
  row.appendChild(miniButton('Save a copy', () => saveCopy(input.value.trim())));
  body.appendChild(row);

  if (session.savedPath) {
    const note = el('p', 'saved-note');
    note.append(el('span', null, 'Written to '), el('code', null, session.savedPath));
    body.appendChild(note);
    body.appendChild(el('p', 'local-note', 'Change the name to save another copy.'));
  }
}

/* -- selection inspector --------------------------------------------- */

function select(target) {
  selected = target;
  if (currentGraph) renderSelection(currentGraph);
  for (const group of document.querySelectorAll('g.group')) {
    group.classList.toggle('selected', Boolean(selected && selected.kind === 'group' && selected.id === group.dataset.group));
  }
}

function field(labelText, control) {
  const wrap = el('div', 'field');
  wrap.appendChild(el('label', null, labelText));
  wrap.appendChild(control);
  return wrap;
}

function selectControl(options, value, onChange) {
  const node = document.createElement('select');
  for (const [optionValue, optionLabel] of options) {
    const option = document.createElement('option');
    option.value = optionValue;
    option.textContent = optionLabel;
    if (optionValue === value) option.selected = true;
    node.appendChild(option);
  }
  node.addEventListener('change', () => onChange(node.value));
  return node;
}

function numberInput(value, attrs = {}) {
  const node = document.createElement('input');
  node.type = 'number';
  Object.assign(node, attrs);
  if (value !== undefined && value !== null) node.value = String(value);
  return node;
}

function miniButton(text, onClick, danger = false) {
  const button = el('button', danger ? 'mini danger' : 'mini', text);
  button.type = 'button';
  button.addEventListener('click', onClick);
  return button;
}

function riskNote(text) {
  return el('p', 'risk-note', text);
}

function machineOptions(graph) {
  const machines = (graph.operator && graph.operator.machines) || graph.machines.map((m) => m.id);
  return machines.map((id) => [id, id]);
}

function renderSelection(graph) {
  const panel = $('#selection-panel');
  if (!selected || !graph.operator) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const body = $('#selection-body');
  body.innerHTML = '';

  if (selected.kind === 'group') {
    const group = graph.groups.find((item) => item.id === selected.id);
    if (!group) { selected = null; panel.hidden = true; return; }
    $('#selection-title').textContent = `Group ${group.id}`;

    const meta = el('div', 'selection-meta');
    meta.append(el('span', null, group.kind), el('span', null, `on ${group.machine_id}`), el('span', null, `${group.service_ids.length} service(s)`));
    body.appendChild(meta);

    if (group.if_lost && group.if_lost.consensus === 'quorum_lost') {
      body.appendChild(riskNote('Losing this group removes QBFT quorum.'));
    }

    const services = el('ul', 'selection-services');
    for (const serviceId of group.service_ids) services.appendChild(el('li', null, serviceId));
    body.appendChild(services);

    const internal = internalLinks(graph, group.service_ids);
    if (internal.length) {
      const note = el('p', 'local-note',
        `${internal.length} link(s) stay inside ${group.machine_id} and are not drawn:`);
      const list = el('ul', 'selection-services');
      for (const row of internal) list.appendChild(el('li', null, row));
      body.appendChild(note);
      body.appendChild(list);
    }

    const operator = graph.operator;
    const isValidator = Object.prototype.hasOwnProperty.call(operator.validator_groups, group.id);
    const isObserver = Object.prototype.hasOwnProperty.call(operator.observer_groups, group.id);

    if (isValidator || isObserver) {
      const current = isValidator ? operator.validator_groups[group.id] : operator.observer_groups[group.id];
      const count = typeof current === 'number' ? current : 1;
      const kind = isValidator ? 'set_validator_count' : 'set_observer_count';

      const stepper = el('div', 'stepper');
      stepper.appendChild(miniButton('−', () => applyOp(kind, { group: group.id, count: Math.max(1, count - 1) })));
      stepper.appendChild(el('output', null, String(count)));
      stepper.appendChild(miniButton('+', () => applyOp(kind, { group: group.id, count: count + 1 })));
      body.appendChild(field(isValidator ? 'Validators in this group' : 'Observers in this group', stepper));
    }

    body.appendChild(field('Move to machine', selectControl(
      machineOptions(graph), group.machine_id,
      (machine) => applyOp('move_group', { group: group.id, machine }),
    )));

    if (isValidator || isObserver) {
      const remove = isValidator ? 'remove_validator_group' : 'remove_observer_group';
      const row = el('div', 'mini-row');
      row.appendChild(miniButton('Remove group', () => applyOp(remove, { group: group.id }), true));
      body.appendChild(row);
    }
    return;
  }

  const machine = graph.machines.find((item) => item.id === selected.id);
  if (!machine) { selected = null; panel.hidden = true; return; }
  $('#selection-title').textContent = `Machine ${machine.id}`;

  const meta = el('div', 'selection-meta');
  meta.append(el('span', null, machine.execution));
  meta.append(el('span', null, machine.management_address));
  meta.append(el('span', null, `${machine.service_count} service(s)`));
  body.appendChild(meta);

  for (const [networkId, address] of Object.entries(machine.addresses)) {
    const row = el('div', 'route');
    row.append(el('span', null, networkId), el('span', null, address));
    body.appendChild(row);
  }

  if (machine.execution !== 'local') {
    for (const network of (graph.operator.networks || [])) {
      const input = document.createElement('input');
      input.type = 'text';
      input.value = machine.addresses[network] || '';
      const row = el('div', 'inline');
      row.append(input, miniButton('Set', () => applyOp('set_machine_address', {
        id: machine.id, network, address: input.value.trim(),
      })));
      body.appendChild(field(`${network} address`, row));
    }
  }

  if (machine.if_lost) {
    if (machine.if_lost.consensus === 'quorum_lost') body.appendChild(riskNote('Losing this machine removes QBFT quorum.'));
    if (machine.if_lost.applications_rpc === 'primary_lost') body.appendChild(riskNote('Losing this machine removes the primary application RPC.'));
    if (machine.if_lost.storage === 'publication_unavailable') body.appendChild(riskNote('Losing this machine stops publication to storage.'));
  }

  const groups = el('ul', 'selection-services');
  for (const groupId of machine.group_ids) groups.appendChild(el('li', null, groupId));
  body.appendChild(groups);

  const here = graph.services.filter((service) => service.machine_id === machine.id).map((service) => service.id);
  const internal = internalLinks(graph, here);
  if (internal.length) {
    body.appendChild(el('p', 'local-note', `${internal.length} link(s) are internal to this machine and not drawn.`));
  }

  // -- add something here
  const kindSelect = selectControl([
    ['validator_group', 'validator group'],
    ['observer_group', 'observer group'],
    ['rpc_node', 'RPC node'],
    ['storage_peer', 'storage peer'],
  ], 'validator_group', () => syncCount());
  const idInput = document.createElement('input');
  idInput.type = 'text';
  idInput.placeholder = 'id (a-z, 0-9, -)';
  const countInput = numberInput(1, { min: 1, max: 100 });
  const countField = field('Count', countInput);
  const syncCount = () => { countField.hidden = !kindSelect.value.endsWith('_group'); };
  syncCount();

  const addRow = el('div', 'mini-row');
  addRow.appendChild(miniButton('Add here', () => {
    const id = idInput.value.trim();
    const group = machine.group_ids[0];
    const kind = kindSelect.value;
    if (!id) { session.error = 'give the new element an identifier'; renderDraftBar(); return; }
    if (kind === 'validator_group') return applyOp('add_validator_group', { group: id, machine: machine.id, count: Number(countInput.value) || 1 });
    if (kind === 'observer_group') return applyOp('add_observer_group', { group: id, machine: machine.id, count: Number(countInput.value) || 1 });
    if (!group) { session.error = `${machine.id} has no group to attach to`; renderDraftBar(); return; }
    const operation = kind === 'rpc_node' ? 'add_rpc_node' : 'add_storage_peer';
    return applyOp(operation, { id, group });
  }));

  body.appendChild(field('Add to this machine', kindSelect));
  body.appendChild(field('Identifier', idInput));
  body.appendChild(countField);
  body.appendChild(addRow);

  const removeRow = el('div', 'mini-row');
  removeRow.appendChild(miniButton('Remove machine', () => applyOp('remove_machine', { id: machine.id }), true));
  body.appendChild(removeRow);
}

/* -- structure panel ------------------------------------------------- */

function renderStructure(graph) {
  const panel = $('#structure-panel');
  const operator = graph.operator;
  if (!operator || session.readOnly) { panel.hidden = true; return; }
  panel.hidden = false;

  const body = $('#structure-body');
  body.innerHTML = '';

  body.appendChild(field('Profile', selectControl(
    PROFILES.map((name) => [name, name]), operator.profile,
    (profile) => applyOp('set_profile', { profile }),
  )));

  body.appendChild(field('Primary RPC', selectControl(
    operator.rpc_nodes.map((node) => [node, node]), operator.primary_rpc,
    (id) => applyOp('set_primary_rpc', { id }),
  )));

  const routingWrap = el('div');
  for (const role of ROUTING_ROLES) {
    routingWrap.appendChild(field(ROUTING_LABEL[role], selectControl(
      operator.networks.map((network) => [network, network]), operator.routing[role],
      (network) => applyOp('set_routing', { role, network }),
    )));
  }
  const routingField = el('div', 'field');
  routingField.appendChild(el('span', 'field-label', 'Routing (which network each link uses)'));
  routingField.appendChild(routingWrap);
  body.appendChild(routingField);

  const publish = numberInput(operator.replication.publish_after_replicas, { min: 1, max: 50 });
  const target = numberInput(operator.replication.target_replicas, { min: 1, max: 50 });
  const replicate = el('div', 'inline');
  replicate.append(publish, el('span', null, 'of'), target,
    miniButton('Apply', () => applyOp('set_replication', {
      publish_after_replicas: Number(publish.value),
      target_replicas: Number(target.value),
    })));
  body.appendChild(field(`Storage replicas (${Object.keys(operator.storage_peers).length} peer(s))`, replicate));

  const networkList = el('div');
  for (const networkId of operator.networks) {
    const row = el('div', 'route');
    row.append(el('span', null, networkId), miniButton('Remove', () => applyOp('remove_network', { id: networkId }), true));
    networkList.appendChild(row);
  }
  const newNetworkId = document.createElement('input');
  newNetworkId.type = 'text';
  newNetworkId.placeholder = 'network id';
  const newNetworkCidr = document.createElement('input');
  newNetworkCidr.type = 'text';
  newNetworkCidr.placeholder = '10.0.0.0/24';
  const newNetworkKind = selectControl([['lan', 'lan'], ['vpn', 'vpn']], 'lan', () => {});
  const networkRow = el('div', 'inline');
  networkRow.append(newNetworkId, newNetworkCidr, newNetworkKind,
    miniButton('Add', () => applyOp('add_network', {
      id: newNetworkId.value.trim(),
      kind: newNetworkKind.value,
      cidr: newNetworkCidr.value.trim(),
    })));
  networkList.appendChild(networkRow);
  body.appendChild(field('Networks', networkList));

  // -- which node answers each RPC consumer
  const consumers = rpcConsumers(graph);
  if (consumers.length) {
    const choices = [['', `primary (${operator.primary_rpc})`]];
    for (const node of operator.rpc_nodes) choices.push([node, node]);
    for (const service of graph.services) {
      if (service.type === 'besu-observer') choices.push([service.id, service.id]);
    }
    const bindings = el('div');
    for (const consumer of consumers) {
      bindings.appendChild(field(consumer, selectControl(
        choices, operator.bindings[consumer] || '',
        (provider) => applyOp('set_binding', { consumer, provider }),
      )));
    }
    body.appendChild(field('RPC bindings', bindings));
  }

  // -- availability policy
  body.appendChild(field('Availability objectives', renderObjectives(graph)));

  // -- NAT / announce overrides
  const advertise = operator.advertise || {};
  const advertiseList = el('div');
  for (const [service, entry] of Object.entries(advertise)) {
    const detail = [
      entry.advertise_address,
      entry.advertise_port ? `port ${entry.advertise_port}` : '',
      entry.p2p_advertise_port ? `p2p ${entry.p2p_advertise_port}` : '',
    ].filter(Boolean).join(' ');
    const row = el('div', 'route');
    row.append(el('span', null, service), el('span', null, detail),
      miniButton('Clear', () => applyOp('set_advertise', { service }), true));
    advertiseList.appendChild(row);
  }
  const targets = advertiseTargets(graph);
  if (targets.length) {
    const service = selectControl(targets.map((id) => [id, id]), targets[0], () => {});
    const address = document.createElement('input');
    address.type = 'text';
    address.placeholder = 'address';
    const port = numberInput('', { min: 1, max: 65535 });
    port.placeholder = 'port';
    const row = el('div', 'inline');
    row.append(service, address, port, miniButton('Set', () => applyOp('set_advertise', {
      service: service.value,
      advertise_address: address.value.trim(),
      advertise_port: port.value === '' ? '' : Number(port.value),
    })));
    advertiseList.appendChild(row);
  }
  body.appendChild(field('Announce overrides (NAT)', advertiseList));
}

const OBJECTIVE_LABEL = {
  tolerate_validator_individual: 'survive a single validator loss',
  tolerate_validator_group: 'survive a validator group loss',
  tolerate_machine: 'survive any machine loss',
  observer_copy: 'keep an observer copy',
  rpc_redundant: 'at least two RPC nodes',
  storage_durable: 'durable storage copies',
};

function rpcConsumers(graph) {
  const providers = new Set(graph.operator ? graph.operator.rpc_nodes : []);
  for (const service of graph.services) {
    if (service.type === 'besu-observer') providers.add(service.id);
  }
  const consumers = new Set();
  for (const edge of graph.edges) {
    if (edge.name === 'rpc' && providers.has(edge.provider)) consumers.add(edge.consumer);
  }
  return [...consumers].sort();
}

function advertiseTargets(graph) {
  const p2p = ['besu-rpc', 'besu-validator', 'besu-observer', 'ipfs-kubo', 'ipfs-cluster'];
  return graph.services
    .filter((service) => (service.exposure && service.exposure.mode === 'private') || p2p.includes(service.type))
    .map((service) => service.id);
}

function objectiveStatus(graph) {
  const availability = graph.availability;
  const operator = graph.operator;
  const groups = graph.groups.filter((group) => group.if_lost);
  const machines = graph.machines.filter((machine) => machine.if_lost);
  return {
    tolerate_validator_individual: availability.validators - 1 >= availability.quorum,
    tolerate_validator_group: groups.every((group) => group.if_lost.consensus !== 'quorum_lost'),
    tolerate_machine: machines.every((machine) => machine.if_lost.consensus !== 'quorum_lost'),
    observer_copy: graph.services.some((service) => service.type === 'besu-observer'),
    rpc_redundant: operator.rpc_nodes.length >= 2,
    storage_durable: machines.every((machine) => (
      machine.if_lost.storage !== 'publication_unavailable'
      && machine.if_lost.storage !== 'durability_target_unmet'
    )),
  };
}

function renderObjectives(graph) {
  const operator = graph.operator;
  const status = objectiveStatus(graph);
  const declared = new Set(operator.objectives);
  const acknowledged = new Set(operator.acknowledgements);
  const list = el('div');

  for (const objective of Object.keys(OBJECTIVE_LABEL)) {
    const met = status[objective];
    const row = el('label', 'objective');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = acknowledged.has(objective);
    // A satisfied objective cannot be acknowledged, so it is not offered.
    box.disabled = met;
    box.addEventListener('change', () => {
      const next = new Set(acknowledged);
      if (box.checked) next.add(objective); else next.delete(objective);
      applyOp('set_acknowledgements', { objectives: [...next] });
    });
    row.append(box, el('span', met ? 'met' : 'unmet', met ? '✓' : '✗'));
    row.appendChild(el('span', null, OBJECTIVE_LABEL[objective]));
    if (declared.has(objective)) row.appendChild(el('span', 'chip', 'declared'));
    list.appendChild(row);
  }
  return list;
}

/* -- the palette ----------------------------------------------------- */

const PALETTE = [
  ['machine', 'machine'],
  ['validator_group', 'validator group'],
  ['observer_group', 'observer group'],
  ['rpc_node', 'RPC node'],
  ['storage_peer', 'storage peer'],
  ['proxy', 'web proxy'],
];

function renderPalette(graph) {
  const panel = $('#palette-panel');
  const operator = graph.operator;
  if (!operator || session.readOnly) { panel.hidden = true; return; }
  panel.hidden = false;

  const body = $('#palette-body');
  body.innerHTML = '';

  const machineList = machineOptions(graph);
  const groupList = graph.groups.map((group) => [group.id, `${group.id} → ${group.machine_id}`]);
  const busyMachines = new Set(
    graph.services.filter((service) => service.type === 'edge-proxy').map((service) => service.machine_id),
  );
  // A proxy may only live in an application group, and a machine runs one.
  const proxyGroups = graph.groups
    .filter((group) => group.kind === 'apps' && !busyMachines.has(group.machine_id))
    .map((group) => [group.id, `${group.id} → ${group.machine_id}`]);

  const form = el('div');
  body.appendChild(form);

  const textInput = (placeholder) => {
    const node = document.createElement('input');
    node.type = 'text';
    node.placeholder = placeholder;
    return node;
  };
  const submitRow = (label, handler) => {
    const row = el('div', 'mini-row');
    row.appendChild(miniButton(label, handler));
    form.appendChild(row);
  };

  const build = {
    machine(idField) {
      const execution = selectControl([['local', 'local'], ['ssh', 'ssh']], 'ssh', () => {});
      const management = textInput('management address');
      const addresses = new Map();
      for (const network of operator.networks) addresses.set(network, textInput(`${network} address`));

      form.append(field('Identifier', idField), field('Execution', execution), field('Management address', management));
      for (const [network, node] of addresses) form.appendChild(field(`${network} address`, node));

      submitRow('Add machine', () => {
        const params = { id: idField.value.trim(), execution: execution.value };
        if (execution.value !== 'local') params.management_address = management.value.trim();
        const filled = {};
        for (const [network, node] of addresses) {
          const value = node.value.trim();
          if (value) filled[network] = value;
        }
        if (Object.keys(filled).length) params.addresses = filled;
        return applyOp('add_machine', params);
      });
    },

    group(idField) {
      const machine = selectControl(machineList, operator.machines[0], () => {});
      const count = numberInput(1, { min: 1, max: 100 });
      const operation = this.operation;
      form.append(field('Identifier', idField), field('Machine', machine), field('Count', count));
      submitRow('Add group', () => applyOp(operation, {
        group: idField.value.trim(), machine: machine.value, count: Number(count.value) || 1,
      }));
    },

    attached(idField) {
      const group = selectControl(groupList, (groupList[0] || [''])[0], () => {});
      const operation = this.operation;
      form.append(field('Identifier', idField), field('Group', group));
      submitRow('Add', () => applyOp(operation, { id: idField.value.trim(), group: group.value }));
    },

    proxy(idField) {
      const group = selectControl(proxyGroups, (proxyGroups[0] || [''])[0], () => {});
      const bind = selectControl([['public', 'public'], ['private', 'private'], ['loopback', 'loopback']], 'public', () => {});
      const port = numberInput(80, { min: 1, max: 65535 });
      const origin = textInput('https://dark.example.org');
      origin.value = 'http://localhost';
      form.append(
        field('Identifier', idField), field('Application group', group), field('Bind', bind),
        field('Port', port), field('Public origin', origin),
      );
      submitRow('Add proxy', () => applyOp('add_proxy', {
        id: idField.value.trim(), group: group.value, bind: bind.value,
        port: Number(port.value) || 80, tls_mode: 'http', host: '',
        public_origin: origin.value.trim(),
        route_id: 'dashboard', path: '/admin/', service: 'dashboard', upstream_path: '/',
      }));
    },
  };

  let type = 'machine';
  body.insertBefore(field('Component', selectControl(PALETTE, type, (value) => {
    type = value;
    draw();
  })), form);

  function draw() {
    form.innerHTML = '';
    const idField = textInput('id (a-z, 0-9, -)');
    if (type === 'validator_group' || type === 'observer_group') {
      build.group.call({ operation: type === 'validator_group' ? 'add_validator_group' : 'add_observer_group' }, idField);
      return;
    }
    if (type === 'rpc_node' || type === 'storage_peer') {
      build.attached.call({ operation: type === 'rpc_node' ? 'add_rpc_node' : 'add_storage_peer' }, idField);
      return;
    }
    build[type](idField);
  }

  draw();
}

/* -- the web edge ---------------------------------------------------- */

function renderWebEdge(graph) {
  const panel = $('#webedge-panel');
  const operator = graph.operator;
  const proxies = operator ? operator.proxies : null;
  if (!operator || session.readOnly || !proxies || !Object.keys(proxies).length) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;

  const body = $('#webedge-body');
  body.innerHTML = '';
  const textInput = (value, placeholder) => {
    const node = document.createElement('input');
    node.type = 'text';
    node.value = value || '';
    node.placeholder = placeholder || '';
    return node;
  };

  for (const [proxyId, definition] of Object.entries(proxies)) {
    body.appendChild(el('div', 'group-label', proxyId));

    const listener = definition.listener || {};
    const bind = selectControl([['public', 'public'], ['private', 'private'], ['loopback', 'loopback']], listener.bind, () => {});
    const port = numberInput(listener.port, { min: 1, max: 65535 });
    const network = selectControl(operator.networks.map((id) => [id, id]), listener.network || operator.networks[0], () => {});
    const listenerRow = el('div', 'inline');
    listenerRow.append(bind, port, network, miniButton('Apply', () => applyOp('set_proxy_listener', {
      id: proxyId, bind: bind.value, port: Number(port.value) || 80,
      ...(bind.value === 'private' ? { network: network.value } : {}),
    })));
    body.appendChild(field('Listener', listenerRow));

    body.appendChild(field('TLS', selectControl(
      [['http', 'http'], ['external', 'external']],
      (definition.tls || {}).mode || 'http',
      (mode) => applyOp('set_proxy_tls', { id: proxyId, mode }),
    )));

    const site = (definition.sites || [])[0] || {};
    const host = textInput(site.host, 'hostname (or empty)');
    const origin = textInput(site.public_origin, 'https://…');
    body.appendChild(field('Host', host));
    body.appendChild(field('Public origin', origin));
    const siteRow = el('div', 'mini-row');
    siteRow.appendChild(miniButton('Apply site', () => applyOp('set_proxy_site', {
      id: proxyId, site_index: 0, host: host.value.trim(), public_origin: origin.value.trim(),
    })));
    body.appendChild(siteRow);

    for (const route of ((definition.sites || [])[0] || {}).routes || []) {
      const row = el('div', 'route');
      row.append(
        el('span', null, `${route.path} → ${route.service}`),
        miniButton('Remove', () => applyOp('remove_proxy_route', {
          id: proxyId, site_index: 0, route_id: route.id,
        }), true),
      );
      body.appendChild(row);
    }

    const routeId = textInput('', 'route id');
    const routePath = textInput('', '/path/');
    const routeService = selectControl(
      [['dashboard', 'dashboard'], ['explorer', 'explorer'], ['minter-api', 'minter-api'], ['resolver-api', 'resolver-api']],
      'dashboard', () => {},
    );
    const upstream = textInput('/', '/');
    const routeRow = el('div', 'inline');
    routeRow.append(routeId, routePath, routeService, upstream, miniButton('Add route', () => applyOp('add_proxy_route', {
      id: proxyId, site_index: 0, route_id: routeId.value.trim(),
      path: routePath.value.trim(), service: routeService.value, upstream_path: upstream.value.trim(),
    })));
    body.appendChild(field('Add route', routeRow));

    const removeRow = el('div', 'mini-row');
    removeRow.appendChild(miniButton('Remove proxy', () => applyOp('remove_proxy', { id: proxyId }), true));
    body.appendChild(removeRow);
  }
}

/* -- box model ------------------------------------------------------- */

function boxServices(box) {
  return box.groups.flatMap((group) => group.items.map((item) => item.service));
}

/* Two box models. The service model nests groups and cards; the fabric model is
 * a compact machine card listing where it sits on the network. */
function buildBoxes(graph, fabric = false) {
  const byId = new Map(graph.services.map((service) => [service.id, service]));
  const boxes = [];

  for (const machine of graph.machines) {
    if (fabric) {
      const rows = graph.networks
        .filter((network) => network.machine_ids.includes(machine.id))
        .map((network) => ({ id: network.id, address: machine.addresses[network.id] || '' }));
      boxes.push({
        machine,
        fabric: true,
        groups: [],
        rows,
        w: MW,
        h: MHEAD + Math.max(rows.length, 1) * ADDRESS_ROW + MIP,
        x: 0,
        y: 0,
      });
      continue;
    }
    const groups = graph.groups.filter((group) => group.machine_id === machine.id);
    const assigned = new Set(groups.flatMap((group) => group.service_ids));
    const buckets = groups.map((group) => ({
      id: group.id,
      services: group.service_ids.map((sid) => byId.get(sid)).filter(Boolean),
    }));
    const loose = graph.services.filter((service) => service.machine_id === machine.id && !assigned.has(service.id));
    if (loose.length) buckets.push({ id: 'unassigned', services: loose });

    const placed = [];
    let top = MHEAD;
    for (const bucket of buckets) {
      const count = Math.max(bucket.services.length, 1);
      const height = GHEAD + GPADY * 2 + count * CH + (count - 1) * CGAP;
      const items = [];
      let ny = top + GHEAD + GPADY;
      for (const service of bucket.services) {
        items.push({ service, localY: ny });
        ny += CH + CGAP;
      }
      placed.push({ id: bucket.id, top, height, items });
      top += height + GGAP;
    }

    boxes.push({
      machine, fabric: false, groups: placed, rows: [],
      w: MW, h: (placed.length ? top - GGAP : MHEAD) + MIP, x: 0, y: 0,
    });
  }
  return boxes;
}

/* -- the fabric: networks as bands above the fleet -------------------- */

function networkBands(graph) {
  return graph.networks.map((network, index) => ({
    id: network.id,
    kind: network.kind,
    cidr: network.cidr,
    machines: network.machine_ids,
    tint: NETWORK_TINT[index % NETWORK_TINT.length],
    y: PAD + index * (FBAND_H + FBAND_GAP),
  }));
}

function fabricHeight(graph) {
  const count = graph.networks.length;
  return count ? count * FBAND_H + (count - 1) * FBAND_GAP + FABRIC_GAP : 0;
}

function tintOf(graph, networkId) {
  const index = graph.networks.findIndex((network) => network.id === networkId);
  return NETWORK_TINT[(index < 0 ? 0 : index) % NETWORK_TINT.length];
}

function dominantFamily(box) {
  const counts = {};
  for (const service of boxServices(box)) counts[service.family] = (counts[service.family] || 0) + 1;
  let best = 'other';
  let bestCount = 0;
  for (const family of FAMILY_ORDER) {
    const count = counts[family] || 0;
    if (count > bestCount) { bestCount = count; best = family; }
  }
  return best;
}

function networkSignature(machineId, graph) {
  const ids = [];
  for (const network of graph.networks) if (network.machine_ids.includes(machineId)) ids.push(network.id);
  return ids.length ? ids.sort().join(' + ') : 'no network';
}

function layoutBoxes(boxes, mode, graph, top) {
  const bands = [];
  if (!boxes.length) return bands;

  if (mode === 'grid') {
    const cols = Math.max(1, Math.ceil(Math.sqrt(boxes.length)));
    const cellH = Math.max(...boxes.map((box) => box.h)) + ROWGAP;
    boxes.forEach((box, index) => {
      box.x = PAD + (index % cols) * (MW + MGAP);
      box.y = top + Math.floor(index / cols) * cellH;
    });
    return bands;
  }

  if (mode === 'function' || mode === 'network') {
    const keyOf = mode === 'function'
      ? (box) => dominantFamily(box)
      : (box) => networkSignature(box.machine.id, graph);
    const grouped = new Map();
    for (const box of boxes) {
      const key = keyOf(box);
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(box);
    }
    const order = mode === 'function'
      ? FAMILY_ORDER.filter((family) => grouped.has(family))
      : [...grouped.keys()].sort();

    let y = top;
    for (const key of order) {
      const list = grouped.get(key);
      list.sort((a, b) => a.machine.id.localeCompare(b.machine.id));
      const rowH = Math.max(...list.map((box) => box.h));
      const rowY = y + BAND_HEAD;
      list.forEach((box, index) => { box.x = PAD + index * (MW + MGAP); box.y = rowY; });
      bands.push({
        label: mode === 'function' ? (FAMILY_LABEL[key] || key) : key,
        x: PAD, y: y + 16, right: PAD + list.length * (MW + MGAP) - MGAP,
      });
      y = rowY + rowH + BAND_GAP;
    }
    return bands;
  }

  boxes.forEach((box, index) => { box.x = PAD + index * (MW + MGAP); box.y = top; });
  return bands;
}

function finalizeLayout(boxes, bands, fabricBands = [], gutter = 0) {
  const centers = new Map();
  for (const box of boxes) {
    for (const group of box.groups) {
      group.rects = group.items.map((item) => ({
        service: item.service, x: box.x + MIP + GIP, y: box.y + item.localY, w: nodeW, h: CH,
      }));
      for (const rect of group.rects) {
        centers.set(rect.service.id, { x: rect.x + nodeW / 2, y: rect.y + CH / 2 });
      }
    }
  }
  const bandRight = Math.max(...boxes.map((box) => box.x + box.w), PAD + MW);
  let width = Math.max(...boxes.map((box) => box.x + box.w), PAD) + PAD;
  if (gutter) width = Math.max(width, bandRight + gutter);
  const height = Math.max(...boxes.map((box) => box.y + box.h), PAD) + PAD;
  return { boxes, bands, fabricBands, centers, width, height, bandRight };
}

function layoutFor(graph, fabric, mode, positions) {
  const bands = networkBands(graph);
  // The route gutter is only needed where the routes are actually drawn.
  const gutter = fabric && graph.routes.length ? ROUTE_GUTTER : 0;
  const boxes = buildBoxes(graph, fabric);
  const top = PAD + fabricHeight(graph);
  const layout = finalizeLayout(boxes, layoutBoxes(boxes, mode, graph, top), bands, gutter);
  if (positions && Object.keys(positions).length) {
    applyManualPositions(layout.boxes, positions);
    return finalizeLayout(layout.boxes, layout.bands, bands, gutter);
  }
  return layout;
}

function applyManualPositions(boxes, positions) {
  for (const box of boxes) {
    const saved = positions[box.machine.id];
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) { box.x = saved.x; box.y = saved.y; }
  }
}

/* -- drawing --------------------------------------------------------- */

function drawBands(root, bands) {
  if (!bands.length) return;
  const layer = svgEl('g', { class: 'band-layer' }, root);
  for (const band of bands) {
    if (band.right > band.x + 60) {
      svgEl('line', { class: 'band-rule', x1: band.x, y1: band.y + 7, x2: band.right, y2: band.y + 7 }, layer);
    }
    svgText(layer, band.x, band.y, band.label, 'band-label');
  }
}

function bezierPoint(p0, p1, p2, p3, t) {
  const u = 1 - t;
  return {
    x: (u * u * u) * p0.x + (3 * u * u * t) * p1.x + (3 * u * t * t) * p2.x + (t * t * t) * p3.x,
    y: (u * u * u) * p0.y + (3 * u * u * t) * p1.y + (3 * u * t * t) * p2.y + (t * t * t) * p3.y,
  };
}

function edgeLabelText(edge) {
  if (edge.port) return `${edge.network ? edge.network + ':' : ''}${edge.port}`;
  return edge.network || '';
}

function drawEdges(root, graph, layout) {
  const layer = svgEl('g', { class: 'edge-layer' }, root);
  const defs = svgEl('defs', {}, root);
  const marker = svgEl('marker', {
    id: 'arrow', viewBox: '0 0 10 10', refX: '9', refY: '5',
    markerWidth: '7', markerHeight: '7', orient: 'auto',
  }, defs);
  svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: '#9a9d93' }, marker);

  const entries = [];
  for (const edge of graph.edges) {
    if (!edge.crosses_host) continue;
    const group = svgEl('g', { class: 'edge-group' }, layer);
    const path = svgEl('path', { class: 'edge', 'marker-end': 'url(#arrow)' }, group);
    const entry = { edge, group, path, label: null };
    const text = edgeLabelText(edge);
    if (text) {
      const label = svgEl('g', { class: 'edge-label-group' }, group);
      const width = text.length * 6.2 + 10;
      svgEl('rect', { class: 'edge-label-bg', x: -width / 2, y: -13, width, height: 15, rx: 2 }, label);
      const node = svgEl('text', { class: 'edge-label', 'text-anchor': 'middle', y: -2 }, label);
      node.textContent = text;
      entry.label = label;
    }
    entries.push(entry);
  }
  placeEdges(entries, layout.centers);
  return entries;
}

function placeEdges(entries, centers) {
  for (const entry of entries) {
    const from = centers.get(entry.edge.consumer);
    const to = centers.get(entry.edge.provider);
    if (!from || !to) { entry.group.style.display = 'none'; continue; }
    entry.group.style.display = '';
    const dx = to.x - from.x;
    const c1 = { x: from.x + dx * 0.35, y: from.y };
    const c2 = { x: to.x - dx * 0.35, y: to.y };
    entry.path.setAttribute('d', `M ${from.x} ${from.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${to.x} ${to.y}`);
    if (entry.label) {
      const mid = bezierPoint(from, c1, c2, to, 0.5);
      entry.label.setAttribute('transform', `translate(${mid.x} ${mid.y})`);
    }
  }
}

function exposureLabel(service) {
  const exposure = service.exposure;
  if (!exposure || exposure.mode === 'none') return '';
  const parts = [exposure.mode];
  if (exposure.advertise_address) parts.push(exposure.advertise_address);
  if (exposure.network) parts.push(exposure.network);
  return `${parts.join(' ')}:${exposure.port}`;
}

/* The fabric: one band per network, every machine attached to the bands it
 * holds an address on, and the declared routes between bands. Drawn faintly in
 * the service view and fully in the fabric view. */
function drawFabric(root, graph, layout, prominent) {
  const bands = layout.fabricBands;
  if (!bands.length) return;
  const layer = svgEl('g', { class: prominent ? 'fabric prominent' : 'fabric' }, root);
  const right = layout.bandRight;

  for (const band of bands) {
    const group = svgEl('g', { class: 'fabric-band', 'data-network': band.id }, layer);
    svgEl('rect', {
      class: 'fabric-strip', x: PAD, y: band.y, width: right - PAD, height: FBAND_H,
      rx: 3, fill: band.tint,
    }, group);
    const label = svgEl('text', { x: PAD + 12, y: band.y + 17, class: 'fabric-label' }, group);
    const name = svgEl('tspan', { class: 'fabric-name' }, label);
    name.textContent = band.id;
    const meta = svgEl('tspan', { class: 'fabric-meta', dx: 10 }, label);
    meta.textContent = `${band.kind}  ${band.cidr}`;
    svgText(group, right - 12, band.y + 17, `${band.machines.length} machine(s)`, 'fabric-count', {
      'text-anchor': 'end',
    });
    const title = svgEl('title', {}, group);
    title.textContent = `${band.id}: ${band.machines.length ? band.machines.join(', ') : 'no machines'}`;
  }

  for (const box of layout.boxes) {
    const on = bands.filter((band) => band.machines.includes(box.machine.id));
    on.forEach((band, index) => {
      // Stagger the stubs so a machine on several networks shows several,
      // rather than one line hiding the others.
      const x = box.x + box.w / 2 + (index - (on.length - 1) / 2) * 14;
      svgEl('line', {
        class: 'fabric-stub', x1: x, y1: box.y, x2: x, y2: band.y + FBAND_H, stroke: band.tint,
      }, layer);
    });
  }

  if (!prominent) return;

  const byId = new Map(bands.map((band) => [band.id, band]));
  graph.routes.forEach((route, index) => {
    const from = byId.get(route.from);
    const to = byId.get(route.to);
    if (!from || !to) return;
    const y1 = from.y + FBAND_H / 2;
    const y2 = to.y + FBAND_H / 2;
    const bulge = right + 30 + index * 16;
    const group = svgEl('g', { class: 'fabric-route' }, layer);
    svgEl('path', {
      class: 'fabric-route-path',
      d: `M ${right} ${y1} C ${bulge + 26} ${y1}, ${bulge + 26} ${y2}, ${right} ${y2}`,
    }, group);
    svgText(group, bulge + 34, (y1 + y2) / 2 + 3, `${route.from} → ${route.to}`, 'fabric-route-label');
    const title = svgEl('title', {}, group);
    title.textContent = route.via
      ? `${route.from} → ${route.to}, via ${route.via}`
      : `${route.from} → ${route.to}`;
  });
}

function drawMachines(root, graph, layout, localMap) {
  for (const box of layout.boxes) {
    const machine = box.machine;
    const group = svgEl('g', {
      class: 'machine', 'data-machine': machine.id, tabindex: 0, role: 'button',
      'aria-label': `Select machine ${machine.id}`,
    }, root);
    svgEl('rect', { class: 'machine-box', x: box.x, y: box.y, width: box.w, height: box.h, rx: 4 }, group);

    const header = svgEl('g', { class: 'machine-header', 'data-machine': machine.id }, group);
    svgEl('rect', { x: box.x, y: box.y, width: box.w, height: MHEAD, fill: 'transparent' }, header);
    svgText(header, box.x + MIP, box.y + 24, machine.id, 'machine-id');
    const subnet = machine.docker_subnet ? `  ${machine.docker_subnet}` : '';
    svgText(header, box.x + MIP, box.y + 43, `${machine.execution === 'local' ? 'local' : 'ssh'}  ${machine.management_address}${subnet}`, 'machine-meta');

    const risk = machineRisk(machine);
    if (risk) {
      const marker = svgEl('g', { class: 'risk-marker' }, header);
      svgEl('circle', { cx: box.x + box.w - 18, cy: box.y + 20, r: 7, class: risk.kind }, marker);
      svgText(marker, box.x + box.w - 18, box.y + 23.5, '!', 'risk-symbol', { 'text-anchor': 'middle' });
      const title = svgEl('title', {}, marker);
      title.textContent = risk.text;
    }

    if (box.fabric) {
      box.rows.forEach((row, index) => {
        const y = box.y + MHEAD + 15 + index * ADDRESS_ROW;
        svgEl('rect', {
          class: 'address-row', x: box.x + MIP, y: y - 13, width: groupW, height: ADDRESS_ROW - 3, rx: 2,
        }, group);
        svgEl('rect', {
          x: box.x + MIP + 7, y: y - 8, width: 9, height: 9, rx: 2, fill: tintOf(graph, row.id),
        }, group);
        svgText(group, box.x + MIP + 24, y, row.id, 'address-net');
        svgText(group, box.x + box.w - MIP - 8, y, row.address, 'address-ip', { 'text-anchor': 'end' });
      });
      if (!box.rows.length) {
        svgText(group, box.x + MIP + 4, box.y + MHEAD + 15, 'no network address', 'machine-meta');
      }
      continue;
    }

    for (const placed of box.groups) {
      const groupEl = svgEl('g', {
        class: 'group', 'data-group': placed.id, 'data-machine': machine.id, tabindex: 0, role: 'button',
        'aria-label': `Select group ${placed.id} on ${machine.id}`,
      }, group);
      svgEl('rect', { class: 'group-box', x: box.x + MIP, y: box.y + placed.top, width: groupW, height: placed.height, rx: 3 }, groupEl);
      svgText(groupEl, box.x + MIP + GIP, box.y + placed.top + 15, placed.id, 'group-label');

      for (const rect of placed.rects) {
        const service = rect.service;
        const nodeGroup = svgEl('g', { class: 'node', 'data-id': service.id }, groupEl);
        svgEl('rect', { class: 'node-box', x: rect.x, y: rect.y, width: rect.w, height: rect.h, rx: 3 }, nodeGroup);
        svgEl('rect', {
          class: 'node-stripe', x: rect.x, y: rect.y + 3, width: 3, height: rect.h - 6,
          fill: FAMILY_COLOR[service.family] || FAMILY_COLOR.other, rx: 1.5,
        }, nodeGroup);
        svgText(nodeGroup, rect.x + 14, rect.y + 20, truncate(service.id, 30), 'node-id');
        if (service.subtitle) svgText(nodeGroup, rect.x + 14, rect.y + 37, truncate(service.subtitle, 22), 'node-sub');
        const exposure = exposureLabel(service);
        if (exposure) {
          svgText(nodeGroup, rect.x + rect.w - 12, rect.y + 37, truncate(exposure, 22), 'node-exposure', { 'text-anchor': 'end' });
        }

        const internal = localMap.get(service.id) || [];
        if (internal.length) {
          const badge = svgEl('g', { class: 'local-badge' }, nodeGroup);
          const cx = rect.x + rect.w - 18;
          svgEl('circle', { cx, cy: rect.y + 16, r: 8.5 }, badge);
          const mark = svgEl('text', { x: cx, y: rect.y + 19.5 }, badge);
          mark.textContent = String(internal.length);
          const title = svgEl('title', {}, badge);
          title.textContent = `inside ${machine.id} (not drawn):\n` + internal.join('\n');
        }
        machineGroups.set(service.id, nodeGroup);
      }
    }
  }
}

/* Links that stay inside a machine share its Docker network and are not drawn,
 * but they are real: without a trace, moving a group next to its peer looks
 * like the connection vanished. */
function localLinks(graph) {
  const map = new Map();
  for (const edge of graph.edges) {
    if (edge.transport !== 'docker') continue;
    const pairs = [[edge.consumer, edge.provider], [edge.provider, edge.consumer]];
    for (const [self, other] of pairs) {
      if (!map.has(self)) map.set(self, []);
      const label = `${edge.name} ↔ ${other}`;
      if (!map.get(self).includes(label)) map.get(self).push(label);
    }
  }
  return map;
}

function internalLinks(graph, serviceIds) {
  const wanted = new Set(serviceIds);
  const seen = new Set();
  const rows = [];
  for (const edge of graph.edges) {
    if (edge.transport !== 'docker') continue;
    if (!wanted.has(edge.consumer) && !wanted.has(edge.provider)) continue;
    const key = `${edge.consumer}|${edge.provider}|${edge.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    rows.push(`${edge.consumer} ↔ ${edge.provider} (${edge.name})`);
  }
  return rows;
}

function wireHover(entries, nodes) {
  const clear = () => {
    for (const entry of entries) entry.group.classList.remove('dim', 'hot');
    for (const node of nodes.values()) node.querySelector('.node-box').classList.remove('rel');
  };
  for (const [id, node] of nodes) {
    node.addEventListener('mouseenter', () => {
      for (const entry of entries) {
        const touches = entry.edge.consumer === id || entry.edge.provider === id;
        entry.group.classList.toggle('hot', touches);
        entry.group.classList.toggle('dim', !touches);
      }
      const related = [];
      for (const entry of entries) {
        if (entry.edge.consumer === id) related.push(entry.edge.provider);
        if (entry.edge.provider === id) related.push(entry.edge.consumer);
      }
      for (const other of related) {
        const target = machineGroups.get(other);
        if (target) target.querySelector('.node-box').classList.add('rel');
      }
    });
    node.addEventListener('mouseleave', clear);
  }
}

/* -- dragging -------------------------------------------------------- */

function svgPoint(event) {
  const svg = $('#canvas');
  const rect = svg.getBoundingClientRect();
  const scaleX = rect.width ? currentLayout.width / rect.width : 1;
  const scaleY = rect.height ? currentLayout.height / rect.height : 1;
  return { x: (event.clientX - rect.left) * scaleX, y: (event.clientY - rect.top) * scaleY };
}

function boxAt(point) {
  return currentLayout.boxes.find((box) => (
    point.x >= box.x && point.x <= box.x + box.w && point.y >= box.y && point.y <= box.y + box.h
  )) || null;
}

function inMachineHeader(machineId, event) {
  const box = currentLayout.boxes.find((item) => item.machine.id === machineId);
  return Boolean(box) && svgPoint(event).y <= box.y + MHEAD;
}

function shiftedCenters(layout, delta, serviceIds) {
  const centers = new Map();
  for (const [id, point] of layout.centers) {
    centers.set(id, serviceIds.has(id) ? { x: point.x + delta.x, y: point.y + delta.y } : point);
  }
  return centers;
}

function clearDropTarget() {
  for (const node of document.querySelectorAll('g.machine.drop-target')) node.classList.remove('drop-target');
  drag.target = null;
}

function commitManualLayout() {
  const positions = {};
  for (const box of currentLayout.boxes) positions[box.machine.id] = { x: Math.round(box.x), y: Math.round(box.y) };
  const stored = readView(currentPath);
  writeView(currentPath, { ...stored, [currentView]: { mode: 'manual', positions } });
}

function wireDrag() {
  const svg = $('#canvas');

  svg.addEventListener('pointerdown', (event) => {
    suppressClick = false;
    if (event.button !== 0 || !currentLayout) return;
    const groupEl = event.target.closest ? event.target.closest('g.group') : null;
    if (groupEl && session.id) {
      const box = currentLayout.boxes.find((item) => item.machine.id === groupEl.dataset.machine);
      const point = svgPoint(event);
      drag.kind = 'group';
      drag.id = groupEl.dataset.group;
      drag.element = groupEl;
      drag.box = box;
      drag.startX = point.x;
      drag.startY = point.y;
      drag.dx = 0;
      drag.dy = 0;
      const placed = box.groups.find((item) => item.id === drag.id);
      drag.serviceIds = new Set((placed ? placed.rects : []).map((rect) => rect.service.id));
      groupEl.classList.add('dragging');
      try { svg.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
      event.preventDefault();
      return;
    }

    const machineEl = event.target.closest ? event.target.closest('g.machine') : null;
    if (machineEl && inMachineHeader(machineEl.dataset.machine, event)) {
      const box = currentLayout.boxes.find((item) => item.machine.id === machineEl.dataset.machine);
      const point = svgPoint(event);
      drag.kind = 'machine';
      drag.id = box.machine.id;
      drag.element = machineEl;
      drag.box = box;
      drag.startX = point.x;
      drag.startY = point.y;
      drag.origX = box.x;
      drag.origY = box.y;
      drag.dx = 0;
      drag.dy = 0;
      drag.serviceIds = new Set(box.groups.flatMap((item) => item.rects.map((rect) => rect.service.id)));
      svg.classList.add('dragging');
      try { svg.setPointerCapture(event.pointerId); } catch (_) { /* ignore */ }
      event.preventDefault();
    }
  });

  svg.addEventListener('pointermove', (event) => {
    if (!drag.kind) return;
    const point = svgPoint(event);
    drag.dx = point.x - drag.startX;
    drag.dy = point.y - drag.startY;

    if (drag.kind === 'machine') {
      drag.element.setAttribute('transform', `translate(${drag.dx} ${drag.dy})`);
      placeEdges(currentEntries, shiftedCenters(currentLayout, { x: drag.dx, y: drag.dy }, drag.serviceIds));
      return;
    }

    drag.element.setAttribute('transform', `translate(${drag.dx} ${drag.dy})`);
    const target = boxAt(point);
    clearDropTarget();
    if (target && target.machine.id !== drag.element.dataset.machine) {
      drag.target = target.machine.id;
      const node = [...document.querySelectorAll('g.machine')].find((item) => item.dataset.machine === drag.target);
      if (node) node.classList.add('drop-target');
    }
  });

  const finish = (event) => {
    if (!drag.kind) return;
    const moved = Math.abs(drag.dx) + Math.abs(drag.dy) > 2;
    const kind = drag.kind;
    const id = drag.id;
    const box = drag.box;
    const element = drag.element;
    const target = drag.target;
    const dx = drag.dx;
    const dy = drag.dy;
    const originX = drag.origX;
    const originY = drag.origY;

    drag.kind = null;
    drag.id = null;
    drag.target = null;
    svg.classList.remove('dragging');
    element.classList.remove('dragging');
    clearDropTarget();
    try { svg.releasePointerCapture(event.pointerId); } catch (_) { /* ignore */ }

    if (!moved) {
      element.removeAttribute('transform');
      if (kind === 'machine') placeEdges(currentEntries, currentLayout.centers);
      return;
    }
    suppressClick = true;
    // Browsers normally emit the synthetic click immediately after pointerup.
    // Clear the guard on the next task as well, so a cancelled/non-clicking
    // pointer sequence can never swallow a later, intentional click.
    setTimeout(() => { suppressClick = false; }, 0);

    if (kind === 'machine') {
      box.x = originX + dx;
      box.y = originY + dy;
      commitManualLayout();
      if (currentGraph) render(currentGraph);
      return;
    }

    element.removeAttribute('transform');
    if (target && target !== element.dataset.machine) {
      applyOp('move_group', { group: id, machine: target });
    } else if (currentGraph) {
      render(currentGraph);
    }
  };

  svg.addEventListener('pointerup', finish);
  svg.addEventListener('pointercancel', finish);

  svg.addEventListener('click', (event) => {
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    if (!currentGraph) return;
    const groupEl = event.target.closest ? event.target.closest('g.group') : null;
    if (groupEl) { select({ kind: 'group', id: groupEl.dataset.group }); return; }
    const machineEl = event.target.closest ? event.target.closest('g.machine') : null;
    if (machineEl) { select({ kind: 'machine', id: machineEl.dataset.machine }); return; }
    select(null);
  });

  svg.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const groupEl = event.target.closest ? event.target.closest('g.group') : null;
    const machineEl = event.target.closest ? event.target.closest('g.machine') : null;
    if (!groupEl && !machineEl) return;
    event.preventDefault();
    if (groupEl) select({ kind: 'group', id: groupEl.dataset.group });
    else select({ kind: 'machine', id: machineEl.dataset.machine });
  });
}

/* -- render ---------------------------------------------------------- */

function render(graph) {
  renderMasthead(graph);
  renderAvailability(graph);
  renderNetworks(graph);
  renderLegend(graph);

  const stored = readView(currentPath);
  currentView = stored.view || 'services';
  currentZoom = Number.isFinite(stored.zoom) ? clampZoom(stored.zoom) : 1;
  const fabric = currentView === 'fabric';
  const saved = layoutOf(stored, currentView);
  const mode = saved.mode || 'columns';

  const layout = layoutFor(
    graph, fabric, mode === 'manual' ? 'columns' : mode, mode === 'manual' ? saved.positions : null,
  );
  currentLayout = layout;
  currentGraph = graph;
  renderToolbar(mode);

  const canvas = $('#canvas');
  canvas.innerHTML = '';
  canvas.setAttribute('viewBox', `0 0 ${layout.width} ${layout.height}`);

  drawBands(canvas, layout.bands);
  drawFabric(canvas, graph, layout, fabric);
  // In the fabric view the service layer is simply absent: that is the point.
  currentEntries = fabric ? [] : drawEdges(canvas, graph, layout);
  machineGroups = new Map();
  drawMachines(canvas, graph, layout, fabric ? new Map() : localLinks(graph));
  if (!fabric) wireHover(currentEntries, machineGroups);
  applyCanvasScale();
  applyTopologyFilter();

  renderSavePanel(graph);
  renderSelection(graph);
  renderStructure(graph);
  renderWebEdge(graph);
  renderPalette(graph);
}

function renderToolbar(mode) {
  for (const button of document.querySelectorAll('.layouts button')) {
    button.setAttribute('aria-pressed', button.dataset.layout === mode ? 'true' : 'false');
  }
  for (const button of document.querySelectorAll('.views button')) {
    button.setAttribute('aria-pressed', button.dataset.view === currentView ? 'true' : 'false');
  }
  $('#layout-hint').textContent = currentView === 'fabric'
    ? (session.readOnly
      ? 'The fabric: which machine sits on which network.'
      : 'The fabric: machines, their networks, and the routes between them.')
    : (session.readOnly
      ? 'Drag a machine by its header to move it. Positions are view-only.'
      : 'Drag a machine header to move it; drag a group onto another machine to re-place it.');
}

/* -- boot ------------------------------------------------------------ */

let booted = false;

function boot() {
  if (booted) return;
  booted = true;
  $('#path-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const value = $('#path').value.trim();
    if (value) openInventory(value);
  });

  for (const button of document.querySelectorAll('.layouts button')) {
    button.addEventListener('click', () => {
      const stored = readView(currentPath);
      writeView(currentPath, { ...stored, [currentView]: { mode: button.dataset.layout, positions: {} } });
      if (currentGraph) render(currentGraph);
    });
  }

  for (const button of document.querySelectorAll('.views button')) {
    button.addEventListener('click', () => {
      const stored = readView(currentPath);
      writeView(currentPath, { ...stored, view: button.dataset.view });
      currentView = button.dataset.view;
      if (currentGraph) render(currentGraph);
    });
  }

  $('#undo').addEventListener('click', undo);
  $('#reset').addEventListener('click', resetDraft);
  $('#zoom-out').addEventListener('click', () => setZoom(currentZoom - 0.1));
  $('#zoom-in').addEventListener('click', () => setZoom(currentZoom + 0.1));
  $('#zoom-fit').addEventListener('click', fitTopology);
  $('#topology-filter').addEventListener('input', (event) => {
    filterQuery = event.target.value;
    applyTopologyFilter();
  });

  wireDrag();
  wireCollapsiblePanels();
  loadInventoryList();
  renderDraftBar();

  const hash = new URLSearchParams(location.hash.replace(/^#/, ''));
  const path = hash.get('path');
  if (path) {
    $('#path').value = path;
    openInventory(path);
  }
}

document.addEventListener('DOMContentLoaded', boot);
