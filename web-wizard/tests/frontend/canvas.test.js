'use strict';

/* Headless checks for canvas.js: layouts, machine dragging (view state),
 * group dragging (a real edit), rejection, undo and persistence.
 *
 * Driven by tests/test_frontend.py, which builds the fixtures from the real
 * draft session. Needs `jsdom`; run `npm install` in this directory (or point
 * NODE_PATH at an existing install) to enable it.
 *
 *   node canvas.test.js <staticDir> <fixtures.json>
 */

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

const [staticDir, fixturesPath] = process.argv.slice(2);
if (!staticDir || !fixturesPath) {
  console.error('usage: node canvas.test.js <staticDir> <fixtures.json>');
  process.exit(2);
}

const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(staticDir, 'canvas.js'), 'utf8');
const fixtures = JSON.parse(fs.readFileSync(fixturesPath, 'utf8'));
const graph = fixtures.graph;
const INVENTORY = '/fixture.json';
const SESSION = 'test-session';

const failures = [];
const check = (label, ok, detail) => {
  if (ok) {
    console.log('  ok    ' + label);
  } else {
    console.log('  FAIL  ' + label + (detail ? ' — ' + detail : ''));
    failures.push(label);
  }
};
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const SUGGESTED = '/tmp/fixture.wizard-20260101-000000.json';
const draft = (changed, sections, canUndo, stage, savedPath = null) => ({
  path: INVENTORY, changed, sections, can_undo: canUndo, stage, written: false,
  saved_path: savedPath, suggested_path: SUGGESTED,
});

async function main() {
  const url = `http://127.0.0.1:9/?token=x#path=${INVENTORY}`;
  const dom = new JSDOM(html, { runScripts: 'outside-only', url });
  const { window } = dom;
  const doc = window.document;

  const json = (obj) => ({ ok: true, status: 200, json: async () => obj });
  const calls = [];
  window.fetch = async (rawUrl, options = {}) => {
    const method = (options.method || 'GET').toUpperCase();
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ method, url: rawUrl, body });

    if (rawUrl.includes('/api/inventories')) {
      return json({ inventories: [{ name: 'fixture.json', path: INVENTORY }] });
    }
    if (rawUrl.includes('/api/graph')) return json(graph);
    if (method === 'POST' && rawUrl.endsWith('/api/session')) {
      return json({ session_id: SESSION, ok: true, error: null, draft: draft(false, [], false, 'loaded'), graph });
    }
    if (method === 'POST' && rawUrl.endsWith('/op')) {
      const expected = fixtures.moveOperation;
      const matches = body.kind === expected.kind
        && JSON.stringify(body.params) === JSON.stringify(expected.params);
      if (matches) {
        return json({ ok: true, error: null, draft: draft(true, ['placement'], true, 'placement'), graph: fixtures.movedGraph });
      }
      // A rejected change leaves the *current* draft as it was.
      return json({
        ok: false,
        error: 'placement.apps must name a declared machine',
        draft: draft(true, ['placement'], true, 'placement'),
        graph: fixtures.movedGraph,
      });
    }
    if (method === 'POST' && rawUrl.endsWith('/undo')) {
      return json({ ok: true, error: null, draft: draft(false, [], false, 'undo'), graph });
    }
    if (method === 'POST' && rawUrl.endsWith('/save')) {
      return json({
        ok: true, error: null, saved: body.path,
        draft: draft(false, [], false, 'loaded', body.path), graph,
      });
    }
    throw new Error('unexpected fetch ' + method + ' ' + rawUrl);
  };

  const errors = [];
  window.addEventListener('error', (event) => errors.push(event.message));
  window.eval(script);
  doc.dispatchEvent(new window.Event('DOMContentLoaded'));
  await delay(200);

  const svg = doc.getElementById('canvas');
  const machinesOf = () => [...doc.querySelectorAll('g.machine')];
  const machineRect = (id) => machinesOf().find((g) => g.dataset.machine === id).querySelector('.machine-box');
  const groupRect = (id) => doc.querySelector(`g.group[data-group="${id}"] .group-box`);
  const rectCenter = (rect) => [
    Number(rect.getAttribute('x')) + Number(rect.getAttribute('width')) / 2,
    Number(rect.getAttribute('y')) + Number(rect.getAttribute('height')) / 2,
  ];
  const rectTopLeft = (rect) => [Number(rect.getAttribute('x')), Number(rect.getAttribute('y'))];
  const distinctRows = () => new Set(machinesOf().map((g) => Number(g.querySelector('.machine-box').getAttribute('y')))).size;
  const bandNames = () => [...doc.querySelectorAll('.band-label')].map((t) => t.textContent);
  const click = (mode) => doc.querySelector(`.layouts button[data-layout="${mode}"]`)
    .dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  const pointer = (type, x, y) => new window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: x, clientY: y });
  const draftBar = () => doc.getElementById('draft-state').textContent;
  const opsSent = () => calls.filter((call) => call.method === 'POST' && call.url.endsWith('/op')).map((call) => call.body);
  const savesSent = () => calls.filter((call) => call.method === 'POST' && call.url.endsWith('/save')).map((call) => call.body);
  const stored = () => JSON.parse(window.localStorage.getItem('webwizard.layout.v2') || '{}')[INVENTORY];

  const crossHost = graph.edges.filter((edge) => edge.crosses_host).length;

  // -- rendering ------------------------------------------------------
  check('renders every machine', doc.querySelectorAll('.machine-box').length === graph.machines.length);
  check('renders every service', doc.querySelectorAll('.node-box').length === graph.services.length);
  check('draws only cross-host edges', doc.querySelectorAll('.edge').length === crossHost);
  check('groups are wrapped for interaction',
    doc.querySelectorAll('g.group').length === graph.groups.length);
  check('machines and groups are keyboard-selectable',
    [...doc.querySelectorAll('g.machine, g.group')].every((node) => (
      node.getAttribute('tabindex') === '0' && node.getAttribute('role') === 'button'
    )));

  // -- finding and zooming -----------------------------------------------
  const filter = doc.getElementById('topology-filter');
  filter.value = 'validator01';
  filter.dispatchEvent(new window.Event('input', { bubbles: true }));
  check('filter highlights matching services',
    doc.querySelectorAll('#canvas .node.search-hit').length >= 1);
  check('filter quiets unrelated services',
    doc.querySelectorAll('#canvas .node.search-miss').length >= 1);
  check('filter reports a match count', doc.getElementById('filter-count').textContent.includes('match'));
  filter.value = '';
  filter.dispatchEvent(new window.Event('input', { bubbles: true }));

  const initialWidth = Number(svg.getAttribute('width'));
  doc.getElementById('zoom-out').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  check('zoom changes the rendered canvas size', Number(svg.getAttribute('width')) < initialWidth);
  check('zoom level is visible', doc.getElementById('zoom-value').textContent === '90%');
  check('zoom is remembered per inventory', (stored() || {}).zoom === 0.9);

  // -- layouts --------------------------------------------------------
  check('columns: single row', distinctRows() === 1);
  click('grid');
  check('grid: wraps into several rows', distinctRows() > 1);
  click('function');
  check('function: bands per dominant family', bandNames().length >= 2, bandNames().join(','));
  click('network');
  check('network: at least one band', bandNames().length >= 1);
  click('columns');

  // -- machine drag is view state only --------------------------------
  // Machines are dragged by their header band, so grab near the top-left.
  const appsOrigin = rectTopLeft(machineRect('apps'));
  const callsBeforeDrag = calls.length;
  doc.querySelector('g.machine[data-machine="apps"] .machine-header rect')
    .dispatchEvent(pointer('pointerdown', appsOrigin[0] + 20, appsOrigin[1] + 20));
  svg.dispatchEvent(pointer('pointermove', appsOrigin[0] + 200, appsOrigin[1] + 80));
  check('machine drag: live transform',
    machinesOf().find((g) => g.dataset.machine === 'apps').getAttribute('transform') === 'translate(180 60)');
  svg.dispatchEvent(pointer('pointerup', appsOrigin[0] + 200, appsOrigin[1] + 80));
  const appsMoved = rectTopLeft(machineRect('apps'));
  check('machine drag: commits the pointer delta',
    appsMoved[0] - appsOrigin[0] === 180 && appsMoved[1] - appsOrigin[1] === 60,
    `delta=${appsMoved[0] - appsOrigin[0]},${appsMoved[1] - appsOrigin[1]}`);
  check('machine drag: sends no operation', calls.length === callsBeforeDrag);

  // -- group drag is a real edit --------------------------------------
  const from = rectCenter(groupRect('blockchain-b'));
  const to = rectCenter(machineRect('apps'));
  groupRect('blockchain-b').dispatchEvent(pointer('pointerdown', from[0], from[1]));
  svg.dispatchEvent(pointer('pointermove', to[0], to[1]));
  check('group drag: highlights the target machine',
    machinesOf().find((g) => g.dataset.machine === 'apps').classList.contains('drop-target'));
  svg.dispatchEvent(pointer('pointerup', to[0], to[1]));
  await delay(80);

  const ops = opsSent();
  check('group drag: sends exactly one operation', ops.length === 1, JSON.stringify(ops));
  check('group drag: sends the expected move_group',
    ops[0] && ops[0].kind === 'move_group'
      && ops[0].params.group === 'blockchain-b' && ops[0].params.machine === 'apps',
    JSON.stringify(ops[0]));
  check('group drag: the group is re-drawn on the target machine',
    Boolean(doc.querySelector('g.machine[data-machine="apps"] g.group[data-group="blockchain-b"]')));
  check('group drag: the old machine no longer holds it',
    !doc.querySelector('g.machine[data-machine="blockchain-b"] g.group[data-group="blockchain-b"]'));
  check('draft bar reports the changed section', draftBar().includes('placement'), draftBar());
  check('undo becomes available', doc.getElementById('undo').disabled === false);

  // -- a rejected edit keeps everything ---------------------------------
  const rejectedFrom = rectCenter(groupRect('storage-1'));
  const rejectedTo = rectCenter(machineRect('apps'));
  groupRect('storage-1').dispatchEvent(pointer('pointerdown', rejectedFrom[0], rejectedFrom[1]));
  svg.dispatchEvent(pointer('pointermove', rejectedTo[0], rejectedTo[1]));
  svg.dispatchEvent(pointer('pointerup', rejectedTo[0], rejectedTo[1]));
  await delay(80);
  check('rejected edit: shows the reason',
    doc.getElementById('draft-error').textContent.includes('declared machine'),
    doc.getElementById('draft-error').textContent);
  check('rejected edit: keeps the draft bar state', draftBar().includes('placement'), draftBar());
  check('rejected edit: leaves the groups where they were',
    Boolean(doc.querySelector('g.machine[data-machine="storage-1"] g.group[data-group="storage-1"]')));

  // -- undo -------------------------------------------------------------
  doc.getElementById('undo').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await delay(80);
  check('undo: restores the original placement',
    Boolean(doc.querySelector('g.machine[data-machine="blockchain-b"] g.group[data-group="blockchain-b"]')));
  check('undo: draft bar goes back to clean', draftBar().includes('matches the file'), draftBar());

  // -- links that stay inside a machine -----------------------------------
  check('counts the links kept inside a machine',
    doc.querySelectorAll('#canvas .local-badge').length >= 1,
    String(doc.querySelectorAll('#canvas .local-badge').length));
  check('a badge explains its links in a tooltip',
    (doc.querySelector('#canvas .local-badge title') || {}).textContent
      && doc.querySelector('#canvas .local-badge title').textContent.includes('not drawn'));
  check('legend explains the local-link badge',
    doc.getElementById('legend').textContent.includes('counted, not drawn'));

  // -- failure domains on the canvas -------------------------------------
  check('marks the machines whose loss breaks quorum',
    doc.querySelectorAll('#canvas .risk-quorum').length >= 1,
    String(doc.querySelectorAll('#canvas .risk-quorum').length));
  check('legend explains the risk marker',
    doc.getElementById('legend').textContent.includes('breaks consensus'));

  // -- palette -------------------------------------------------------------
  const palette = doc.getElementById('palette-panel');
  check('palette panel is available', palette.hidden === false);
  const paletteTypes = [...palette.querySelectorAll('select option')].map((option) => option.value);
  check('palette offers every addable component',
    ['machine', 'validator_group', 'observer_group', 'rpc_node', 'storage_peer', 'proxy']
      .every((type) => paletteTypes.includes(type)),
    paletteTypes.join(','));
  check('palette has an identifier field', Boolean(palette.querySelector('input[type="text"]')));

  // -- web edge ------------------------------------------------------------
  const edge = doc.getElementById('webedge-panel');
  check('web edge panel is available', edge.hidden === false);
  check('web edge lists both proxies',
    edge.textContent.includes('resolver-public') && edge.textContent.includes('apps-private'));
  check('web edge shows the authored routes', edge.textContent.includes('/admin/'), edge.textContent.slice(0, 120));

  // -- structure -----------------------------------------------------------
  const structure = doc.getElementById('structure-panel');
  check('structure shows the availability objectives',
    structure.textContent.includes('survive any machine loss'));
  check('structure marks the unmet objectives',
    structure.querySelectorAll('.objective .unmet').length >= 1);
  check('structure offers RPC bindings', structure.textContent.includes('RPC bindings'));

  // -- saving --------------------------------------------------------------
  const save = doc.getElementById('save-panel');
  check('save panel is available', save.hidden === false);
  check('save panel prefills a brand new file name',
    (save.querySelector('input') || {}).value === SUGGESTED,
    (save.querySelector('input') || {}).value);
  check('save panel promises never to overwrite',
    save.textContent.includes('never modified or overwritten'));
  save.querySelector('button.mini').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await delay(80);
  check('save posts the destination', savesSent().length === 1, JSON.stringify(savesSent()));
  check('save confirms the written path',
    doc.getElementById('save-panel').textContent.includes('Written to'),
    doc.getElementById('save-panel').textContent.slice(0, 120));

  // -- selection -----------------------------------------------------------
  groupRect('storage-1').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  const selection = doc.getElementById('selection-panel');
  check('clicking a group opens its inspector',
    selection.hidden === false && selection.textContent.includes('storage-1'));
  check('the inspector offers a move-to control',
    selection.textContent.includes('Move to machine'));
  check('the inspector states the links kept inside the machine',
    selection.textContent.includes('stay inside'), selection.textContent.slice(0, 160));

  // -- the network fabric --------------------------------------------------
  const attachments = graph.networks.reduce((total, network) => total + network.machine_ids.length, 0);
  check('networks are drawn as bands',
    doc.querySelectorAll('#canvas .fabric-band').length === graph.networks.length,
    String(doc.querySelectorAll('#canvas .fabric-band').length));
  check('every machine is attached to every network it holds',
    doc.querySelectorAll('#canvas .fabric-stub').length === attachments,
    `${doc.querySelectorAll('#canvas .fabric-stub').length} of ${attachments}`);
  check('the fabric stays quiet while services are shown',
    !doc.querySelector('#canvas .fabric').classList.contains('prominent'));
  check('service edges are drawn in the service view',
    doc.querySelectorAll('#canvas .edge').length === crossHost);

  const viewButton = (name) => doc.querySelector(`.views button[data-view="${name}"]`);
  viewButton('fabric').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await delay(60);

  check('fabric view: the network layer comes forward',
    Boolean(doc.querySelector('#canvas .fabric.prominent')));
  check('fabric view: no service edges', doc.querySelectorAll('#canvas .edge').length === 0);
  check('fabric view: no service cards', doc.querySelectorAll('#canvas .node-box').length === 0);
  check('fabric view: every machine lists its addresses',
    doc.querySelectorAll('#canvas .address-row').length === attachments,
    `${doc.querySelectorAll('#canvas .address-row').length} of ${attachments}`);
  check('fabric view: the switch is remembered', (stored() || {}).view === 'fabric');
  check('fabric view: declared routes are drawn between bands',
    doc.querySelectorAll('#canvas .fabric-route').length === graph.routes.length,
    `${doc.querySelectorAll('#canvas .fabric-route').length} of ${graph.routes.length}`);
  check('the rail lists the declared routes',
    doc.getElementById('routes').textContent.includes(`${graph.routes[0].from} → ${graph.routes[0].to}`),
    doc.getElementById('routes').textContent.slice(0, 120));

  viewButton('services').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  await delay(60);
  check('switching back restores the service cards',
    doc.querySelectorAll('#canvas .node-box').length === graph.services.length);

  check('no runtime errors', errors.length === 0, errors.join(' | '));

  if (failures.length) {
    console.error(`\n${failures.length} frontend check(s) failed`);
    process.exit(1);
  }
  console.log('\nall frontend checks passed');
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
