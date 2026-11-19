// 사용자 매뉴얼 (Word, A4 가로) 생성: 단계마다 왼쪽에 실제 화면, 오른쪽에 할 일과 읽는 법.
// 사용: NODE_PATH=$(npm root -g) node scripts/build_user_manual.js [출력.docx]
// 그림: docs/manual_img/*.png (scripts/capture_manual.js 와 구동 시험의 터미널 기록). 수치는 docs/manual_img/capture_log.json.
const fs = require("fs");
const path = require("path");
const { Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun, Header, Footer, AlignmentType, PageNumber,
  HeadingLevel, BorderStyle, WidthType, ShadingType, VerticalAlign, PageOrientation, LevelFormat } = require("docx");

const ROOT = path.resolve(__dirname, "..");
const IMG = path.join(ROOT, "docs", "manual_img");
const LOGC = JSON.parse(fs.readFileSync(path.join(IMG, "capture_log.json"), "utf8"));
const FONT = "맑은 고딕", MONO = "Consolas";
const NAVY = "004E8F", TEAL = "1B6E8C", INK = "16202A", GRAY = "5B6770", LIGHT = "E3EEF3", PALE = "F4F7F9", RED = "C0201B";
const W = 15238;                                   // 본문 폭 [DXA] (A4 가로, 좌우 여백 0.8 in)

function runs(text, opt = {}) {
  const out = [], re = /(\*\*.+?\*\*|`[^`]+`)/g;
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
const P = (t, o = {}) => new Paragraph({ children: runs(t, o), spacing: { after: o.after ?? 100, line: 300 }, alignment: o.align, keepNext: o.keepNext });
const H1 = (t, brk = true) => new Paragraph({ heading: HeadingLevel.HEADING_1, pageBreakBefore: brk, children: [new TextRun({ text: t, font: FONT })] });
const H2 = (t, brk = false) => new Paragraph({ heading: HeadingLevel.HEADING_2, pageBreakBefore: brk, children: [new TextRun({ text: t, font: FONT })] });
const B = (t, o = {}) => new Paragraph({ children: runs(t, o), numbering: { reference: "bul", level: 0 }, spacing: { after: 60, line: 290 } });
const code = (lines) => lines.map((ln, i) => new Paragraph({
  children: [new TextRun({ text: ln, font: { ascii: MONO, hAnsi: MONO, eastAsia: FONT }, size: 19, color: INK })],
  shading: { type: ShadingType.CLEAR, fill: PALE }, spacing: { after: i === lines.length - 1 ? 120 : 0, line: 270 }, indent: { left: 120, right: 120 },
  keepLines: true, keepNext: i < lines.length - 1 }));
function size(f) { const b = fs.readFileSync(f); return [b.readUInt32BE(16), b.readUInt32BE(20)]; }
const JPG = process.env.MANUAL_JPG;
function img(name, widthPx) {
  const f = path.join(IMG, name + ".png"), [w, h] = size(f);
  const j = JPG && path.join(JPG, name + ".jpg");
  const use = j && fs.existsSync(j);
  return new ImageRun({ type: use ? "jpg" : "png", data: fs.readFileSync(use ? j : f), transformation: { width: widthPx, height: Math.round(widthPx * h / w) } });
}
const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const thin = { style: BorderStyle.SINGLE, size: 4, color: "C5CED6" };
function table(head, rows, widths, o = {}) {
  const tot = widths.reduce((a, b) => a + b, 0), tw = o.width || W;
  const cw = widths.map((w) => Math.round(w * tw / tot)); cw[cw.length - 1] += tw - cw.reduce((a, b) => a + b, 0);
  const cell = (t, j, hd) => new TableCell({ width: { size: cw[j], type: WidthType.DXA }, borders: { top: thin, bottom: thin, left: thin, right: thin },
    shading: hd ? { type: ShadingType.CLEAR, fill: LIGHT } : undefined, margins: { top: 50, bottom: 50, left: 90, right: 90 }, verticalAlign: VerticalAlign.CENTER,
    children: [new Paragraph({ spacing: { after: 0, line: 270 }, alignment: j === 0 && o.center0 ? AlignmentType.CENTER : AlignmentType.LEFT,
      children: runs(String(t), { size: o.size || 19, bold: hd, color: hd ? NAVY : (j === 0 && o.redFirst ? RED : INK) }) })] });
  return new Table({ width: { size: tw, type: WidthType.DXA }, columnWidths: cw,
    rows: [new TableRow({ tableHeader: true, cantSplit: true, children: head.map((t, j) => cell(t, j, true)) }),
      ...rows.map((r) => new TableRow({ cantSplit: true, children: r.map((t, j) => cell(t, j, false)) }))] });
}
// 단계 한 장: 왼쪽 화면, 오른쪽 설명
const LEFT = 11000, RIGHT = W - LEFT;
function step(title, imgName, right, o = {}) {
  const pic = new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 40 }, children: [img(imgName, o.px || 715)] });
  const cap = new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 }, children: [new TextRun({ text: o.cap || "실제 실행 화면 (빨간 테두리·번호는 설명용으로 덧그림)", font: FONT, size: 16, color: GRAY })] });
  const rightKids = [];
  for (const r of right) {
    if (typeof r === "string") rightKids.push(P(r, { size: 20 }));
    else if (r.n) rightKids.push(new Paragraph({ spacing: { after: 70, line: 290 }, indent: { left: 420, hanging: 420 },
      children: [new TextRun({ text: r.n + "  ", font: FONT, size: 22, bold: true, color: RED }), ...runs(r.t, { size: 20 })] }));
    else if (r.tip) rightKids.push(new Paragraph({ spacing: { before: 80, after: 60, line: 280 }, shading: { type: ShadingType.CLEAR, fill: "FFF4E5" },
      border: { left: { style: BorderStyle.SINGLE, size: 16, color: "E4A11B", space: 6 } }, children: runs(r.tip, { size: 19 }) }));
    else if (r.code) rightKids.push(...code(r.code));
  }
  return [H2(title, !!o.brk), new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [LEFT, RIGHT], rows: [new TableRow({ cantSplit: true, children: [
    new TableCell({ width: { size: LEFT, type: WidthType.DXA }, borders: { top: none, bottom: none, left: none, right: none }, margins: { right: 160 }, children: [pic, cap] }),
    new TableCell({ width: { size: RIGHT, type: WidthType.DXA }, borders: { top: none, bottom: none, left: { style: BorderStyle.SINGLE, size: 8, color: LIGHT }, right: none },
      margins: { left: 220 }, children: rightKids })] })] })];
}
function wide(title, imgName, before = [], after = [], px = 960, brk = false) {
  return [...(title ? [H2(title, brk)] : []), ...before,
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 60, after: 40 }, keepNext: true, children: [img(imgName, px)] }),
    new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 120 }, children: [new TextRun({ text: "실제 출력 (폴더 경로는 ~/dpo 로 줄임)", font: FONT, size: 16, color: GRAY })] }), ...after];
}

const c = [];
// 표지
c.push(new Paragraph({ spacing: { before: 1400, after: 200 }, children: [new TextRun({ text: "드론 SoC 최대화 경로계획", font: FONT, size: 56, bold: true, color: NAVY })] }));
c.push(new Paragraph({ spacing: { after: 400 }, border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: TEAL, space: 12 } },
  children: [new TextRun({ text: "사용자 매뉴얼 – 프로그램을 켜서 경로를 생성하기까지 (v1.2.0)", font: FONT, size: 32, color: TEAL })] }));
c.push(P("2026년 11월 19일", { size: 22, color: GRAY, after: 500 }));
c.push(table(["단계", "할 일", "걸린 시간 (실측)", "쪽"], [
  ["1. 설치 (처음 한 번)", "가상환경 만들기 → 프로그램 설치 → 자료 묶음 풀기 → 점검", "약 20초 + 점검 8초", "2"],
  ["2. 켜기", "`dpo-server --open` → 브라우저에 3D 지도", `서버 4초, 화면 ${LOGC.load_s.toFixed(0)}초`, "3"],
  ["3. 임무 설정", "기체·탑재중량 → 임무 형태 → 출발지·도착지 → 출발 시각", "사용자 입력", "4–7"],
  ["4. 경로 생성", "'경로 생성' 누르기", `${LOGC.plan_s.toFixed(0)}초`, "8"],
  ["5. 결과·비교·내보내기", "결과 읽기, 재생, 기존 경로와 비교, 미션 파일 받기", "수 초", "9–12"],
], [3, 8, 3, 1], { size: 20 }));
c.push(P("", { after: 200 }));
c.push(P("**터미널 명령 4개, 화면 조작 6번**이면 첫 경로가 나옵니다. 시간은 저장소와 떨어진 빈 폴더에 새 가상환경을 만들어 처음부터 설치해 잰 값입니다 (Linux, 인터넷 연결). 화면 그림은 모두 실제 실행 화면이고, 빨간 테두리와 번호는 누를 곳을 표시하려고 덧그린 것입니다.", { size: 20 }));

// 1. 설치
c.push(H1("1. 설치 (처음 한 번)"));
c.push(table(["받는 파일", "내용"], [
  ["`drone_path_opt-1.2.0-py3-none-any.whl`", "프로그램 (약 0.4 MB)"],
  ["`dpo_data_1.2.0.zip`", "자료 묶음: 지형·건물 격자, 예보 바람, 건물 모양, 오차 모형 (약 20 MB)"],
], [5, 9], { width: 10000 }));
c.push(P("", { after: 60 }));
c.push(P("필요한 것: **Python 3.10 이상**, 설치할 때 인터넷 연결 (numpy 등 기본 라이브러리를 받습니다), WebGL이 되는 브라우저 (Chrome, Edge 등). 두 파일을 한 폴더에 두고 그 폴더의 터미널에서 실행합니다.", { size: 20 }));
c.push(...wide(null, "t1_install", [], [
  B("**Windows:** `py -m venv dpo-env`, `dpo-env\\Scripts\\activate` 로 바꾸고, zip은 탐색기의 '압축 풀기'로 풉니다 (Windows에서는 시험하지 않았습니다)."),
  B("자료 묶음을 풀면 `dpo_data` 폴더가 생깁니다. **이 폴더 옆에서 실행하면 자료를 저절로 찾습니다.** 다른 곳에서 실행하려면 환경변수 `DPO_HOME` 을 `dpo_data` 폴더로 지정합니다."),
], 900));
c.push(...wide("1.1 점검: dpo-plan --check", "t2_check", [P("'정상'이 나오면 준비가 끝났습니다. 설치 후 처음 한 번은 계산 코드를 컴파일하느라 몇 초 더 걸립니다.", { size: 20 })], [], 900));

// 2. 켜기
c.push(H1("2. 프로그램 켜기"));
c.push(...wide(null, "t3_server", [P("`dpo-server --open` 을 실행합니다. 브라우저가 3D 지도 화면을 엽니다. 저절로 열리지 않으면 터미널에 나온 주소 `http://127.0.0.1:8765/3d` 를 주소창에 넣습니다. **이 터미널 창은 닫지 마십시오** – 닫으면 프로그램이 꺼집니다.", { size: 20 })], [], 900));
c.push(...step("2.1 첫 화면", "01_start", [
  { n: "①", t: "**3D 지도** – 지형·건물·비행금지구역(붉은 원기둥). 왼쪽 끌기 = 회전, 오른쪽 끌기 = 이동, 휠 = 확대" },
  { n: "②", t: "**표시 선택** – 건물·바람·경로 등 보일 것. '경로에 맞춰 보기'로 화면을 경로에 맞춥니다." },
  { n: "③", t: "**임무 설정** – 여기서 기체·탑재중량·임무·출발 시각을 정하고 경로를 생성합니다 (3장)." },
  "출발(파랑)·도착(주황) 표식은 지역의 기본 위치입니다.",
]));

// 3. 임무 설정
c.push(H1("3. 임무 설정"));
c.push(...step("3.1 기체와 탑재중량", "02_drone", [
  { n: "①", t: "**기체**를 고릅니다. 아래에 배터리 용량·자체 무게·속도 한계가 나옵니다." },
  { n: "②", t: "**탑재중량** [kg]. 오른쪽 괄호의 최대값 안에서 넣습니다." },
  { tip: "Holybro X650의 전력 모델은 제원으로 만든 추정입니다. 비행 기록으로 보정하기 전 SOC 수치는 참고용입니다." },
]));
c.push(...step("3.2 임무 형태", "03_mission", [
  { n: "①", t: "**임무 형태** – 배송·회수 (출발지 ↔ 도착지) 또는 순회 (경유점을 돌고 출발지로)" },
  { n: "②", t: "**임무** – 배송: 갈 때 싣고 감 / 회수: 올 때 싣고 옴" },
  { n: "③", t: "**왕복** – 끄면 편도만 계획합니다." },
], { brk: true }));
c.push(...step("3.3 출발지와 도착지", "04_points", [
  { n: "①", t: "'**출발지 찍기**' 또는" },
  { n: "②", t: "'**도착지 찍기**'를 누른 뒤" },
  { n: "④", t: "**지도를 누르면** 그 자리로 옮겨집니다. 지도 위의 표식(구슬)을 끌어도 됩니다." },
  { n: "③", t: "지금 **좌표**가 표시됩니다." },
  "기본 위치를 그대로 써도 됩니다. 이 예에서는 기본 위치를 썼습니다.",
], { brk: true }));
c.push(...step("3.4 출발 시각과 예비 SOC", "05_time", [
  { n: "①", t: "**출발 시각** – 그 시각의 예보 바람으로 계획합니다. 오른쪽에 평균 풍속이 나옵니다." },
  { n: "②", t: "**예비 SOC** [%] – 착륙 때 남겨야 할 배터리. 이보다 적게 남으면 '임무 불가'입니다." },
  { n: "③", t: "**대지속도 상한** (선택) – 비우면 기체 기본값. DJI 경로 파일로 내보낼 계획이면 15." },
], { brk: true }));

// 4. 경로 생성
c.push(H1("4. 경로 생성"));
c.push(...step("4.1 '경로 생성' 누르기", "06_plan", [
  { n: "①", t: "**경로 생성**을 누릅니다." },
  `버튼 옆에 '계산 중…'이 나옵니다. 이 예(판교, X650, 탑재 1 kg, 15시, 왕복 약 5.7 km)에서는 **${LOGC.plan_s.toFixed(0)}초** 걸렸습니다.`,
  "바람이 센 날, 거리가 길 때, 장애물이 많을 때 더 걸립니다.",
]));
c.push(...step("4.2 결과", "07_result", [
  { n: "①", t: `**결과** – 도착 SOC와 임무 가능 여부, 구간별 에너지·SOC 소모·시간·거리·고도. 이 예: **도착 SOC ${LOGC.soc}, ${LOGC.feas}**` },
  { n: "②", t: "**생성된 경로** – 가는 편 주황, 오는 편 파랑. 반투명 막은 경로를 땅으로 내린 그림자입니다." },
  { n: "③", t: "**고도 단면** – 경로를 따라 지형·건물과 비행 고도. 점선은 비행할 수 있는 가장 낮은 높이입니다." },
], { brk: true }));

// 5. 결과 활용
c.push(H1("5. 결과 보기·비교·내보내기"));
c.push(...step("5.1 비행 재생", "08_play", [
  { n: "①", t: "**재생**을 누르면 기체가 경로를 따라 움직입니다. 다시 누르면 멈춥니다." },
  { n: "②", t: "**시간 막대**를 끌어 원하는 시각으로 갑니다." },
  { n: "③", t: "**비행 정보** – 그 시각의 위치·고도·속도·바람·SOC" },
]));
c.push(...step("5.2 기존 경로와 비교", "09_compare", [
  "결과 아래의 '**기존 경로와 비교**'를 누릅니다.",
  { n: "②", t: "지도에 **기존 경로**(최단 경로를 정속 10 m/s로 비행)가 주황, **제안 경로**가 파랑으로 겹쳐집니다." },
  { n: "①", t: "세 방식의 수치와 **왜 다른지**를 수치로 설명하는 문장이 나옵니다." },
  "'비교 끄기'로 원래대로 돌아갑니다.",
], { brk: true }));
c.push(...step("5.3 미션 파일 내보내기", "10_export", [
  { n: "①", t: "**미션 파일 (.waypoints)** – ArduPilot 미션 (Mission Planner, QGroundControl). 왕복이면 가는 편·오는 편 2개" },
  { n: "②", t: "**DJI 경로 파일 (.kmz)** – DJI Pilot 2, FlightHub 2" },
  { n: "③", t: "DJI 파일을 만들 **기종**" },
  { tip: "고도는 출발지(이륙점) 기준 상대 고도입니다. 실제 이륙 위치가 계획의 출발지와 같아야 합니다. DJI 경로 파일은 DJI 기체·소프트웨어에서 확인하지 못했습니다. 실제 비행 전에는 현장 확인과 비행승인이 필요합니다." },
], { brk: true }));

// 6. 끄기·명령줄
c.push(H1("6. 끄기와 명령줄 사용 (선택)"));
c.push(P("**끄기:** 서버를 켠 터미널에서 **Ctrl+C**. `[platform] 종료`가 나오면 꺼진 것입니다. 다음부터는 1장 없이 다음 두 줄만 실행합니다.", { size: 20 }));
c.push(...code(["source dpo-env/bin/activate        (Windows: dpo-env\\Scripts\\activate)", "dpo-server --open"]));
c.push(...wide("6.1 화면 없이 명령 한 줄로", "t4_cli", [P("같은 계획을 화면 없이 할 수 있습니다. 결과 요약이 출력되고 미션 파일이 `--out` 폴더에 생깁니다.", { size: 20 })], [], 860));
c.push(...wide(null, "t5_cli_wpml", [P("지역을 바꾸려면 `--site changwon`, 편도는 `--one-way`, DJI 경로 파일까지 만들려면 `--wpml m30`을 붙입니다.", { size: 20 })], [], 960));
c.push(table(["자주 쓰는 인자", "뜻"], [
  ["`--site pangyo` / `changwon`", "지역 (서버도 `dpo-server --site changwon`)"],
  ["`--drone m100` / `x650`, `--payload KG`", "기체, 탑재중량"],
  ["`--dep-hour H`, `--reserve %`", "출발 시각 (9–20시), 예비 SOC"],
  ["`--one-way`, `--kind pickup`", "편도, 회수 임무"],
  ["`--confidence 0.95 --forecast-age H`", "예보 오차를 감안한 출발 판정 (예보 시점 H시간 전)"],
  ["`--wpml MODEL`", "DJI 경로 파일도 만듦"],
], [5, 9], { size: 19 }));

// 7. 문제 해결
c.push(H1("7. 문제가 생기면"));
c.push(table(["증상", "원인과 조치"], [
  ["`필수 자료가 없습니다`", "자료 묶음을 풀지 않았거나 다른 폴더에서 실행했습니다. `dpo_data` 폴더 옆에서 실행하거나 `DPO_HOME` 을 지정합니다 (아래 ①)."],
  ["`포트 8765 를 열 수 없습니다`", "서버가 이미 켜져 있습니다. 브라우저로 `http://127.0.0.1:8765/3d` 를 바로 열거나 `dpo-server --port 8766` 으로 켭니다 (아래 ②)."],
  ["`입력 오류: …`", "입력이 허용 범위를 넘었습니다. 안내된 범위 안에서 넣습니다 (아래 ③)."],
  ["결과가 **임무 불가**", "예비 SOC를 지키는 경로가 없습니다. 탑재중량·예비 SOC·출발 시각·거리를 바꿉니다. 원인이 구간별로 나옵니다 (아래 ④)."],
  ["지도가 안 나옴", "브라우저의 WebGL(그래픽 가속)이 꺼져 있습니다. Chrome·Edge 최신판으로 열거나 그래픽 가속을 켭니다."],
  ["첫 실행이 느림", "설치 후 처음 한 번은 계산 코드를 컴파일합니다 (이 시험에서 6초). 다음부터는 빠릅니다."],
], [3, 10], { size: 19 }));
const errRow = (a, b) => new Table({ width: { size: W, type: WidthType.DXA }, columnWidths: [W / 2, W / 2], rows: [new TableRow({ cantSplit: true, children: [a, b].map(([n, t]) =>
  new TableCell({ width: { size: W / 2, type: WidthType.DXA }, borders: { top: none, bottom: none, left: none, right: none }, margins: { right: 120 }, children: [
    new Paragraph({ spacing: { before: 120, after: 40 }, children: [new TextRun({ text: t, font: FONT, size: 18, bold: true, color: RED })] }),
    new Paragraph({ children: [img(n, n === "e4_infeasible" ? 300 : 480)] })] })) })] });
c.push(new Paragraph({ pageBreakBefore: true, children: [] }));
c.push(errRow(["e1_no_data", "① 자료 폴더를 못 찾을 때"], ["e2_port", "② 서버가 이미 켜져 있을 때"]));
c.push(errRow(["e3_bad", "③ 입력 범위를 넘었을 때"], ["e4_infeasible", "④ 임무 불가일 때의 결과 화면"]));

// 8. 시험 기록
c.push(H1("8. 구동 시험 기록"));
c.push(table(["항목", "내용"], [
  ["환경", "Linux, Python 3.13, Chromium (자동 조작). 저장소와 떨어진 빈 폴더에 새 가상환경"],
  ["설치", "가상환경 1.9 s · 프로그램 설치 14.9 s (처음 받을 때 24.3 s) · 자료 풀기 0.1 s · 점검 8.2 s"],
  ["첫 경로까지", `서버 준비 3.8 s · 화면 열림 ${LOGC.load_s.toFixed(1)} s · 경로 생성 ${LOGC.plan_s.toFixed(1)} s`],
  ["화면 오류", LOGC.errors.length ? LOGC.errors.join(", ") : "브라우저 콘솔 오류 없음"],
  ["내려받기", "미션 파일 2개, DJI 경로 파일 2개를 실제로 받아 다시 읽어 확인"],
], [2, 10], { size: 19 }));
c.push(P("", { after: 80 }));
c.push(table(["시험하면서 발견한 점", "고친 것"], [
  ["자료 폴더를 매번 환경변수로 지정해야 했음", "`dpo_data` 폴더 옆에서 실행하면 저절로 찾음"],
  ["자료가 없을 때 현재 폴더에 빈 폴더가 생김", "만들지 않음. 오류 문구에 할 일을 넣음"],
  ["자료 없음·포트 사용 중일 때 긴 오류 추적이 나옴", "한두 줄 안내로 바꿈"],
  ["화면의 요청마다 터미널에 한 줄씩 찍힘", "오류만 출력 (`DPO_VERBOSE=1` 이면 전부)"],
  ["새로 설치하면 건물이 상자 모양으로 그려짐", "건물 모양 라이브러리(shapely)를 기본 설치에 넣음"],
  ["브라우저를 직접 열고 주소를 쳐야 했음", "`--open` 으로 3D 화면을 바로 엶"],
  ["임무 불가인데 도착 SOC가 크게 표시됨 (앞 구간까지의 값)", "'–'로 표시, 바꿔 볼 것을 안내, 비교 단추를 숨김"],
  ["DJI 경로 파일의 복귀 고도가 209 m로 나옴 (주변 산 때문)", "150 m로 제한하고 경고"],
], [6, 6], { size: 19 }));
c.push(P("", { after: 80 }));
c.push(P("**시험하지 못한 것:** Windows·macOS, 인터넷이 없는 PC에서의 설치 (기본 라이브러리를 미리 받아 두는 방법이 필요합니다), 여러 사용자가 동시에 쓰는 경우.", { size: 20 }));

const doc = new Document({
  creator: "drone-path-opt", title: "드론 SoC 최대화 경로계획 – 사용자 매뉴얼 v1.2.0",
  styles: { default: { document: { run: { font: FONT, size: 21 } },
    heading1: { run: { font: FONT, size: 30, bold: true, color: NAVY }, paragraph: { spacing: { before: 0, after: 160 }, keepNext: true } },
    heading2: { run: { font: FONT, size: 24, bold: true, color: TEAL }, paragraph: { spacing: { before: 120, after: 100 }, keepNext: true } } } },
  numbering: { config: [{ reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 400, hanging: 240 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838, orientation: PageOrientation.LANDSCAPE }, margin: { top: 900, bottom: 800, left: 800, right: 800 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, spacing: { after: 0 },
      children: [new TextRun({ text: "드론 SoC 최대화 경로계획 · 사용자 매뉴얼 v1.2.0", font: FONT, size: 16, color: GRAY })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 },
      children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: GRAY })] })] }) },
    children: c,
  }],
});
const out = process.argv[2] || path.join(ROOT, "docs", "drone_soc_user_manual.docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(out, buf); console.log(`saved ${out} (${Math.round(buf.length / 1024)} KB)`); });
