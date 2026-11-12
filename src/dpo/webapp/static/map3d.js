// 3D 지도 경로 생성 화면 – three.js (static/vendor 에 동봉, 인터넷 없이 동작)
import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { mergeGeometries } from "/vendor/BufferGeometryUtils.js";

const $ = id => document.getElementById(id);
const LEG_KO = { out: "가는 편", back: "오는 편" };
const LEG_COLOR = { out: 0xE46C0A, back: 0x1B6CA8 };
const LEG_CSS = { out: "#E46C0A", back: "#1B6CA8" };
const S = { site: null, sc: null, hub: null, dst: null, plan: null, mission: null, track: [], t: 0, tEnd: 0, playing: false,
            pick: null, obstacles: [], example: null, wind: null };
window.__map3d = S;                      // 자동 시험에서 상태를 읽을 수 있게 노출

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ------------------------------------------------------------ three.js 기본 구성
let renderer, scene, camera, controls, root, needRender = true;
const G = {};                            // 장면 묶음: terrain, bld, nfz, floor, wind, path, base, obs, marks, drone

function initThree() {
  const view = $("view");
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  } catch (e) {
    $("webglerr").style.display = "flex";
    throw e;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  view.insertBefore(renderer.domElement, view.firstChild);
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0xDCE6EE);
  camera = new THREE.PerspectiveCamera(42, 1, 20, 80000);
  camera.up.set(0, 0, 1);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.maxPolarAngle = Math.PI / 2 - 0.03;
  controls.addEventListener("change", () => { needRender = true; });
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8794A3, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(-0.6, -0.8, 1.2);
  scene.add(sun);
  root = new THREE.Group();
  scene.add(root);
  for (const k of ["terrain", "bld", "nfz", "floor", "wind", "base", "path", "obs", "marks", "drone"]) {
    G[k] = new THREE.Group();
    root.add(G[k]);
  }
  new ResizeObserver(resize).observe(view);
  resize();
  const loop = () => {
    if (S.playing) stepPlay();
    if (needRender) { controls.update(); renderer.render(scene, camera); needRender = false; }
    requestAnimationFrame(loop);
  };
  loop();
}

function resize() {
  const v = $("view");
  const w = v.clientWidth, h = v.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
  needRender = true;
  drawProfile();
}

function clearGroup(g) {
  for (const o of [...g.children]) {
    g.remove(o);
    o.traverse(c => { c.geometry?.dispose(); if (c.material) (Array.isArray(c.material) ? c.material : [c.material]).forEach(m => m.dispose()); });
  }
}

// ------------------------------------------------------------ 격자 보조
function cell(x, y) {
  const sc = S.sc;
  const i = Math.min(Math.max(Math.floor(x / sc.dx), 0), sc.nx - 1), j = Math.min(Math.max(Math.floor(y / sc.dx), 0), sc.ny - 1);
  return j * sc.nx + i;
}
function groundAt(x, y) {              // 셀 중심 값의 쌍선형 보간
  const sc = S.sc, fx = Math.min(Math.max(x / sc.dx - 0.5, 0), sc.nx - 1.001), fy = Math.min(Math.max(y / sc.dx - 0.5, 0), sc.ny - 1.001);
  const i = Math.floor(fx), j = Math.floor(fy), a = fx - i, b = fy - j, g = sc.ground, n = sc.nx;
  return (g[j * n + i] * (1 - a) + g[j * n + i + 1] * a) * (1 - b) + (g[(j + 1) * n + i] * (1 - a) + g[(j + 1) * n + i + 1] * a) * b;
}
function floorAt(x, y) {               // 계획 격자의 비행 가능 하한. 셀 경계 위의 점은 맞닿은 셀 중 낮은 값
  let best = null;
  for (const ex of [-1, 1]) for (const ey of [-1, 1]) {
    const f = S.sc.floor[cell(x + ex, y + ey)];
    if (f != null && (best == null || f < best)) best = f;
  }
  return best;
}
function lerpColor(stops, v) {         // stops: [[값, 0xRRGGBB], ...]
  let k = 0;
  while (k < stops.length - 2 && v > stops[k + 1][0]) k++;
  const [v0, c0] = stops[k], [v1, c1] = stops[k + 1];
  const f = Math.min(Math.max((v - v0) / (v1 - v0), 0), 1);
  return new THREE.Color(c0).lerp(new THREE.Color(c1), f);
}
const HYPS = [[15, 0x6E9B6A], [60, 0xA9BC7E], [120, 0xCDBB86], [200, 0xA98F6B], [300, 0xEDE8DE]];

// ------------------------------------------------------------ 장면 만들기
function gridMesh(values, material, colorFn) {
  // 셀 중심 격자 → 삼각형 그물. 값이 null 인 꼭짓점이 낀 삼각형은 뺀다.
  const sc = S.sc, nx = sc.nx, ny = sc.ny;
  const pos = new Float32Array(nx * ny * 3), col = colorFn ? new Float32Array(nx * ny * 3) : null, idx = [];
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
    const q = j * nx + i, v = values[q];
    pos[q * 3] = (i + 0.5) * sc.dx; pos[q * 3 + 1] = (j + 0.5) * sc.dx; pos[q * 3 + 2] = v ?? 0;
    if (col) { const c = colorFn(v); col[q * 3] = c.r; col[q * 3 + 1] = c.g; col[q * 3 + 2] = c.b; }
  }
  for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
    const a = j * nx + i, b = a + 1, c = a + nx, d = c + 1;
    if (values[a] == null || values[b] == null || values[c] == null || values[d] == null) continue;
    idx.push(a, b, d, a, d, c);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  if (col) g.setAttribute("color", new THREE.BufferAttribute(col, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  return new THREE.Mesh(g, material);
}

function buildScene() {
  const sc = S.sc, W = sc.nx * sc.dx, H = sc.ny * sc.dx;
  // 지형
  G.terrain.add(gridMesh(sc.ground, new THREE.MeshLambertMaterial({ vertexColors: true }), v => lerpColor(HYPS, v)));
  // 건물: 바닥 다각형을 높이만큼 세운다 (하나의 그물로 합쳐 그리기 부담을 줄임)
  const geos = [];
  for (const b of sc.buildings) {
    if (b.p.length < 3) continue;
    const shape = new THREE.Shape(b.p.map(p => new THREE.Vector2(p[0], p[1])));
    let g;
    try { g = new THREE.ExtrudeGeometry(shape, { depth: b.h, bevelEnabled: false }); } catch (e) { continue; }
    g.translate(0, 0, b.b);
    const c = lerpColor([[0, 0xE4E8EC], [40, 0xB9C2CC], [110, 0x7F8C9A]], b.h), n = g.attributes.position.count, ca = new Float32Array(n * 3);
    for (let q = 0; q < n; q++) { ca[q * 3] = c.r; ca[q * 3 + 1] = c.g; ca[q * 3 + 2] = c.b; }
    g.setAttribute("color", new THREE.BufferAttribute(ca, 3));
    geos.push(g);
  }
  if (geos.length) {
    G.bld.add(new THREE.Mesh(mergeGeometries(geos, false), new THREE.MeshLambertMaterial({ vertexColors: true })));
    geos.forEach(g => g.dispose());
  }
  // 비행금지구역 (원기둥)
  const zTop = sc.z[sc.z.length - 1] + sc.dz;
  for (const [cx, cy, r] of sc.nfz) {
    const z0 = groundAt(cx, cy) - 20, g = new THREE.CylinderGeometry(r, r, zTop - z0, 64, 1, true);
    g.rotateX(Math.PI / 2);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color: 0xD7263D, transparent: true, opacity: 0.28, side: THREE.DoubleSide, depthWrite: false }));
    m.position.set(cx, cy, (zTop + z0) / 2);
    G.nfz.add(m);
  }
  // 계획 격자의 비행 가능 하한면 (지면+30 m, 건물+20 m, 수평 1셀 여유를 반영한 가장 낮은 레이어)
  G.floor.add(gridMesh(sc.floor, new THREE.MeshBasicMaterial({ color: 0x1B6CA8, transparent: true, opacity: 0.25, side: THREE.DoubleSide, depthWrite: false })));
  G.floor.visible = false;
  G.wind.visible = false;
  frame();
}

function frame() {
  // 출발지–도착지가 화면에 들어오도록 남서쪽 위에서 내려다본다
  const a = S.hub, b = S.dst, cx = (a[0] + b[0]) / 2, cy = (a[1] + b[1]) / 2, span = Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]), 800);
  const zc = groundAt(cx, cy) * root.scale.z + 80;
  controls.target.set(cx, cy, zc);
  camera.position.set(cx - span * 0.35, cy - span * 1.05, zc + span * 0.85);
  controls.update();
  needRender = true;
}

function pin(x, y, color) {
  const g = new THREE.Group(), z0 = groundAt(x, y), h = 150;
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, h, 8).rotateX(Math.PI / 2), new THREE.MeshBasicMaterial({ color }));
  pole.position.set(x, y, z0 + h / 2);
  const head = new THREE.Mesh(new THREE.SphereGeometry(22, 16, 12), new THREE.MeshLambertMaterial({ color }));
  head.position.set(x, y, z0 + h);
  g.add(pole, head);
  return g;
}
function drawMarks() {
  clearGroup(G.marks);
  if (S.hub) G.marks.add(pin(S.hub[0], S.hub[1], 0x004E8F));
  if (S.dst) G.marks.add(pin(S.dst[0], S.dst[1], 0xE46C0A));
  const f = p => p ? `(${Math.round(p[0])}, ${Math.round(p[1])})` : "–";
  $("ptinfo").innerHTML = `<b style="color:#004E8F">■</b> 출발 ${f(S.hub)} · <b style="color:#E46C0A">■</b> 도착 ${f(S.dst)} m`;
  needRender = true;
}

async function loadWind() {
  S.wind = await api(`/api/wind3d?hour=${$("hour").value}`);
  drawWind();
}
function drawWind() {
  clearGroup(G.wind);
  const w = S.wind, sc = S.sc;
  if (!w || !$("l_wind").checked) { needRender = true; return; }
  const k = +$("wk").value, off = Math.floor(w.step / 2);
  for (let a = 0; a < w.u[k].length; a++) for (let b = 0; b < w.u[k][a].length; b++) {
    if (!w.free[k][a][b]) continue;
    const u = w.u[k][a][b], v = w.v[k][a][b], sp = Math.hypot(u, v);
    if (sp < 0.3) continue;
    const o = new THREE.Vector3((off + b * w.step + 0.5) * sc.dx, (off + a * w.step + 0.5) * sc.dx, w.z[k]);
    const L = sp * 22;
    G.wind.add(new THREE.ArrowHelper(new THREE.Vector3(u / sp, v / sp, 0), o, L, lerpColor([[5, 0x2C7FB8], [9, 0x7FBF7B], [13, 0xD7263D]], sp).getHex(), L * 0.32, L * 0.16));
  }
  needRender = true;
}

// ------------------------------------------------------------ 경로 그리기
function vz() {
  const d = S.site.drones.find(x => x.key === (S.plan?.drone?.key ?? $("drone").value));
  return [d.vz_up, d.vz_dn];
}
function buildTrack(res) {
  // 구간별 표본에 이륙·착륙 수직 구간을 붙여 하나의 시간 궤적으로 만든다
  const [up, dn] = vz(), tr = [];
  for (const L of res.legs) {
    if (!L.feasible) break;
    const s = L.samples, a = s[0], b = s[s.length - 1], ga = groundAt(a.x, a.y), gb = groundAt(b.x, b.y);
    tr.push({ t: a.t - (a.z - ga) / up - 1e-3, x: a.x, y: a.y, z: ga, v: 0, ws: a.ws, soc: a.soc, leg: L.name });
    for (const p of s) tr.push({ ...p, leg: L.name });
    tr.push({ t: b.t + (b.z - gb) / dn, x: b.x, y: b.y, z: gb, v: 0, ws: b.ws, soc: b.soc, leg: L.name });
  }
  return tr;
}
function tube(pts, color, radius) {
  const curve = new THREE.CatmullRomCurve3(pts, false, "catmullrom", 0.0);
  return new THREE.Mesh(new THREE.TubeGeometry(curve, Math.max(pts.length * 2, 8), radius, 8, false), new THREE.MeshLambertMaterial({ color }));
}
function curtain(samples, color) {
  const n = samples.length, pos = new Float32Array(n * 6), idx = [];
  samples.forEach((p, q) => {
    pos.set([p.x, p.y, p.z, p.x, p.y, groundAt(p.x, p.y)], q * 6);
    if (q < n - 1) idx.push(q * 2, q * 2 + 1, q * 2 + 3, q * 2, q * 2 + 3, q * 2 + 2);
  });
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setIndex(idx);
  return new THREE.Mesh(g, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.16, side: THREE.DoubleSide, depthWrite: false }));
}
function drawPlan() {
  clearGroup(G.path); clearGroup(G.base); clearGroup(G.drone); clearGroup(G.obs);
  const res = S.plan;
  if (!res) { needRender = true; return; }
  for (const B of res.baseline || []) {
    const pts = B.samples.map(p => new THREE.Vector3(p.x, p.y, p.z));
    if (pts.length > 1) G.base.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x555E68 })));
  }
  for (const L of res.legs) {
    if (!L.feasible) break;
    const s = L.samples, color = LEG_COLOR[L.name];
    const a = s[0], b = s[s.length - 1];
    const pts = [new THREE.Vector3(a.x, a.y, groundAt(a.x, a.y)), ...s.map(p => new THREE.Vector3(p.x, p.y, p.z)), new THREE.Vector3(b.x, b.y, groundAt(b.x, b.y))];
    G.path.add(tube(pts, color, 14), curtain(s, color));
  }
  // 드론 표식
  const body = new THREE.Mesh(new THREE.SphereGeometry(30, 20, 14), new THREE.MeshLambertMaterial({ color: 0xFFD23F, emissive: 0x5A4500 }));
  const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]), new THREE.LineBasicMaterial({ color: 0x16202A }));
  G.drone.add(body, drop);
  G.drone.userData = { body, drop };
  // 이동 장애물
  for (const o of res.obstacles || []) {
    const zl = Math.max(o.z_min ?? 0, 0), zh = Math.min(o.z_max ?? 400, S.sc.z[S.sc.z.length - 1] + 40), ext = o.margin ?? 0;
    let g;
    if (o.kind === "box") g = new THREE.BoxGeometry(2 * (o.half_x + ext), 2 * (o.half_y + ext), zh - zl);
    else if (o.kind === "sphere") g = new THREE.SphereGeometry(o.radius + ext, 24, 16);
    else g = new THREE.CylinderGeometry(o.radius + ext, o.radius + ext, zh - zl, 40).rotateX(Math.PI / 2);
    const m = new THREE.Mesh(g, new THREE.MeshLambertMaterial({ color: 0x8E44AD, transparent: true, opacity: 0.42, depthWrite: false }));
    m.userData = { o, zc: o.kind === "sphere" ? null : (zl + zh) / 2 };
    G.obs.add(m);
  }
  setTime(S.t);
}

// ------------------------------------------------------------ 시간 재생
function obsPos(o, t) {
  const ts = o.path.map(p => p.t);
  const t0 = o.t_start ?? (ts.length > 1 ? ts[0] : 0), t1 = o.t_end ?? (ts.length > 1 ? ts[ts.length - 1] : Infinity);
  if (t < t0 || t > t1 || !o.path.length) return null;
  const P = o.path;
  if (P.length === 1 || t <= P[0].t) return P[0];
  for (let q = 0; q < P.length - 1; q++) if (t <= P[q + 1].t) {
    const f = (t - P[q].t) / (P[q + 1].t - P[q].t);
    return { x: P[q].x + f * (P[q + 1].x - P[q].x), y: P[q].y + f * (P[q + 1].y - P[q].y), z: (P[q].z ?? 0) + f * ((P[q + 1].z ?? 0) - (P[q].z ?? 0)) };
  }
  return P[P.length - 1];
}
function stateAt(t) {
  const tr = S.track;
  if (!tr.length) return null;
  if (t <= tr[0].t) return tr[0];
  for (let q = 0; q < tr.length - 1; q++) if (t <= tr[q + 1].t) {
    const a = tr[q], b = tr[q + 1];
    if (a.leg !== b.leg) return { ...a, v: 0, ground: true };      // 구간 사이: 도착지에서 대기
    const f = (t - a.t) / Math.max(b.t - a.t, 1e-9);
    return { t, x: a.x + f * (b.x - a.x), y: a.y + f * (b.y - a.y), z: a.z + f * (b.z - a.z), v: b.v, ws: b.ws,
             soc: a.soc + f * (b.soc - a.soc), leg: b.leg };
  }
  return tr[tr.length - 1];
}
function setTime(t) {
  S.t = t;
  const st = stateAt(t), hud = $("hud");
  if (st) {
    const { body, drop } = G.drone.userData, g = groundAt(st.x, st.y);
    body.position.set(st.x, st.y, st.z);
    drop.position.set(st.x, st.y, st.z);
    drop.scale.set(1, 1, Math.max(st.z - g, 0.01));
    hud.style.display = "block";
    hud.innerHTML = `<b>${LEG_KO[st.leg] ?? ""}</b> · 출발 후 ${Math.round(t)} s<br>고도 해발 ${Math.round(st.z)} m · 지면 위 ${Math.round(st.z - g)} m<br>` +
      `대지속도 ${st.v.toFixed(1)} m/s · 바람 ${st.ws.toFixed(1)} m/s<br>SOC <b>${st.soc.toFixed(1)} %</b>`;
    $("tlab").textContent = `출발 후 ${Math.round(t)} s / ${Math.round(S.tEnd)} s`;
  } else hud.style.display = "none";
  for (const m of G.obs.children) {
    const p = obsPos(m.userData.o, t);
    m.visible = !!p && $("l_obs").checked;
    if (p) m.position.set(p.x, p.y, m.userData.zc ?? (p.z ?? 100));
  }
  $("tl").value = t;
  needRender = true;
  drawProfile();
}
let lastTick = 0;
function stepPlay() {
  const now = performance.now(), dt = Math.min((now - lastTick) / 1000, 0.1);
  lastTick = now;
  let t = S.t + dt * Math.max(S.tEnd / 25, 8);     // 전체를 약 25초에 재생
  if (t >= S.tEnd) { t = S.tEnd; S.playing = false; $("play").textContent = "▶ 재생"; }
  setTime(t);
}

// ------------------------------------------------------------ 고도 단면
function drawProfile() {
  const cv = $("profile"), dpr = Math.min(window.devicePixelRatio || 1, 2), w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !S.sc) return;
  cv.width = w * dpr; cv.height = h * dpr;
  const c = cv.getContext("2d");
  c.setTransform(dpr, 0, 0, dpr, 0, 0);
  c.clearRect(0, 0, w, h);
  c.font = "11px 'Malgun Gothic','NanumGothic',sans-serif";
  const legs = (S.plan?.legs || []).filter(L => L.feasible);
  if (!legs.length) {
    c.fillStyle = "#4C5A66";
    c.fillText("고도 단면: 경로를 생성하면 경로 아래의 지형·건물과 비행 고도를 보여 줍니다", 10, h / 2);
    return;
  }
  const sc = S.sc, rows = [];
  let d0 = 0;
  for (const L of legs) {
    let d = d0, prev = null;
    for (const p of L.samples) {
      if (prev) d += Math.hypot(p.x - prev.x, p.y - prev.y);
      const q = cell(p.x, p.y);
      rows.push({ d, z: p.z, g: sc.ground[q], s: sc.ground[q] + sc.bh[q], f: floorAt(p.x, p.y), t: p.t, leg: L.name });
      prev = p;
    }
    d0 = d;
  }
  const dMax = rows[rows.length - 1].d || 1, zLo = Math.min(...rows.map(r => r.g)) - 8, zHi = Math.max(...rows.map(r => r.z)) + 25;
  const mL = 44, mR = 10, mT = 8, mB = 20, X = d => mL + d / dMax * (w - mL - mR), Y = z => h - mB - (z - zLo) / (zHi - zLo) * (h - mT - mB);
  // 축
  c.strokeStyle = "#D5DCE2"; c.fillStyle = "#4C5A66"; c.lineWidth = 1;
  const stepZ = (zHi - zLo) > 160 ? 50 : 25;
  for (let z = Math.ceil(zLo / stepZ) * stepZ; z <= zHi; z += stepZ) {
    c.beginPath(); c.moveTo(mL, Y(z)); c.lineTo(w - mR, Y(z)); c.stroke();
    c.fillText(`${z} m`, 6, Y(z) + 4);
  }
  const stepD = dMax > 4000 ? 1000 : 500;
  for (let d = 0; d <= dMax; d += stepD) c.fillText(`${(d / 1000).toFixed(1)} km`, X(d) - 14, h - 5);
  // 건물 포함 표면, 지형
  const area = (key, color) => {
    c.beginPath(); c.moveTo(X(0), Y(zLo));
    for (const r of rows) c.lineTo(X(r.d), Y(r[key]));
    c.lineTo(X(dMax), Y(zLo)); c.closePath(); c.fillStyle = color; c.fill();
  };
  area("s", "#B9C2CC"); area("g", "#C9B88A");
  // 비행 가능 하한 (계획 격자)
  c.setLineDash([4, 3]); c.strokeStyle = "#1B6CA8"; c.beginPath();
  let pen = false;
  for (const r of rows) { if (r.f == null) { pen = false; continue; } pen ? c.lineTo(X(r.d), Y(r.f)) : c.moveTo(X(r.d), Y(r.f)); pen = true; }
  c.stroke(); c.setLineDash([]);
  // 경로
  for (const L of legs) {
    const rr = rows.filter(r => r.leg === L.name);
    c.strokeStyle = LEG_CSS[L.name]; c.lineWidth = 2.2; c.beginPath();
    c.moveTo(X(rr[0].d), Y(rr[0].g));
    for (const r of rr) c.lineTo(X(r.d), Y(r.z));
    c.lineTo(X(rr[rr.length - 1].d), Y(rr[rr.length - 1].g));
    c.stroke();
    c.fillStyle = LEG_CSS[L.name];
    c.fillText(LEG_KO[L.name], X(rr[0].d) + 6, mT + 10);
  }
  // 범례
  c.fillStyle = "#4C5A66";
  c.fillText("■ 지형", w - 250, mT + 10); c.fillStyle = "#8D99A6"; c.fillText("■ 건물", w - 200, mT + 10);
  c.fillStyle = "#1B6CA8"; c.fillText("┄ 비행 가능 하한 (20 m 층)", w - 150, mT + 10);
  // 현재 시각
  const st = stateAt(S.t);
  if (st) {
    const same = rows.filter(r => r.leg === st.leg);
    let best = same[0] ?? rows[0];
    for (const r of same) if (Math.abs(r.t - S.t) < Math.abs(best.t - S.t)) best = r;
    c.strokeStyle = "#16202A"; c.lineWidth = 1; c.beginPath(); c.moveTo(X(best.d), mT); c.lineTo(X(best.d), h - mB); c.stroke();
    c.fillStyle = "#FFD23F"; c.strokeStyle = "#16202A"; c.beginPath(); c.arc(X(best.d), Y(st.z), 5, 0, 7); c.fill(); c.stroke();
  }
}

// ------------------------------------------------------------ 임무 설정
function droneSel() { return S.site.drones.find(d => d.key === $("drone").value); }
function refreshDrone() {
  const d = droneSel();
  $("droneinfo").innerHTML = `<span>배터리</span><span>${d.battery_wh} Wh</span><span>자체무게</span><span>${d.m_empty} kg</span>` +
    `<span>속도 한계</span><span>대기 ${d.va_max} m/s · 대지 ${d.vg_max} m/s</span><span>상승·하강</span><span>${d.vz_up} / ${d.vz_dn} m/s</span>`;
  $("payload").max = d.payload_max;
  if (+$("payload").value > d.payload_max) $("payload").value = d.payload_max;
  $("pmax").textContent = `(최대 ${d.payload_max} kg)`;
  const w = $("dronewarn");
  w.style.display = d.key === "x650" ? "block" : "none";
  w.innerHTML = "X650의 전력 모델은 제원과 제조사 호버 시간(무탑재 약 30분)으로 추정한 값입니다. 비행 기록으로 보정하기 전에는 SOC 수치를 참고용으로만 쓰세요.";
}
function refreshWindInfo() {
  const w = S.site.wind.find(x => x.hour === +$("hour").value);
  $("windinfo").textContent = w ? `지면 위 100 m 평균 ${w.speed.toFixed(1)} m/s, ${Math.round(w.dir_from)}°에서 불어옴` : "";
}

async function doPlan() {
  $("errors").textContent = ""; $("status").textContent = "계산 중…"; $("plan").disabled = true;
  try {
    if ($("useobs").checked && !S.example) S.example = await api("/api/example");
    const req = { drone: $("drone").value, payload: +$("payload").value, kind: $("kind").value, round_trip: $("rt").checked,
                  dep_hour: +$("hour").value, reserve: +$("reserve").value, wind: $("wind").value, hub: S.hub, dst: S.dst,
                  obstacles: $("useobs").checked ? S.example.obstacles : [] };
    const res = await api("/api/plan", req);
    S.plan = res; S.mission = null;
    S.track = buildTrack(res);
    S.tEnd = S.track.length ? S.track[S.track.length - 1].t : 0;
    S.t = 0;
    $("tl").max = Math.ceil(S.tEnd); $("tl").disabled = !S.track.length; $("play").disabled = !S.track.length;
    showResult(res);
    drawPlan();
    frame();
    $("status").textContent = `완료 (${res.elapsed.toFixed(1)} s)`;
    if (res.errors?.length) $("errors").textContent = res.errors.join(" / ");
  } catch (e) {
    $("errors").textContent = e.message; $("status").textContent = "";
  } finally { $("plan").disabled = false; }
}

function showResult(res) {
  $("rescard").style.display = "block";
  $("r_soc").textContent = `${res.soc_end.toFixed(1)} %`;
  $("r_feas").innerHTML = res.feasible ? `<span class="ok">임무 가능 (예비 ${$("reserve").value} % 이상)</span>` : `<span class="bad">임무 불가</span>`;
  const rows = res.legs.map(L => {
    if (!L.feasible) return `<tr><td>${LEG_KO[L.name]}</td><td colspan="6" class="bad">${L.reason}</td></tr>`;
    const zs = L.samples.map(p => p.z);
    return `<tr><td><b style="color:${LEG_CSS[L.name]}">■</b> ${LEG_KO[L.name]}</td><td>${L.E_kJ.toFixed(0)} kJ</td><td>${L.soc_leg.toFixed(1)} %p</td>` +
      `<td>${Math.round(L.T)} s</td><td>${(L.L / 1000).toFixed(2)} km</td><td>${Math.round(Math.min(...zs))}–${Math.round(Math.max(...zs))} m</td><td>${L.cpu.toFixed(1)} s</td></tr>`;
  });
  $("r_legs").innerHTML = rows.join("");
  const waits = res.legs.filter(L => L.feasible && (L.wait > 0 || L.ground_delay > 0)).map(L => `${LEG_KO[L.name]} 대기 ${Math.round(L.wait)} s, 출발 지연 ${Math.round(L.ground_delay)} s`);
  $("r_note").textContent = `${res.drone.label} · 탑재 ${res.drone.payload} kg · ${res.dep_hour}시 출발` + (waits.length ? ` · ${waits.join(" · ")}` : "");
  const ok = res.legs.some(L => L.feasible);
  $("miscard").style.display = ok ? "block" : "none";
  $("m_tab").style.display = "none";
  const d = S.site.drones.find(x => x.key === res.drone.key);
  $("m_up").textContent = d.vz_up; $("m_dn").textContent = d.vz_dn;
}

async function getMission() {
  if (!S.mission) {
    S.mission = await api("/api/mission", {});
    $("m_tab").style.display = "table";
    $("m_rows").innerHTML = S.mission.legs.map(L => { const s = L.summary;
      return `<tr><td>${LEG_KO[L.name]}</td><td>${s.n_items}</td><td>${s.n_waypoints}</td><td>${s.n_speed_changes}</td>` +
        `<td>${s.alt_rel_min.toFixed(0)}–${s.alt_rel_max.toFixed(0)} m</td><td>${s.speed_min ?? "–"}–${s.speed_max ?? "–"} m/s</td></tr>`; }).join("");
  }
  return S.mission;
}
function download(name, text, type) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
async function dl(kind) {
  try {
    const m = await getMission(), key = S.plan.drone.key;
    for (const L of m.legs) {
      const base = `${key}_${m.dep_hour}h_${L.name}`;
      if (kind === "wpl") download(`${base}.waypoints`, L.wpl, "text/plain");
      else download(`${base}.csv`, "﻿" + L.csv, "text/csv");
    }
  } catch (e) { $("errors").textContent = e.message; }
}

// ------------------------------------------------------------ 지도 클릭으로 출발·도착 지정
function setPick(k) {
  S.pick = S.pick === k ? null : k;
  $("pickhub").classList.toggle("active", S.pick === "hub");
  $("pickdst").classList.toggle("active", S.pick === "dst");
  $("hint").textContent = S.pick ? `지도에서 ${S.pick === "hub" ? "출발지" : "도착지"}를 누르세요` : "왼쪽 끌기: 회전 · 오른쪽 끌기: 이동 · 휠: 확대";
}
function initPicking() {
  const el = renderer.domElement, ray = new THREE.Raycaster();
  let down = null;
  el.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY]; });
  el.addEventListener("pointerup", e => {
    if (!S.pick || !down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
    const r = el.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), camera);
    const hit = ray.intersectObjects(G.terrain.children, false)[0];
    if (!hit) return;
    S[S.pick] = [hit.point.x, hit.point.y];
    setPick(null);
    drawMarks();
  });
}

// ------------------------------------------------------------ 시작
async function init() {
  initThree();
  [S.site, S.sc] = await Promise.all([api("/api/site"), api("/api/scene3d")]);
  S.hub = [...S.sc.hub]; S.dst = [...S.sc.dst];
  $("drone").innerHTML = S.site.drones.map(d => `<option value="${d.key}">${d.label}</option>`).join("");
  $("drone").value = "x650";
  $("hour").innerHTML = S.sc.hours.map(h => `<option value="${h}"${h === 15 ? " selected" : ""}>${h}시</option>`).join("");
  $("wk").innerHTML = S.sc.z.map((z, k) => `<option value="${k}">해발 ${z} m</option>`).join("");
  $("wk").value = Math.min(4, S.sc.z.length - 1);
  $("payload").value = 1.0;
  refreshDrone(); refreshWindInfo();
  root.scale.z = +$("zs").value;
  buildScene(); drawMarks(); initPicking();
  $("sub").textContent = `${S.site.date} · 성남 판교·분당 ${(S.site.W / 1000).toFixed(1)}×${(S.site.H / 1000).toFixed(1)} km · 건물 ${S.sc.buildings.length.toLocaleString()}동`;

  $("drone").onchange = refreshDrone;
  $("hour").onchange = () => { refreshWindInfo(); if ($("l_wind").checked) loadWind(); };
  $("plan").onclick = doPlan;
  $("pickhub").onclick = () => setPick("hub");
  $("pickdst").onclick = () => setPick("dst");
  $("dl_wpl").onclick = () => dl("wpl");
  $("dl_csv").onclick = () => dl("csv");
  $("l_bld").onchange = e => { G.bld.visible = e.target.checked; needRender = true; };
  $("l_nfz").onchange = e => { G.nfz.visible = e.target.checked; needRender = true; };
  $("l_floor").onchange = e => { G.floor.visible = e.target.checked; needRender = true; };
  $("l_base").onchange = e => { G.base.visible = e.target.checked; needRender = true; };
  $("l_obs").onchange = () => setTime(S.t);
  $("l_wind").onchange = e => { G.wind.visible = e.target.checked; if (e.target.checked) loadWind(); else needRender = true; };
  $("wk").onchange = drawWind;
  $("fit").onclick = frame;
  $("zs").onchange = e => { root.scale.z = +e.target.value; needRender = true; };
  $("tl").oninput = e => { S.playing = false; $("play").textContent = "▶ 재생"; setTime(+e.target.value); };
  $("play").onclick = () => {
    S.playing = !S.playing;
    if (S.playing && S.t >= S.tEnd) S.t = 0;
    lastTick = performance.now();
    $("play").textContent = S.playing ? "❚❚ 멈춤" : "▶ 재생";
  };
  S.ready = true;
}
init().catch(e => { $("errors").textContent = e.message; console.error(e); });
