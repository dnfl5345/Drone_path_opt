// 드론 실시간 운용 화면 – SSE 로 상태를 받아 지도·계기·이벤트를 갱신하고, 비행 중 입력을 서버에 보낸다.
"use strict";
const $ = (id) => document.getElementById(id);
const L = { site: null, img: new Image(), snap: null, plan: [], pending: [], trail: [], tel: [], session: null,
            mode: null, clickA: null, frames: 0, lastFps: performance.now(), fps: 0 };
const cv = $("map"), ctx = cv.getContext("2d");
const W = () => L.site.W, H = () => L.site.H;
const toPx = (x, y) => [x / W() * cv.width, cv.height - y / H() * cv.height];
const toM = (px, py) => [px / cv.width * W(), (cv.height - py) / cv.height * H()];
const sc = () => cv.width / W();
function evPos(e) { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) * cv.width / r.width, (e.clientY - r.top) * cv.height / r.height]; }

async function api(path, body) {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function toast(msg) { const b = $("banner"); b.textContent = msg; b.style.background = "#16202A"; b.style.display = "block";
  clearTimeout(toast.t); toast.t = setTimeout(() => { b.style.display = "none"; b.style.background = ""; }, 2500); }

// ------------------------------------------------------------ 그리기
function circle(x, y, r, fill, stroke, dash) {
  const [px, py] = toPx(x, y); ctx.beginPath(); ctx.arc(px, py, Math.max(r * sc(), 2), 0, 2 * Math.PI);
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.setLineDash(dash || []); ctx.stroke(); ctx.setLineDash([]); }
}
function poly(pts, color, width, dash) {
  if (!pts || pts.length < 2) return;
  ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = width; ctx.setLineDash(dash || []); ctx.beginPath();
  pts.forEach((p, k) => { const [px, py] = toPx(p[0], p[1]); k ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
  ctx.stroke(); ctx.restore();
}
function marker(x, y, label, shape) {
  const [px, py] = toPx(x, y);
  ctx.save(); ctx.fillStyle = "#fff"; ctx.strokeStyle = "#16202A"; ctx.lineWidth = 2;
  if (shape === "sq") { ctx.fillRect(px - 7, py - 7, 14, 14); ctx.strokeRect(px - 7, py - 7, 14, 14); }
  else { ctx.beginPath(); ctx.arc(px, py, 7, 0, 2 * Math.PI); ctx.fillStyle = "#F2B134"; ctx.fill(); ctx.stroke(); }
  ctx.font = "bold 12px sans-serif"; ctx.fillStyle = "#16202A"; ctx.fillText(label, px + 10, py - 8); ctx.restore();
}
function draw() {
  if (!L.site) return;
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (L.img.complete) ctx.drawImage(L.img, 0, 0, cv.width, cv.height);
  const [nx, ny, nr] = L.site.nfz;
  ctx.save(); ctx.globalAlpha = 0.9; circle(nx, ny, nr, "rgba(215,38,61,0.18)", "#D7263D", [5, 4]); ctx.restore();
  marker(...L.site.hub, "거점", "sq"); marker(...L.site.dst, "목적지", "dot");
  const s = L.snap;
  if (s && s.state !== "idle") {
    poly(L.plan, "#1B6CA8", 3);
    poly(L.pending, "#E46C0A", 3, [8, 5]);
    poly(L.trail, "#16202A", 2);
    // 예측 + 튜브
    (s.predictions || []).forEach(p => {
      ctx.save(); ctx.globalAlpha = 0.9; poly([[p.x, p.y], [p.x1, p.y1]], "rgba(215,38,61,0.55)", 1.5, [4, 4]); ctx.restore();
      circle(p.x, p.y, p.r, "rgba(215,38,61,0.12)", "rgba(215,38,61,0.6)", [3, 3]);
    });
    // 실제 장애물·침입기
    (s.obstacles || []).forEach(o => {
      const col = o.src === "user" ? "#7A3E9D" : "#D7263D";
      if (o.kind === "box") {
        const [px, py] = toPx(o.x, o.y); ctx.save(); ctx.fillStyle = col + "55"; ctx.strokeStyle = col; ctx.lineWidth = 2;
        ctx.fillRect(px - o.hx * sc(), py - o.hy * sc(), 2 * o.hx * sc(), 2 * o.hy * sc());
        ctx.strokeRect(px - o.hx * sc(), py - o.hy * sc(), 2 * o.hx * sc(), 2 * o.hy * sc()); ctx.restore();
      } else circle(o.x, o.y, o.r, col + "55", col);
      const [px, py] = toPx(o.x, o.y);
      ctx.save(); ctx.font = "bold 11px sans-serif"; ctx.fillStyle = col; ctx.fillText(`${o.id} ${o.src === "replay" ? "(ADS-B)" : o.src === "report" ? "(보고)" : ""}`, px + 6, py - 6); ctx.restore();
    });
    // 드론
    const d = s.drone; const [px, py] = toPx(d.x, d.y);
    ctx.save(); ctx.beginPath(); ctx.arc(px, py, 9, 0, 2 * Math.PI); ctx.fillStyle = s.sep != null && s.sep < 30 ? "#D7263D" : "#1E7A63";
    ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = "#fff"; ctx.stroke();
    ctx.font = "bold 12px sans-serif"; ctx.fillStyle = "#16202A";
    ctx.fillText(`${d.soc.toFixed(1)}% · ${Math.round(d.z)} m`, px + 12, py + 4); ctx.restore();
  }
  if (L.mode === "move" && L.clickA) circle(L.clickA[0], L.clickA[1], Number($("o_r").value), "rgba(122,62,157,.25)", "#7A3E9D", [4, 3]);
}
function drawChart() {
  const c = $("chart"), g = c.getContext("2d"), w = c.width, h = c.height;
  g.clearRect(0, 0, w, h);
  const T = L.tel; if (T.length < 2) return;
  const t1 = Math.max(T[T.length - 1].t, 60), pad = 28;
  const X = t => pad + (w - pad - 8) * t / t1;
  g.strokeStyle = "#ddd"; g.beginPath(); g.moveTo(pad, h - 16); g.lineTo(w - 8, h - 16); g.stroke();
  g.font = "10px sans-serif"; g.fillStyle = "#4C5A66"; g.fillText("0", pad - 8, h - 4); g.fillText(`${Math.round(t1)} s`, w - 40, h - 4);
  // SOC (0–100%)
  g.strokeStyle = "#2CA58D"; g.lineWidth = 2; g.beginPath();
  T.forEach((p, k) => { const y = 6 + (h - 24) * (1 - p.soc / 100); k ? g.lineTo(X(p.t), y) : g.moveTo(X(p.t), y); }); g.stroke();
  g.fillStyle = "#2CA58D"; g.fillText("SOC", 2, 14);
  // 이격 (0–600 m, 로그 아님)
  g.strokeStyle = "#D7263D"; g.lineWidth = 1.5; g.beginPath(); let on = false;
  T.forEach(p => { if (p.sep == null) { on = false; return; } const y = 6 + (h - 24) * (1 - Math.min(Math.max(p.sep, 0), 600) / 600);
    on ? g.lineTo(X(p.t), y) : g.moveTo(X(p.t), y); on = true; }); g.stroke();
  g.fillStyle = "#D7263D"; g.fillText("이격(0–600 m)", 2, 26);
}

// ------------------------------------------------------------ 상태 반영
function fmt(x, d = 0) { return x == null ? "–" : Number(x).toFixed(d); }
function apply(s) {
  if (s.session !== L.session) { L.session = s.session; L.trail = []; L.tel = []; L.plan = []; L.pending = []; $("events").innerHTML = ""; }
  L.snap = s;
  const running = s.state === "running";
  $("pause").disabled = !running; $("stop").disabled = !running;
  ["inject", "addobs", "addmov"].forEach(id => $(id).disabled = !running);
  $("pause").textContent = s.paused ? "재개" : "일시정지";
  if (s.state === "idle") { draw(); return; }
  if (s.plan !== undefined) { L.plan = s.plan; L.pending = s.pending_plan || []; }
  if (s.pending == null) L.pending = []; else if (s.pending_plan && s.pending_plan.length) L.pending = s.pending_plan;
  const d = s.drone;
  const last = L.trail[L.trail.length - 1];
  if (!last || Math.hypot(last[0] - d.x, last[1] - d.y) > 4) L.trail.push([d.x, d.y]);
  if (!L.tel.length || s.t - L.tel[L.tel.length - 1].t >= 1) L.tel.push({ t: s.t, soc: d.soc, sep: s.sep });
  $("phase").textContent = `· ${s.phase}${s.leg ? (s.leg === "out" ? " (갈 때)" : " (올 때)") : ""}${s.paused ? " · 일시정지" : ""}`;
  $("clock").textContent = `${s.clock} · ×${s.speed}`;
  $("g_t").innerHTML = `${fmt(s.t)} <small>s</small>`;
  $("g_soc").innerHTML = `${fmt(d.soc, 1)} <small>%</small>`;
  $("socfill").style.width = `${Math.max(d.soc, 0)}%`;
  $("socfill").style.background = d.soc < d.usable_soc ? "#D7263D" : d.soc < d.usable_soc + 10 ? "#E46C0A" : "#2CA58D";
  $("socres").style.left = `${d.usable_soc}%`; $("g_res").textContent = d.usable_soc;
  $("g_e").innerHTML = `${fmt(d.E / 1000, 1)} <small>kJ</small>`;
  $("g_sep").innerHTML = s.sep == null ? "–" : `${fmt(s.sep)} <small>m ${s.sep_id || ""}</small>`;
  $("g_min").innerHTML = s.min_sep == null ? "–" : `${fmt(s.min_sep)} <small>m</small>`;
  $("g_al").innerHTML = `${s.stats.alerts} / <span style="color:${s.stats.collisions ? "#D7263D" : "inherit"}">${s.stats.collisions}</span>`;
  $("g_rp").innerHTML = `${s.stats.replans} / ${s.stats.refined}`;
  $("g_cpu").innerHTML = s.stats.cpu_last == null ? "–" : `${fmt(s.stats.cpu_last, 2)} <small>s</small>`;
  $("g_chk").innerHTML = s.stats.check_ms == null ? "–" : `${fmt(s.stats.check_ms, 1)} <small>ms</small>`;
  $("g_state").textContent = (s.replanning ? "재계획 계산 중… " : "") + (s.pending != null ? `경로 교체 예약 t=${s.pending} s · ` : "")
    + `예상 계산 ${fmt(s.est_cpu, 2)} s × ${s.speed}배속 · 감시 ${s.stats.checks}회 · 늦은 계산 ${s.stats.late} · 완화 ${s.stats.degraded} · 실패 ${s.stats.fail}`
    + ` · 화면 ${L.fps.toFixed(0)} fps · 시계 주기 p95 ${fmt(s.stats.tick_p95)} ms (최대 ${fmt(s.stats.tick_max)} ms)`;
  const ul = $("events");
  (s.events || []).forEach(e => {
    const li = document.createElement("li"); li.className = e.kind;
    li.innerHTML = `<span class="t">t=${e.t}</span><span class="m"></span>`; li.querySelector(".m").textContent = e.msg;
    ul.insertBefore(li, ul.firstChild);
    if (e.kind === "alert") { const b = $("banner"); b.textContent = e.msg; b.style.background = ""; b.style.display = "block";
      clearTimeout(apply.t); apply.t = setTimeout(() => b.style.display = "none", 3000); }
  });
  L.frames++; const now = performance.now();
  if (now - L.lastFps > 1000) { L.fps = L.frames * 1000 / (now - L.lastFps); L.frames = 0; L.lastFps = now; }
  draw(); drawChart();
}

// ------------------------------------------------------------ 연결 (SSE, 끊기면 자동 재연결)
function connect() {
  const es = new EventSource("/api/live/stream");
  es.onopen = () => { $("conn").textContent = "실시간 연결"; $("conn").className = "on"; };
  es.onmessage = (m) => { try { apply(JSON.parse(m.data)); } catch (e) { console.error(e); } };
  es.onerror = () => { $("conn").textContent = "재연결 중…"; $("conn").className = ""; };
  window.__es = es;
}

// ------------------------------------------------------------ 입력
function request() {
  return { dep_hour: Number($("dep").value), payload: Number($("payload").value), kind: $("kind").value,
           round_trip: $("round").checked, eps: $("eps").value === "" ? null : Number($("eps").value), shape: $("shape").value,
           period: Number($("period").value), latency: Number($("latency").value), speed: Number($("speed").value),
           refine: $("refine").checked, obstacles: [] };
}
function setMode(m, hint) {
  L.mode = L.mode === m ? null : m; L.clickA = null;
  $("addobs").classList.toggle("active", L.mode === "static"); $("addmov").classList.toggle("active", L.mode === "move");
  $("modehint").style.display = L.mode ? "block" : "none"; $("modehint").textContent = hint || "";
}
function bind() {
  $("start").onclick = async () => { try { await api("/api/live/start", request()); } catch (e) { toast(e.message); } };
  $("pause").onclick = () => api("/api/live/control", { action: L.snap && L.snap.paused ? "resume" : "pause" });
  $("stop").onclick = () => api("/api/live/control", { action: "stop" });
  $("speed").onchange = () => { if (L.snap && L.snap.state === "running") api("/api/live/control", { speed: Number($("speed").value) }); };
  $("inject").onclick = async () => {
    try { const r = await api("/api/live/intruder", { speed: Number($("i_speed").value), radius: Number($("i_r").value), lead: Number($("i_lead").value) });
      toast(`침입기 ${r.id} 투입`); } catch (e) { toast(e.message); } };
  $("addobs").onclick = () => setMode("static", "지도를 클릭해 정지 장애물을 놓으세요 (반경은 오른쪽 입력값)");
  $("addmov").onclick = () => setMode("move", "이동 장애물: 시작점을 클릭한 뒤 끝점을 클릭하세요 (지금 출발, 입력 속도로 등속 이동)");
  cv.addEventListener("click", async (ev) => {
    if (!L.snap || L.snap.state !== "running") return;
    const [px, py] = evPos(ev); const [x, y] = toM(px, py);
    const R = Number($("o_r").value), t = L.snap.t;
    try {
      if (L.mode === "static") {
        await api("/api/live/obstacle", { obstacle: { name: "사용자 정지", kind: "cylinder", radius: R, margin: 15, z_min: 0, z_max: 1000,
          path: [{ x, y, t: 0 }], t_start: t, t_end: null } }); setMode(null);
      } else if (L.mode === "move") {
        if (!L.clickA) { L.clickA = [x, y]; $("modehint").textContent = "끝점을 클릭하세요"; draw(); return; }
        const [ax, ay] = L.clickA, v = Number($("o_v").value), dur = Math.max(Math.hypot(x - ax, y - ay) / v, 1);
        await api("/api/live/obstacle", { obstacle: { name: "사용자 이동", kind: "cylinder", radius: R, margin: 15, z_min: 0, z_max: 1000,
          path: [{ x: ax, y: ay, t: t }, { x, y, t: t + dur }], t_start: t, t_end: t + dur } }); setMode(null);
      } else {   // 클릭한 장애물 삭제
        const hit = (L.snap.obstacles || []).find(o => Math.hypot(o.x - x, o.y - y) <= (o.r || Math.max(o.hx, o.hy)) + 20);
        if (hit && confirm(`${hit.id} (${hit.name}) 를 삭제할까요?`)) await api("/api/live/obstacle/delete", { id: hit.id });
      }
    } catch (e) { toast(e.message); setMode(null); }
  });
}

async function init() {
  L.site = await api("/api/site");
  const dep = $("dep");
  L.site.wind.forEach(w => { const o = document.createElement("option"); o.value = w.hour;
    o.textContent = `${w.hour}:00 (100 m ${w.speed.toFixed(1)} m/s)`; dep.appendChild(o); });
  dep.value = 15;
  L.img.onload = draw; L.img.src = "/basemap.png";
  bind(); connect(); draw();
}
init();
