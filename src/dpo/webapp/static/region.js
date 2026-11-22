// 지역 선택·새 지역 만들기·장소 검색 (3D 지도 화면). map3d.js 가 window.__map3d 에 상태(S)와 S.setPoint 를 연다.
const $ = id => document.getElementById(id);
const esc = t => String(t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const R = { regions: null, map: null, rect: null, marker: null, center: null, poll: null };

async function loadRegions() {
  R.regions = await api("/api/regions");
  const sel = $("rg_sel");
  sel.innerHTML = R.regions.sites.map(s => `<option value="${esc(s.key)}"${s.ready ? "" : " disabled"}>${esc(s.label)}${s.user ? "" : " (기본)"}${s.ready ? "" : " – 자료 없음"}</option>`).join("");
  sel.value = R.regions.current;
  const cur = R.regions.sites.find(s => s.key === R.regions.current);
  $("rg_wind").style.display = cur && cur.user ? "" : "none";
  if (R.regions.job.state === "running") showJob();
}

async function openRegion(key) {
  $("rg_sel").disabled = true;
  document.body.style.cursor = "progress";
  try {
    await api("/api/region/open", { key });
    location.reload();
  } catch (e) {
    alert("지역을 열지 못했습니다: " + e.message);
    $("rg_sel").disabled = false; $("rg_sel").value = R.regions.current; document.body.style.cursor = "";
  }
}

// ---------------------------------------------------------------- 새 지역 대화창
function sizeKm() { return +$("rg_size").value; }
function drawArea() {
  if (!R.map || !R.center) return;
  const [lat, lon] = R.center, h = sizeKm() * 500;
  const dla = h / (111132.92 - 559.82 * Math.cos(2 * lat * Math.PI / 180)), dlo = h / (111412.84 * Math.cos(lat * Math.PI / 180));
  const b = [[lat - dla, lon - dlo], [lat + dla, lon + dlo]];
  if (R.rect) R.rect.setBounds(b); else R.rect = L.rectangle(b, { color: "#004E8F", weight: 2, fillOpacity: 0.08 }).addTo(R.map);
  if (R.marker) R.marker.setLatLng(R.center); else R.marker = L.marker(R.center).addTo(R.map);
  R.map.fitBounds(b, { padding: [30, 30], maxZoom: 15 });
  $("rg_center").textContent = `가운데 ${lat.toFixed(5)}, ${lon.toFixed(5)} · ${sizeKm()} × ${sizeKm()} km (지도를 눌러 옮김)`;
  $("rg_make").disabled = R.regions?.job?.state === "running";
}
function setCenter(lat, lon, name) {
  R.center = [lat, lon];
  if (name && !$("rg_name").value) $("rg_name").value = name.split(",")[0].slice(0, 40);
  drawArea();
}
function openDialog() {
  $("rg_dlg").style.display = "flex";
  if (!R.map && window.L) {
    R.map = L.map("rg_map", { zoomControl: true }).setView([36.4, 127.8], 7);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap 기여자" }).addTo(R.map);
    R.map.on("click", e => setCenter(e.latlng.lat, e.latlng.lng));
  }
  if (!window.L) $("rg_map").innerHTML = '<div class="hint" style="padding:12px">지도를 불러오지 못했습니다. 위에서 장소를 찾거나 위도, 경도를 넣으십시오.</div>';
  setTimeout(() => R.map && R.map.invalidateSize(), 50);
  $("rg_q").focus();
}
async function search(q, list, onPick) {
  list.innerHTML = `<li>찾는 중…</li>`;
  try {
    const r = await api("/api/geo/search", { q });
    if (!r.items.length) { list.innerHTML = `<li>찾지 못했습니다. 다른 이름이나 위도, 경도로 넣어 보십시오.</li>`; return; }
    list.innerHTML = "";
    r.items.forEach(it => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${esc(it.name)} <span class="hint">(${it.lat.toFixed(5)}, ${it.lon.toFixed(5)} · ${esc(it.source)})</span></span><span class="acts"></span>`;
      onPick(it, li.querySelector(".acts"));
      list.appendChild(li);
    });
  } catch (e) { list.innerHTML = `<li style="color:#C0201B">${esc(e.message)}</li>`; }
}

function showJob() {
  const box = $("rg_log");
  const tick = async () => {
    let r;
    try { r = (await api("/api/regions")).job; } catch (e) { return; }
    box.innerHTML = r.log.map(l => `<li>[${l.step}/${l.n}] ${esc(l.msg)} <span style="color:#999">${l.t} s</span></li>`).join("");
    const run = r.state === "running";
    $("rg_make").disabled = run || !R.center; $("rg_cancel").style.display = run ? "" : "none"; $("rg_wind").disabled = run;
    if (run) return;
    clearInterval(R.poll); R.poll = null;
    if (r.state === "done" && r.key) {
      box.innerHTML += `<li><b>${r.kind === "wind" ? "예보 바람을 새로 받았습니다" : "완료"}</b> – 지역을 엽니다…</li>`;
      openRegion(r.key);
    } else if (r.state === "failed") box.innerHTML += `<li style="color:#C0201B"><b>실패:</b> ${esc(r.error || "")}</li>`;
  };
  if (!R.poll) R.poll = setInterval(tick, 1000);
  tick();
}

async function make() {
  if (!R.center) return;
  try {
    await api("/api/region/create", { name: $("rg_name").value.trim(), lat: R.center[0], lon: R.center[1], size_km: sizeKm() });
    showJob();
  } catch (e) { $("rg_log").innerHTML = `<li style="color:#C0201B">${esc(e.message)}</li>`; }
}

// ---------------------------------------------------------------- 임무 설정의 장소 검색 → 출발지·도착지
function pickButtons(it, acts) {
  if (!it.inside) { acts.innerHTML = `<span class="hint">이 지역 밖</span>`; return; }
  for (const [type, lab] of [["hub", "출발지로"], ["dst", "도착지로"], ["via", "경유점 추가"]]) {
    const b = document.createElement("button");
    b.textContent = lab;
    b.onclick = () => { window.__map3d.setPoint(type, it.x, it.y); $("pt_res").innerHTML = ""; };
    acts.appendChild(b);
  }
}

function init() {
  loadRegions().catch(e => console.warn(e));
  $("rg_sel").onchange = e => openRegion(e.target.value);
  $("rg_new").onclick = openDialog;
  $("rg_close").onclick = () => { $("rg_dlg").style.display = "none"; };
  $("rg_size").onchange = drawArea;
  $("rg_make").onclick = make;
  $("rg_cancel").onclick = () => api("/api/region/cancel", {}).then(showJob);
  $("rg_wind").onclick = async () => {
    if (!confirm("이 지역의 예보 바람을 받을 수 있는 최신(00Z)으로 바꿉니다. 1분 정도 걸립니다.")) return;
    try { await api("/api/region/wind", {}); openDialog(); showJob(); } catch (e) { alert(e.message); }
  };
  const go = () => search($("rg_q").value, $("rg_res"), (it, acts) => {
    const b = document.createElement("button"); b.textContent = "이곳으로";
    b.onclick = () => setCenter(it.lat, it.lon, it.name);
    acts.appendChild(b);
  });
  $("rg_go").onclick = go;
  $("rg_q").onkeydown = e => { if (e.key === "Enter") go(); };
  const gp = () => search($("pt_q").value, $("pt_res"), pickButtons);
  $("pt_go").onclick = gp;
  $("pt_q").onkeydown = e => { if (e.key === "Enter") gp(); };
}
init();
