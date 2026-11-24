// 사용자 매뉴얼의 '지역' 장 화면: 지역 선택 → 새 지역 대화창 → 진행 → 만든 지역 → 장소 검색으로 출발·도착 지정.
//   node scripts/capture_region.js <서버 주소> <출력 폴더> <위도,경도> <이름>
const { chromium } = require("playwright");
const fs = require("fs");
const [URL0, OUT, LL, NAME] = [process.argv[2], process.argv[3] || "docs/manual_img", process.argv[4], process.argv[5] || "새지역"];
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  const log = [];
  // 표시: [{sel, n, text}] – 요소를 패널 안에서 보이게 한 뒤 테두리·번호를 덧그린다
  async function mark(items) {
    for (const it of items) await pg.$eval(it.sel, e => e.scrollIntoView({ block: "center" })).catch(() => {});
    await pg.waitForTimeout(250);
    await pg.evaluate(items => {
      for (const it of items) {
        const e = document.querySelector(it.sel); if (!e) continue;
        const r = e.getBoundingClientRect(), pad = it.pad ?? 4;
        const box = document.createElement("div"); box.className = "__mk";
        Object.assign(box.style, { position: "fixed", left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px",
          height: r.height + 2 * pad + "px", border: "3px solid #E0201B", borderRadius: "6px", zIndex: 99999, pointerEvents: "none",
          boxShadow: "0 0 0 2px rgba(255,255,255,.8)" });
        const tag = document.createElement("div"); tag.className = "__mk"; tag.textContent = it.n + (it.text ? "  " + it.text : "");
        const left = it.side === "left" ? r.left - pad - 8 : r.right + pad + 6;
        Object.assign(tag.style, { position: "fixed", top: Math.max(4, r.top - pad - 2) + "px", zIndex: 100000, pointerEvents: "none",
          background: "#E0201B", color: "#fff", font: "bold 15px 'Malgun Gothic','NanumGothic',sans-serif", padding: "3px 9px",
          borderRadius: "12px", whiteSpace: "nowrap", boxShadow: "0 1px 4px rgba(0,0,0,.35)" });
        document.body.appendChild(tag);
        const tw = tag.getBoundingClientRect().width, th = tag.getBoundingClientRect().height;
        if (it.side === "left") tag.style.left = left - tw + "px";
        else if (it.side === "inside") { tag.style.left = r.left - pad + 10 + "px"; tag.style.top = r.top - pad + 10 + "px"; }
        else if (it.side === "bottom") { tag.style.left = r.left + r.width / 2 - tw / 2 + "px"; tag.style.top = r.bottom + pad - th - 10 + "px"; }
        else if (it.side === "above") { tag.style.left = r.left - pad + "px"; tag.style.top = r.top - pad - th - 4 + "px"; }
        else tag.style.left = left + "px";
        if (tag.getBoundingClientRect().right > innerWidth - 4) tag.style.left = innerWidth - 4 - tag.getBoundingClientRect().width + "px";
        document.body.appendChild(box);
      }
    }, items);
  }
  async function shot(name, items = [], note = "") {
    await mark(items);
    await pg.screenshot({ path: `${OUT}/${name}.png` });
    await pg.evaluate(() => document.querySelectorAll(".__mk").forEach(e => e.remove()));
    log.push({ name, note });
    console.log("saved", name, note);
  }
  const ready = async () => { await pg.waitForFunction(() => window.__map3d && window.__map3d.sc && document.getElementById("rg_sel").options.length, null, { timeout: 180000 }); await pg.waitForTimeout(1500); };
  await pg.goto(URL0 + "/3d"); await ready();
  await shot("r1_region_bar", [{ sel: "#rg_sel", n: "①", text: "지역 고르기", side: "left" }, { sel: "#rg_new", n: "②", text: "새 지역", side: "bottom" }]);
  await pg.click("#rg_new"); await pg.waitForSelector("#rg_dlg", { state: "visible" });
  await pg.fill("#rg_q", LL); await pg.click("#rg_go"); await pg.waitForSelector("#rg_res li button");
  await pg.click("#rg_res li button"); await pg.fill("#rg_name", NAME); await pg.selectOption("#rg_size", "4");
  await pg.waitForTimeout(800);
  await shot("r2_dialog", [{ sel: "#rg_q", n: "①", text: "장소·주소 또는 위도, 경도", side: "above" }, { sel: "#rg_res", n: "②", text: "'이곳으로'", side: "left" },
    { sel: "#rg_map", n: "③", text: "영역 (지도를 눌러 옮김)", side: "inside" }, { sel: "#rg_name", n: "④", text: "이름" }, { sel: "#rg_size", n: "⑤", text: "크기" },
    { sel: "#rg_make", n: "⑥", text: "만들기", side: "bottom" }]);
  await pg.click("#rg_make");
  await pg.waitForFunction(() => /격자|바람/.test(document.getElementById("rg_log").textContent), null, { timeout: 600000 });
  await shot("r3_progress", [{ sel: "#rg_log", n: "", text: "진행 상황 (끝나면 저절로 열림)", side: "above" }]);
  await pg.waitForNavigation({ timeout: 900000 }); await ready();
  await shot("r4_new_region", [{ sel: "#sub", n: "①", text: "만든 지역 · 예보 날짜" }, { sel: "#rg_wind", n: "②", text: "바람 새로 받기", side: "left" }]);
  const site = await pg.evaluate(() => window.__map3d.site.site);
  const q = `${(site.lat[0] + 0.3 * (site.lat[1] - site.lat[0])).toFixed(5)}, ${(site.lon[0] + 0.3 * (site.lon[1] - site.lon[0])).toFixed(5)}`;
  await pg.fill("#pt_q", q); await pg.click("#pt_go"); await pg.waitForSelector("#pt_res li button");
  await shot("r5_place_search", [{ sel: "#pt_q", n: "①", text: "주소·장소 또는 위도, 경도", side: "left" }, { sel: "#pt_res", n: "②", text: "출발지로 / 도착지로 / 경유점", side: "left" }]);
  fs.writeFileSync(`${OUT}/capture_region_log.json`, JSON.stringify({ steps: log, site }, null, 1));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
