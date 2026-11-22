// 사용자 매뉴얼용 화면 캡처: 서버를 켠 뒤 3D 지도에서 경로를 생성하고 비교·내보내기까지 단계별로 저장한다.
//   node scripts/capture_manual.js [서버 주소] [출력 폴더]
// 누를 곳은 빨간 테두리와 번호로 표시한다 (캡처할 때만 화면 위에 덧그리고 지운다).
const { chromium } = require("playwright");
const fs = require("fs");
const URL0 = process.argv[2] || "http://127.0.0.1:8765";
const OUT = process.argv[3] || "docs/manual_img";
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2, acceptDownloads: true });
  const errs = [];
  pg.on("pageerror", e => errs.push(String(e)));
  pg.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
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
  const t0 = Date.now();
  await pg.goto(URL0 + "/3d");
  await pg.waitForFunction(() => window.__map3d && window.__map3d.sc, null, { timeout: 120000 });
  await pg.waitForTimeout(2500);
  const tLoad = (Date.now() - t0) / 1000;
  await shot("01_start", [{ sel: "#view", n: "①", text: "3D 지도 (끌어서 회전·이동, 휠로 확대)", pad: -6, side: "bottom" },
    { sel: "#layers", n: "②", text: "표시 선택" }, { sel: "aside .card", n: "③", text: "임무 설정", side: "inside" }], `첫 화면 (${tLoad.toFixed(1)} s)`);
  // 2. 기체·탑재
  await pg.selectOption("#drone", "m4t"); await pg.fill("#payload", "0.2");
  await shot("02_drone", [{ sel: "#drone", n: "①", text: "기체" }, { sel: "#payload", n: "②", text: "탑재중량" }]);
  // 3. 임무 형태
  await pg.selectOption("#mtype", "ab"); await pg.selectOption("#kind", "delivery");
  if (!(await pg.isChecked("#rt"))) await pg.click("#rt");
  await shot("03_mission", [{ sel: "#mtype", n: "①", text: "임무 형태" }, { sel: "#kind", n: "②", text: "배송/회수" }, { sel: "#rt", n: "③", text: "왕복", pad: 6 }]);
  // 4. 출발지·도착지
  await pg.click("#pickdst"); await pg.waitForTimeout(300);
  await shot("04_points", [{ sel: "#pickhub", n: "①" , side: "left"}, { sel: "#pickdst", n: "②" }, { sel: "#ptinfo", n: "③", text: "좌표" }, { sel: "#hint", n: "④", text: "지도를 눌러 위치 지정" }]);
  await pg.click("#pickdst");                                   // 찍기 모드 끄기 (기본 위치 그대로 쓴다)
  // 5. 시각·예비
  await pg.selectOption("#hour", "15"); await pg.fill("#reserve", "20");
  await shot("05_time", [{ sel: "#hour", n: "①", text: "출발 시각" }, { sel: "#reserve", n: "②", text: "예비 SOC" }, { sel: "#vgmax", n: "③", text: "속도 상한 (선택)" }]);
  // 6. 경로 생성
  await shot("06_plan", [{ sel: "#plan", n: "①", text: "경로 생성 누르기" }]);
  const tp = Date.now();
  await pg.click("#plan");
  await pg.waitForTimeout(400);
  await shot("06b_computing", [{ sel: "#status", n: "", text: "계산 중" }]);
  await pg.waitForFunction(() => /완료/.test(document.getElementById("status").textContent), null, { timeout: 180000 });
  const tPlan = (Date.now() - tp) / 1000;
  await pg.waitForTimeout(1500);
  // 7. 결과
  await shot("07_result", [{ sel: "#rescard", n: "①", text: "결과", side: "left" }, { sel: "#view", n: "②", text: "생성된 경로 (색 = 구간)", pad: -6, side: "bottom" },
    { sel: "#profile", n: "③", text: "고도 단면", side: "inside" }], `경로 생성 ${tPlan.toFixed(1)} s`);
  // 8. 재생
  await pg.click("#play"); await pg.waitForTimeout(4500); await pg.click("#play");
  await shot("08_play", [{ sel: "#play", n: "①", text: "재생/멈춤" }, { sel: "#tl", n: "②", text: "시간 막대" }, { sel: "#hud", n: "③", text: "비행 정보 (위치·속도·SOC)", side: "left" }]);
  // 9. 비교
  await pg.click("#cmpbtn");
  await pg.waitForSelector("#cmpcard", { state: "visible", timeout: 120000 }); await pg.waitForTimeout(1200);
  await shot("09_compare", [{ sel: "#cmpcard", n: "①", text: "비교 표·이유", side: "left" }, { sel: "#view", n: "②", text: "주황 = 기존 경로, 파랑 = 제안 경로", pad: -6, side: "bottom" }]);
  // 10. 내보내기
  const dl = [];
  pg.on("download", async d => { const p = `${OUT}/../../dist/manual_dl_${d.suggestedFilename()}`; dl.push(d.suggestedFilename()); await d.saveAs(p).catch(() => {}); });
  await pg.click("#dl_wpl"); await pg.waitForTimeout(800);
  await pg.selectOption("#dji_model", "template"); await pg.dispatchEvent("#dji_model", "change"); await pg.waitForTimeout(300);
  await shot("10b_export_m4t", [{ sel: "#dji_model", n: "①", text: "Matrice 4T 등 – Pilot 2 파일로 지정" }, { sel: "#dji_tpl", n: "②", text: "Pilot 2 에서 내보낸 .kmz" }]);
  await pg.selectOption("#dji_model", "m30"); await pg.dispatchEvent("#dji_model", "change");
  await pg.click("#dl_kmz"); await pg.waitForSelector("#k_tab", { state: "visible", timeout: 60000 }); await pg.waitForTimeout(1200);
  await shot("10_export", [{ sel: "#dl_wpl", n: "①", text: "ArduPilot", side: "left" }, { sel: "#dl_kmz", n: "②", text: "DJI", side: "left" }, { sel: "#dji_model", n: "③", text: "DJI 기종" }]);
  fs.writeFileSync(`${OUT}/capture_log.json`, JSON.stringify({ load_s: tLoad, plan_s: tPlan, downloads: dl, errors: errs, steps: log,
    soc: await pg.$eval("#r_soc", e => e.textContent), feas: await pg.$eval("#r_feas", e => e.textContent) }, null, 1));
  console.log({ tLoad, tPlan, dl, errs });
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
