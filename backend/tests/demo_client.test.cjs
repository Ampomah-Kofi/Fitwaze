// Interaction regressions for the inline client, using a small DOM stub.
// These exercise state and request handling, not browser layout or Leaflet.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../app/static/demo.html'), 'utf8');
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];

function setup(fetch = async () => { throw Error('Unexpected network request'); }) {
  const timers = new Map();
  let timerId = 0;
  function node() {
    const attrs = {}, classes = new Set();
    return {dataset: {}, style: {}, hidden: false, value: '', innerHTML: '', textContent: '',
      classList: {add: value => classes.add(value), remove: value => classes.delete(value), contains: value => classes.has(value)},
      setAttribute: (key, value) => attrs[key] = value, removeAttribute: key => delete attrs[key],
      getAttribute: key => attrs[key], querySelectorAll: () => []};
  }
  const nodes = new Map([...html.matchAll(/id="([^"]+)"/g)].map(([, id]) => [id, node()]));
  const root = node();
  const tabs = ['today', 'route', 'progress'].map(screen => ({...node(), dataset: {screen}}));
  const context = vm.createContext({
    document: {documentElement: root, getElementById: id => {
      assert(nodes.has(id), `Missing element: ${id}`); return nodes.get(id);
    }, querySelectorAll: selector => selector === '#tabs button' ? tabs : [], addEventListener() {}},
    window: {location: {pathname: '/mobile'}, addEventListener() {}}, navigator: {},
    setTimeout: () => 1, clearTimeout() {},
    setInterval: callback => {timers.set(++timerId, callback); return timerId;},
    clearInterval: id => timers.delete(id), fetch, console,
  });
  vm.runInContext(script, context);
  return {nodes, root, timers, run: code => vm.runInContext(code, context)};
}
const response = body => ({ok: true, text: async () => JSON.stringify(body)});
const seed = `recommendation = {id: 'recommendation-1', activity_type: 'walk', duration_minutes: 30};
              startPoint = {latitude: 40.7128, longitude: -74.006};`;

test('main interface has accessible mobile map/list controls', () => {
  const app = setup();
  app.nodes.get('btn-route-list').onclick();
  assert.equal(app.nodes.get('screen-route').dataset.panel, 'routes');
  assert.equal(app.nodes.get('btn-route-list').getAttribute('aria-pressed'), 'true');
  app.nodes.get('btn-route-map').onclick();
  assert.equal(app.nodes.get('screen-route').dataset.panel, 'map');
});

test('changing location clears previously offered candidates', () => {
  const app = setup();
  app.run(seed + `offeredContext = {options: [{label: 'small_loop'}]}; setStartPoint(41, -73, null, 'Start');`);
  assert.equal(app.run('offeredContext'), null);
  assert.match(app.nodes.get('options-out').innerHTML, /fresh route/);
});

test('a late options response cannot restore routes for an old location', async () => {
  let resolve;
  const pending = new Promise(r => resolve = r);
  const app = setup(() => pending);
  app.run(seed);
  const request = app.nodes.get('btn-options').onclick();
  app.run(`setStartPoint(41, -73, null, 'New start');`);
  resolve(response({options: [], provider: 'mock'}));
  await request;
  assert.equal(app.run('offeredContext'), null);
  assert.equal(app.nodes.get('screen-route').dataset.panel, undefined);
});

test('selection uses the displayed revision and prevents duplicate selection', async () => {
  let resolve;
  const pending = new Promise(r => resolve = r);
  const calls = [];
  const app = setup((url, options) => {calls.push({url, body: JSON.parse(options.body)}); return pending;});
  app.run(seed + `offeredContext = {activity_recommendation_id: 'recommendation-1', latitude: 40.7128,
     longitude: -74.006, options: [{label: 'small_loop', candidate_revision: 'revision-1'}]};`);
  const selection = app.run(`selectRoute('small_loop')`);
  await app.run(`selectRoute('small_loop')`);
  app.run(`setStartPoint(42, -72, null, 'Changed start');`);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].body.candidate_revision, 'revision-1');
  assert.equal(app.run('startPoint.latitude'), 40.7128);
  resolve(response({id: 'session-1', status: 'selected', distance_m: 1000, estimated_minutes: 30}));
  await selection;
  assert.equal(app.run('offeredContext'), null);
  assert.equal(app.nodes.get('session-card').hidden, false);
});

test('unknown steps are displayed as unverified, never as a percentage', async () => {
  const app = setup(async () => response({provider: 'ors', options: [{
    label: 'small_loop', candidate_revision: 'revision-1', distance_m: 1000, estimated_minutes: 15,
    score: 50, explanation: 'Unverified', score_breakdown: {step_free: 0.5}, unverified: ['stairs'],
  }]}));
  app.run(seed);
  await app.nodes.get('btn-options').onclick();
  assert.match(app.nodes.get('options-out').innerHTML, /Steps unverified/);
  assert.doesNotMatch(app.nodes.get('options-out').innerHTML, /% step-free/);
});

test('simulation follows the route and completes without writing real activity', async () => {
  const requests = [];
  const app = setup(async url => {requests.push(url); throw Error('Simulation must stay local');});
  app.run(seed + `offeredContext = {activity_recommendation_id: 'recommendation-1', latitude: 40.7128,
    longitude: -74.006, options: [{label: 'small_loop', distance_m: 1000, estimated_minutes: 12,
    geometry: [[40.7128, -74.006], [40.714, -74.005], [40.7128, -74.006]]}]};`);
  await app.run(`selectRoute('small_loop', true)`);
  assert.equal(app.run('session.simulated'), true);
  app.run(`map = {panTo(point) {globalThis.lastPan = point;}};
    L = {polyline() {return {addTo() {return this;}, addLatLng() {}, remove() {}};},
         circleMarker() {return {addTo() {return this;}, setLatLng() {}, remove() {}};}};`);
  app.nodes.get('btn-track').onclick();
  assert(app.nodes.get('screen-route').classList.contains('is-journey'));
  assert.equal(app.nodes.get('screen-route').dataset.panel, 'map');
  for (let tick = 0; tick < 81; tick++) {
    for (const callback of [...app.timers.values()]) callback();
  }
  assert.equal(app.timers.size, 0);
  assert(Math.abs(app.run('lastFix[0]') - 40.7128) < 0.000001);
  assert(Math.abs(app.run('lastFix[1]') + 74.006) < 0.000001);
  assert(Math.abs(app.run('lastPan[0]') - 40.7128) < 0.000001);
  assert(Math.abs(app.run('travelledMetres') - 1000) < 0.001);
  await app.run(`updateSession('completed')`);
  assert.equal(app.run('simulatedSessions.length'), 1);
  assert.match(app.nodes.get('simulation-progress').innerHTML, /separate from your real/);
  assert.equal(requests.length, 0);
});

test('starting GPS tracking opens the map and keeps session controls in journey view', () => {
  const app = setup();
  app.run(seed + `session = {status: 'selected'};
    window.isSecureContext = true;
    navigator.geolocation = {watchPosition() {return 17;}, clearWatch() {}};
    startTracking();`);
  assert.equal(app.nodes.get('screen-route').dataset.panel, 'map');
  assert(app.nodes.get('screen-route').classList.contains('is-journey'));
  assert.equal(app.nodes.get('track-out').hidden, false);
  assert.equal(app.nodes.get('btn-track').textContent, 'Stop tracking');
});

test('recommendation route button opens the start-point panel at the top', async () => {
  const app = setup(async () => response({id: 'rec-1', activity_type: 'walk', duration_minutes: 12,
    rationale: 'Short session', disclaimer: 'Wellness guidance'}));
  await app.nodes.get('btn-recommend').onclick();
  app.run(`showRoutePanel('routes')`);
  app.nodes.get('app-main').scrollTop = 600;
  app.nodes.get('btn-goroute').onclick();
  assert.equal(app.nodes.get('screen-route').dataset.panel, 'map');
  assert(app.nodes.get('screen-route').classList.contains('active'));
  assert.equal(app.nodes.get('app-main').scrollTop, 0);
  assert.match(app.nodes.get('start-state').textContent, /Choose a starting point/);
});

test('route failures remain visible and the Find button can be retried', async () => {
  const app = setup(async () => ({ok: false, status: 503, text: async () => JSON.stringify({detail: 'Routing unavailable'})}));
  app.run(seed);
  await app.nodes.get('btn-options').onclick();
  assert.equal(app.nodes.get('route-error').hidden, false);
  assert.match(app.nodes.get('route-error').textContent, /Routing unavailable/);
  assert.equal(app.nodes.get('btn-options').disabled, false);
});

test('route cards do not try to animate a hidden map', () => {
  const app = setup();
  app.run(`map = {flyToBounds() {throw Error('Hidden map animation');}};
    optionLines = [{setStyle() {}, getBounds() {return {pad() {return {};}};}}];
    showScreen('route'); showRoutePanel('routes'); focusOption(0);`);
  assert.equal(app.run('focusedOptionIndex'), 0);
});

test('concurrent expired-token requests share one refresh and retry with the new token', async () => {
  let refreshes = 0;
  const app = setup(async (url, options) => {
    if (url === '/auth/refresh') {
      refreshes++;
      return {ok: true, json: async () => ({access_token: 'fresh-token'})};
    }
    if (options.headers.Authorization === 'Bearer expired-token') return {status: 401, ok: false};
    assert.equal(options.headers.Authorization, 'Bearer fresh-token');
    return response({ok: true});
  });
  app.run(`accessToken = 'expired-token'`);
  await Promise.all([app.run(`api('/route/options', {method: 'POST', body: {}})`), app.run(`api('/progress')`)]);
  assert.equal(refreshes, 1);
});

test('expired refresh session returns to sign-in while preserving the selected route', async () => {
  const app = setup(async () => ({status: 401, ok: false}));
  app.run(`accessToken = 'expired-token'; session = {id: 'keep-this-route', status: 'selected'};`);
  await assert.rejects(app.run(`api('/progress')`), /renew your session/);
  assert(app.nodes.get('screen-auth').classList.contains('active'));
  assert.equal(app.run('session.id'), 'keep-this-route');
});

test('finding a route near me starts from saved home and fetches routes straight away', async () => {
  const requests = [];
  const app = setup(async (url, options) => {
    requests.push({url, body: options && options.body ? JSON.parse(options.body) : null});
    if (url === '/activity/recommendation') return response({id: 'rec-1', activity_type: 'walk',
      duration_minutes: 12, rationale: 'Short session', disclaimer: 'Wellness guidance'});
    return response({options: [], provider: 'mock', excluded: []});
  });
  app.run(`homePoint = {latitude: 5.6037, longitude: -0.187};`);
  await app.nodes.get('btn-recommend').onclick();
  app.nodes.get('btn-goroute').onclick();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(app.run('JSON.stringify(startPoint)'), JSON.stringify({latitude: 5.6037, longitude: -0.187}));
  const routeRequest = requests.find(item => item.url === '/route/options');
  assert(routeRequest, 'routes were requested automatically');
  assert.equal(routeRequest.body.latitude, 5.6037);
  assert.equal(app.nodes.get('btn-save-home').hidden, true, 'already home, nothing to save');
});

test('a new start point can be saved as home', () => {
  const app = setup();
  app.run(`setStartPoint(5.61, -0.2, 20, 'Your location');`);
  assert.equal(app.nodes.get('btn-save-home').hidden, false);
  assert.equal(app.nodes.get('btn-home').hidden, true);
});

test('arriving back at the start after most of the route says so', () => {
  const app = setup();
  app.nodes.get('back-home').hidden = true;
  app.run(`session = {status: 'selected', distance_m: 1000,
    route_geometry: [[5.6037, -0.187], [5.61, -0.187], [5.6037, -0.187]]};
    travelledMetres = 300; checkBackHome([5.6037, -0.187]);`);
  assert.equal(app.nodes.get('back-home').hidden, true, 'not after only a third of the route');
  app.run(`travelledMetres = 900; checkBackHome([5.6038, -0.187]);`);
  assert.equal(app.nodes.get('back-home').hidden, false);
});

test('a walk announces its start, the turn-back point and the return', () => {
  const app = setup();
  app.nodes.get('journey-alert').hidden = true;
  app.nodes.get('back-home').hidden = true;
  app.run(`recommendation = {id: 'rec-1', activity_type: 'walk', duration_minutes: 12};
    session = {status: 'selected', distance_m: 1000, label: 'out_and_back',
      route_geometry: [[33.5186, -86.8104], [33.5231, -86.8104], [33.5186, -86.8104]]};
    announceStart();`);
  assert.equal(app.nodes.get('journey-alert').hidden, false);
  assert.match(app.nodes.get('journey-alert-title').textContent, /walk has started/);

  app.run(`travelledMetres = 200; checkTurnaround([33.5200, -86.8104]);`);
  assert.equal(app.run('session.turnAnnounced'), false, 'not before the turning point');
  app.run(`travelledMetres = 480; checkTurnaround([33.5230, -86.8104]);`);
  assert.equal(app.run('session.turnAnnounced'), true);
  assert.match(app.nodes.get('journey-alert-title').textContent, /Turn back now/);

  app.run(`setSheetHidden(true); travelledMetres = 980; checkBackHome([33.5186, -86.8104]);`);
  assert.match(app.nodes.get('journey-alert-title').textContent, /back at your start/);
  assert(!app.nodes.get('session-card').classList.contains('slid-away'), 'Finish is visible again');
});

test('a loop says to head back at halfway, even when GPS misses the exact spot', () => {
  const app = setup();
  app.run(`session = {status: 'selected', distance_m: 1000, label: 'small_loop',
      route_geometry: [[33.5186, -86.8104], [33.5200, -86.8104], [33.5200, -86.8090], [33.5186, -86.8104]]};
    travelledMetres = 600; checkTurnaround([33.53, -86.80]);`);
  assert.match(app.nodes.get('journey-alert-title').textContent, /head back/);
});

test('the details panel slides away and comes back', () => {
  const app = setup();
  app.run(`setSheetHidden(true);`);
  assert(app.nodes.get('session-card').classList.contains('slid-away'));
  assert.equal(app.nodes.get('btn-sheet-show').hidden, false);
  assert.equal(app.nodes.get('mini-stats').hidden, false);
  app.nodes.get('btn-sheet-show').onclick();
  assert(!app.nodes.get('session-card').classList.contains('slid-away'));
  assert.equal(app.nodes.get('mini-stats').hidden, true);
});

test('sound can be switched off', () => {
  const app = setup();
  app.nodes.get('btn-sound').onclick();
  assert.equal(app.run('soundOn'), false);
  assert.match(app.nodes.get('btn-sound').textContent, /off/);
});

test('"Not feeling well?" stops the walk and offers to call the emergency contact', () => {
  const app = setup();
  app.nodes.get('help-sheet').hidden = true;
  app.run(`session = {status: 'selected', distance_m: 1000, route_geometry: [[33.5, -86.8], [33.51, -86.8], [33.5, -86.8]]};
    savedProfile = {emergency_contact_name: 'Ama', emergency_contact_phone: '(205) 555-0142'};
    window.isSecureContext = true;
    navigator.geolocation = {watchPosition() {return 7;}, clearWatch() {globalThis.cleared = true;}};
    startTracking();`);
  app.nodes.get('btn-unwell').onclick();
  assert.equal(app.nodes.get('help-sheet').hidden, false);
  assert.equal(app.run('watchId'), null, 'tracking stopped');
  assert.equal(app.nodes.get('help-call-contact').hidden, false);
  assert.equal(app.nodes.get('help-call-contact').getAttribute('href'), 'tel:2055550142');
  assert.match(app.nodes.get('help-call-contact').textContent, /Ama/);
  app.nodes.get('help-better').onclick();
  assert.equal(app.nodes.get('help-sheet').hidden, true);
  assert.equal(app.run('watchId'), 7, 'tracking resumed');
});

test('finishing a real walk asks how it felt and sends the check-in', async () => {
  const requests = [];
  const app = setup(async (url, options) => {
    requests.push({url, body: JSON.parse(options.body)});
    return response({id: 's1', status: 'completed', distance_m: 1000, estimated_minutes: 12, route_geometry: [],
      after_walk_advice: 'Your blood sugar is in a good range after your activity.'});
  });
  app.nodes.get('finish-sheet').hidden = true;
  app.run(`session = {id: 's1', status: 'selected', distance_m: 1000, route_geometry: [[33.5, -86.8]]};`);
  await app.nodes.get('btn-complete').onclick();
  assert.equal(app.nodes.get('finish-sheet').hidden, false);
  assert.equal(requests.length, 0, 'nothing saved until the check-in is answered');
  app.run(`effortChoice = 'hard';`);
  app.nodes.get('post-glucose').value = '118';
  app.nodes.get('post-glucose-unit').value = 'mg/dL';
  await app.nodes.get('btn-save-walk').onclick();
  assert.deepEqual({...requests[0].body}, {status: 'completed', effort: 'hard', post_glucose_unit: 'mg/dL', post_glucose_value: 118});
  assert.equal(app.nodes.get('after-walk-out').hidden, false);
  assert.match(app.nodes.get('after-walk-out').innerHTML, /good range/);
});

test('route options show the heat check', async () => {
  const app = setup(async () => response({options: [], provider: 'mock', excluded: [],
    weather: {temperature_f: 96, heat_index_f: 108, level: 'danger', advice: "It's dangerously hot right now."}}));
  app.run(seed);
  await app.nodes.get('btn-options').onclick();
  assert.match(app.nodes.get('options-out').innerHTML, /weather-card danger/);
  assert.match(app.nodes.get('options-out').innerHTML, /feels like 108/);
});

test('safety answers are part of the saved profile', () => {
  const app = setup();
  app.nodes.get('takes_glucose_lowering_medication').checked = true;
  app.nodes.get('emergency_contact_phone').value = ' (205) 555-0142 ';
  const payload = app.run('profilePayload()');
  assert.equal(payload.takes_glucose_lowering_medication, true);
  assert.equal(payload.emergency_contact_phone, '(205) 555-0142');
  assert.equal(payload.emergency_contact_name, null);
});

test('a server crash shows a plain message, not a JSON parse error', async () => {
  const app = setup(async () => ({ok: false, status: 500, text: async () => 'Internal Server Error'}));
  app.run(`recommendation = null;`);
  await app.nodes.get('btn-recommend').onclick();
  assert.match(app.nodes.get('status').textContent, /server had a problem \(error 500\)/);
  assert.doesNotMatch(app.nodes.get('status').textContent, /JSON|Unexpected token/);
});

test('route cards show measured sidewalks and busy roads', () => {
  const app = setup();
  const chips = app.run(`routeChips({distance_m: 1609, estimated_minutes: 20, unverified: [],
    score_breakdown: {traffic_exposure_inv: 0.7}, environment: {sidewalk_pct: 82, busy_road_pct: 25}})`);
  assert.match(chips, /Sidewalks 82%/);
  assert.match(chips, /Busy roads 25%/);
});

test('height and weight are entered in feet, inches and pounds', () => {
  const app = setup();
  app.nodes.get('height_ft').value = '5';
  app.nodes.get('height_in').value = '7';
  app.nodes.get('weight_lb').value = '180';
  const payload = app.run('profilePayload()');
  assert.equal(payload.height_cm, 170.2);   // 67 in
  assert.equal(payload.weight_kg, 81.6);    // 180 lb
  app.run(`showUsUnits({height_cm: 182.9, weight_kg: 95.3})`);
  assert.equal(app.nodes.get('height_ft').value, '6');
  assert.equal(app.nodes.get('height_in').value, '0');
  assert.equal(app.nodes.get('weight_lb').value, '210');
});

test('Google Maps gets the whole loop, starting and finishing at the start', () => {
  const app = setup();
  const url = app.run(`googleLoopUrl([[33.5186, -86.8104], [33.5200, -86.8104], [33.5200, -86.8090], [33.5186, -86.8104]], 'walk')`);
  const params = new URL(url).searchParams;
  assert.equal(params.get('origin'), '33.518600,-86.810400');
  assert.equal(params.get('destination'), '33.518600,-86.810400');
  assert.equal(params.get('travelmode'), 'walking');
  assert(params.get('waypoints'));
});

test('an Apple Maps handoff is reminded to head back at the turn-back point', () => {
  const app = setup();
  app.run(`session = {status: 'selected', distance_m: 1000, label: 'small_loop', appleOutLeg: true,
      route_geometry: [[33.5186, -86.8104], [33.5231, -86.8104], [33.5186, -86.8104]]};
    travelledMetres = 600; checkTurnaround([33.5230, -86.8104]);`);
  assert.match(app.nodes.get('journey-alert-text').textContent, /Directions back to my start/);
});

test('progress shows the weekly ring against 150 minutes', () => {
  const app = setup();
  const ring = app.run('weekRing(60, 3)');
  assert.match(ring, /90 more active minutes/);
  assert.match(ring, /3 days in a row/);
  assert.match(app.run('weekRing(160, 0)'), /Weekly goal reached/);
});

test('the greeting follows the time of day', () => {
  const app = setup();
  app.run('renderGreeting(new Date(2026, 8, 26, 7, 30))');
  assert.equal(app.nodes.get('today-greeting').textContent, 'Good morning');
  app.run('renderGreeting(new Date(2026, 8, 26, 19, 0))');
  assert.equal(app.nodes.get('today-greeting').textContent, 'Good evening');
});

test('calories are estimated live from weight, time and pace', () => {
  const app = setup();
  assert.equal(app.run('estimateCalories("walk", 20, 81.6, 1609.3)'), 95);
  assert.equal(app.run('estimateCalories("walk", 20, null, 1609.3)'), null);
  app.run(`recommendation = {activity_type: 'walk', duration_minutes: 20};
    savedProfile = {weight_kg: 81.6};
    session = {simulated: true, status: 'selected'};
    simulationElapsedSeconds = 1200; travelledMetres = 1609.3; renderTrackStats();`);
  assert.match(app.nodes.get('track-out').innerHTML, /<b>95<\/b><span>calories/);
  assert.match(app.nodes.get('mini-stats').innerHTML, /95 <span>cal/);
});

test('continuing after "Not feeling well?" keeps the time and distance', () => {
  const app = setup();
  app.run(`session = {status: 'selected', distance_m: 1000, route_geometry: [[33.5, -86.8], [33.51, -86.8], [33.5, -86.8]]};
    window.isSecureContext = true;
    navigator.geolocation = {watchPosition() {return 7;}, clearWatch() {}};
    startTracking(); travelledMetres = 420; globalThis.started = trackStartedAt;`);
  app.nodes.get('btn-unwell').onclick();
  app.nodes.get('help-better').onclick();
  assert.equal(app.run('travelledMetres'), 420);
  assert.equal(app.run('trackStartedAt === globalThis.started'), true);
});

test('saving a tracked walk sends what the phone measured', async () => {
  const requests = [];
  const app = setup(async (url, options) => {
    requests.push(JSON.parse(options.body));
    return response({id: 's1', status: 'completed', distance_m: 1000, estimated_minutes: 12, route_geometry: []});
  });
  app.run(`session = {id: 's1', status: 'selected', distance_m: 1000, route_geometry: [[33.5, -86.8]]};
    trackStartedAt = Date.now() - 14 * 60000; travelledMetres = 1180.4; watchId = 3;
    navigator.geolocation = {clearWatch() {}};`);
  await app.nodes.get('btn-complete').onclick();
  await app.nodes.get('btn-skip-checkin').onclick();
  assert.equal(requests[0].status, 'completed');
  assert(Math.abs(requests[0].measured_minutes - 14) < 0.2);
  assert.equal(requests[0].measured_distance_m, 1180);
});
