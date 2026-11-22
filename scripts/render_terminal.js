// 설치 시험 기록(docs/manual_img/install_log.json, scripts/release_test.py)으로 매뉴얼의 터미널 화면을 그린다.
//   node scripts/render_terminal.js [출력 폴더]
// 실제 실행한 명령·출력·걸린 시간만 쓴다 (폴더 경로는 ~/dpo 로 줄이고, pip 진행 표시는 -q 로 생략).
const { chromium } = require("playwright");
const fs = require("fs");
const OUT = process.argv[2] || "docs/manual_img";
const L = JSON.parse(fs.readFileSync(`${OUT}/install_log.json`, "utf8"));
const S = L.steps;
const esc = t => t.replace(/&/g, "&amp;").replace(/</g, "&lt;");
const P = (cmd, env = true, note = "") => `<div><span class="p">${env ? "(dpo-env) " : ""}$</span> ${esc(cmd)}${note ? `<span class="c">  # ${esc(note)}</span>` : ""}</div>`;
const O = t => t.trim() ? `<div class="o">${esc(t.trimEnd())}</div>` : "";
const T = s => `<div class="c"># [${s.sec} s, 종료 코드 ${s.rc}]</div>`;
const shots = {
  t1_install: P(S.venv.cmd, false, "가상환경 만들기") + T(S.venv) +
    P("source dpo-env/bin/activate", false, "켜기 (Windows: dpo-env\\Scripts\\activate)") +
    P(S.pip.cmd) + T(S.pip) + P(S.unzip.cmd, true, "자료 묶음 풀기 → dpo_data 폴더") + P("ls") + O(S.ls.out.split("\n").filter(Boolean).join("  ")),
  t2_check: P(S.check.cmd) + O(S.check.out) + T(S.check),
  t3_server: P("dpo-server --open", true, "브라우저에 3D 화면이 열립니다") +
    O(`[platform] 준비 완료\n3D 지도: http://127.0.0.1:8765/3d   (끄기: Ctrl+C)`) +
    `<div class="c"># 이 시험에서는 화면 없는 환경이라 --port ${S.server.cmd.split(" ").pop()} 로 켜서 /api/health 로 확인: ${S.server.sec} s, ` +
    `v${S.server.health.version}, 기체 ${S.server.health.drones.join(", ")}</div>`,
  t4_cli: P(S.plan.cmd) + O(S.plan.out) + T(S.plan),
  t5_cli_wpml: P(S.plan_wpml.cmd) + O(S.plan_wpml.out) + T(S.plan_wpml) + P(S.files.cmd) + O(S.files.out.split("\n").filter(Boolean).join("  ")),
};
const css = `body{margin:0;background:#fff}.w{width:1180px;background:#1E222B;border-radius:12px;overflow:hidden;font:15.5px/1.55 'NanumGothicCoding','D2Coding','NanumGothic',monospace;color:#E6E6E6}
.h{background:#2C313C;padding:9px 14px;color:#AEB4BE;font:14px 'NanumGothic',sans-serif}.h b{display:inline-block;width:12px;height:12px;border-radius:6px;margin-right:6px;vertical-align:-1px}
.b{padding:14px 16px 16px;white-space:pre-wrap;word-break:break-all}.p{color:#7FD07F;font-weight:bold}.c{color:#8B93A1}.o{color:#D7DAE0}`;
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: 1200, height: 400 }, deviceScaleFactor: 2 });
  for (const [name, body] of Object.entries(shots)) {
    await pg.setContent(`<html><head><style>${css}</style></head><body><div class="w" id="w"><div class="h"><b style="background:#FF5F57"></b><b style="background:#FEBC2E"></b><b style="background:#28C840"></b>` +
      `&nbsp;터미널 · 실제 실행 기록 (v${L.version}, 폴더 경로는 ~/dpo 로 줄임)</div><div class="b">${body}</div></div></body></html>`);
    await (await pg.$("#w")).screenshot({ path: `${OUT}/${name}.png` });
    console.log(name);
  }
  await b.close();
})();
