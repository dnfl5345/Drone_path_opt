// 배포판 보고서 (Word, 약 30쪽) 생성. 사용: node scripts/build_deploy_report.js [출력.docx]
// 수치는 results/*.json 에서 읽는다 (본문에 직접 적지 않는다).
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, Header, Footer, AlignmentType, PageNumber,
  HeadingLevel, BorderStyle, WidthType, ShadingType, LevelFormat, PageBreak, VerticalAlign,
} = require("docx");

const ROOT = path.resolve(__dirname, "..");
const RES = path.join(ROOT, "results"), FIG = path.join(RES, "figures");
const J = (f) => JSON.parse(fs.readFileSync(path.join(RES, f), "utf8"));
const E13 = J("E13_stats.json"), E14 = J("E14_stats.json"), E15 = J("E15_stats.json"), N = J("deploy_report_numbers.json");

const FONT = "맑은 고딕", MONO = "Consolas";
const NAVY = "004E8F", TEAL = "1B6E8C", INK = "16202A", GRAY = "5B6770", LIGHT = "E3EEF3", PALE = "F4F7F9", ORANGE = "C55A11";
const W = 9412;                       // 본문 폭 [DXA] (A4, 좌우 여백 2.2 cm)
let figNo = 0, tabNo = 0;

const f1 = (v, d = 1) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(d);
const neg = (v, d = 1) => (v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
const num = (v, d = 1) => Number(v).toFixed(d);

function runs(text, opt = {}) {
  // **굵게**, `코드` 만 해석한다
  const out = [];
  const re = /(\*\*.+?\*\*|`[^`]+`)/g;
  let last = 0, m;
  const base = { font: FONT, size: opt.size || 21, color: opt.color || INK, bold: opt.bold };
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(new TextRun({ ...base, text: text.slice(last, m.index) }));
    const t = m[0];
    if (t.startsWith("**")) out.push(new TextRun({ ...base, text: t.slice(2, -2), bold: true }));
    else out.push(new TextRun({ ...base, text: t.slice(1, -1), font: { ascii: MONO, hAnsi: MONO, eastAsia: FONT }, size: (opt.size || 21) - 1, color: "7A3E00" }));
    last = m.index + t.length;
  }
  if (last < text.length) out.push(new TextRun({ ...base, text: text.slice(last) }));
  return out;
}
const P = (text, opt = {}) => new Paragraph({ children: runs(text, opt), spacing: { after: opt.after ?? 120, line: 320 }, alignment: opt.align, keepNext: opt.keepNext, indent: opt.indent });
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun({ text, font: FONT })], pageBreakBefore: true });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun({ text, font: FONT })] });
const H3 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun({ text, font: FONT })] });
const B = (text, level = 0) => new Paragraph({ children: runs(text), numbering: { reference: "bul", level }, spacing: { after: 70, line: 310 } });
const NUM = (text) => new Paragraph({ children: runs(text), numbering: { reference: "num", level: 0 }, spacing: { after: 70, line: 310 } });
const SP = (after = 80) => new Paragraph({ children: [], spacing: { after } });

function code(lines) {
  return lines.map((ln, i) => new Paragraph({
    children: [new TextRun({ text: ln, font: { ascii: MONO, hAnsi: MONO, eastAsia: FONT }, size: 18, color: INK })],
    shading: { type: ShadingType.CLEAR, fill: PALE }, spacing: { after: i === lines.length - 1 ? 140 : 0, line: 270 },
    indent: { left: 140, right: 140 }, keepLines: true, keepNext: i < lines.length - 1,
  }));
}

function pngSize(file) { const b = fs.readFileSync(file); return [b.readUInt32BE(16), b.readUInt32BE(20)]; }
function figure(name, caption, widthPx = 620) {
  const file = path.join(FIG, name);
  const [w, h] = pngSize(file);
  figNo += 1;
  return [
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 100, after: 60 }, keepNext: true,
      children: [new ImageRun({ type: "png", data: fs.readFileSync(file), transformation: { width: widthPx, height: Math.round(widthPx * h / w) } })] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 180 },
      children: [new TextRun({ text: `그림 ${figNo}. `, font: FONT, size: 18, bold: true, color: TEAL }), new TextRun({ text: caption, font: FONT, size: 18, color: GRAY })] }),
  ];
}

const border = { style: BorderStyle.SINGLE, size: 4, color: "C5CED6" };
const borders = { top: border, bottom: border, left: border, right: border };
function table(caption, head, rows, widths, opt = {}) {
  const tot = widths.reduce((a, b) => a + b, 0);
  const cw = widths.map((w) => Math.round(w * W / tot));
  cw[cw.length - 1] += W - cw.reduce((a, b) => a + b, 0);
  const size = opt.size || 18;
  const cell = (t, j, isHead, fill, keep) => new TableCell({
    width: { size: cw[j], type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.CENTER,
    shading: fill ? { type: ShadingType.CLEAR, fill } : undefined, margins: { top: 50, bottom: 50, left: 90, right: 90 },
    children: [new Paragraph({ keepNext: keep, alignment: (opt.align && opt.align[j]) || (j === 0 ? AlignmentType.LEFT : AlignmentType.CENTER), spacing: { after: 0, line: 270 },
      children: runs(String(t), { size, bold: isHead, color: isHead ? NAVY : INK }) })],
  });
  tabNo += 1;
  const whole = rows.length <= 12;          // 짧은 표는 쪽을 넘겨 나뉘지 않게 한다
  const trs = [new TableRow({ tableHeader: true, cantSplit: true, children: head.map((t, j) => cell(t, j, true, LIGHT, true)) })];
  rows.forEach((r, i) => trs.push(new TableRow({ cantSplit: true, children: r.map((t, j) => cell(t, j, false, opt.highlight && opt.highlight.includes(i) ? "FFF4E5" : undefined, whole && i < rows.length - 1)) })));
  return [
    new Paragraph({ spacing: { before: 120, after: 70 }, keepNext: true,
      children: [new TextRun({ text: `표 ${tabNo}. `, font: FONT, size: 19, bold: true, color: TEAL }), ...runs(caption, { size: 19, bold: true })] }),
    new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: cw, rows: trs }),
    SP(160),
  ];
}
const note = (text) => new Paragraph({ children: runs(text, { size: 19, color: GRAY }), spacing: { after: 140, line: 290 }, indent: { left: 200 },
  border: { left: { style: BorderStyle.SINGLE, size: 18, color: "C5CED6", space: 8 } } });

// ------------------------------------------------------------------ 수치
const SETS = [["changwon_x650", "창원 · X650"], ["pangyo_x650", "판교 · X650"], ["pangyo_m100", "판교 · M100"]];
const s13 = E13.sets;
const nAll = SETS.reduce((a, [k]) => a + s13[k].EX_P0.n, 0);
const nWin = SETS.reduce((a, [k]) => a + Math.round(s13[k].EX_P0.win * s13[k].EX_P0.n), 0);
const ex = SETS.map(([k]) => s13[k].EX_P0.mean_pct), pth = SETS.map(([k]) => s13[k].EXv_P0.mean_pct), spd = SETS.map(([k]) => s13[k].EX_EXv.mean_pct);
const nShort = SETS.reduce((a, [k]) => a + s13[k].n_shortest_chosen, 0);
const rng = (a, d = 1) => `${Math.min(...a.map(Math.abs)).toFixed(d)}–${Math.max(...a.map(Math.abs)).toFixed(d)}`;
const CASES15 = [["real", "실제 예보 오차"], ["rot30", "풍향 30°"], ["rot60", "풍향 60°"], ["rot90", "풍향 90°"], ["spd0.6", "풍속 0.6배"]];
const S15 = [["changwon_x650", "창원 · X650"], ["pangyo_m100", "판교 · M100"], ["pangyo_x650", "판교 · X650"]];
const all15 = S15.flatMap(([k]) => CASES15.map(([c]) => E15[k].cases[c]));
const p3 = all15.map((e) => e.P3_vs_P0.mean), p1 = all15.map((e) => e.P1_vs_P0.mean);
const p3p1worst = Math.max(...all15.map((e) => e.P3_vs_P1.worst));
const n15 = all15.reduce((a, e) => a + e.n_ok, 0);
const obs15 = S15.flatMap(([k]) => ["real", "rot60"].map((c) => E15[k].obstacle[c]));
const obsN = obs15.reduce((a, o) => a + o.n, 0), obsCol = obs15.reduce((a, o) => a + o.P1.col + o.P3.col, 0);
const obsRej = obs15.reduce((a, o) => a + o.P3.rej, 0), obsSep = obs15.flatMap((o) => [o.P1.min_sep, o.P3.min_sep]);
const cpu15 = all15.map((e) => e.cpu_ms).filter((v) => v != null);
const TL = N.timeline, SM = N.sample, DRN = N.drones;
const rec14 = (q) => { const v = []; for (const k of Object.keys(E14)) for (const c of ["rot30", "rot60", "rot90", "rot180", "spd0.6"]) { const e = E14[k][c]; if (e && e[q] && e[q].recovered != null) v.push(100 * e[q].recovered); } return v; };
const r120 = rec14("S_120"), r60 = rec14("S_60"), r30 = rec14("S_30");
const mm = (a) => `${Math.round(Math.min(...a))}–${Math.round(Math.max(...a))}`;

// ------------------------------------------------------------------ 본문
const c = [];
// 표지
c.push(SP(1500));
c.push(new Paragraph({ alignment: AlignmentType.LEFT, spacing: { after: 200 }, children: [new TextRun({ text: "드론 SoC 최대화 경로계획", font: FONT, size: 52, bold: true, color: NAVY })] }));
c.push(new Paragraph({ alignment: AlignmentType.LEFT, spacing: { after: 500 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEAL, space: 12 } },
  children: [new TextRun({ text: "속도 갱신·안전장치의 실시간 플랫폼 연결과 배포판 (v1.0.0)", font: FONT, size: 30, color: TEAL })] }));
c.push(P("기술 보고서", { size: 26, bold: true, after: 60 }));
c.push(P("2026년 11월 16일", { size: 22, color: GRAY, after: 900 }));
c.push(...table("이 문서의 범위", ["항목", "내용"], [
  ["다루는 것", "안전장치와 속도 갱신의 설계, 실시간 플랫폼·미션 파일·MAVLink 연결, 폐루프 검증, 설치·운용 방법"],
  ["대상 기체", "Holybro X650 (ArduPilot ArduCopter). 전력 모델은 제원 기반 추정이며 비교용으로 DJI M100 실측 보정 모델을 함께 씁니다."],
  ["결과의 성격", "모든 수치는 시뮬레이션입니다. 실기체와 ArduPilot SITL에서는 시험하지 않았습니다."],
  ["프로그램", "`drone-path-opt` 1.0.0 (명령 `dpo-server`, `dpo-plan`)"],
], [2, 7.4], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
tabNo = 0;   // 표지의 표는 번호에서 뺀다
c[c.length - 3] = new Paragraph({ spacing: { after: 70 }, children: [] });

// 목차
c.push(new Paragraph({ pageBreakBefore: true, spacing: { after: 240 }, children: [new TextRun({ text: "차례", font: FONT, size: 34, bold: true, color: NAVY })] }));
const toc = [["1", "요약", "무엇을 만들었고 무엇을 확인했는가"], ["2", "문제와 목표", "최적 설계 문제, 기존 경로의 정의, 입력 자료"],
  ["3", "계획 알고리즘", "에너지 모델, WE-LT*, 장애물 회피, 대상 기체"], ["4", "설계의 근거", "기존 경로 대비 우수성(E13), 기상 정보 갱신 주기(E14)"],
  ["5", "안전장치", "최단 경로 후보 비교의 규칙과 효과"], ["6", "속도 갱신", "바람 편차 추정, 속도 재결정, 채택 조건"],
  ["7", "실시간 플랫폼 연결", "엔진의 처리 순서, 화면, API"], ["8", "미션 파일과 기체 연결", "남은 구간 미션, 속도 명령, MAVLink"],
  ["9", "폐루프 검증", "실시간 엔진으로 잰 효과와 안전(E15)"], ["10", "배포", "패키지, 설치, 실행, 설정, 자료 폴더"],
  ["11", "시험과 검증 범위", "확인한 것과 확인하지 못한 것"], ["12", "한계와 다음 단계", "남은 일의 우선순위"], ["부록", "설정 표와 용어", "명령줄 인자, 실시간 설정, 용어"]];
c.push(new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [900, 3100, 5412],
  rows: toc.map(([a, b, d]) => new TableRow({ children: [[a, 900, true], [b, 3100, true], [d, 5412, false]].map(([t, w, bold]) => new TableCell({
    width: { size: w, type: WidthType.DXA }, margins: { top: 90, bottom: 90, left: 100, right: 100 },
    borders: { top: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, left: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, right: { style: BorderStyle.NONE, size: 0, color: "FFFFFF" }, bottom: { style: BorderStyle.SINGLE, size: 4, color: "D9E0E6" } },
    children: [new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: t, font: FONT, size: 22, bold, color: bold ? NAVY : GRAY })] })] })) })) }));
c.push(SP(300));
c.push(note("그림과 표의 수치는 저장소의 결과 파일(`results/E13_stats.json`, `E14_stats.json`, `E15_stats.json`)에서 읽어 넣었습니다. 재현 명령은 10장과 11장에 있습니다."));

// 1 요약
c.push(H1("1. 요약"));
c.push(P("이전 검토에서 두 가지가 실험 코드에만 있었습니다. 에너지 탐색이 최단 경로보다 나쁜 경로를 내는 경우를 막는 **안전장치**와, 기체가 잰 바람으로 구간 속도를 다시 정하는 **속도 갱신**입니다. 이번 작업은 이 둘을 실시간 운용 플랫폼과 미션 파일에 연결하고, 설치해서 쓸 수 있는 패키지로 묶은 것입니다."));
c.push(...table("한 장 요약", ["질문", "답", "근거"], [
  ["무엇을 연결했나", "안전장치는 계획 API·명령줄·실시간 엔진에, 속도 갱신은 실시간 엔진에 넣었습니다. 속도 명령은 화면과 MAVLink로 나갑니다.", "5–8장"],
  ["효과가 있나", `연결 전 플랫폼 대비 SOC 소모가 ${rng(p3)} % 줄었습니다 (${n15}건).`, "9장, E15"],
  ["안전한가", `가로지르는 장애물을 넣은 ${obsN}건에서 침범이 ${obsCol}건입니다. 속도 갱신이 위험구간과 겹치면 채택하지 않습니다 (${obsRej}회 보류).`, "9장"],
  ["설치해서 쓸 수 있나", "wheel을 새 가상환경에 설치해 저장소 밖에서 서버와 명령줄이 동작했습니다. 시험 41개가 통과합니다.", "10–11장"],
  ["확인하지 못한 것", "Docker 이미지 빌드, ArduPilot SITL, 실기체 비행입니다.", "11–12장"],
], [2.3, 5.6, 1.5], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER] }));
c.push(H2("1.1 결과를 읽을 때 알아 둘 점"));
c.push(B("**속도 갱신의 이득은 예보가 틀린 만큼 나옵니다.** 예보가 맞으면(바람 차이 0.3–0.4 m/s) 안전장치의 몫만 남습니다."));
c.push(B("**틀린 예보는 가정으로 만들었습니다.** 풍향을 돌리거나 풍속 배율을 바꾼 것이고, 실증 지역에서 실측 바람과 예보의 차이를 잰 적은 없습니다."));
c.push(B("**X650의 수치는 전력 모델 가정에 좌우됩니다.** 항력 면적을 실측한 적이 없어, 속도에서 나오는 이득의 크기는 비행 시험으로 보정해야 합니다."));
c.push(B("**실시간 엔진은 기체를 모의합니다.** 기체의 실제 위치를 받아 따라가는 단계는 아직 없습니다."));
c.push(...figure("F31_architecture.png", "구성. 주황색이 이번에 연결한 부분입니다. 기체와의 통신은 모의 기체와의 루프백까지 확인했습니다.", 610));

// 2 문제
c.push(H1("2. 문제와 목표"));
c.push(P("목표는 바람·지형·장애물을 고려해 도착 SOC를 최대화하고, 국토 정보와 기상 정보를 주기적으로 받아 비행 중에도 효율을 유지하는 것입니다. 이 문제를 다음과 같이 정리합니다."));
c.push(...table("최적 설계 문제", ["구분", "내용", "구현"], [
  ["목적", "도착 SOC 최대화 (임무 SOC 소모 최소화)", "에너지 탐색, 구간 속도 최적화"],
  ["결정 변수", "수평 경로, 고도, 구간 대지속도, 호버 대기", "WE-LT*, 시공간 탐색"],
  ["설계 변수", "속도 갱신 주기, 채택 한계, 안전장치 사용 여부", "이번 작업에서 설정으로 노출"],
  ["제약", "지형·건물 이격, 정적·동적 장애물, 금지구역, 대기속도·상승률 한계, 예비 SOC 20 %", "점유 격자, 위험 시간구간 표"],
  ["입력 (국토)", "지형, 건물, 금지구역, 이동 장애물", "Copernicus DSM 30 m, Overture 건물, 화면·API 입력"],
  ["입력 (기상)", "고도별 바람 예보, 기체가 잰 바람", "GFS 0.25° 1시간, MAVLink `WIND`"],
], [1.5, 4.4, 3.5], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("2.1 기존 경로의 정의"));
c.push(P("\"기존 경로보다 우수하다\"를 보이려면 기존 경로가 무엇인지 먼저 정해야 합니다. 이 문서는 **최단 경로를 정속 10 m/s로 비행하는 것**을 기존 경로로 둡니다. ArduCopter의 기본 수평 속도가 10 m/s이기 때문입니다. 속도만 최적화한 최단 경로도 대조로 함께 봅니다."));
c.push(note("기준에 따라 주장의 크기가 달라집니다. '최단 + 정속 10 m/s' 대비로는 약 18 %, '최단 + 속도 최적화' 대비로는 2–5 %입니다(4장). 과제 계획서의 비교 대상을 확인해 두는 것이 좋습니다."));
c.push(H2("2.2 정보를 언제 받는가"));
c.push(...table("정보별 갱신 방식", ["정보", "받는 때", "받으면 하는 일", "근거"], [
  ["지형·건물", "출발 전 한 번", "점유 격자 생성", "거의 변하지 않음"],
  ["금지구역·이동 장애물", "비행 중 수시 (감시 주기 2 s)", "남은 경로 검사, 충돌 예상 시 재계획", "안전"],
  ["바람 예보", "출발 전 한 번", "경로와 구간 속도 계획", "더 새 예보로 계획해도 차이 없음 (4장)"],
  ["기체가 잰 바람", "순항 5 s 뒤, 이후 30 s마다", "예보 편차 추정, 구간 속도 갱신", "이득이 속도에서 나옴 (4장)"],
], [2.2, 2.6, 2.9, 2.3], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));

// 3 알고리즘
c.push(H1("3. 계획 알고리즘"));
c.push(H2("3.1 에너지와 SOC"));
c.push(P("구간의 소비 에너지는 전력과 시간의 곱이고, 전력은 대기속도·상승률·질량·공기밀도로 정해집니다. 바람은 같은 대지속도를 내는 데 필요한 대기속도를 바꿉니다."));
c.push(...table("에너지 계산의 구성", ["단계", "식", "설명"], [
  ["① 비행 시간", "t = d / V_g", "V_g는 대지속도. 바람과 대기속도 한계로 정해집니다."],
  ["② 구간 에너지", "E = P(V_a, V_z, m, ρ) · t", "회전익 전력 모델 (유도·형상·유해 항력·상승)"],
  ["③ 임무 에너지", "ΣE + 이륙 + 착륙 + 호버", "호버 대기와 경유점 체류도 포함합니다."],
  ["④ SOC 소모", "ΔSOC = E / E_batt × 100", "단위 %p"],
  ["⑤ 판정", "도착 SOC ≥ 예비 20 %", "미달이면 임무 불가"],
], [1.7, 3.0, 4.7], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(P("구간마다 미터당 에너지가 가장 적은 대지속도를 0.5 m/s 간격으로 고릅니다. 이것이 **구간 속도 최적화**이고, 미션 파일에는 구간 대지속도 명령(`DO_CHANGE_SPEED`)으로 나갑니다."));
c.push(H2("3.2 WE-LT*와 시공간 탐색"));
c.push(B("**WE-LT* (Wind-aware Energy Lazy Theta*)**: 3차원 격자(수평 25 m, 수직 20 m)에서 에너지를 비용으로 탐색하고, 두 노드 사이가 비어 있으면 직선으로 잇습니다."));
c.push(B("**시공간 탐색**: 이동 장애물이 칸을 차지하는 시간구간을 표로 만들고, 그 구간을 피해 지나가거나 호버로 기다립니다."));
c.push(B("**불확실성 튜브**: 장애물 위치 오차가 시간에 따라 커지는 만큼 위험 반경을 키웁니다(위험 한도 ε)."));
c.push(B("**탐색 가중치**: 실시간 엔진은 빠른 답을 위해 가중치 2.0으로 계획하고 배경에서 개선합니다. 가중치가 클수록 최적에서 멀어질 수 있습니다. 안전장치가 필요한 이유입니다."));
c.push(...figure("F27_changwon_tour.png", "창원 창이대로 71 주변 순회 임무의 계획 예. 실선은 장애물을 피한 경로, 점선은 장애물이 없을 때의 경로입니다.", 620));
c.push(H2("3.3 대상 기체"));
c.push(...table("기체 모델", ["항목", "Holybro X650", "DJI M100"], [
  ["자체 무게", `${DRN.x650.m_empty} kg`, `${DRN.m100.m_empty} kg`],
  ["배터리", `${DRN.x650.battery_wh} Wh`, `${DRN.m100.battery_wh} Wh`],
  ["최대 탑재", `${DRN.x650.payload_max} kg`, `${DRN.m100.payload_max} kg`],
  ["최대 대기속도 / 대지속도", `${DRN.x650.va_max} / ${DRN.x650.vg_max} m/s`, `${DRN.m100.va_max} / ${DRN.m100.vg_max} m/s`],
  ["상승 / 하강률", `${DRN.x650.vz_up} / ${DRN.x650.vz_dn} m/s`, `${DRN.m100.vz_up} / ${DRN.m100.vz_dn} m/s`],
  ["전력 모델의 근거", "제원과 제조사 호버 시간으로 추정 (미보정)", "CMU 실측 비행으로 보정"],
], [3.2, 3.1, 3.1]));
c.push(...figure("F26_x650_target.png", "X650의 추정 전력 곡선(a), 탑재별 왕복 SOC 소모(b), 모델 가정에 따른 차이(c). 판교 2.4 km 왕복 사례이고, 안전장치를 넣기 전에 계산한 값입니다.", 620));
c.push(note("X650의 최대 대기속도 15 m/s와 항력 면적 0.08 m²는 가정입니다. 이 값에서는 최고 속도가 곧 최적 속도인 경우가 많아, 속도 갱신으로 바꿀 것이 없는 구간이 생깁니다(6.5절)."));

// 4 근거
c.push(H1("4. 설계의 근거"));
c.push(P("안전장치와 속도 갱신은 두 실험의 결과에서 나왔습니다. 하나는 기존 경로와의 비교(E13)이고, 다른 하나는 기상 정보를 얼마나 자주 받아야 하는지(E14)입니다."));
c.push(H2("4.1 기존 경로 대비 우수성 (E13)"));
c.push(P(`7일, 2개 지역, 2개 기체로 ${nAll}건을 비교했습니다. 비행 거리는 1.5–2.8 km이고 탑재는 1.0 kg입니다. 계획에는 00Z 예보를, 평가에는 6시간 뒤의 06Z 예보를 썼습니다.`));
c.push(...table("기존 경로(최단 + 정속 10 m/s) 대비 제안 경로의 SOC 소모", ["조합", "기존 → 제안 [%p]", "변화 (95 % 구간)", "제안이 우수", "속도의 몫", "경로의 몫"],
  SETS.map(([k, lb]) => { const a = s13[k].EX_P0; return [lb, `${num(a.a_soc)} → ${num(a.b_soc)}`, `${neg(a.mean_pct)} % (${neg(a.ci[0])}, ${neg(a.ci[1])})`,
    `${Math.round(a.win * a.n)}/${a.n}`, `${neg(s13[k].EX_EXv.mean_pct)} %`, `${neg(s13[k].EXv_P0.mean_pct)} %`]; }),
  [1.9, 2.0, 2.7, 1.4, 1.3, 1.3]));
c.push(...figure("F28_superiority.png", "E13. (a) 경우별 비교, (b) 절감의 분해, (c) 바람 세기에 따른 절감, (d) 속도를 매 순간 맞춘다고 가정했을 때 주기 재계획의 효과.", 560));
c.push(B(`**절감의 대부분은 속도 선택에서 나옵니다.** 속도만 바꿔도 ${rng(spd)} %가 줄고, 경로 모양이 더하는 몫은 ${rng(pth)} %입니다.`));
c.push(B("**에너지 탐색이 최단 경로보다 나쁜 경로를 낸 경우가 15–19 % 있었습니다.** 탐색이 근사 해법이기 때문입니다. 안전장치를 만든 이유입니다."));
c.push(B("**더 새 예보로 계획해도 차이가 없었습니다** (−0.1 – +0.0 %). 두 예보의 바람 차이가 0.1–0.7 m/s뿐이기 때문입니다."));
c.push(H2("4.2 기상 정보 갱신 주기 (E14)"));
c.push(P("미션 파일은 구간 속도를 계획 때 정해 내려보냅니다. 그 속도를 끝까지 고정해 나는 것을 기준으로, 비행 중에 무엇을 얼마나 자주 갱신해야 하는지를 쟀습니다."));
{
  const k = "pangyo_m100", S = E14[k];
  const lab = { real: "실제 (00Z ↔ 06Z)", rot30: "풍향 30°", rot60: "풍향 60°", rot90: "풍향 90°", rot180: "풍향 180°", "spd0.6": "풍속 0.6배" };
  c.push(...table("고정 비행 대비 SOC 변화 [%] (판교 · M100, 음수 = 절감)", ["예보 오차", "바람 차이", "고정의 손해", "속도만 2분", "속도만 1분", "속도만 30초", "경로+속도 1분", "매 순간"],
    Object.keys(lab).filter((n) => S[n]).map((n) => { const e = S[n]; return [lab[n], `${num(e.err)} m/s`, `${f1(e.penalty_pct)}`, neg(e.S_120.mean_pct), neg(e.S_60.mean_pct), neg(e.S_30.mean_pct), neg(e.R_60.mean_pct), neg(e.ADAPT.mean_pct)]; }),
    [2.1, 1.2, 1.2, 1.1, 1.1, 1.2, 1.4, 1.1], { size: 17 }));
}
c.push(...figure("F29_update_period.png", "E14. 왼쪽은 갱신 없이 계획대로 날 때의 손해, 오른쪽은 갱신 방식별로 되찾은 양입니다.", 600));
c.push(B(`**주기가 짧을수록 손해를 더 되찾습니다.** 틀린 예보에서 되찾은 비율은 2분 주기 ${mm(r120)} %, 1분 주기 ${mm(r60)} %, 30초 주기 ${mm(r30)} %입니다.`));
c.push(B("**이득은 경로가 아니라 속도에서 나옵니다.** 경로까지 다시 짜도 0.5 %p 이내로만 더 줄었고, 풍향이 반대인 경우에는 덜 줄었습니다."));
c.push(P("이 두 결과에서 설계 방침이 정해졌습니다. **경로는 출발 전에 한 번 계획하고 안전장치로 확인합니다. 비행 중에는 속도만 갱신합니다. 경로는 장애물이 바뀔 때만 다시 짭니다.**", { after: 60 }));

// 5 안전장치
c.push(H1("5. 안전장치"));
c.push(H2("5.1 왜 필요한가"));
c.push(P("에너지 탐색은 가중치를 준 근사 탐색이고 직선 연결도 국소 판단입니다. 그래서 드물게 최단 경로를 속도만 최적화해 나는 것보다 에너지가 더 드는 경로를 냅니다. 제안 방법이 기존 방법보다 나쁜 경우가 있으면 \"더 우수하다\"는 주장이 약해집니다."));
c.push(H2("5.2 규칙"));
c.push(P("에너지 탐색 결과와 최단 경로 후보를 **같은 예보 바람에서** 비교해 예상 에너지가 적은 쪽을 고릅니다. 최단 후보는 다음 네 조건을 모두 통과해야 합니다."));
c.push(...table("최단 경로 후보의 통과 조건", ["순서", "조건", "통과하지 못하면"], [
  ["①", "기체의 상승·하강률로 날 수 있다. 급경사 구간은 '수평 이동 뒤 수직 이동'으로 고치고, 고친 구간이 건물·금지구역을 지나지 않는지 확인합니다.", "후보에서 제외"],
  ["②", "속도를 다시 정한 궤적이 위험 시간구간과 겹치지 않는다.", "후보에서 제외"],
  ["③", "예상 에너지가 남은 배터리 예산 안이다.", "후보에서 제외"],
  ["④", "예상 에너지가 에너지 탐색 결과보다 0.2 % 넘게 적다.", "에너지 탐색 결과를 사용"],
], [0.8, 6.3, 2.3], { align: [AlignmentType.CENTER, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...code([
  "r_e ← 에너지 탐색(출발, 도착, 장애물 표)                 # 기존 계획",
  "r_s ← 최단 탐색(출발, 도착, 장애물 표)                   # 거리 비용",
  "r_s ← 급경사 구간 고치기(r_s)   → 못 고치면 r_e 사용",
  "E_e, E_s ← 같은 예보(+편차 보정)에서 속도를 최적화한 예상 에너지",
  "r_s 의 궤적이 위험구간과 겹치거나 E_s > 예산   → r_e 사용",
  "E_s < E_e × (1 − 0.002)                          → r_s 사용,  아니면 r_e 사용",
]));
c.push(B("**②가 필요한 이유:** 최단 경로를 탐색할 때는 정속으로 시간을 잡지만, 실제로는 속도를 최적화해 납니다. 장애물과 만나는 시각이 달라지므로 다시 검사합니다."));
c.push(B("**④의 한계값:** 두 후보가 거의 같을 때 선택이 오락가락하지 않도록 둔 값입니다."));
c.push(B("화면의 '장애물 무시' 기준선도 같은 규칙으로 만들어, 계획 경로와 같은 조건에서 비교되게 했습니다."));
c.push(H2("5.3 연결한 곳"));
c.push(...table("안전장치의 적용 위치", ["위치", "언제", "끄는 방법"], [
  ["계획 API `/api/plan`, 3D 화면", "구간마다 탐색 직후", "요청에 `safeguard: false`"],
  ["명령줄 `dpo-plan`", "위와 같음", "`--no-safeguard`"],
  ["실시간 엔진", "구간 초기 계획과 비행 중 재계획", "시작 설정 `safeguard: false`, 화면의 체크 상자"],
  ["미션 파일", "채택한 경로로 만들어지므로 따로 할 일이 없습니다.", "–"],
], [3.4, 3.4, 2.6], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("5.4 효과"));
c.push(...table("안전장치의 효과", ["실험", "조건", "결과"], [
  ["E13 (계획 단계)", `${nAll}건, 탐색 가중치 1.0`, `최단 경로 채택 ${nShort}건. 기존보다 나쁜 경우가 15–19 %에서 1 % 이하로, 최악이 +8.0 %에서 +0.2 % 이내로 줄었습니다.`],
  ["E15 (실시간 엔진)", `예보가 맞는 경우 72건, 탐색 가중치 2.0`, `SOC 소모 ${rng(S15.map(([k]) => E15[k].cases.real.P1_vs_P0.mean))} % 감소, 나빠진 경우 없음`],
  ["E15 (실시간 엔진)", "예보가 틀린 경우 288건", `SOC 소모 ${rng(S15.flatMap(([k]) => ["rot30", "rot60", "rot90", "spd0.6"].map((q) => E15[k].cases[q].P1_vs_P0.mean)))} % 감소. 조합에 따라 0–21 %의 건에서 최대 +2.7 % 나빠졌습니다.`],
], [2.2, 2.8, 4.4], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...table("선택의 예 (판교, 15시 출발, 예상 에너지 [kJ])", ["어디서", "탐색 가중치", "에너지 탐색", "최단 후보", "선택"],
  N.safeguard_examples.map((e) => [e.where, num(e.weight), num(e.energy), e.shortest == null ? "조건 미달로 제외" : num(e.shortest), e.chosen === "shortest" ? "**최단 경로**" : "에너지 탐색"]),
  [4.2, 1.2, 1.3, 1.5, 1.2], { align: [AlignmentType.LEFT] }));
c.push(P("가중치 1.2로 계획하는 화면·명령줄에서는 에너지 탐색이 대체로 이깁니다. 가중치 2.0으로 빠르게 계획하는 실시간 엔진에서는 최단 후보가 이기는 경우가 더 자주 나옵니다. 오는 편처럼 최단 후보가 조건을 통과하지 못하면 에너지 탐색 결과를 그대로 씁니다."));
c.push(note("안전장치는 예보를 기준으로 판단합니다. 예보가 틀리면 예보에서 더 싼 경로가 실제 바람에서는 더 비쌀 수 있습니다. 이 손해는 출발 전에는 알 수 없고, 비행 중 속도 갱신이 일부를 되찾습니다."));

// 6 속도 갱신
c.push(H1("6. 속도 갱신"));
c.push(H2("6.1 생각"));
c.push(P("계획 때 정한 구간 속도는 예보 바람에서 최적입니다. 실제 바람이 다르면 같은 대지속도를 내는 데 필요한 대기속도가 달라져, 그 속도는 더 이상 최적이 아닙니다. 기체가 잰 바람과 예보의 차이를 알면 남은 구간의 속도를 다시 정할 수 있습니다. 경로는 바꾸지 않습니다."));
c.push(...figure("F32_speed_update_flow.png", "속도 갱신 한 번의 판단 순서.", 610));
c.push(H2("6.2 바람 편차의 추정"));
c.push(P("기체가 잰 바람 w_m과 같은 위치·시각의 예보 w_f의 차이를 지수평활합니다."));
c.push(...code(["b ← (1 − α)·b + α·(w_m − w_f),   α = 0.6   (첫 측정은 그대로 받음)", "|b| 가 15 m/s 를 넘으면 15 m/s 로 자름   (센서 이상값 방지)"]));
c.push(B("추정한 편차 b는 **예보 바람장 전체에 더하는 보정값**으로 씁니다. 한 지점의 차이를 영역 전체에 적용하는 단순한 방법입니다."));
c.push(B("실기체에서는 MAVLink `WIND` 메시지가 측정값입니다. 모의 운용에서는 '실제 바람장'에서 읽고, 측정 잡음을 넣을 수 있습니다."));
c.push(H2("6.3 속도 재결정과 채택"));
c.push(...table("채택 조건", ["조건", "내용", "기본값"], [
  ["이득", "보정한 예보에서, 새 속도의 남은 구간 예상 에너지가 지금 속도를 유지할 때보다 한계 넘게 적다.", "0.3 %"],
  ["비행 가능", "새 속도로 실제로 비행할 수 있다. 낼 수 없는 속도는 낼 수 있는 속도까지 낮춥니다.", "–"],
  ["안전", "새 속도로 비행한 궤적이 위험 시간구간과 겹치지 않는다.", "–"],
  ["우선순위", "경로 재계획이 진행 중이거나 예약돼 있으면 건너뛴다. 재계획이 요청되면 예약된 속도 갱신은 버린다.", "–"],
  ["남은 시간", "남은 비행 시간이 이보다 짧으면 갱신하지 않는다.", "20 s"],
], [1.5, 6.6, 1.3], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER] }));
c.push(P("**안전 조건이 핵심입니다.** 속도를 바꾸면 장애물과 만나는 시각이 달라집니다. 계획기가 넣어 둔 호버 대기는 특정 시각에 맞춘 것이므로, 속도만 바꾼 궤적은 다시 검사해야 합니다. 검사에 걸리면 지금 속도를 유지하고 '보류'로 기록합니다."));
c.push(...code([
  "매 주기 (순항 중):",
  "  w_m ← 기체가 잰 바람,  w_f ← 같은 위치·시각의 예보",
  "  b   ← 0.4·b + 0.6·(w_m − w_f)",
  "  재계획이 진행 중이거나 교체가 예약돼 있으면 끝",
  "  남은 경로 ← 지금부터 3 s 뒤의 위치에서 자른 경로와 대기",
  "  새 속도 ← (예보 + b) 에서 남은 구간의 최적 대지속도",
  "  E_new ← 새 속도의 예상 에너지,  E_keep ← 지금 속도를 유지할 때의 예상 에너지",
  "  E_new ≥ E_keep × (1 − 0.003)            → 유지",
  "  새 속도로 비행한 궤적이 위험구간과 겹침   → 유지 (보류로 기록)",
  "  그 밖에는 3 s 뒤에 새 속도로 교체하고, 속도 변경 지점을 명령 대기열에 넣음",
]));
c.push(H2("6.4 주기"));
c.push(...table("속도 갱신의 설정값", ["설정", "기본값", "근거"], [
  ["첫 측정", "순항 시작 5 s 뒤", "편차를 일찍 알수록 남은 구간 전체에 적용됩니다."],
  ["갱신 주기 `speed_update`", "30 s", "E14: 3–5분 비행에서 30초 이하 권고"],
  ["채택 한계 `speed_margin`", "0.003", "예보가 맞을 때 불필요한 교체를 막습니다."],
  ["교체 지연 `latency`", "3 s", "명령이 기체에 닿는 시간"],
  ["판단 비용", `${num(Math.min(...cpu15), 0)}–${num(Math.max(...cpu15), 0)} ms`, "E15의 중앙값. 감시 스레드에서 계산합니다."],
], [3.0, 2.2, 4.2], { align: [AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.LEFT] }));
c.push(H2("6.5 한 편의 예"));
c.push(...figure("F33_live_timeline.png", `판교, M100, 왕복. 실제 풍향이 예보와 60° 다른 경우. 속도 갱신을 켜면 도착 SOC가 ${num(TL.off.soc_end)} %에서 ${num(TL.on.soc_end)} %로 늘고, 비행 시간이 ${num(TL.off.T, 0)} s에서 ${num(TL.on.T, 0)} s로 줄었습니다.`, 580));
c.push(B(`갱신은 ${TL.on.updates}번 채택됐고, 추정한 바람 편차는 약 ${num(TL.on.bias)} m/s입니다.`));
c.push(B("가는 편은 뒷바람이라 차이가 작고, 오는 편(맞바람)에서 차이가 납니다."));
c.push(B("**X650은 이 경로에서 갱신이 채택되지 않았습니다.** 가정한 모델에서 최고 속도 15 m/s가 이미 최적이라, 바람이 달라도 바꿀 속도가 없기 때문입니다. 속도 갱신의 이득은 기체의 전력 곡선과 속도 상한에 달려 있습니다."));

// 7 플랫폼
c.push(H1("7. 실시간 플랫폼 연결"));
c.push(H2("7.1 엔진의 처리 순서"));
c.push(P("실시간 엔진은 시계에 맞춰 비행을 전진시키면서 감시·재계획·속도 갱신을 따로 계산합니다. 계산하는 동안에도 비행은 멈추지 않습니다."));
c.push(...table("실시간 엔진의 스레드", ["스레드", "주기", "하는 일", "이번에 더한 것"], [
  ["시계", "20 Hz", "시각 전진, 비행 단계 전환, 예약된 계획 교체, 이격 감시, 원격측정", "속도 갱신 시점 판단, 속도 명령 대기열"],
  ["감시", "2 s (설정)", "장애물 예측 갱신, 위험구간 표, 남은 경로 검사", "속도 갱신 계산, 속도 명령 전송"],
  ["재계획", "요청 시", "교체 시각의 위치에서 새 경로 계획, 완화 단계", "안전장치, 보정한 예보로 속도 결정"],
  ["개선", "배경", "정밀 가중치로 다시 풀어 더 싸면 교체", "보정한 예보로 속도 결정"],
], [1.2, 1.3, 3.9, 3.0], { align: [AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("7.2 실제 바람의 분리"));
c.push(P("이전 엔진은 계획한 궤적을 그대로 따라갔습니다. 예보와 실제가 같다고 본 것입니다. 이번에는 **계획에 쓰는 바람(예보)**과 **비행에 쓰는 바람(실제)**을 나눴습니다."));
c.push(B("계획: 예보(+편차 보정)에서 구간 속도 명령을 정합니다."));
c.push(B("비행: 실제 바람에서 그 명령 대지속도를 따라 날고, 에너지를 실제 바람으로 계산합니다."));
c.push(B("모의 운용에서 실제 바람은 세 가지로 줄 수 있습니다. 예보와 같음, 더 늦게 발표된 예보(06Z), 풍향 회전·풍속 배율입니다."));
c.push(H2("7.3 화면"));
c.push(...figure("platform_live_speed.png", "실시간 운용 화면(/live). 오른쪽 위 '효율'·'실제 바람' 줄이 새 설정이고, 아래 '미션 연결' 카드에 채택한 속도 명령과 남은 구간 미션 내려받기가 있습니다.", 600));
c.push(...table("화면에 더한 것", ["위치", "내용"], [
  ["임무 설정", "기체 선택, 안전장치 켬/끔, 속도 갱신 주기, 모의 실제 바람, 측정 잡음"],
  ["실시간 상태", "명령 대지속도, 추정한 바람 편차, 속도 갱신 채택 수 / 측정 수와 누적 이득"],
  ["미션 연결", "남은 구간 미션(.waypoints)·경로표(.csv) 내려받기, 안전장치 채택 내역, 보낼 속도 명령 표"],
  ["이벤트", "속도 갱신 채택·보류, 안전장치의 최단 경로 채택"],
], [2.0, 7.4], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...figure("platform_3d_changwon.png", "3D 지도 화면(/3d). 계획 단계의 안전장치는 이 화면과 명령줄의 계획에 함께 적용됩니다. 창원 지도, 순회 임무와 예시 장애물.", 560));
c.push(H2("7.4 운용 순서"));
c.push(NUM("`dpo-plan --check`로 자료와 계획기를 점검합니다."));
c.push(NUM("3D 화면이나 `dpo-plan`으로 경로를 계획하고 미션 파일을 만듭니다. 안전장치가 고른 경로가 파일에 들어갑니다."));
c.push(NUM("미션 파일을 지상국으로 기체에 올립니다."));
c.push(NUM("실시간 화면에서 같은 임무를 시작합니다. 기체 연결을 지정했으면 순항 중 속도 명령이 나갑니다."));
c.push(NUM("비행 중에는 화면에서 SOC, 이격, 명령 속도, 바람 편차, 속도 갱신 채택 수를 봅니다."));
c.push(NUM("장애물·금지구역이 바뀌면 화면이나 API로 넣습니다. 엔진이 남은 경로를 검사해 필요하면 다시 계획합니다."));
c.push(NUM("끝나면 로그(JSON)를 내려받습니다. 설정, 이벤트, 원격측정, 속도 갱신 이력이 들어 있습니다."));
c.push(...table("한 편의 이벤트 기록 예 (판교, M100, 편도, 풍향 60° 오차)", ["시각 [s]", "종류", "내용"],
  N.events.slice(0, 7).map((e) => [num(e.t, 0), { phase: "단계", replan: "계획", speed: "속도" }[e.kind] || e.kind, e.msg]),
  [1.0, 1.0, 7.4], { align: [AlignmentType.CENTER, AlignmentType.CENTER, AlignmentType.LEFT], size: 17 }));
c.push(H2("7.5 API"));
c.push(...table("더하거나 바꾼 API", ["경로", "방식", "내용"], [
  ["`/api/health`", "GET", "버전, 지역, 기체, 격자 크기, 실시간 상태, MAVLink 연결 상태"],
  ["`/api/plan`", "POST", "`safeguard` 설정. 응답에 구간별 채택 경로와 두 후보의 예상 에너지"],
  ["`/api/live/start`", "POST", "`drone`, `safeguard`, `speed_update`, `speed_margin`, `wind_truth`, `wind_noise`"],
  ["`/api/live/state`, `/stream`", "GET", "바람 측정값·편차, 속도 갱신 통계, 안전장치 내역, 명령 속도"],
  ["`/api/live/mission`", "GET", "남은 구간 미션 요약, 속도 명령 목록, 갱신 이력"],
  ["`/api/live/mission.waypoints`, `.csv`", "GET", "남은 구간 미션 파일 내려받기"],
  ["`/api/live/upload`", "POST", "남은 구간 미션을 MAVLink로 올리기 (연결했을 때)"],
], [3.5, 0.9, 5.0], { align: [AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.LEFT] }));

// 8 미션
c.push(H1("8. 미션 파일과 기체 연결"));
c.push(H2("8.1 두 가지 방식"));
c.push(...table("속도 갱신을 기체에 전하는 방식", ["방식", "하는 일", "장점", "주의"], [
  ["명령 방식 (기본)", "기체의 미션은 그대로 두고, 속도 변경 지점마다 `MAV_CMD_DO_CHANGE_SPEED`를 보냅니다.", "경로점을 건드리지 않습니다.", "미션 안의 속도 항목이 나중에 덮어쓰지 않는지 확인해야 합니다."],
  ["파일 방식", "지금 위치에서 시작하는 남은 구간 미션을 다시 올립니다.", "운용자가 지상국에서 내용을 볼 수 있습니다.", "비행 중 미션 교체입니다. 운용자가 판단해서 실행합니다."],
], [1.8, 3.3, 2.0, 2.3], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("8.2 남은 구간 미션"));
c.push(P("출발 전 미션은 홈 → 이륙 → 경로점·속도 변경 → 착륙으로 이루어집니다. 비행 중 미션은 이륙 항목이 없고, 첫 경로점이 지금 위치입니다."));
{
  const rows = fs.readFileSync(path.join(RES, "missions", "live_remaining_sample.csv"), "utf8").trim().split("\n").slice(1).map((l) => l.split(","));
  const pick = rows.slice(0, 7).concat(rows.slice(-2));
  c.push(...table(`남은 구간 미션의 예 (출발 120 s 뒤, 항목 ${SM.summary.n_items}개 중 일부)`, ["번호", "명령", "상대 고도 [m]", "지면 위 [m]", "속도 [m/s]", "시각 [s]", "SOC [%]", "설명"],
    pick.map((r) => [r[0], r[1], r[4] || "–", r[6] || "–", r[8] || "–", r[9], r[10], r[11]]), [0.7, 1.9, 1.2, 1.1, 1.1, 1.0, 1.0, 2.2], { size: 17 }));
}
c.push(B(`이 미션의 예상 도착 SOC는 ${num(SM.summary.soc_end)} %이고, 그 뒤 실제로 도착했을 때의 SOC는 ${num(SM.soc_final)} %였습니다.`));
c.push(B("일직선 위의 중간 경로점은 합치고, 75 m보다 짧은 조각의 작은 속도 변화(1.5 m/s 미만)는 건너뜁니다. 명령이 너무 잦지 않게 하기 위해서입니다."));
c.push(B("파일은 `pymavlink`의 미션 로더로 다시 읽어 항목 수와 속도 변경 수가 같은지 확인합니다."));
c.push(H2("8.3 MAVLink 메시지"));
c.push(...table("쓰는 메시지", ["방향", "메시지", "내용"], [
  ["지상 → 기체", "`COMMAND_LONG` (`MAV_CMD_DO_CHANGE_SPEED`, 178)", "p1 = 1 (대지속도), p2 = 속도 [m/s], p3 = −1"],
  ["기체 → 지상", "`COMMAND_ACK`", "수락 여부. 거부되면 이벤트에 경고를 남깁니다."],
  ["기체 → 지상", "`WIND`", "불어오는 방향과 풍속. ArduCopter는 `EK3_DRAG_BCOEF_X/Y`를 설정해야 추정합니다."],
  ["기체 → 지상", "`GLOBAL_POSITION_INT`, `BATTERY_STATUS`", "위치, 배터리 잔량 (읽기 함수만 제공)"],
  ["지상 ↔ 기체", "`MISSION_COUNT`, `MISSION_REQUEST_INT`, `MISSION_ITEM_INT`, `MISSION_ACK`", "미션 올리기"],
], [1.5, 4.4, 3.5], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...code(["dpo-server --mavlink udpin:0.0.0.0:14550        # 연결하면 순항 중 속도 변경 지점마다 명령을 보냅니다"]));
c.push(note("검증 범위: 같은 컴퓨터의 UDP 루프백에서 모의 기체와 속도 명령(수락·거부), 바람 수신, 상태 수신, 미션 올리기를 왕복했습니다. 실시간 엔진을 붙여 한 편을 날렸을 때 보낸 명령이 모두 수락됐습니다. ArduPilot SITL과 실기체에서는 시험하지 않았습니다."));

// 9 폐루프
c.push(H1("9. 폐루프 검증 (E15)"));
c.push(P("4장의 실험은 실험용 코드로 잰 값입니다. 여기서는 **배포하는 코드 경로**, 곧 실시간 엔진을 그대로 돌려 출발 전 계획부터 착륙까지의 SOC를 쟀습니다."));
c.push(...table("실험 설계", ["항목", "내용"], [
  ["조합", "창원 · X650, 판교 · M100, 판교 · X650"],
  ["날짜·경로", "4일(10월 1·12·27·31일) × 출발·도착 쌍 6개, 15시 출발, 편도 1.5–2.8 km"],
  ["바람 경우", "실제 예보 오차(00Z ↔ 06Z), 풍향 30°·60°·90° 회전, 풍속 0.6배"],
  ["설정", "연결 전(안전장치 끔, 속도 갱신 끔) / 안전장치만 / + 속도 갱신 60·30·15 s"],
  ["장애물", "경로 한가운데를 가로지르는 이동 장애물(반경 80 m)을 넣은 경우를 따로 실행"],
  ["통계", "평균과 95 % 구간 (날짜 단위 부트스트랩)"],
], [1.7, 7.7], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...figure("F30_live_closed_loop.png", "E15. 연결 전 플랫폼 대비 SOC 소모 변화. 회색은 안전장치만, 파란색은 속도 갱신을 더한 것입니다.", 625));
c.push(...table("연결 전 플랫폼 대비 SOC 소모 변화 [%] (기본 설정: 안전장치 + 속도 갱신 30 s)", ["바람 경우", "추정 편차", ...S15.map(([, lb]) => lb), "그중 속도 갱신의 몫"],
  CASES15.map(([cn, lb]) => [lb, `${num(E15.changwon_x650.cases[cn].bias)} m/s`, ...S15.map(([k]) => `${neg(E15[k].cases[cn].P3_vs_P0.mean)} (${neg(E15[k].cases[cn].P3_vs_P0.ci[0])}, ${neg(E15[k].cases[cn].P3_vs_P0.ci[1])})`),
    `${neg(Math.max(...S15.map(([k]) => E15[k].cases[cn].P3_vs_P1.mean)))} – ${neg(Math.min(...S15.map(([k]) => E15[k].cases[cn].P3_vs_P1.mean)))}`]),
  [1.6, 1.1, 1.9, 1.9, 1.9, 1.6], { size: 17 }));
c.push(B(`**속도 갱신은 ${n15}건 모두에서 안전장치만 쓴 것보다 나쁘지 않았습니다** (가장 나쁜 경우 ${f1(p3p1worst, 2)} %).`));
c.push(B("**예보가 틀릴수록 이득이 큽니다.** 실제 예보 오차에서는 0.1–0.3 %이고, 풍향 90° 오차에서는 2.1–4.8 %입니다."));
c.push(B("**주기 15·30·60초의 차이는 거의 없었습니다.** 순항 5초 뒤의 첫 측정이 대부분을 고칩니다. 편도 비행에서 채택은 평균 0.1–1.0회였습니다."));
c.push(note("주기의 차이가 없는 것은 틀린 예보를 만든 방법 때문이기도 합니다. 풍향 회전과 풍속 배율은 오차가 영역 전체에 같으므로 한 번 재면 끝납니다. 실제 바람의 오차가 장소마다 다르면 주기가 다시 중요해집니다."));
c.push(...table("설정별 SOC 소모 변화 [%] (연결 전 대비 평균)", ["조합", "바람 경우", "안전장치만", "+ 갱신 60 s", "+ 갱신 30 s", "+ 갱신 15 s", "채택 횟수 (30 s)"],
  S15.flatMap(([k, lb]) => CASES15.map(([cn, cl]) => { const e = E15[k].cases[cn];
    return [lb, cl, neg(e.P1_vs_P0.mean, 2), neg(e.P2_vs_P0.mean, 2), neg(e.P3_vs_P0.mean, 2), neg(e.P4_vs_P0.mean, 2), num(e.P3_upd, 2)]; })),
  [1.9, 1.9, 1.3, 1.3, 1.3, 1.3, 1.6], { size: 17 }));
c.push(H2("9.1 장애물이 있을 때의 안전"));
c.push(...table("가로지르는 이동 장애물을 넣은 경우", ["조합 · 바람", "건수", "완주 (끔 / 켬)", "침범 (끔 / 켬)", "최소 이격 (끔 / 켬)", "보류된 갱신", "SOC 변화"],
  S15.flatMap(([k, lb]) => [["real", "실제 오차"], ["rot60", "풍향 60°"]].map(([cn, cl]) => { const o = E15[k].obstacle[cn];
    return [`${lb} · ${cl}`, o.n, `${o.P1.done} / ${o.P3.done}`, `${o.P1.col} / ${o.P3.col}`, `${num(o.P1.min_sep, 0)} / ${num(o.P3.min_sep, 0)} m`, o.P3.rej, `${neg(o.P3_vs_P1, 2)} %`]; })),
  [2.6, 0.8, 1.5, 1.4, 1.8, 1.2, 1.2], { size: 17 }));
c.push(B(`${obsN}건 모두 완주했고 침범이 없습니다. 최소 이격은 ${num(Math.min(...obsSep), 0)}–${num(Math.max(...obsSep), 0)} m입니다.`));
c.push(B(`새 속도의 궤적이 위험구간과 겹쳐 보류된 갱신이 ${obsRej}회 있었습니다. 안전 조건이 실제로 작동한 것입니다.`));

c.push(H2("9.2 이 결과로 말할 수 있는 것과 없는 것"));
c.push(...table("주장의 범위", ["말할 수 있는 것", "아직 말할 수 없는 것"], [
  ["배포하는 코드에서 안전장치와 속도 갱신이 SOC 소모를 줄이고, 속도 갱신은 한 번도 손해가 아니었다 (모의 운용).", "실제 비행에서 같은 크기의 절감이 나온다."],
  ["속도를 갱신해도 장애물 회피가 유지된다 (위험구간 검사, 침범 0).", "실제 감시 자료의 지연·누락이 있을 때도 유지된다."],
  ["이득은 예보가 틀린 만큼 커진다.", "실증 지역에서 예보가 얼마나 틀리는지, 따라서 평소의 이득이 얼마인지."],
  ["속도 명령과 미션이 MAVLink 형식으로 오가고 수락·거부가 처리된다 (모의 기체).", "ArduCopter가 그 명령을 비행 중 의도대로 적용한다."],
], [4.9, 4.5], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(P("오른쪽 칸을 채우는 일이 12장의 다음 단계입니다. 그중 전력 곡선 보정과 SITL 시험이 가장 먼저입니다."));

// 10 배포
c.push(H1("10. 배포"));
c.push(H2("10.1 패키지"));
c.push(...table("패키지 구성", ["항목", "내용"], [
  ["이름 · 버전", "`drone-path-opt` 1.0.0 (wheel 약 355 KB, 순수 파이썬)"],
  ["명령", "`dpo-server` (플랫폼 서버), `dpo-plan` (명령줄 계획·미션 파일)"],
  ["기본 의존성", "numpy, scipy, numba, matplotlib"],
  ["선택 설치", "`mavlink` (기체 연결), `data` (지형·예보 내려받기), `report` (자료 생성), `dev` (시험)"],
  ["파이썬", "3.10 이상"],
  ["포함 파일", "화면 정적 파일, three.js 0.160 (동봉)"],
], [2.0, 7.4], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...table("주요 모듈", ["모듈", "역할"], [
  ["`dpo.planner`, `dpo.st_planner`", "에너지 탐색(WE-LT*), 시공간 탐색, 경로 시뮬레이션 (`simulate`: 명령 속도·바람 보정 지원)"],
  ["`dpo.mission`", "임무 구성, 안전장치 (`st_safeguard`, `plan_best_of_two`), 급경사 구간 고치기"],
  ["`dpo.speed_update`", "바람 편차 추정, 남은 경로 자르기, 속도 재결정, 속도 명령 목록"],
  ["`dpo.live`", "실시간 엔진 (감시, 재계획, 속도 갱신, 속도 명령 대기열)"],
  ["`dpo.ardupilot`", "미션 파일 (출발 전 `leg_items`, 비행 중 `remaining_mission`)"],
  ["`dpo.mavlink_bridge`", "MAVLink 연결 (`MavBridge`), 모의 기체 (`FakeVehicle`)"],
  ["`dpo.webapp`", "서버, API, 화면 (계획, 3D 지도, 실시간 운용)"],
  ["`dpo.cli`", "명령줄 `dpo-plan`"],
], [3.3, 6.1], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(P("운용 설치에서 pandas·rasterio·pygrib·shapely가 필요 없도록 정리했습니다. 실측 항적 읽기를 표준 라이브러리로 바꿨고(결과가 이전과 같은지 대조), 건물 원자료가 없으면 3D 화면이 격자 높이로 상자를 그립니다."));
c.push(H2("10.2 설치와 실행"));
c.push(...code(["pip install \"drone-path-opt[mavlink]\"     # 저장소에서는: pip install -e \".[dev]\"",
  "export DPO_HOME=/opt/dpo                  # 자료 폴더",
  "dpo-plan --check                          # 자료와 계획기 점검",
  "dpo-plan --drone x650 --payload 1.0 --dep-hour 15 --out missions/",
  "dpo-server --host 0.0.0.0 --port 8765 --site changwon"]));
c.push(P("`dpo-plan`은 구간별 SOC, 시간, 거리와 미션 파일의 위치를 출력합니다. 새 가상환경에 설치한 뒤 저장소 밖에서 실행한 결과입니다."));
c.push(...code(["성남 판교·분당 · Holybro X650 (대상 기체, 추정) · 15시 출발 · 안전장치 켬",
  "    out: SOC  16.1 %p ·   249 s ·  2.62 km · 대기 0 s",
  "   back: SOC  32.1 %p ·   576 s ·  3.05 km · 대기 0 s",
  "  합계 SOC 소모 48.2 %p → 도착 SOC 51.8 % (예비 확보)",
  "  → missions/pangyo_x650_15h_out.waypoints",
  "  → missions/pangyo_x650_15h_out.csv   (back 도 같은 형식)"]));
c.push(...table("환경변수", ["변수", "뜻", "기본값"], [
  ["`DPO_HOME`", "자료 폴더", "소스 저장소 또는 현재 폴더"],
  ["`DPO_SITE`", "대상 지역 (`pangyo`, `changwon`)", "`pangyo`"],
  ["`DPO_HOST`, `DPO_PORT`", "서버 주소와 포트", "`127.0.0.1`, `8765`"],
  ["`DPO_MAVLINK`", "기체 연결 주소", "없음"],
], [2.6, 4.2, 2.6], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("10.3 자료 폴더"));
c.push(...table("`DPO_HOME` 아래의 자료", ["경로", "내용", "필수"], [
  ["`data/processed/site_grid*.npz`", "지형·건물 점유 격자", "예"],
  ["`data/raw/gfs_*_00z.npz`", "예보 바람", "예"],
  ["`data/raw/gfs_*_06z.npz`", "모의 운용의 실제 바람 (06Z)", "아니오"],
  ["`data/raw/adsb/`", "실측 ADS-B 항적 (침입기 투입)", "아니오"],
  ["`results/adsb_error_model.json`", "실측 오차 분위수 모양", "아니오"],
  ["`scenarios/`", "예시 장애물", "아니오"],
], [3.8, 4.4, 1.2], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER] }));
c.push(P("필수 자료가 없으면 시작할 때 없는 파일 이름과 지금의 자료 폴더를 알려 줍니다. 운용에 필요한 자료는 약 20 MB입니다."));
c.push(H2("10.4 Docker와 CI"));
c.push(...code(["docker build -t drone-path-opt .", "docker run --rm -p 8765:8765 -e DPO_SITE=changwon drone-path-opt"]));
c.push(B("`Dockerfile`은 wheel을 설치하고 운용 자료만 복사한 뒤, 일반 사용자 권한으로 `dpo-server`를 띄웁니다. 상태 점검은 `/api/health`를 봅니다."));
c.push(B("**이 환경에는 Docker 데몬이 없어 이미지 빌드는 시험하지 못했습니다.** 같은 순서(설치 → 자료 복사 → 저장소 밖 실행 → 상태 점검)는 가상환경에서 확인했습니다."));
c.push(B("`.github/workflows/ci.yml`은 파이썬 3.10과 3.12에서 설치, 점검, 시험, wheel 빌드를 실행합니다."));
c.push(B("서버는 종료 신호(SIGTERM)를 받으면 실시간 엔진과 MAVLink 연결을 닫고 끝납니다."));
c.push(note("서버에는 인증이 없습니다. `0.0.0.0`으로 열 때는 신뢰하는 망 안에서만 쓰십시오."));

// 11 시험
c.push(H1("11. 시험과 검증 범위"));
c.push(...table("시험 (pytest 41개)", ["파일", "개수", "확인하는 것"], [
  ["`tests/test_core.py`", "17", "전력 모델, 계획기, 시공간 탐색, 장애물 표, 재계획"],
  ["`tests/test_live.py`", "5", "실시간 엔진: 침입기 회피, 비상 정지, 배터리 예비, 개선"],
  ["`tests/test_target.py`", "7", "X650 모델, 미션 내보내기, 3D 장면, 순회, 지역 전환"],
  ["`tests/test_superiority.py`", "4", "급경사 구간 고치기, 통계 함수, 계획 단계 안전장치, 갱신 규칙"],
  ["`tests/test_deploy.py`", "8", "이번 작업 (아래)"],
], [3.2, 0.9, 5.3], { align: [AlignmentType.LEFT, AlignmentType.CENTER, AlignmentType.LEFT] }));
c.push(...table("이번 작업의 시험", ["시험", "확인하는 것"], [
  ["바람 편차", "지수평활, 상한, 잘못된 측정 버리기"],
  ["명령 속도 비행과 경로 자르기", "같은 바람에서 계획과 같음, 어느 기록에서 잘라도 남은 에너지·도착 시각이 같음(호버 중간 포함), 낼 수 없는 속도는 감속"],
  ["시공간 안전장치", "고른 경로가 후보 중 최소이고 충돌이 없음, 에너지 탐색 결과가 없으면 최단 후보로 답함"],
  ["실시간 속도 갱신과 안전장치", "예보 = 실제면 채택 0회이고 결과가 같음, 풍향 60° 오차에서 SOC 소모 2 % 이상 감소"],
  ["장애물 안전", "속도 갱신을 켜도 완주, 침범 0"],
  ["남은 구간 미션", "이륙 항목 없음, 예상 도착 SOC = 실제 도착 SOC, pymavlink로 다시 읽기"],
  ["MAVLink 루프백", "속도 명령 수락·거부, 바람·상태 수신, 미션 올리기"],
  ["엔진 + 연결", "한 편을 날리며 보낸 속도 명령이 모두 수락됨"],
], [3.0, 6.4], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...table("검증 범위", ["항목", "방법", "결과"], [
  ["단위·통합 시험", "`pytest -q` (본 환경, 새 가상환경)", "41개 통과"],
  ["설치", "wheel → 새 가상환경 → 저장소 밖에서 `dpo-plan`, `dpo-server`", "동작, `/api/health` 200"],
  ["실시간 화면", "브라우저 자동 시험: 설정 → 비행 → 갱신 채택 → 명령 표 → 미션 내려받기", "콘솔 오류 없음"],
  ["폐루프 효과와 안전", `E15: ${n15}건 + 장애물 ${obsN}건`, "9장"],
  ["MAVLink", "모의 기체와 UDP 루프백", "통과"],
  ["Docker 이미지 빌드", "–", "**미검증**"],
  ["ArduPilot SITL · 실기체", "–", "**미검증**"],
], [2.4, 4.9, 2.1], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER], highlight: [5, 6] }));
c.push(H2("11.1 설치와 화면을 확인한 기록"));
c.push(...table("새 가상환경 설치 확인 (저장소 밖에서 실행)", ["단계", "결과"], [
  ["wheel 빌드", "`drone_path_opt-1.0.0-py3-none-any.whl`, 약 355 KB"],
  ["새 가상환경에 설치", "기본 의존성과 pymavlink만 설치됨 (pandas·rasterio 없음)"],
  ["자료 폴더 없이 `dpo-plan --check`", "없는 파일 이름과 자료 폴더 위치를 알리고 종료 코드 2"],
  ["`DPO_HOME` 지정 후 `dpo-plan --check`", "정상: 판교, 격자 22 × 221 × 247, 바람 13시각, 기체 2종"],
  ["`dpo-plan`으로 판교 왕복·창원 순회", "미션 파일(.waypoints, .csv)과 요약(JSON) 생성"],
  ["`dpo-server` 기동", "`/api/health` 200, 화면 3종과 정적 파일 200"],
  ["계획·실시간 API 호출", "계획 가능, 실시간 운용 완료, 속도 갱신 1회 채택"],
  ["종료 신호 (SIGTERM)", "엔진을 멈추고 정상 종료"],
  ["같은 환경에서 `pytest -q`", "41개 통과"],
], [3.6, 5.8], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(...table("실시간 화면 자동 시험 (판교, M100, 편도, 풍향 60° 오차, 10배속)", ["확인한 것", "관찰한 값"], [
  ["새 설정이 화면에 있고 요청에 실림", "안전장치 켬, 속도 갱신 30 s, 실제 바람 '풍향 +60°'"],
  ["속도 갱신 채택이 화면에 나타남", "채택 1 / 측정 1, 남은 구간 예상 에너지 −0.7 %, 바람 편차 10.5 m/s"],
  ["속도 명령 표", "7개 명령 (시각, 위치, 고도, 대지속도)"],
  ["안전장치 내역", "최단 경로 1회 채택"],
  ["남은 구간 미션 내려받기", "응답 200, 첫 줄 `QGC WPL 110`"],
  ["기체를 X650으로, 실제 바람을 06Z로 바꿔 다시 비행", "완료. 탑재 한도가 2.2 kg으로 바뀜"],
  ["브라우저 콘솔 오류", "없음"],
], [4.2, 5.2], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("11.2 재현 명령"));
c.push(...code(["pip install -e \".[dev]\" && dpo-plan --check && pytest -q            # 시험",
  "PYTHONPATH=src python scripts/exp_live_closed_loop.py run pangyo m100   # E15 한 조합",
  "PYTHONPATH=src python scripts/exp_live_closed_loop.py analyze           # 통계와 그림",
  "PYTHONPATH=src python scripts/make_deploy_figures.py                    # 구성도 등",
  "node scripts/build_deploy_report.js                                     # 이 문서"]));

// 12 한계
c.push(H1("12. 한계와 다음 단계"));
c.push(...table("한계", ["한계", "영향"], [
  ["모든 수치가 시뮬레이션이고, 평가용 실제 바람도 예보이거나 가정입니다.", "실제 바람과 예보의 차이를 모릅니다. 속도 갱신의 실제 이득은 이 차이에 달려 있습니다."],
  ["X650 전력 모델이 추정입니다.", "속도에서 나오는 이득의 크기가 달라질 수 있습니다."],
  ["바람 편차를 한 지점에서 재어 영역 전체에 적용합니다.", "장소마다 다른 국지 바람에서는 보정이 맞지 않을 수 있습니다."],
  ["실시간 엔진이 기체를 모의합니다.", "기체의 실제 위치·속도를 받아 계획과 맞추는 단계가 없습니다."],
  ["명령 방식에서 미션 내 속도 항목과의 우선순위를 확인하지 못했습니다.", "SITL에서 확인하기 전에는 실기체에 쓰면 안 됩니다."],
  ["순회 임무는 실시간 화면에서 지원하지 않습니다.", "계획과 미션 파일까지만 됩니다."],
], [5.2, 4.2], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("12.1 다음 단계"));
c.push(...table("다음 단계 (우선순위 순)", ["순위", "할 일", "필요한 것"], [
  ["1", "X650 전력 곡선 보정", "정속 수평 비행 5–15 m/s, 탑재 0·1·2 kg의 전력 로그"],
  ["2", "ArduPilot SITL 시험", "속도 명령의 우선순위, `WIND` 수신, 남은 구간 미션 올리기"],
  ["3", "기체 위치 추종", "`GLOBAL_POSITION_INT`를 받아 계획과 실제 위치의 차이를 다루는 단계"],
  ["4", "실측 바람과 예보의 차이 측정", "실증 지역의 비행 고도 바람, 기상청 고해상도 자료"],
  ["5", "실비행 비교", "기존 경로와 제안 경로를 같은 날 번갈아 비행 (10 % 차이는 4쌍, 5 % 차이는 7쌍 필요 추정)"],
  ["6", "Docker 이미지 빌드 확인, 서버 인증", "운용 환경"],
], [0.8, 3.3, 5.3], { align: [AlignmentType.CENTER, AlignmentType.LEFT, AlignmentType.LEFT] }));

// 부록
c.push(H1("부록. 설정 표와 용어"));
c.push(H2("A. 명령줄 `dpo-plan`"));
c.push(...table("`dpo-plan`의 인자", ["인자", "뜻"], [
  ["`--site`, `--drone`, `--payload`, `--kind`, `--dep-hour`", "지역, 기체, 탑재중량, 배송/회수, 출발 시각"],
  ["`--hub X,Y`, `--dst X,Y`, `--lonlat`", "출발지·목적지. 지역 좌표 [m] 또는 경도,위도"],
  ["`--tour \"X,Y;X,Y\"`, `--dwell`", "순회 경유점과 체류 시간"],
  ["`--one-way`, `--reserve`, `--eps`, `--obstacles FILE`", "편도, 예비 SOC, 위험 한도, 장애물 파일"],
  ["`--no-safeguard`", "안전장치 끔"],
  ["`--out DIR`, `--json`, `--check`", "미션 파일 폴더, JSON 출력, 점검"],
], [4.6, 4.8], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(P("종료 코드는 0(임무 가능), 1(임무 불가), 2(입력·자료 오류)입니다."));
c.push(H2("B. 실시간 운용 설정"));
c.push(...table("`POST /api/live/start`의 주요 키", ["키", "뜻", "기본값"], [
  ["`drone`", "`m100` 또는 `x650`", "`m100`"],
  ["`safeguard`", "안전장치", "`true`"],
  ["`speed_update`", "속도 갱신 주기 [s]. 0이면 끔", "`30`"],
  ["`speed_margin`", "채택에 필요한 예상 에너지 감소 비율", "`0.003`"],
  ["`wind_truth`", "모의 실제 바람: `null`, `\"06z\"`, `{\"rot\": 60, \"scale\": 1}`", "`null`"],
  ["`wind_noise`", "바람 측정 잡음 σ [m/s]", "`0`"],
  ["`wind_from_link`", "MAVLink `WIND` 값을 측정으로 사용", "`false`"],
  ["`eps`, `period`, `latency`, `reserve`", "위험 한도, 감시 주기, 교체 지연, 예비 SOC", "0.05, 2 s, 3 s, 20 %"],
], [2.7, 5.0, 1.7], { align: [AlignmentType.LEFT, AlignmentType.LEFT, AlignmentType.CENTER] }));
c.push(H2("C. 산출물"));
c.push(...table("이번 작업의 산출물", ["파일", "내용"], [
  ["`src/dpo/speed_update.py`, `mavlink_bridge.py`, `cli.py`", "새 모듈: 속도 갱신, 기체 연결, 명령줄"],
  ["`src/dpo/live.py`, `mission.py`, `planner.py`, `ardupilot.py`, `webapp/`", "연결을 위해 고친 모듈"],
  ["`pyproject.toml`, `Dockerfile`, `.github/workflows/ci.yml`", "패키징, 이미지, 자동 시험"],
  ["`tests/test_deploy.py`", "이번 작업의 시험 8개"],
  ["`scripts/exp_live_closed_loop.py` → `results/E15_*.json`", "폐루프 검증과 결과"],
  ["`results/missions/live_remaining_sample.*`", "남은 구간 미션의 예"],
  ["`docs/deployment.md`, `HANDOVER.md`", "배포 안내, 인수인계"],
  ["`docs/drone_soc_deployment_report.docx`", "이 문서 (`scripts/build_deploy_report.js`로 생성)"],
], [5.2, 4.2], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));
c.push(H2("D. 용어"));
c.push(...table("용어", ["용어", "뜻"], [
  ["SOC", "배터리 잔량 [%]. 'SOC 소모'는 임무에 쓴 양 [%p]"],
  ["대지속도 / 대기속도", "땅에 대한 속도 / 공기에 대한 속도. 둘의 차이가 바람입니다."],
  ["위험 시간구간", "이동 장애물이 어떤 칸을 차지하는 시간 범위. 불확실성만큼 넓힙니다."],
  ["안전장치", "에너지 탐색 경로와 최단 경로 후보 중 예보 기준으로 더 싼 쪽을 고르는 규칙"],
  ["속도 갱신", "기체가 잰 바람으로 예보를 보정해 남은 구간의 대지속도 명령을 다시 정하는 것"],
  ["바람 편차", "기체가 잰 바람 − 예보 바람 (지수평활 추정값)"],
  ["폐루프 검증", "계획·비행·감시·갱신이 서로 영향을 주는 전체 과정을 돌려서 재는 것"],
], [2.6, 6.8], { align: [AlignmentType.LEFT, AlignmentType.LEFT] }));

// ------------------------------------------------------------------ 문서
const doc = new Document({
  creator: "drone-path-opt", title: "드론 SoC 최대화 경로계획 – 속도 갱신·안전장치의 실시간 플랫폼 연결과 배포판",
  styles: {
    default: {
      document: { run: { font: FONT, size: 21 } },
      heading1: { run: { font: FONT, size: 32, bold: true, color: NAVY }, paragraph: { spacing: { before: 0, after: 220 }, keepNext: true } },
      heading2: { run: { font: FONT, size: 25, bold: true, color: TEAL }, paragraph: { spacing: { before: 260, after: 120 }, keepNext: true } },
      heading3: { run: { font: FONT, size: 22, bold: true, color: INK }, paragraph: { spacing: { before: 180, after: 90 }, keepNext: true } },
    },
  },
  numbering: { config: [
    { reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 240 } } } },
      { level: 1, format: LevelFormat.BULLET, text: "–", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 800, hanging: 240 } } } }] },
    { reference: "num", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 460, hanging: 300 } } } }] },
  ] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838 }, margin: { top: 1300, bottom: 1250, left: 1247, right: 1247 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, spacing: { after: 0 },
      children: [new TextRun({ text: "드론 SoC 최대화 경로계획 · 배포판 v1.0.0", font: FONT, size: 16, color: GRAY })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 },
      children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: GRAY })] })] }) },
    children: c,
  }],
});

const out = process.argv[2] || path.join(ROOT, "docs", "drone_soc_deployment_report.docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(out, buf); console.log(`saved ${out} (${Math.round(buf.length / 1024)} KB) · 그림 ${figNo} · 표 ${tabNo}`); });
