// 3D 지도 경로 생성·장애물 편집 화면 – three.js (static/vendor 에 동봉, 인터넷 없이 동작)
import * as THREE from "three";
import { OrbitControls } from "/vendor/OrbitControls.js";
import { mergeGeometries } from "/vendor/BufferGeometryUtils.js";

const $ = id => document.getElementById(id);
const PALETTE = [0xE46C0A, 0x1B6CA8, 0x2CA58D, 0x8E44AD, 0xC9A227, 0xD7263D];
const OBS_COLORS = [0x8E44AD, 0xD7263D, 0x2C7FB8, 0x7A5C00, 0x117A65, 0xA04000];
const KIND_KO = { cylinder: "원기둥", sphere: "구", box: "박스" };
const hex = c => "#" + c.toString(16).padStart(6, "0");
const S = { site: null, sc: null, hub: null, dst: null, tour: [], obs: [], sel: -1, plan: null, mission: null, track: [],
            t: 0, tEnd: 300, playing: false, mode: null, example: null, wind: null, dirty: false };
window.__map3d = S;                      // 자동 시험에서 상태를 읽을 수 있게 노출

function legIndex(name) { return name === "out" ? 0 : name === "back" ? 1 : (parseInt(name.slice(1), 10) - 1) || 0; }
function legColor(name) { return PALETTE[legIndex(name) % PALETTE.length]; }
function legKo(name) { return name === "out" ? "가는 편" : name === "back" ? "오는 편" : name === "tour" ? "순회" : `구간 ${legIndex(name) + 1}`; }

async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

// ------------------------------------------------------------ three.js 기본 구성
let renderer, scene, camera, controls, root, needRender = true;
const G = {};                            // 장면 묶음

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
  for (const k of ["terrain", "bld", "nfz", "floor", "wind", "base", "path", "obs", "edit", "marks", "drone"]) {
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
    o.traverse(c => {
      c.geometry?.dispose();
      if (c.material) (Array.isArray(c.material) ? c.material : [c.material]).forEach(m => { m.map?.dispose(); m.dispose(); });
    });
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
let HYPS = [[15, 0x6E9B6A], [60, 0xA9BC7E], [120, 0xCDBB86], [200, 0xA98F6B], [300, 0xEDE8DE]];

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
  const sc = S.sc;
  // 지형 (색은 이 지역의 고도 범위에 맞춘다)
  const g0 = Math.min(...sc.ground), g1 = Math.max(...sc.ground), sp = Math.max(g1 - g0, 60);
  HYPS = [[g0, 0x6E9B6A], [g0 + sp * 0.18, 0xA9BC7E], [g0 + sp * 0.42, 0xCDBB86], [g0 + sp * 0.7, 0xA98F6B], [g1, 0xEDE8DE]];
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

function missionPoints() {
  const pts = [S.hub];
  if ($("mtype").value === "tour") pts.push(...S.tour); else pts.push(S.dst);
  return pts.filter(Boolean);
}
function frame() {
  // 임무 지점들이 화면에 들어오도록 남서쪽 위에서 내려다본다
  const pts = missionPoints();
  const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const span = Math.max(Math.hypot(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)), 900);
  const zc = groundAt(cx, cy) * root.scale.z + 80;
  controls.target.set(cx, cy, zc);
  camera.position.set(cx - span * 0.3, cy - span * 1.0, zc + span * 0.85);
  controls.update();
  needRender = true;
}

function label(text, color) {            // 글자 표식 (항상 화면을 향함)
  const cv = document.createElement("canvas"), c = cv.getContext("2d");
  c.font = "bold 44px 'Malgun Gothic','NanumGothic',sans-serif";
  const w = Math.ceil(c.measureText(text).width) + 28;
  cv.width = w; cv.height = 64;
  c.font = "bold 44px 'Malgun Gothic','NanumGothic',sans-serif";
  c.fillStyle = color; c.beginPath(); c.roundRect(0, 0, w, 64, 14); c.fill();
  c.fillStyle = "#fff"; c.textBaseline = "middle"; c.fillText(text, 14, 34);
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), depthTest: false }));
  sp.scale.set(w * 1.6, 64 * 1.6, 1);
  sp.renderOrder = 10;
  return sp;
}
function pin(x, y, color, text, handle) {
  const g = new THREE.Group(), z0 = groundAt(x, y), h = 150;
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, h, 8).rotateX(Math.PI / 2), new THREE.MeshBasicMaterial({ color }));
  pole.position.set(x, y, z0 + h / 2);
  const head = new THREE.Mesh(new THREE.SphereGeometry(24, 16, 12), new THREE.MeshLambertMaterial({ color }));
  head.position.set(x, y, z0 + h);
  head.userData.handle = handle;
  const lb = label(text, hex(color));
  lb.position.set(x, y, z0 + h + 55);
  g.add(pole, head, lb);
  return g;
}
function drawMarks() {
  clearGroup(G.marks);
  const tour = $("mtype").value === "tour";
  if (S.hub) G.marks.add(pin(S.hub[0], S.hub[1], 0x004E8F, "출발", { type: "hub" }));
  if (tour) {
    S.tour.forEach((p, n) => G.marks.add(pin(p[0], p[1], 0xE46C0A, String(n + 1), { type: "via", n })));
    if (S.hub && S.tour.length) {      // 순회 순서를 지면 위 점선으로
      const pts = [S.hub, ...S.tour, S.hub].map(p => new THREE.Vector3(p[0], p[1], groundAt(p[0], p[1]) + 150));
      const ln = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color: 0x004E8F, dashSize: 40, gapSize: 30 }));
      ln.computeLineDistances();
      G.marks.add(ln);
    }
  } else if (S.dst) G.marks.add(pin(S.dst[0], S.dst[1], 0xE46C0A, "도착", { type: "dst" }));
  const f = p => p ? `(${Math.round(p[0])}, ${Math.round(p[1])})` : "–";
  $("ptinfo").innerHTML = tour ? `<b style="color:#004E8F">■</b> 출발·복귀 ${f(S.hub)} m`
    : `<b style="color:#004E8F">■</b> 출발 ${f(S.hub)} · <b style="color:#E46C0A">■</b> 도착 ${f(S.dst)} m`;
  $("vialist").innerHTML = S.tour.map((p, n) => `<li><span><b style="color:#E46C0A">${n + 1}</b> (${Math.round(p[0])}, ${Math.round(p[1])}) m</span>` +
    `<span><button data-up="${n}" ${n ? "" : "disabled"}>↑</button> <button data-dn="${n}" ${n < S.tour.length - 1 ? "" : "disabled"}>↓</button> <button data-rm="${n}" class="warn">✕</button></span></li>`).join("")
    || `<li style="color:var(--gray)">경유점이 없습니다. 지도에서 찍어 추가하세요.</li>`;
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

// ------------------------------------------------------------ 장애물 모델 (서버와 같은 규칙)
function obsWindow(o) {
  const ts = o.path.map(p => p.t);
  return [o.t_start ?? (ts.length > 1 ? ts[0] : 0), o.t_end ?? (ts.length > 1 ? ts[ts.length - 1] : Infinity)];
}
function obsPos(o, t) {
  const [t0, t1] = obsWindow(o);
  if (t < t0 || t > t1 || !o.path.length) return null;
  const P = o.path;
  if (P.length === 1 || t <= P[0].t) return P[0];
  for (let q = 0; q < P.length - 1; q++) if (t <= P[q + 1].t) {
    const f = (t - P[q].t) / (P[q + 1].t - P[q].t);
    return { x: P[q].x + f * (P[q + 1].x - P[q].x), y: P[q].y + f * (P[q + 1].y - P[q].y), z: (P[q].z ?? 0) + f * ((P[q + 1].z ?? 0) - (P[q].z ?? 0)) };
  }
  return P[P.length - 1];
}
function tubeExtra(o, t) {             // 기회 제약 튜브의 추가 반경 κ_ε·σ(t)
  const e = Number($("eps").value);
  if (!e) return 0;
  const sig = (o.sigma0 || 0) + (o.sigma_v || 0) * Math.max(t - (o.t_obs || 0), 0);
  return Math.sqrt(2 * Math.log(1 / e)) * sig;
}
function zRange(o) {
  const top = S.sc.z[S.sc.z.length - 1] + 40, g0 = Math.min(...S.sc.ground);
  return [Math.max(o.z_min ?? 0, g0 - 10), Math.min(o.z_max ?? 1000, top)];
}
function midZ(o, p) { if (o.kind === "sphere") return p.z ?? 120; const [a, b] = zRange(o); return (a + b) / 2; }
function newObstacle(kind, x, y) {
  const n = S.obs.length + 1, zs = S.sc.z, zmid = Math.round(groundAt(x, y) + 90);
  const o = { name: `${KIND_KO[kind]} ${n}`, kind, path: [{ x, y, t: 0 }], radius: 100, half_x: 150, half_y: 80, z_min: 0, z_max: 1000,
              t_start: 0, t_end: null, margin: 15, sigma0: 0, sigma_v: 0, sigma_t: 0, t_obs: 0 };
  if (kind === "sphere") { o.radius = 60; o.path[0].z = Math.min(zmid, zs[zs.length - 1]); o.z_min = o.path[0].z - 60; o.z_max = o.path[0].z + 60; }
  return o;
}

function obsGeometry(o, extra) {
  const [zl, zh] = zRange(o);
  if (o.kind === "box") return new THREE.BoxGeometry(2 * (o.half_x + extra), 2 * (o.half_y + extra), zh - zl);
  if (o.kind === "sphere") return new THREE.SphereGeometry(o.radius + extra, 28, 18);
  return new THREE.CylinderGeometry(o.radius + extra, o.radius + extra, zh - zl, 44).rotateX(Math.PI / 2);
}
function drawObstacles() {
  // 장애물 몸체(실제 크기)와 바깥 껍질(안전 이격 + 불확실성), 선택한 장애물의 이동 경로와 경로점 구슬
  clearGroup(G.obs); clearGroup(G.edit);
  S.obs.forEach((o, k) => {
    const color = OBS_COLORS[k % OBS_COLORS.length], sel = k === S.sel;
    const body = new THREE.Mesh(obsGeometry(o, 0), new THREE.MeshLambertMaterial({ color, transparent: true, opacity: sel ? 0.62 : 0.45, depthWrite: false }));
    body.userData = { k, part: "body" };
    const shell = new THREE.Mesh(obsGeometry(o, 0), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.13, depthWrite: false, side: THREE.DoubleSide }));
    shell.userData = { k, part: "shell" };
    G.obs.add(body, shell);
    if (o.path.length > 1 || sel) {
      const pts = o.path.map(p => new THREE.Vector3(p.x, p.y, midZ(o, p)));
      if (pts.length > 1) {
        const ln = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineDashedMaterial({ color, dashSize: 30, gapSize: 18 }));
        ln.computeLineDistances();
        G.edit.add(ln);
      }
      if (sel) o.path.forEach((p, q) => {
        const h = new THREE.Mesh(new THREE.SphereGeometry(20, 16, 12), new THREE.MeshLambertMaterial({ color: 0xffffff, emissive: color, emissiveIntensity: 0.55 }));
        h.position.set(p.x, p.y, midZ(o, p));
        h.userData.handle = { type: "obs", k, q };
        G.edit.add(h);
        const lb = label(`${q + 1} · ${Math.round(p.t)} s`, hex(color));
        lb.scale.multiplyScalar(0.55);
        lb.position.set(p.x, p.y, midZ(o, p) + 50);
        G.edit.add(lb);
      });
    }
  });
  $("obscount").textContent = S.obs.length ? `· ${S.obs.length}개` : "";
  placeObstacles();
}
function placeObstacles() {
  const show = $("l_obs").checked;
  for (const m of G.obs.children) {
    const o = S.obs[m.userData.k], p = obsPos(o, S.t);
    m.visible = !!p && show;
    if (!p) continue;
    m.position.set(p.x, p.y, midZ(o, p));
    if (m.userData.part === "shell") {    // 바깥 껍질: 안전 이격 + 불확실성 팽창
      const ex = (o.margin || 0) + tubeExtra(o, S.t);
      const base = o.kind === "box" ? [o.half_x, o.half_y] : [o.radius, o.radius];
      m.scale.set((base[0] + ex) / base[0], (base[1] + ex) / base[1], o.kind === "sphere" ? (o.radius + ex) / o.radius : 1);
      m.visible = m.visible && ex > 0.5;
    }
  }
  G.edit.visible = show;
  needRender = true;
}

// ------------------------------------------------------------ 장애물 편집 패널
function markDirty() {
  if (S.plan) { S.dirty = true; $("dirty").style.display = "block"; $("miscard").style.display = "none"; S.mission = null; }
  updateTimeline();
}
function refreshList() {
  $("obslist").innerHTML = S.obs.map((o, k) => {
    const [a, b] = obsWindow(o);
    return `<li data-k="${k}" class="${k === S.sel ? "sel" : ""}"><span><b style="color:${hex(OBS_COLORS[k % OBS_COLORS.length])}">■</b> ${o.name} · ${KIND_KO[o.kind]} · ${o.path.length > 1 ? "이동" : "정지"}</span><span>${Math.round(a)}~${isFinite(b) ? Math.round(b) : "∞"} s</span></li>`;
  }).join("") || `<li style="color:var(--gray);cursor:default">장애물이 없습니다. 위 버튼을 누른 뒤 지도를 눌러 추가하세요.</li>`;
  refreshEditor();
  drawObstacles();
}
function refreshEditor() {
  const o = S.obs[S.sel];
  $("editor").style.display = o ? "block" : "none";
  if (!o) return;
  $("e_name").value = o.name; $("e_kind").value = o.kind; $("e_margin").value = o.margin;
  $("e_radius").value = o.radius; $("e_hx").value = o.half_x; $("e_hy").value = o.half_y;
  $("e_zmin").value = o.z_min; $("e_zmax").value = o.z_max;
  $("e_s0").value = o.sigma0 ?? 0; $("e_sv").value = o.sigma_v ?? 0; $("e_st").value = o.sigma_t ?? 0;
  $("e_t0").value = o.t_start ?? ""; $("e_t1").value = o.t_end ?? "";
  $("r_radius").style.display = o.kind === "box" ? "none" : "flex";
  $("r_box").style.display = o.kind === "box" ? "flex" : "none";
  $("r_z").style.display = o.kind === "sphere" ? "none" : "flex";
  $("th_z").style.display = o.kind === "sphere" ? "" : "none";
  $("pts").innerHTML = o.path.map((p, q) => {
    const v = q ? Math.hypot(p.x - o.path[q - 1].x, p.y - o.path[q - 1].y) / Math.max(p.t - o.path[q - 1].t, 1e-9) : null;
    return `<tr><td>${q + 1}</td><td><input type="number" data-q="${q}" data-f="x" value="${Math.round(p.x)}"></td>` +
      `<td><input type="number" data-q="${q}" data-f="y" value="${Math.round(p.y)}"></td>` +
      `<td style="display:${o.kind === "sphere" ? "" : "none"}"><input type="number" data-q="${q}" data-f="z" value="${Math.round(p.z ?? 120)}"></td>` +
      `<td><input type="number" data-q="${q}" data-f="t" value="${Math.round(p.t)}"></td><td>${v == null ? "–" : v.toFixed(1) + " m/s"}</td>` +
      `<td>${o.path.length > 1 ? `<button data-delpt="${q}" class="warn">✕</button>` : ""}</td></tr>`;
  }).join("");
}
function selectObs(k) { S.sel = k; refreshList(); }
function bindEditor() {
  const num = (id, key, post) => $(id).onchange = () => {
    const o = S.obs[S.sel]; if (!o) return;
    const v = $(id).value;
    o[key] = v === "" ? null : Number(v);
    post?.(o); markDirty(); refreshList();
  };
  $("e_name").onchange = () => { const o = S.obs[S.sel]; if (o) { o.name = $("e_name").value || o.name; refreshList(); } };
  $("e_kind").onchange = () => {
    const o = S.obs[S.sel]; if (!o) return;
    o.kind = $("e_kind").value;
    if (o.kind === "sphere") { o.path.forEach(p => { p.z = p.z ?? Math.round(groundAt(p.x, p.y) + 90); }); o.radius = Math.min(o.radius, 200); }
    markDirty(); refreshList();
  };
  num("e_margin", "margin"); num("e_radius", "radius"); num("e_hx", "half_x"); num("e_hy", "half_y");
  num("e_zmin", "z_min"); num("e_zmax", "z_max"); num("e_s0", "sigma0"); num("e_sv", "sigma_v"); num("e_st", "sigma_t");
  num("e_t0", "t_start"); num("e_t1", "t_end");
  $("pts").onchange = e => {
    const o = S.obs[S.sel], q = e.target.dataset.q, f = e.target.dataset.f;
    if (!o || q == null) return;
    o.path[+q][f] = Number(e.target.value);
    markDirty(); refreshList();
  };
  $("pts").onclick = e => {
    const q = e.target.dataset.delpt, o = S.obs[S.sel];
    if (q == null || !o) return;
    o.path.splice(+q, 1);
    markDirty(); refreshList();
  };
  $("obslist").onclick = e => { const li = e.target.closest("li[data-k]"); if (li) selectObs(+li.dataset.k); };
  document.querySelectorAll("[data-add]").forEach(b => b.onclick = () => setMode("addobs:" + b.dataset.add));
  $("addpt").onclick = () => setMode("addpt");
  $("delobs").onclick = () => { if (S.sel < 0) return; S.obs.splice(S.sel, 1); S.sel = Math.min(S.sel, S.obs.length - 1); markDirty(); refreshList(); };
  $("clear").onclick = () => { S.obs = []; S.sel = -1; markDirty(); refreshList(); };
  $("example").onclick = async () => {
    S.example = S.example || await api("/api/example");
    S.obs = JSON.parse(JSON.stringify(S.example.obstacles)); S.sel = S.obs.length ? 0 : -1;
    markDirty(); refreshList();
  };
  $("export").onclick = () => download("obstacles.json", JSON.stringify({ obstacles: S.obs }, null, 1), "application/json");
  $("import").onchange = async e => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const j = JSON.parse(await f.text());
      S.obs = Array.isArray(j) ? j : (j.obstacles || []); S.sel = S.obs.length ? 0 : -1;
      markDirty(); refreshList();
    } catch (ex) { $("errors").textContent = "JSON 파일을 읽지 못했습니다: " + ex.message; }
    e.target.value = "";
  };
  $("validate").onclick = validate;
}
async function validate() {
  try {
    const r = await api("/api/validate", { obstacles: S.obs });
    $("vstatus").innerHTML = r.errors.length ? `<span class="bad">${r.errors.join(" / ")}</span>` : `<span class="ok">장애물 ${r.valid.length}개 모두 허용 범위 안입니다</span>`;
  } catch (e) { $("vstatus").innerHTML = `<span class="bad">${e.message}</span>`; }
}

// ------------------------------------------------------------ 경로 그리기
function vz() {
  const d = S.site.drones.find(x => x.key === (S.plan?.drone?.key ?? $("drone").value));
  return [d.vz_up, d.vz_dn];
}
function buildTrack(res) {
  // 구간별 표본에 이륙·착륙 수직 구간을 붙여 하나의 시간 궤적으로 만든다 (순회의 중간 구간은 이착륙 없음)
  const [up, dn] = vz(), tr = [];
  for (const L of res.legs) {
    if (!L.feasible) break;
    const s = L.samples, a = s[0], b = s[s.length - 1], ga = groundAt(a.x, a.y), gb = groundAt(b.x, b.y);
    if (!L.air_start) tr.push({ t: a.t - (a.z - ga) / up - 1e-3, x: a.x, y: a.y, z: ga, v: 0, ws: a.ws, soc: a.soc, leg: L.name });
    for (const p of s) tr.push({ ...p, leg: L.name });
    if (!L.air_end) tr.push({ t: b.t + (b.z - gb) / dn, x: b.x, y: b.y, z: gb, v: 0, ws: b.ws, soc: b.soc, leg: L.name, landed: true });
  }
  return tr;
}
function tube(pts, color, radius) {
  // 호버 대기 구간은 같은 점이 여러 번 나온다. 겹친 점을 그대로 두면 곡선 계산이 깨지므로 걸러 낸다.
  pts = pts.filter((p, q) => q === 0 || p.distanceTo(pts[q - 1]) > 0.5);
  if (pts.length < 2) return new THREE.Group();
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
  clearGroup(G.path); clearGroup(G.base); clearGroup(G.drone);
  const res = S.plan;
  if (!res) { needRender = true; return; }
  for (const B of res.baseline || []) {
    const pts = B.samples.map(p => new THREE.Vector3(p.x, p.y, p.z));
    if (pts.length > 1) G.base.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x555E68 })));
  }
  for (const L of res.legs) {
    if (!L.feasible) break;
    const s = L.samples, color = legColor(L.name), a = s[0], b = s[s.length - 1];
    const pts = s.map(p => new THREE.Vector3(p.x, p.y, p.z));
    if (!L.air_start) pts.unshift(new THREE.Vector3(a.x, a.y, groundAt(a.x, a.y)));
    if (!L.air_end) pts.push(new THREE.Vector3(b.x, b.y, groundAt(b.x, b.y)));
    if (pts.length > 1) {
      // 건물 뒤에 가린 부분도 보이도록, 깊이 검사를 끈 반투명 관을 한 겹 더 그린다
      const ghost = tube(pts, color, 14);
      if (ghost.isMesh) {
        ghost.material.dispose();
        ghost.material = new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5, depthTest: false, depthWrite: false });
        ghost.renderOrder = 5;
      }
      G.path.add(tube(pts, color, 14), curtain(s, color), ghost);
    }
  }
  const body = new THREE.Mesh(new THREE.SphereGeometry(30, 20, 14), new THREE.MeshLambertMaterial({ color: 0xFFD23F, emissive: 0x5A4500 }));
  const drop = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]), new THREE.LineBasicMaterial({ color: 0x16202A }));
  G.drone.add(body, drop);
  G.drone.userData = { body, drop };
  setTime(S.t);
}

// ------------------------------------------------------------ 시간 재생
function updateTimeline() {
  let end = S.track.length ? S.track[S.track.length - 1].t : 0;
  for (const o of S.obs) { const [, b] = obsWindow(o); end = Math.max(end, isFinite(b) ? b : 0, ...o.path.map(p => p.t)); }
  S.tEnd = Math.max(Math.ceil(end), 120);
  $("tl").max = S.tEnd;
  if (S.t > S.tEnd) S.t = S.tEnd;
}
function stateAt(t) {
  const tr = S.track;
  if (!tr.length) return null;
  if (t <= tr[0].t) return tr[0];
  for (let q = 0; q < tr.length - 1; q++) if (t <= tr[q + 1].t) {
    const a = tr[q], b = tr[q + 1];
    if (a.landed && a.leg !== b.leg) return { ...a, v: 0 };       // 구간 사이: 도착지 지상 대기
    const f = (t - a.t) / Math.max(b.t - a.t, 1e-9);
    return { t, x: a.x + f * (b.x - a.x), y: a.y + f * (b.y - a.y), z: a.z + f * (b.z - a.z), v: b.v, ws: b.ws,
             soc: a.soc + f * (b.soc - a.soc), leg: b.leg };
  }
  return tr[tr.length - 1];
}
function setTime(t) {
  S.t = t;
  const st = stateAt(t), hud = $("hud");
  if (st && G.drone.userData.body) {
    const { body, drop } = G.drone.userData, g = groundAt(st.x, st.y);
    body.position.set(st.x, st.y, st.z);
    drop.position.set(st.x, st.y, st.z);
    drop.scale.set(1, 1, Math.max(st.z - g, 0.01));
    hud.style.display = "block";
    hud.innerHTML = `<b>${legKo(st.leg)}</b> · 출발 후 ${Math.round(t)} s<br>고도 해발 ${Math.round(st.z)} m · 지면 위 ${Math.round(st.z - g)} m<br>` +
      `대지속도 ${st.v.toFixed(1)} m/s · 바람 ${st.ws.toFixed(1)} m/s<br>SOC <b>${st.soc.toFixed(1)} %</b>`;
  } else hud.style.display = "none";
  $("tlab").textContent = `출발 후 ${Math.round(t)} s / ${Math.round(S.tEnd)} s`;
  placeObstacles();
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
  c.strokeStyle = "#D5DCE2"; c.fillStyle = "#4C5A66"; c.lineWidth = 1;
  const stepZ = (zHi - zLo) > 160 ? 50 : 25;
  for (let z = Math.ceil(zLo / stepZ) * stepZ; z <= zHi; z += stepZ) {
    c.beginPath(); c.moveTo(mL, Y(z)); c.lineTo(w - mR, Y(z)); c.stroke();
    c.fillText(`${z} m`, 6, Y(z) + 4);
  }
  const stepD = dMax > 4000 ? 1000 : 500;
  for (let d = 0; d <= dMax; d += stepD) c.fillText(`${(d / 1000).toFixed(1)} km`, X(d) - 14, h - 5);
  const area = (key, color) => {
    c.beginPath(); c.moveTo(X(0), Y(zLo));
    for (const r of rows) c.lineTo(X(r.d), Y(r[key]));
    c.lineTo(X(dMax), Y(zLo)); c.closePath(); c.fillStyle = color; c.fill();
  };
  area("s", "#B9C2CC"); area("g", "#C9B88A");
  c.setLineDash([4, 3]); c.strokeStyle = "#1B6CA8"; c.beginPath();
  let pen = false;
  for (const r of rows) { if (r.f == null) { pen = false; continue; } pen ? c.lineTo(X(r.d), Y(r.f)) : c.moveTo(X(r.d), Y(r.f)); pen = true; }
  c.stroke(); c.setLineDash([]);
  for (const L of legs) {
    const rr = rows.filter(r => r.leg === L.name), col = hex(legColor(L.name));
    c.strokeStyle = col; c.lineWidth = 2.2; c.beginPath();
    if (L.air_start) c.moveTo(X(rr[0].d), Y(rr[0].z)); else c.moveTo(X(rr[0].d), Y(rr[0].g));
    for (const r of rr) c.lineTo(X(r.d), Y(r.z));
    if (!L.air_end) c.lineTo(X(rr[rr.length - 1].d), Y(rr[rr.length - 1].g));
    c.stroke();
    c.fillStyle = col;
    c.fillText(legKo(L.name), X(rr[0].d) + 6, mT + 10);
  }
  c.fillStyle = "#4C5A66";
  c.fillText("■ 지형", w - 250, mT + 22); c.fillStyle = "#8D99A6"; c.fillText("■ 건물", w - 200, mT + 22);
  c.fillStyle = "#1B6CA8"; c.fillText("┄ 비행 가능 하한 (20 m 층)", w - 150, mT + 22);
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
function refreshMissionType() {
  const tour = $("mtype").value === "tour";
  $("r_tour").style.display = tour ? "block" : "none";
  $("r_ab").style.display = tour ? "none" : "flex";
  $("pickdst").style.display = tour ? "none" : "";
  drawMarks();
}

async function doPlan() {
  $("errors").textContent = ""; $("status").textContent = "계산 중…"; $("plan").disabled = true;
  try {
    const tour = $("mtype").value === "tour";
    if (tour && !S.tour.length) throw new Error("경유점을 하나 이상 지정하세요");
    const req = { drone: $("drone").value, payload: +$("payload").value, kind: $("kind").value, round_trip: $("rt").checked,
                  dep_hour: +$("hour").value, reserve: +$("reserve").value, wind: $("wind").value, hub: S.hub, dst: S.dst,
                  tour: tour ? S.tour : [], dwell: tour ? +$("dwell").value : 0, eps: $("eps").value || null, obstacles: S.obs };
    const res = await api("/api/plan", req);
    S.plan = res; S.mission = null; S.dirty = false; S.planDwell = req.dwell;
    $("dirty").style.display = "none";
    S.track = buildTrack(res);
    S.t = 0;
    updateTimeline();
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
    if (!L.feasible) return `<tr><td>${legKo(L.name)}</td><td colspan="6" class="bad">${L.reason}</td></tr>`;
    const zs = L.samples.map(p => p.z), cf = L.conflicts;
    const sep = !res.obstacles.length || cf.min_clearance == null ? "–" : `${cf.min_clearance.toFixed(0)} m`;
    return `<tr><td><b style="color:${hex(legColor(L.name))}">■</b> ${legKo(L.name)}</td><td>${L.E_kJ.toFixed(0)} kJ</td><td>${L.soc_leg.toFixed(1)} %p</td>` +
      `<td>${Math.round(L.T)} s</td><td>${(L.L / 1000).toFixed(2)} km</td><td>${Math.round(Math.min(...zs))}–${Math.round(Math.max(...zs))} m</td>` +
      `<td class="${cf.n_collision_samples ? "bad" : "ok"}">${sep}</td></tr>`;
  });
  const ok = res.legs.filter(L => L.feasible);
  if (ok.length > 1) rows.push(`<tr style="font-weight:bold"><td>합계</td><td>${ok.reduce((a, L) => a + L.E_kJ, 0).toFixed(0)} kJ</td>` +
    `<td>${res.soc_used.toFixed(1)} %p</td><td>${Math.round(S.track.length ? S.track[S.track.length - 1].t - S.track[0].t : 0)} s</td>` +
    `<td>${(ok.reduce((a, L) => a + L.L, 0) / 1000).toFixed(2)} km</td><td></td><td></td></tr>`);
  $("r_legs").innerHTML = rows.join("");
  // 장애물을 피하려고 추가로 기다린 시간 (경유점 체류는 뺀다)
  const extra = L => L.wait - (L.air_start ? (S.planDwell || 0) : 0) + (L.air_start ? 0 : L.ground_delay);
  const waits = ok.filter(L => extra(L) > 0.5).map(L => `${legKo(L.name)}에서 ${Math.round(extra(L))} s 대기`);
  const nb = (res.baseline || []).filter(b => b.conflicts.n_collision_samples).length;
  $("r_note").textContent = `${res.drone.label} · 탑재 ${res.drone.payload} kg · ${res.dep_hour}시 출발` + (waits.length ? ` · ${waits.join(" · ")}` : "") +
    (res.obstacles.length ? ` · 장애물을 무시한 경로는 ${nb}개 구간에서 충돌` : "");
  $("miscard").style.display = ok.length ? "block" : "none";
  $("m_tab").style.display = "none";
  const d = S.site.drones.find(x => x.key === res.drone.key);
  $("m_up").textContent = d.vz_up; $("m_dn").textContent = d.vz_dn;
}

async function getMission() {
  if (!S.mission) {
    S.mission = await api("/api/mission", {});
    $("m_tab").style.display = "table";
    $("m_rows").innerHTML = S.mission.legs.map(L => { const s = L.summary;
      return `<tr><td>${legKo(L.name)}</td><td>${s.n_items}</td><td>${s.n_waypoints}</td><td>${s.n_speed_changes}</td>` +
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
      const base = `${S.site.site.key}_${key}_${m.dep_hour}h_${L.name}`;
      if (kind === "wpl") download(`${base}.waypoints`, L.wpl, "text/plain");
      else download(`${base}.csv`, "﻿" + L.csv, "text/csv");
    }
  } catch (e) { $("errors").textContent = e.message; }
}

// ------------------------------------------------------------ 지도 조작: 찍기·선택·끌기
const HINT0 = "왼쪽 끌기: 회전 · 오른쪽 끌기: 이동 · 휠: 확대 · 표식(구슬)을 끌면 위치 이동";
function setMode(m) {
  S.mode = S.mode === m ? null : m;
  $("pickhub").classList.toggle("active", S.mode === "hub");
  $("pickdst").classList.toggle("active", S.mode === "dst");
  $("addvia").classList.toggle("active", S.mode === "via");
  $("addpt").classList.toggle("active", S.mode === "addpt");
  document.querySelectorAll("[data-add]").forEach(b => b.classList.toggle("active", S.mode === "addobs:" + b.dataset.add));
  const txt = { hub: "지도에서 출발지를 누르세요", dst: "지도에서 도착지를 누르세요", via: "지도에서 경유점을 차례로 누르세요 (끝내려면 버튼을 다시 누름)",
                addpt: "지도에서 장애물의 다음 경로점을 누르세요 (끝내려면 버튼을 다시 누름)" };
  $("hint").textContent = !S.mode ? HINT0 : S.mode.startsWith("addobs") ? "지도에서 장애물을 놓을 곳을 누르세요" : txt[S.mode];
  renderer.domElement.style.cursor = S.mode ? "crosshair" : "";
}
function applyClick(x, y) {
  const m = S.mode;
  if (m === "hub" || m === "dst") { S[m] = [x, y]; setMode(null); drawMarks(); markDirty(); }
  else if (m === "via") { S.tour.push([x, y]); drawMarks(); markDirty(); }
  else if (m?.startsWith("addobs")) {
    S.obs.push(newObstacle(m.split(":")[1], x, y)); S.sel = S.obs.length - 1;
    setMode(null); markDirty(); refreshList();
  } else if (m === "addpt") {
    const o = S.obs[S.sel]; if (!o) return;
    const last = o.path[o.path.length - 1], d = Math.hypot(x - last.x, y - last.y);
    const p = { x, y, t: Math.round(last.t + Math.max(d / 12, 5)) };       // 기본 12 m/s
    if (o.kind === "sphere") p.z = last.z;
    o.path.push(p);
    markDirty(); refreshList();
  }
}
function moveHandle(h, x, y) {
  if (h.type === "hub") S.hub = [x, y];
  else if (h.type === "dst") S.dst = [x, y];
  else if (h.type === "via") S.tour[h.n] = [x, y];
  else if (h.type === "obs") { const p = S.obs[h.k].path[h.q]; p.x = x; p.y = y; }
}
function initPointer() {
  const el = renderer.domElement, ray = new THREE.Raycaster();
  let down = null, drag = null;
  const cast = e => {
    const r = el.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), camera);
  };
  const terrainHit = () => ray.intersectObjects(G.terrain.children, false)[0]?.point;
  const handles = () => [...G.edit.children, ...G.marks.children.flatMap(g => g.children ?? [])].filter(o => o.userData?.handle);
  el.addEventListener("pointerdown", e => {
    if (e.button !== 0) return;
    down = [e.clientX, e.clientY];
    if (S.mode) return;
    cast(e);
    const h = ray.intersectObjects(handles(), false)[0];
    if (h) {                               // 구슬을 잡았다: 지도 회전을 잠그고 끌어 옮긴다
      drag = { h: h.object.userData.handle, moved: false };
      controls.enabled = false;
      el.setPointerCapture(e.pointerId);
      if (drag.h.type === "obs" && drag.h.k !== S.sel) selectObs(drag.h.k);
    }
  });
  el.addEventListener("pointermove", e => {
    if (!drag) return;
    cast(e);
    const p = terrainHit();
    if (!p) return;
    drag.moved = true;
    moveHandle(drag.h, p.x, p.y);
    if (drag.h.type === "obs") drawObstacles(); else drawMarks();
  });
  el.addEventListener("pointerup", e => {
    if (drag) {
      const moved = drag.moved, h = drag.h;
      drag = null; controls.enabled = true;
      if (moved) { markDirty(); if (h.type === "obs") refreshList(); else drawMarks(); }
      return;
    }
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
    cast(e);
    if (S.mode) { const p = terrainHit(); if (p) applyClick(p.x, p.y); return; }
    const hit = ray.intersectObjects(G.obs.children.filter(m => m.visible && m.userData.part === "body"), false)[0];
    if (hit) selectObs(hit.object.userData.k);
  });
}

// 지도 좌표 → 화면 좌표 (자동 시험과 디버깅용)
S.toScreen = (x, y, z) => {
  const v = new THREE.Vector3(x, y, (z ?? groundAt(x, y)) * root.scale.z).project(camera), r = renderer.domElement.getBoundingClientRect();
  return [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
};
S.midZ = (k, q) => midZ(S.obs[k], S.obs[k].path[q]);

// ------------------------------------------------------------ 시작
async function init() {
  initThree();
  [S.site, S.sc] = await Promise.all([api("/api/site"), api("/api/scene3d")]);
  S.hub = [...S.sc.hub]; S.dst = [...S.sc.dst]; S.tour = (S.sc.tour || []).map(p => [...p]);
  $("drone").innerHTML = S.site.drones.map(d => `<option value="${d.key}">${d.label}</option>`).join("");
  $("drone").value = "x650";
  $("hour").innerHTML = S.sc.hours.map(h => `<option value="${h}"${h === 15 ? " selected" : ""}>${h}시</option>`).join("");
  $("wk").innerHTML = S.sc.z.map((z, k) => `<option value="${k}">해발 ${z} m</option>`).join("");
  $("wk").value = Math.min(4, S.sc.z.length - 1);
  $("payload").value = 1.0;
  $("mtype").value = S.tour.length ? "tour" : "ab";
  refreshDrone(); refreshWindInfo();
  root.scale.z = +$("zs").value;
  refreshMissionType();
  buildScene(); initPointer(); bindEditor(); refreshList(); updateTimeline(); setTime(0);
  $("sub").textContent = `${S.site.date} · ${S.site.site.label} ${(S.site.W / 1000).toFixed(1)}×${(S.site.H / 1000).toFixed(1)} km · 건물 ${S.sc.buildings.length.toLocaleString()}동`;

  $("drone").onchange = () => { refreshDrone(); markDirty(); };
  $("hour").onchange = () => { refreshWindInfo(); markDirty(); if ($("l_wind").checked) loadWind(); };
  $("mtype").onchange = () => { refreshMissionType(); markDirty(); frame(); };
  $("eps").onchange = () => { placeObstacles(); markDirty(); };
  for (const id of ["payload", "kind", "rt", "reserve", "wind", "dwell"]) $(id).addEventListener("change", markDirty);
  $("plan").onclick = doPlan;
  $("pickhub").onclick = () => setMode("hub");
  $("pickdst").onclick = () => setMode("dst");
  $("addvia").onclick = () => setMode("via");
  $("viadef").onclick = () => { S.tour = (S.sc.tour || []).map(p => [...p]); drawMarks(); markDirty(); };
  $("viaclr").onclick = () => { S.tour = []; drawMarks(); markDirty(); };
  $("vialist").onclick = e => {
    const d = e.target.dataset;
    if (d.rm != null) S.tour.splice(+d.rm, 1);
    else if (d.up != null) S.tour.splice(+d.up - 1, 2, S.tour[+d.up], S.tour[+d.up - 1]);
    else if (d.dn != null) S.tour.splice(+d.dn, 2, S.tour[+d.dn + 1], S.tour[+d.dn]);
    else return;
    drawMarks(); markDirty();
  };
  $("dl_wpl").onclick = () => dl("wpl");
  $("dl_csv").onclick = () => dl("csv");
  $("l_bld").onchange = e => { G.bld.visible = e.target.checked; needRender = true; };
  $("l_nfz").onchange = e => { G.nfz.visible = e.target.checked; needRender = true; };
  $("l_floor").onchange = e => { G.floor.visible = e.target.checked; needRender = true; };
  $("l_base").onchange = e => { G.base.visible = e.target.checked; needRender = true; };
  $("l_obs").onchange = placeObstacles;
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
