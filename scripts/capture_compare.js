// 3D 화면에서 '기존 경로와 비교'를 실행하고 화면을 저장한다 (보고서용 그림).
//   node scripts/capture_compare.js <서버 주소> <기체> <탑재 kg> <출발 시각> <저장 이름> [왕복 1|0]
// 서버: PYTHONPATH=src python -m dpo.webapp --port 8791 [--site changwon]
const { chromium } = require("playwright");
const fs = require("fs");
(async () => {
  const [url, drone, payload, hour, out, rt = "1"] = process.argv.slice(2);
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1600, height: 1080 }, deviceScaleFactor: 2 });
  await pg.goto(url + "/3d");
  await pg.waitForSelector("#drone option", { state: "attached" });
  await pg.waitForFunction(() => window.__map3d && window.__map3d.sc);
  await pg.selectOption("#mtype", "ab");
  await pg.selectOption("#drone", drone);
  await pg.fill("#payload", payload);
  await pg.selectOption("#hour", hour);
  if ((rt === "1") !== (await pg.isChecked("#rt"))) await pg.click("#rt");
  await pg.click("#plan");
  await pg.waitForFunction(() => /완료/.test(document.getElementById("status").textContent), null, { timeout: 180000 });
  await pg.click("#cmpbtn");
  await pg.waitForSelector("#cmpcard", { state: "visible", timeout: 120000 });
  await pg.waitForTimeout(1500);
  const C = await pg.evaluate(() => { const c = window.__map3d.cmp; return { ...c, methods: c.methods.map(m => ({ ...m, legs: undefined })) }; });
  const card = await pg.$("#cmpcard");
  await card.scrollIntoViewIfNeeded();
  await pg.evaluate(() => document.getElementById("cmpcard").scrollIntoView({ block: "end" }));
  await pg.waitForTimeout(400);
  await pg.screenshot({ path: `results/figures/${out}.png` });           // 지도 + 비교 표
  await card.screenshot({ path: `results/figures/${out}_card.png` });
  const cv = await pg.$("canvas");
  await cv.screenshot({ path: `results/figures/${out}_map.png` });
  fs.writeFileSync(`results/${out}.json`, JSON.stringify(C, null, 1));
  console.log(out, C.methods.map(m => `${m.key}=${m.soc_used == null ? "x" : m.soc_used.toFixed(1)}`).join(" "), JSON.stringify(C.by));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
