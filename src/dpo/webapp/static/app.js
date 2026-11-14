// 드론 3D 시공간 경로계획 – 동적 장애물 플랫폼 (vanilla JS)
"use strict";
const $ = (id) => document.getElementById(id);
const S = { site: null, img: new Image(), obs: [], sel: -1, mode: null, res: null, t: 0, playing: false,
            hub: null, dst: null, drag: null, dirty: false, ghosts: [] };
function markChanged() {
  if (S.res) { S.dirty = true; $("replanbar").style.display = "block"; }
}
// 계획 이후의 장애물 표시: 변경 이력(revision) 중 시각 t 에 유효한 판을 사용
function obsForTime(t) {
  if (!S.res || S.dirty || !S.res.revisions) return S.obs;
  let cur = S.res.revisions[0].obstacles;
  S.res.revisions.forEach(r => { if (r.t <= t) cur = r.obstacles; });
  return cur;
}
const KIND_KO = { cylinder: "원기둥", sphere: "구", box: "박스" };
const COLORS = ["#9B5DE5", "#E07B39", "#2CA58D", "#D7263D", "#F2B134", "#0B3954", "#8F2D56"];

// ------------------------------------------------------------ 좌표 변환
const cv = $("map"), ctx = cv.getContext("2d");
const W = () => S.site.W, H = () => S.site.H;
const toPx = (x, y) => [x / W() * cv.width, cv.height - y / H() * cv.height];
const toM = (px, py) => [px / cv.width * W(), (cv.height - py) / cv.height * H()];
const mScale = () => cv.width / W();
function evPos(e) {
  const r = cv.getBoundingClientRect();
  return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height];
}

// ------------------------------------------------------------ 장애물 모델 (서버와 동일 규칙)
function obsWindow(o) {
  const ts = o.path.map(p => p.t);
  const t0 = o.t_start ?? (ts.length > 1 ? ts[0] : 0);
  const t1 = o.t_end ?? (ts.length > 1 ? ts[ts.length - 1] : Infinity);
  return [t0, t1];
}
function obsPos(o, t) {
  const [t0, t1] = obsWindow(o);
  if (t < t0 || t > t1 || !o.path.length) return null;
  const P = o.path;
  if (P.length === 1 || t <= P[0].t) return P[0];
  for (let q = 0; q < P.length - 1; q++) {
    if (t <= P[q + 1].t) {
      const f = (t - P[q].t) / (P[q + 1].t - P[q].t);
      return { x: P[q].x + f * (P[q + 1].x - P[q].x), y: P[q].y + f * (P[q + 1].y - P[q].y),
               z: (P[q].z ?? 0) + f * ((P[q + 1].z ?? 0) - (P[q].z ?? 0)) };
    }
  }
  return P[P.length - 1];
}
function newObstacle(kind, x, y) {
  const n = S.obs.length + 1;
  const zmid = 140;
  const o = { name: `${KIND_KO[kind]} ${n}`, kind, path: [{ x, y, t: 0 }], radius: 120, half_x: 150, half_y: 80,
              z_min: 0, z_max: 1000, t_start: 0, t_end: null, margin: 15,
              sigma0: 10, sigma_v: 0.5, sigma_t: 2, t_obs: 0 };
  if (kind === "sphere") { o.radius = 80; o.path[0].z = zmid; o.z_min = zmid - 80; o.z_max = zmid + 80; }
  return o;
}

// ------------------------------------------------------------ 그리기
function tubeExtra(o, t) {
  // 기회 제약 튜브의 추가 반경 κ_ε·σ(t) (서버 obstacles.risk_kappa 와 같은 식)
  const e = Number($("eps").value);
  if (!e) return 0;
  const A = S.site && S.site.adsb;
  if ($("shape").value === "adsb" && A && o.path.length > 1 && A.q[String(e)]) {
    // 실측 ADS-B 분위수 곡선 × 장애물 속도 (서버 apply_shape 와 같은 규칙, 관측 시각 이후 τ)
    const P = o.path, ts = P.map(p => p.t), tob = o.t_obs || 0;
    let k = 0; while (k < P.length - 2 && ts[k + 1] <= tob) k++;
    const V = Math.hypot(P[k + 1].x - P[k].x, P[k + 1].y - P[k].y) / Math.max(P[k + 1].t - P[k].t, 1e-9);
    const kn = A.knots, q = A.q[String(e)], tau = Math.min(Math.max(t - tob, 0), A.horizon || 20);
    let r;
    if (tau >= kn[kn.length - 1]) { const n = kn.length; r = q[n - 1] + (q[n - 1] - q[n - 2]) / (kn[n - 1] - kn[n - 2]) * (tau - kn[n - 1]); }
    else { let j = 0; while (kn[j + 1] < tau) j++; r = q[j] + (q[j + 1] - q[j]) * (tau - kn[j]) / (kn[j + 1] - kn[j]); }
    return V * r;
  }
  const sig = (o.sigma0 || 0) + (o.sigma_v || 0) * Math.max(t - (o.t_obs || 0), 0);
  return Math.sqrt(2 * Math.log(1 / e)) * sig;
}
function drawShape(o, p, color, alpha) {
  const [px, py] = toPx(p.x, p.y);
  const sc = mScale();
  const ex = tubeExtra(o, S.t);
  if (ex > 0) {
    ctx.save(); ctx.globalAlpha = 0.13; ctx.fillStyle = color; ctx.beginPath();
    if (o.kind === "box") { const w = o.half_x + o.margin + ex, h = o.half_y + o.margin + ex; ctx.fillRect(px - w * sc, py - h * sc, 2 * w * sc, 2 * h * sc); }
    else { ctx.arc(px, py, (o.radius + o.margin + ex) * sc, 0, 2 * Math.PI); ctx.fill(); }
    ctx.restore();
  }
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color; ctx.strokeStyle = color; ctx.lineWidth = 2;
  if (o.kind === "box") {
    ctx.fillRect(px - o.half_x * sc, py - o.half_y * sc, 2 * o.half_x * sc, 2 * o.half_y * sc);
    ctx.globalAlpha = 1; ctx.strokeRect(px - o.half_x * sc, py - o.half_y * sc, 2 * o.half_x * sc, 2 * o.half_y * sc);
  } else {
    ctx.beginPath(); ctx.arc(px, py, o.radius * sc, 0, 2 * Math.PI); ctx.fill();
    ctx.globalAlpha = 1; ctx.stroke();
    if (o.margin > 0) { ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.arc(px, py, (o.radius + o.margin) * sc, 0, 2 * Math.PI); ctx.stroke(); ctx.setLineDash([]); }
  }
  ctx.restore();
}
function drawPathLine(samples, color, width, dash) {
  if (!samples || !samples.length) return;
  ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []);
  ctx.beginPath();
  samples.forEach((s, i) => { const [px, py] = toPx(s.x, s.y); i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
  ctx.stroke(); ctx.restore();
}
function sampleAt(samples, t) {
  if (!samples || !samples.length) return null;
  if (t <= samples[0].t) return samples[0];
  for (let q = 0; q < samples.length - 1; q++) {
    if (t <= samples[q + 1].t) {
      const a = samples[q], b = samples[q + 1], f = (t - a.t) / Math.max(b.t - a.t, 1e-9);
      return { x: a.x + f * (b.x - a.x), y: a.y + f * (b.y - a.y), z: a.z + f * (b.z - a.z), soc: a.soc };
    }
  }
  return null;
}
function marker(x, y, shape, fill, label) {
  const [px, py] = toPx(x, y);
  ctx.save(); ctx.fillStyle = fill; ctx.strokeStyle = "#16202A"; ctx.lineWidth = 2;
  ctx.beginPath();
  if (shape === "sq") ctx.rect(px - 7, py - 7, 14, 14);
  else if (shape === "star") { for (let k = 0; k < 10; k++) { const r = k % 2 ? 4 : 10, a = Math.PI / 5 * k - Math.PI / 2; ctx.lineTo(px + r * Math.cos(a), py + r * Math.sin(a)); } ctx.closePath(); }
  else ctx.arc(px, py, 6, 0, 2 * Math.PI);
  ctx.fill(); ctx.stroke();
  if (label) { ctx.font = "bold 12px sans-serif"; ctx.fillStyle = "#16202A"; ctx.fillText(label, px + 10, py - 8); }
  ctx.restore();
}
function draw() {
  if (!S.site) return;
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (S.img.complete) ctx.drawImage(S.img, 0, 0, cv.width, cv.height);
  // 정적 비행금지구역 (예시)
  const [nx, ny, nr] = S.site.nfz;
  ctx.save(); const [qx, qy] = toPx(nx, ny); ctx.fillStyle = "rgba(215,38,61,0.22)"; ctx.strokeStyle = "#D7263D";
  ctx.setLineDash([5, 4]); ctx.beginPath(); ctx.arc(qx, qy, nr * mScale(), 0, 2 * Math.PI); ctx.fill(); ctx.stroke(); ctx.restore();
  // 장애물 궤적 + 현재 위치
  const shown = obsForTime(S.t);
  shown.forEach((o, i) => {
    const col = COLORS[i % COLORS.length];
    if (o.path.length > 1) {
      ctx.save(); ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.setLineDash([6, 4]); ctx.beginPath();
      o.path.forEach((p, k) => { const [px, py] = toPx(p.x, p.y); k ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
      ctx.stroke(); ctx.restore();
    }
    if (i === S.sel && shown === S.obs) o.path.forEach((p, k) => {
      const [px, py] = toPx(p.x, p.y);
      ctx.save(); ctx.fillStyle = "#fff"; ctx.strokeStyle = col; ctx.lineWidth = 2; ctx.fillRect(px - 5, py - 5, 10, 10); ctx.strokeRect(px - 5, py - 5, 10, 10);
      ctx.font = "11px sans-serif"; ctx.fillStyle = col; ctx.fillText(`t=${p.t}`, px + 7, py + 12); ctx.restore();
    });
    const p = obsPos(o, S.t);
    if (p) {
      drawShape(o, p, col, i === S.sel ? 0.45 : 0.32);
      const [px, py] = toPx(p.x, p.y);
      ctx.save(); ctx.font = "bold 11px sans-serif"; ctx.fillStyle = "#16202A";
      const zt = o.kind === "sphere" ? `z≈${Math.round(p.z)}±${o.radius}` : `${o.z_min}~${o.z_max} m`;
      ctx.fillText(`${o.name} (${zt})`, px + 6, py - 6); ctx.restore();
    }
  });
  // 계획 결과
  if (S.res) {
    (S.res.legs || []).forEach(l => {
      if (!l.samples) return;
      drawPathLine(l.samples, "rgba(27,108,168,0.9)", 4);
      (l.waypoints || []).forEach(w => {
        if (w.wait > 0) { const [px, py] = toPx(w.x, w.y); ctx.save(); ctx.strokeStyle = "#E46C0A"; ctx.lineWidth = 3;
          ctx.beginPath(); ctx.arc(px, py, 10, 0, 2 * Math.PI); ctx.stroke(); ctx.font = "bold 12px sans-serif"; ctx.fillStyle = "#E46C0A";
          ctx.fillText(`대기 ${w.wait.toFixed(0)} s`, px + 12, py + 4); ctx.restore(); }
      });
    });
    (S.res.baseline || []).forEach(b => {          // 비교 경로는 위에 점선으로
      drawPathLine(b.samples, "#3A3A3A", 1.8, [7, 5]);
      (b.conflicts?.collisions || []).forEach(([name, t]) => {
        const s = sampleAt(b.samples, t); if (!s) return;
        const [px, py] = toPx(s.x, s.y); ctx.save(); ctx.strokeStyle = "#D7263D"; ctx.lineWidth = 3;
        ctx.beginPath(); ctx.moveTo(px - 6, py - 6); ctx.lineTo(px + 6, py + 6); ctx.moveTo(px + 6, py - 6); ctx.lineTo(px - 6, py + 6); ctx.stroke(); ctx.restore();
      });
    });
    S.ghosts.forEach(g => drawPathLine(g, "#E46C0A", 2, [3, 4]));
    const allp = (S.res.legs || []).flatMap(l => l.samples || []);
    (S.res.replans || []).forEach((e, k) => {
      if (!e.replanned) return;
      const p = sampleAt(allp, e.t_switch); if (!p) return;
      const [px, py] = toPx(p.x, p.y);
      ctx.save(); ctx.fillStyle = "#E46C0A"; ctx.strokeStyle = "#16202A"; ctx.lineWidth = 1.5; ctx.beginPath();
      for (let q = 0; q < 10; q++) { const r = q % 2 ? 5 : 12, a = Math.PI / 5 * q - Math.PI / 2; ctx.lineTo(px + r * Math.cos(a), py + r * Math.sin(a)); }
      ctx.closePath(); ctx.fill(); ctx.stroke(); ctx.font = "bold 12px sans-serif"; ctx.fillStyle = "#E46C0A";
      ctx.fillText(`재계획 #${k + 1} (t=${e.t_switch.toFixed(0)} s)`, px + 14, py - 6); ctx.restore();
    });
    // 현재 시각의 드론
    const all = (S.res.legs || []).flatMap(l => l.samples || []);
    const d = sampleAt(all, S.t);
    if (d) marker(d.x, d.y, "o", "#FFD23F", `드론 z=${Math.round(d.z)} m`);
    const ball = (S.res.baseline || []).flatMap(b => b.samples || []);
    const d0 = sampleAt(ball, S.t);
    if (d0) marker(d0.x, d0.y, "o", "#BDBDBD", null);
  }
  marker(S.hub[0], S.hub[1], "sq", "#FFFFFF", "물류거점");
  marker(S.dst[0], S.dst[1], "star", "#FFD23F", "목적지");
  drawChart();
}

// ------------------------------------------------------------ 고도·SOC 차트
function drawChart() {
  const c = $("chart"), g = c.getContext("2d");
  g.clearRect(0, 0, c.width, c.height);
  const pad = { l: 46, r: 46, t: 10, b: 24 }, w = c.width - pad.l - pad.r, h = c.height - pad.t - pad.b;
  const T = Number($("tslider").max) || 300;
  const zr = [S.site.z[0] - 10, S.site.z[S.site.z.length - 1] / 2 + 60];
  const X = t => pad.l + t / T * w, Y = z => pad.t + h - (z - zr[0]) / (zr[1] - zr[0]) * h, Ys = s => pad.t + h - s / 100 * h;
  g.strokeStyle = "#ccc"; g.font = "11px sans-serif"; g.fillStyle = "#4C5A66";
  for (let k = 0; k <= 4; k++) { const z = zr[0] + k * (zr[1] - zr[0]) / 4; g.beginPath(); g.moveTo(pad.l, Y(z)); g.lineTo(pad.l + w, Y(z)); g.stroke(); g.fillText(Math.round(z), 6, Y(z) + 4); }
  g.fillStyle = "#E46C0A";
  for (let k = 0; k <= 4; k++) g.fillText(`${k * 25}`, c.width - pad.r + 6, Ys(k * 25) + 4);
  g.fillStyle = "#4C5A66"; g.fillText("고도[m] (파랑)", pad.l + 4, pad.t + 34); g.fillText("SOC[%] (주황)", c.width - pad.r - 80, pad.t + 34);
  for (let k = 0; k <= 5; k++) { const t = k * T / 5; g.fillText(`${Math.round(t)}s`, X(t) - 8, c.height - 6); }
  // 장애물 존재 구간 (상단 띠)
  S.obs.forEach((o, i) => { const [a, b] = obsWindow(o); g.fillStyle = COLORS[i % COLORS.length]; g.globalAlpha = 0.35;
    g.fillRect(X(Math.max(a, 0)), pad.t + i * 5, X(Math.min(b, T)) - X(Math.max(a, 0)), 4); g.globalAlpha = 1; });
  const line = (arr, key, fy, color, dash) => { if (!arr.length) return; g.save(); g.strokeStyle = color; g.lineWidth = 2; g.setLineDash(dash || []);
    g.beginPath(); arr.forEach((s, i) => { const px = X(s.t), py = fy(s[key]); i ? g.lineTo(px, py) : g.moveTo(px, py); }); g.stroke(); g.restore(); };
  if (S.res) {
    line((S.res.baseline || []).flatMap(b => b.samples), "z", Y, "#9E9E9E", [5, 4]);
    const all = (S.res.legs || []).flatMap(l => l.samples || []);
    line(all, "z", Y, "#1B6CA8");
    line(all.filter(s => s.soc != null), "soc", Ys, "#E46C0A");
  }
  g.strokeStyle = "#D7263D"; g.beginPath(); g.moveTo(X(S.t), pad.t); g.lineTo(X(S.t), pad.t + h); g.stroke();
}

// ------------------------------------------------------------ 목록·편집기
function refreshList() {
  const ul = $("obslist"); ul.innerHTML = "";
  S.obs.forEach((o, i) => {
    const li = document.createElement("li");
    if (i === S.sel) li.className = "sel";
    const [a, b] = obsWindow(o);
    li.innerHTML = `<span><b style="color:${COLORS[i % COLORS.length]}">■</b> ${o.name} · ${KIND_KO[o.kind]} · ${o.path.length > 1 ? "이동" : "정지"}</span><span>${a}~${isFinite(b) ? b : "∞"} s</span>`;
    li.onclick = () => { S.sel = i; refreshList(); refreshEditor(); draw(); };
    ul.appendChild(li);
  });
  refreshEditor();
  updateTimeRange();
}
function refreshEditor() {
  const o = S.obs[S.sel];
  $("editor").style.display = o ? "block" : "none";
  if (!o) return;
  $("e_name").value = o.name; $("e_kind").value = o.kind; $("e_margin").value = o.margin;
  $("e_radius").value = o.radius; $("e_hx").value = o.half_x; $("e_hy").value = o.half_y;
  $("e_zmin").value = o.z_min; $("e_zmax").value = o.z_max;
  $("e_t0").value = o.t_start ?? ""; $("e_t1").value = o.t_end ?? "";
  $("e_s0").value = o.sigma0 ?? 0; $("e_sv").value = o.sigma_v ?? 0; $("e_st").value = o.sigma_t ?? 0;
  $("r_radius").style.display = o.kind === "box" ? "none" : "flex";
  $("r_box").style.display = o.kind === "box" ? "flex" : "none";
  $("r_z").style.display = o.kind === "sphere" ? "none" : "flex";
  $("th_z").style.display = o.kind === "sphere" ? "" : "none";
  const tb = $("pts"); tb.innerHTML = "";
  o.path.forEach((p, k) => {
    const tr = document.createElement("tr");
    let v = "";
    if (k > 0) { const q = o.path[k - 1]; v = (Math.hypot(p.x - q.x, p.y - q.y) / Math.max(p.t - q.t, 1e-9)).toFixed(1) + " m/s"; }
    tr.innerHTML = `<td>${k + 1}</td><td><input type="number" data-k="${k}" data-f="x" value="${Math.round(p.x)}"></td>
      <td><input type="number" data-k="${k}" data-f="y" value="${Math.round(p.y)}"></td>
      <td style="display:${o.kind === "sphere" ? "" : "none"}"><input type="number" data-k="${k}" data-f="z" value="${Math.round(p.z ?? 140)}"></td>
      <td><input type="number" data-k="${k}" data-f="t" value="${p.t}" step="5"></td><td>${v}</td>
      <td>${o.path.length > 1 ? `<button data-del="${k}" class="warn" style="padding:1px 5px">×</button>` : ""}</td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach(inp => inp.onchange = () => {
    o.path[+inp.dataset.k][inp.dataset.f] = Number(inp.value); markChanged(); refreshList(); draw(); });
  tb.querySelectorAll("button[data-del]").forEach(b => b.onclick = () => { o.path.splice(+b.dataset.del, 1); markChanged(); refreshList(); draw(); });
}
function bindEditor() {
  const num = (id, f, nullable) => $(id).onchange = () => { const o = S.obs[S.sel]; if (!o) return;
    const v = $(id).value; o[f] = (nullable && v === "") ? null : Number(v); markChanged(); refreshList(); draw(); };
  $("e_name").onchange = () => { S.obs[S.sel].name = $("e_name").value; markChanged(); refreshList(); };
  $("e_kind").onchange = () => { const o = S.obs[S.sel]; o.kind = $("e_kind").value;
    if (o.kind === "sphere") o.path.forEach(p => p.z = p.z ?? 140); markChanged(); refreshList(); draw(); };
  num("e_margin", "margin"); num("e_radius", "radius"); num("e_hx", "half_x"); num("e_hy", "half_y");
  num("e_zmin", "z_min"); num("e_zmax", "z_max"); num("e_t0", "t_start", true); num("e_t1", "t_end", true);
  num("e_s0", "sigma0"); num("e_sv", "sigma_v"); num("e_st", "sigma_t");
}

// ------------------------------------------------------------ 지도 상호작용
function hit(px, py) {
  // 선택된 장애물의 경로점 → 다른 장애물 본체 순으로 탐색
  if (S.sel >= 0) {
    const o = S.obs[S.sel];
    for (let k = 0; k < o.path.length; k++) { const [qx, qy] = toPx(o.path[k].x, o.path[k].y); if (Math.hypot(px - qx, py - qy) < 9) return { i: S.sel, k }; }
  }
  for (let i = S.obs.length - 1; i >= 0; i--) {
    const o = S.obs[i], p = obsPos(o, S.t) || o.path[0];
    const [qx, qy] = toPx(p.x, p.y);
    const r = (o.kind === "box" ? Math.max(o.half_x, o.half_y) : o.radius) * mScale();
    if (Math.hypot(px - qx, py - qy) < Math.max(r, 8)) return { i, k: o.path.length === 1 ? 0 : -1 };
  }
  return null;
}
cv.addEventListener("mousedown", e => {
  const [px, py] = evPos(e), [x, y] = toM(px, py);
  if (S.mode && S.mode.startsWith("add:")) {
    S.obs.push(newObstacle(S.mode.slice(4), x, y)); S.sel = S.obs.length - 1; setMode(null); markChanged(); refreshList(); draw(); return;
  }
  if (S.mode === "addpt") {
    const o = S.obs[S.sel]; const last = o.path[o.path.length - 1];
    const d = Math.hypot(x - last.x, y - last.y);
    o.path.push({ x, y, t: Math.round(last.t + Math.max(10, d / 20)), z: last.z });   // 기본 20 m/s
    if (o.t_end != null && o.t_end < o.path[o.path.length - 1].t) o.t_end = null;
    markChanged(); refreshList(); draw(); return;
  }
  if (S.mode === "hub" || S.mode === "dst") { S[S.mode] = [x, y]; setMode(null); S.res = null; draw(); return; }
  const h = hit(px, py);
  if (h) { S.sel = h.i; S.drag = h; S.dragMoved = false; refreshList(); draw(); }
});
cv.addEventListener("mousemove", e => {
  if (!S.drag) return;
  const [px, py] = evPos(e), [x, y] = toM(px, py);
  const o = S.obs[S.drag.i];
  if (!S.dragMoved) { S.dragMoved = true; markChanged(); }
  if (S.drag.k >= 0) { o.path[S.drag.k].x = x; o.path[S.drag.k].y = y; }
  else { const p0 = obsPos(o, S.t) || o.path[0]; const dx = x - p0.x, dy = y - p0.y; o.path.forEach(p => { p.x += dx; p.y += dy; }); }
  draw();
});
window.addEventListener("mouseup", () => { if (S.drag) { S.drag = null; refreshEditor(); } });

function setMode(m) {
  S.mode = m;
  document.querySelectorAll("button[data-add]").forEach(b => b.classList.toggle("active", m === "add:" + b.dataset.add));
  $("addpt").classList.toggle("active", m === "addpt");
  $("mvhub").classList.toggle("active", m === "hub"); $("mvdst").classList.toggle("active", m === "dst");
  $("status").textContent = m ? { addpt: "지도를 클릭해 경로점 추가 (다시 누르면 종료)", hub: "지도에서 거점 위치 클릭", dst: "지도에서 목적지 위치 클릭" }[m] || "지도를 클릭해 장애물 배치" : "";
}

// ------------------------------------------------------------ 시간·재생
function updateTimeRange() {
  let T = 200;
  S.obs.forEach(o => o.path.forEach(p => T = Math.max(T, p.t)));
  if (S.res) (S.res.legs || []).forEach(l => (l.samples || []).forEach(s => T = Math.max(T, s.t)));
  $("tslider").max = Math.ceil(T / 10) * 10;
}
function setT(t) { S.t = t; $("tslider").value = t; $("tlabel").textContent = `t = ${t.toFixed(0)} s (${fmtClock(t)})`;
  $("rp_t").textContent = `t = ${t.toFixed(0)} s`; draw(); }
function fmtClock(t) { const dep = Number($("dep").value) * 3600 + t; const h = Math.floor(dep / 3600), m = Math.floor(dep % 3600 / 60), s = Math.floor(dep % 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`; }
let lastTs = null;
function loop(ts) {
  if (!S.playing) { lastTs = null; return; }
  if (lastTs != null) { let t = S.t + (ts - lastTs) / 1000 * Number($("speed").value); if (t > Number($("tslider").max)) t = 0; setT(t); }
  lastTs = ts; requestAnimationFrame(loop);
}

// ------------------------------------------------------------ 서버 호출
async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function request() {
  return { obstacles: S.obs, hub: S.hub, dst: S.dst, dep_hour: Number($("dep").value), payload: Number($("payload").value),
           kind: $("kind").value, round_trip: $("round").checked, allow_wait: $("allowwait").checked,
           max_wait: Number($("maxwait").value), wind: $("wind").value, reserve: Number($("reserve").value),
           weight: Number($("weight").value), eps: $("eps").value === "" ? null : Number($("eps").value),
           tube_shape: $("shape").value };
}
function showErrors(errs) { $("errors").innerHTML = (errs || []).map(e => `• ${e}`).join("<br>"); }
async function doValidate() {
  try { const r = await api("/api/validate", { obstacles: S.obs }); showErrors(r.errors);
    $("status").textContent = r.errors.length ? `허용 범위를 벗어난 장애물 ${r.errors.length}건 (계획에서 제외됨)` : "모든 장애물이 허용 범위 안입니다"; }
  catch (e) { showErrors([e.message]); }
}
async function doPlan() {
  $("status").textContent = "계획 중…"; $("plan").disabled = true;
  const t0 = performance.now();
  try {
    const r = await api("/api/plan", request());
    S.res = r; S.dirty = false; S.ghosts = []; $("replanbar").style.display = "none";
    showErrors(r.errors); showResult(r);
    $("status").textContent = `완료 (${((performance.now() - t0) / 1000).toFixed(1)} s, 위험구간 셀 ${r.table_cells.toLocaleString()}개)`;
    updateTimeRange(); setT(0);
  } catch (e) { showErrors([e.message]); $("status").textContent = "실패"; }
  $("plan").disabled = false;
}
async function doReplan() {
  if (!S.res) return;
  const lastRev = S.res.revisions ? S.res.revisions[S.res.revisions.length - 1].t : 0;
  if (S.t < lastRev) { showErrors([`변경 시각(t=${S.t.toFixed(0)} s)은 직전 변경(t=${lastRev.toFixed(0)} s) 이후여야 합니다`]); return; }
  $("status").textContent = `t=${S.t.toFixed(0)} s 변경 반영 중…`; $("replan").disabled = true;
  const t0 = performance.now();
  const prev = (S.res.legs || []).map(l => (l.samples || []).filter(s => s.t >= S.t));
  try {
    const r = await api("/api/replan", { obstacles: S.obs, t_now: S.t });
    const ev = r.replans[r.replans.length - 1];
    if (ev.replanned) S.ghosts = S.ghosts.concat(prev.filter(g => g.length > 1));
    S.res = r; S.dirty = false; $("replanbar").style.display = "none";
    showErrors(r.errors); showResult(r);
    $("status").textContent = `${ev.reason} (검사 ${ev.check_ms.toFixed(0)} ms${ev.cpu ? `, 계산 ${ev.cpu.toFixed(1)} s` : ""}, 총 ${((performance.now() - t0) / 1000).toFixed(1)} s)`;
    updateTimeRange(); setT(S.t);
  } catch (e) { showErrors([e.message]); $("status").textContent = "실패"; }
  $("replan").disabled = false;
}
function showResult(r) {
  $("rescard").style.display = "block";
  $("r_soc").textContent = `${r.soc_end.toFixed(1)}%`;
  $("r_feas").innerHTML = r.feasible ? `<span class="ok">임무 가능 (예비 ${$("reserve").value}% 이상)</span>` : `<span class="bad">임무 불가</span>`;
  $("r_legs").innerHTML = r.legs.map(l => l.feasible ? `<tr><td>${l.name === "out" ? "갈 때" : "올 때"}</td><td>${l.E_kJ.toFixed(1)} kJ</td><td>${l.soc_leg.toFixed(1)}%p</td>
      <td>${l.T.toFixed(0)} s</td><td>${l.wait.toFixed(0)} s</td><td>${(l.L / 1000).toFixed(2)} km</td>
      <td class="${l.conflicts.n_collision_samples ? "bad" : "ok"}">${l.conflicts.min_clearance == null ? "–" : l.conflicts.min_clearance.toFixed(0) + " m"}</td><td>${l.cpu.toFixed(2)} s</td></tr>`
    : `<tr><td>${l.name === "out" ? "갈 때" : "올 때"}</td><td colspan="7" class="bad">${l.reason}</td></tr>`).join("");
  const nb = (r.baseline || []).reduce((a, b) => a + (b.conflicts.n_collision_samples ? 1 : 0), 0);
  const names = [...new Set((r.baseline || []).flatMap(b => (b.conflicts.collisions || []).map(c => c[0])))];
  $("r_hist").innerHTML = (r.replans || []).length ? "<b>변경 이력</b><br>" + r.replans.map((e, k) =>
    `• t=${e.t.toFixed(0)} s 발표: ${e.reason} <span style="color:#4C5A66">(검사 ${e.check_ms.toFixed(0)} ms${e.cpu ? `, 재계획 ${e.cpu.toFixed(1)} s` : ""})</span>`).join("<br>") : "";
  $("r_base").innerHTML = nb ? `<span class="bad">비교: 장애물을 무시한 경로는 ${names.join(", ")} 와(과) 충돌</span>`
                             : `<span class="ok">비교: 장애물을 무시한 경로도 충돌 없음</span>`;
}

// ------------------------------------------------------------ 초기화
async function init() {
  S.site = await api("/api/site");
  S.hub = S.site.hub.slice(); S.dst = S.site.dst.slice();
  S.img.onload = draw; S.img.src = "/basemap.png";
  $("siteinfo").textContent = `${S.site.site?.label ?? "성남 판교·분당"} ${(S.site.W / 1000).toFixed(1)}×${(S.site.H / 1000).toFixed(1)} km · 실제 지형/건물 · ${S.site.date} · ${S.site.drone.name}`;
  S.site.wind.slice(0, -1).forEach(w => { const o = document.createElement("option"); o.value = w.hour;
    o.textContent = `${w.hour}:00 (100 m ${w.speed.toFixed(1)} m/s, ${Math.round(w.dir_from)}°)`; $("dep").appendChild(o); });
  $("dep").value = 15;
  document.querySelectorAll("button[data-add]").forEach(b => b.onclick = () => setMode(S.mode === "add:" + b.dataset.add ? null : "add:" + b.dataset.add));
  $("addpt").onclick = () => setMode(S.mode === "addpt" ? null : "addpt");
  $("mvhub").onclick = () => setMode(S.mode === "hub" ? null : "hub");
  $("mvdst").onclick = () => setMode(S.mode === "dst" ? null : "dst");
  $("delobs").onclick = () => { S.obs.splice(S.sel, 1); S.sel = -1; markChanged(); refreshList(); draw(); };
  $("clear").onclick = () => { S.obs = []; S.sel = -1; S.res = null; refreshList(); draw(); };
  $("example").onclick = async () => { const j = await api("/api/example"); S.obs = j.obstacles; S.sel = 0; S.res = null; refreshList(); draw(); };
  $("eps").onchange = () => draw();
  $("shape").onchange = () => draw();
  $("export").onclick = () => { const blob = new Blob([JSON.stringify({ meta: request(), obstacles: S.obs }, null, 1)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "scenario.json"; a.click(); };
  $("import").onchange = async e => { const f = e.target.files[0]; if (!f) return; const j = JSON.parse(await f.text());
    S.obs = j.obstacles || []; S.sel = S.obs.length ? 0 : -1; S.res = null; refreshList(); draw(); };
  $("validate").onclick = doValidate; $("plan").onclick = doPlan;
  $("replan").onclick = doReplan; $("replanall").onclick = doPlan;
  $("tslider").oninput = () => setT(Number($("tslider").value));
  $("play").onclick = () => { S.playing = !S.playing; $("play").textContent = S.playing ? "❚❚ 정지" : "▶ 재생"; if (S.playing) requestAnimationFrame(loop); };
  bindEditor(); refreshList(); setT(0);
}
init();
