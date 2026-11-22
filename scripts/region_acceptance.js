// 새 지역 기능 인수 시험: 지역 목록 → 지역 전환 → 장소 검색으로 출발지·도착지 → 경로 생성 → '새 지역' 대화창으로 만들기 → 원래 지역으로.
//   node scripts/region_acceptance.js <서버 주소> <출력 폴더> <열 지역 key> <새 지역: 위도,경도> [크기 km]
// 장소 이름 검색은 인터넷의 검색 서버를 쓰므로, 시험은 위도, 경도 입력으로 한다 (검색창에 그대로 넣음).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [URL0, OUT, KEY, NEWLL, SIZE] = [process.argv[2], process.argv[3], process.argv[4], process.argv[5], process.argv[6] || "3"];
fs.mkdirSync(OUT, { recursive: true });
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1600, height: 1000 } });
  const errs = [], R = { steps: [] };
  pg.on("pageerror", e => errs.push("pageerror: " + e));
  pg.on("console", m => { if (m.type() === "error" && !/tile\.openstreetmap|ERR_|net::/.test(m.text())) errs.push("console: " + m.text()); });
  pg.on("requestfailed", r => { if (!/openstreetmap/.test(r.url())) errs.push("requestfailed: " + r.url()); });
  const step = (id, name, ok, detail) => { R.steps.push({ id, name, ok, detail }); console.log(ok ? "통과" : "실패", id, name, "::", detail); };
  const ready = async () => { await pg.waitForFunction(() => window.__map3d && window.__map3d.sc && document.getElementById("rg_sel").options.length, null, { timeout: 180000 }); await pg.waitForTimeout(800); };
  const plan = async () => {
    await pg.evaluate(() => { document.getElementById("errors").textContent = ""; document.getElementById("status").textContent = ""; });
    const t = Date.now(); await pg.click("#plan");
    await pg.waitForFunction(() => /완료|실패|오류/.test(document.getElementById("status").textContent) || document.getElementById("errors").textContent, null, { timeout: 300000 });
    return [(Date.now() - t) / 1000, await pg.$eval("#r_soc", e => e.textContent.trim()), await pg.$eval("#r_feas", e => e.textContent.trim()), await pg.$eval("#errors", e => e.textContent.trim())];
  };

  await pg.goto(URL0 + "/3d"); await ready();
  const opts = await pg.$$eval("#rg_sel option", o => o.map(x => x.value + "=" + x.textContent));
  step("G1", "지역 목록", opts.length >= 3, opts.join(" | "));

  // 지역 전환
  let t = Date.now();
  await Promise.all([pg.waitForNavigation({ timeout: 180000 }), pg.selectOption("#rg_sel", KEY)]);
  await ready();
  const sub = await pg.$eval("#sub", e => e.textContent);
  step("G2", "서버를 끄지 않고 지역 전환", (await pg.$eval("#rg_sel", e => e.value)) === KEY, `${((Date.now() - t) / 1000).toFixed(1)} s · ${sub}`);
  await pg.screenshot({ path: path.join(OUT, "G2_region.png") });

  // 장소 검색으로 출발지·도착지 (지역 안의 좌표)
  const site = await pg.evaluate(() => window.__map3d.site.site);
  const [la0, la1] = site.lat, [lo0, lo1] = site.lon;
  const P1 = `${(la0 + 0.30 * (la1 - la0)).toFixed(5)}, ${(lo0 + 0.30 * (lo1 - lo0)).toFixed(5)}`;
  const P2 = `${(la0 + 0.70 * (la1 - la0)).toFixed(5)}, ${(lo0 + 0.75 * (lo1 - lo0)).toFixed(5)}`;
  for (const [q, lab, sel] of [[P1, "출발지로", "hub"], [P2, "도착지로", "dst"]]) {
    await pg.fill("#pt_q", q); await pg.click("#pt_go");
    await pg.waitForSelector("#pt_res li button", { timeout: 30000 });
    await pg.click(`#pt_res li button:has-text("${lab}")`);
  }
  const pts = await pg.evaluate(() => [window.__map3d.hub, window.__map3d.dst]);
  step("G3", "장소 검색으로 출발지·도착지 지정", pts[0] && pts[1] && Math.hypot(pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]) > 500,
    `출발 (${pts[0].map(Math.round)}) · 도착 (${pts[1].map(Math.round)}) m`);
  await pg.fill("#pt_q", "10.0, 10.0"); await pg.click("#pt_go"); await pg.waitForTimeout(800);
  step("G3b", "지역 밖 좌표는 지정 불가 안내", /지역 밖/.test(await pg.$eval("#pt_res", e => e.textContent)), await pg.$eval("#pt_res", e => e.textContent.trim()));

  await pg.selectOption("#drone", "m4t"); await pg.fill("#payload", "0.2");
  if (!(await pg.isChecked("#rt"))) await pg.click("#rt");
  const [tp, soc, feas, err] = await plan();
  step("G4", "새 지역에서 경로 생성 (M4T 왕복)", !err && /가능/.test(feas), `${tp.toFixed(1)} s · ${soc} · ${feas} ${err}`);
  await pg.screenshot({ path: path.join(OUT, "G4_plan.png") });
  await pg.click("#cmpbtn"); await pg.waitForSelector("#cmpcard", { state: "visible", timeout: 180000 });
  const rows = await pg.$$eval("#c_rows tr", tr => tr.map(r => [...r.cells].slice(0, 3).map(c => c.textContent.trim()).join(" ")));
  step("G5", "새 지역에서 기존 경로와 비교", rows.length >= 2, rows.join(" / "));

  // 새 지역 대화창으로 만들기
  await pg.click("#rg_new"); await pg.waitForSelector("#rg_dlg", { state: "visible" });
  await pg.fill("#rg_q", NEWLL); await pg.click("#rg_go");
  await pg.waitForSelector("#rg_res li button", { timeout: 30000 }); await pg.click("#rg_res li button");
  await pg.fill("#rg_name", "시험지역"); await pg.selectOption("#rg_size", SIZE);
  await pg.screenshot({ path: path.join(OUT, "G6_dialog.png") });
  t = Date.now();
  await pg.click("#rg_make");
  await pg.waitForNavigation({ timeout: 900000 });
  await ready();
  const log = await pg.evaluate(() => fetch("/api/regions").then(r => r.json()));
  step("G6", "새 지역 대화창으로 만들기 → 자동으로 열림", log.job.state === "done" && (await pg.$eval("#rg_sel", e => e.value)) === log.job.key,
    `${((Date.now() - t) / 1000).toFixed(0)} s · ` + log.job.log.map(l => l.msg).join(" → "));
  const [tp2, soc2, feas2, err2] = await plan();
  step("G7", "만든 지역에서 경로 생성", !err2 && /가능|불가/.test(feas2), `${tp2.toFixed(1)} s · ${soc2} · ${feas2} ${err2}`);
  await pg.screenshot({ path: path.join(OUT, "G7_new_region.png") });

  // 원래 지역으로
  await Promise.all([pg.waitForNavigation({ timeout: 180000 }), pg.selectOption("#rg_sel", "pangyo")]);
  await ready();
  step("G8", "기본 지역(판교)으로 돌아가기", (await pg.$eval("#rg_sel", e => e.value)) === "pangyo", await pg.$eval("#sub", e => e.textContent));
  R.errors = errs; R.new_key = log.job.key; R.ok = R.steps.every(s => s.ok) && !errs.length;
  fs.writeFileSync(path.join(OUT, "region_acceptance.json"), JSON.stringify(R, null, 1));
  console.log(R.ok ? "모두 통과" : "실패 있음", errs);
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
