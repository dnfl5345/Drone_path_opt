// 업체 인수 시험: 설치한 프로그램의 3D 화면에서 시험안내서 T2–T6 을 사람이 누르는 순서대로 실행하고 결과를 기록한다.
//   node scripts/client_acceptance.js <서버 주소> <출력 폴더> <Pilot 2 템플릿 .kmz> [지역 이름]
// 출력: <출력 폴더>/acceptance_<지역>.json, 화면 캡처, 내려받은 미션·경로 파일
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const URL0 = process.argv[2] || "http://127.0.0.1:8765";
const OUT = process.argv[3] || "acceptance";
const TPL = process.argv[4];
const SITE = process.argv[5] || "pangyo";
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1, acceptDownloads: true });
  const errs = [], R = { site: SITE, steps: [] };
  pg.on("pageerror", e => errs.push("pageerror: " + String(e)));
  pg.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text()); });
  const files = [];
  pg.on("download", async d => { const p = path.join(OUT, d.suggestedFilename()); await d.saveAs(p); files.push(p); });
  const step = (id, name, ok, detail) => { R.steps.push({ id, name, ok, detail }); console.log(ok ? "통과" : "실패", id, name, "::", detail); };
  const txt = sel => pg.$eval(sel, e => e.textContent.trim());
  const errText = async () => (await txt("#errors")) || "";

  async function plan(drone, payload) {
    await pg.selectOption("#drone", drone); await pg.fill("#payload", String(payload));
    await pg.selectOption("#mtype", "ab"); await pg.selectOption("#kind", "delivery");
    if (!(await pg.isChecked("#rt"))) await pg.click("#rt");
    await pg.selectOption("#hour", "15"); await pg.fill("#reserve", "20");
    await pg.evaluate(() => { document.getElementById("errors").textContent = ""; document.getElementById("status").textContent = ""; });
    const t = Date.now();
    await pg.click("#plan");
    await pg.waitForFunction(() => /완료|실패|오류/.test(document.getElementById("status").textContent) || document.getElementById("errors").textContent,
      null, { timeout: 240000 });
    return (Date.now() - t) / 1000;
  }

  // T2-0. 화면 열기
  const t0 = Date.now();
  await pg.goto(URL0 + "/3d");
  await pg.waitForFunction(() => window.__map3d && window.__map3d.sc, null, { timeout: 120000 });
  await pg.waitForTimeout(1500);
  const webgl = await pg.$eval("#webglerr", e => getComputedStyle(e).display !== "none").catch(() => false);
  const defDrone = await pg.$eval("#drone", e => e.value), defPay = await pg.$eval("#payload", e => e.value);
  step("T2-0", "3D 화면 열림", !webgl, `${((Date.now() - t0) / 1000).toFixed(1)} s, 기본 기체 ${defDrone} · 탑재 ${defPay} kg`);

  // T2. M4T 0.2 kg 배송 왕복 15시
  const tp = await plan("m4t", 0.2);
  const soc = await txt("#r_soc"), feas = await txt("#r_feas"), e2 = await errText();
  step("T2", "경로 생성 (M4T 0.2 kg 왕복 15시)", !e2 && /가능/.test(feas) && !/불가/.test(feas), `${tp.toFixed(1)} s · ${soc} · ${feas}${e2 ? " · 오류 " + e2 : ""}`);
  await pg.screenshot({ path: path.join(OUT, `T2_${SITE}.png`) });

  // T3. 기존 경로와 비교
  await pg.click("#cmpbtn");
  await pg.waitForSelector("#cmpcard", { state: "visible", timeout: 180000 }); await pg.waitForTimeout(800);
  const rows = await pg.$$eval("#c_rows tr", tr => tr.map(r => [...r.cells].map(c => c.textContent.trim()).join(" | ")));
  step("T3", "기존 경로와 비교", rows.length >= 2 && !(await errText()), rows.join(" / "));
  await pg.screenshot({ path: path.join(OUT, `T3_${SITE}.png`) });
  await pg.click("#cmpoff").catch(() => {});

  // T2-1. 미션 파일 (.waypoints, .csv)
  let n0 = files.length;
  await pg.click("#dl_wpl"); await pg.click("#dl_csv"); await pg.waitForTimeout(1500);
  step("T2-1", "미션 파일 내려받기 (.waypoints · .csv)", files.length - n0 === 4, files.slice(n0).map(f => path.basename(f)).join(", "));

  // T5. M4T: Pilot 2 파일로 지정 → DJI 경로 파일
  await pg.selectOption("#dji_model", "template"); await pg.dispatchEvent("#dji_model", "change"); await pg.waitForTimeout(300);
  await pg.evaluate(() => { document.getElementById("errors").textContent = ""; });
  await pg.click("#dl_kmz"); await pg.waitForTimeout(800);
  const noFile = await errText();
  step("T5-0", "템플릿 없이 누르면 안내", /Pilot 2/.test(noFile), noFile);
  if (TPL) {
    await pg.setInputFiles("#dji_tpl", TPL);
    await pg.evaluate(() => { document.getElementById("errors").textContent = ""; });
    n0 = files.length;
    await pg.click("#dl_kmz");
    await pg.waitForSelector("#k_tab", { state: "visible", timeout: 60000 }); await pg.waitForTimeout(1500);
    const krows = await pg.$$eval("#k_rows tr", tr => tr.map(r => [...r.cells].map(c => c.textContent.trim()).join(" | ")));
    const notes = await pg.$$eval("#k_notes li", li => li.map(l => l.textContent.trim()));
    step("T5", "Pilot 2 파일로 DJI 경로 파일 만들기", files.length - n0 === 2 && !(await errText()),
      `${files.slice(n0).map(f => path.basename(f)).join(", ")} · ${krows.join(" / ")} · 알림 ${notes.length}건: ${notes.join(" ; ")}`);
    await pg.screenshot({ path: path.join(OUT, `T5_${SITE}.png`) });
  }

  // 입력 검사: M4T 탑재 0.3 kg 은 막아야 함
  const nErr = errs.length;
  await plan("m4t", 0.3);
  await pg.waitForTimeout(300);
  const e3 = await errText();
  errs.splice(nErr).forEach(e => { if (!/status of 400/.test(e)) errs.push(e); });   // 400 = 입력 거부 (예상한 응답)
  step("T2-2", "M4T 탑재 0.3 kg 입력 거부", /0\.2/.test(e3), e3);

  // T6. X650 → ArduPilot 미션
  const tx = await plan("x650", 1.0);
  const fx = await txt("#r_feas"), ex = await errText();
  n0 = files.length;
  await pg.click("#dl_wpl"); await pg.waitForTimeout(1200);
  step("T6", "X650 경로와 미션 파일", !ex && files.length - n0 === 2, `${tx.toFixed(1)} s · ${await txt("#r_soc")} · ${fx} · ${files.slice(n0).map(f => path.basename(f)).join(", ")}`);

  // 실시간 운용 화면이 열리는지
  const lv = await b.newPage();
  lv.on("pageerror", e => errs.push("live pageerror: " + String(e)));
  await lv.goto(URL0 + "/live"); await lv.waitForTimeout(2500);
  step("L", "실시간 운용 화면 열림", (await lv.title()).length > 0, await lv.title());
  // 모의 비행: 기본 설정(M4T)으로 시작 → 완료까지 (배속은 화면 최대)
  await lv.selectOption("#drone", "m4t").catch(() => {});
  const spd = await lv.$$eval("#speed option", o => o.map(x => x.value)).catch(() => []);
  if (spd.length) await lv.selectOption("#speed", spd[spd.length - 1]);
  await lv.click("#start");
  const tl = Date.now();
  await lv.waitForFunction(() => /완료|중단/.test(document.getElementById("phase").textContent), null, { timeout: 300000 }).catch(() => {});
  const ph = await lv.$eval("#phase", e => e.textContent.trim()), gs = await lv.$eval("#g_soc", e => e.textContent.trim()),
    gsep = await lv.$eval("#g_sep", e => e.textContent.trim());
  await lv.screenshot({ path: path.join(OUT, `L_${SITE}.png`) });
  step("L2", "실시간 모의 비행 (M4T, 기본 설정)", /완료/.test(ph), `${((Date.now() - tl) / 1000).toFixed(0)} s · 단계 ${ph} · SoC ${gs} · 최소 이격 ${gsep}`);

  R.errors = errs; R.files = files.map(f => path.basename(f));
  R.ok = R.steps.every(s => s.ok) && errs.length === 0;
  fs.writeFileSync(path.join(OUT, `acceptance_${SITE}.json`), JSON.stringify(R, null, 1));
  console.log(R.ok ? "모두 통과" : "실패 있음", "브라우저 오류", errs.length, errs.slice(0, 5));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
