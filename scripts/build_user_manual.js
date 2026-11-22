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
const ILOG = JSON.parse(fs.readFileSync(path.join(IMG, "install_log.json"), "utf8"));   // scripts/release_test.py 의 실제 인수 시험 기록
const INST = ILOG.steps, ACC = ILOG.acceptance, VER = ILOG.version;
const sec = k => INST[k].sec.toFixed(0);
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
  children: [new TextRun({ text: `사용자 매뉴얼 – 배포판을 받아 경로를 생성하기까지 (v${VER})`, font: FONT, size: 32, color: TEAL })] }));
c.push(P("2026년 11월 22일", { size: 22, color: GRAY, after: 500 }));
c.push(table(["단계", "할 일", "걸린 시간 (실측)", "쪽"], [
  ["1. 설치 (처음 한 번)", "배포판 zip 풀기 → 설치 파일 실행 (Windows: `install_windows.bat` 두 번 누르기)", `약 ${sec("install")}초 (인터넷 속도에 따라 다름)`, "2–3"],
  ["2. 켜기", "실행 파일 (Windows: `run_windows.bat` 두 번 누르기) → 브라우저에 3D 지도", `서버 ${sec("server_pangyo")}초, 화면 ${LOGC.load_s.toFixed(0)}초`, "4–5"],
  ["3. 임무 설정", "기체·탑재중량 → 임무 형태 → 출발지·도착지 → 출발 시각", "사용자 입력", "6–9"],
  ["4. 경로 생성", "'경로 생성' 누르기", `${LOGC.plan_s.toFixed(0)}초`, "10–11"],
  ["5. 결과·비교·내보내기", "결과 읽기, 재생, 기존 경로와 비교, 미션 파일 받기", "수 초", "12–15"],
], [3, 8, 3, 1], { size: 20 }));
c.push(P("", { after: 200 }));
c.push(P("**파일 두 개를 두 번 누르고, 화면 조작 6번**이면 첫 경로가 나옵니다. 시간은 배포판 zip 을 빈 폴더에 풀어 받은 사람과 똑같이 설치·실행해 잰 값입니다 (Linux, 인터넷 연결). 화면 그림은 모두 실제 실행 화면이고, 빨간 테두리와 번호는 누를 곳을 표시하려고 덧그린 것입니다.", { size: 20 }));

// 1. 설치
c.push(H1("1. 설치 (처음 한 번)"));
c.push(P(`받은 **DronePathOpt_v${VER}.zip** 을 풀면 다음 파일이 나옵니다. 필요한 것: **Python 3.10 이상**, 설치할 때 인터넷 연결 (numpy 등 기본 라이브러리를 받습니다), Chrome 또는 Edge.`, { size: 20 }));
c.push(table(["파일", "내용"], [
  ["`install_windows.bat` / `install_linux_mac.sh`", "설치 (처음 한 번)"],
  ["`run_windows.bat` / `run_windows_changwon.bat` / `run_linux_mac.sh`", "실행 – 판교 / 창원"],
  [`\`drone_path_opt-${VER}-py3-none-any.whl\`, \`dpo_data_${VER}.zip\``, "프로그램 (약 0.4 MB), 자료 묶음: 지형·건물·예보 바람·오차 모형 (약 20 MB) – 설치 파일이 씀"],
  ["`사용자매뉴얼.docx`, `시험안내서.hwpx`, `비행기록표.xlsx`", "이 문서, 시험 항목과 회신 자료, 비행마다 적는 기록표"],
  ["`README.txt`, `SHA256SUMS.txt`", "요약 안내, 파일이 온전한지 확인하는 값"],
], [6, 8], { size: 19 }));
c.push(H2("1.1 Windows"));
c.push(table(["순서", "할 일", "화면에 나오는 것"], [
  ["①", "Python 이 없으면 python.org 에서 3.10 이상(권장 3.12)을 설치합니다. **첫 화면의 'Add python.exe to PATH' 를 반드시 켭니다.**", "설치 끝"],
  ["②", "메일로 받은 zip 은 오른쪽 클릭 → 속성 → **'차단 해제'** 를 켜고 확인한 뒤, 오른쪽 클릭 → **'모두 압축 풀기'**. 경로에 한글이 있어도 됩니다.", "DronePathOpt 폴더"],
  ["③", "폴더 안의 **install_windows.bat** 를 두 번 누릅니다. 'Windows의 PC 보호' 창이 뜨면 '추가 정보' → '실행'.", "검은 창에 [1/4] Python 확인 → [2/4] 가상환경 → [3/4] 프로그램 설치 (몇 분) → [4/4] 자료 묶음 풀기 → '정상: v… · 성남 판교·분당 …' → '설치가 끝났습니다'"],
  ["④", "아무 키나 눌러 창을 닫습니다. 다음부터는 2장의 실행 파일만 누릅니다.", ""],
], [1, 8, 8], { size: 19 }));
c.push(P("", { after: 40 }));
c.push(P("**[오류] 로 시작하는 줄이 나오면** 그 줄에 할 일이 적혀 있습니다 (Python 없음 → ①, 설치 실패 → 인터넷·회사 프록시 확인 후 다시 실행). 해결되지 않으면 검은 창을 화면 캡처하여 회신해 주십시오. Windows 설치·실행 파일은 개발 환경에 Windows 가 없어 **실제 Windows 에서는 시험하지 못했습니다** (같은 순서를 Linux 에서 시험하였고, Windows 용 라이브러리가 Python 3.10–3.14 모두 있음을 확인함).", { size: 19 }));
c.push(...wide("1.2 Linux · macOS", "t1_install", [P("터미널에서 풀고 설치 파일을 실행합니다. 마지막에 **'정상'** 과 '설치가 끝났습니다' 가 나오면 준비가 끝난 것입니다 (아래는 실제 실행 기록).", { size: 20 })], [
  B("설치 파일은 가상환경 `dpo-env` 를 만들고 프로그램을 넣은 뒤 자료 묶음을 풀어 `dpo_data` 폴더를 만들고 점검(`dpo-plan --check`)까지 합니다."),
  B("설치 후 처음 한 번은 계산 코드를 컴파일하느라 몇 초 더 걸립니다."),
], 900, true));

// 2. 켜기
c.push(H1("2. 프로그램 켜기"));
c.push(...wide(null, "t3_server", [P("**Windows:** `run_windows.bat` (판교) 또는 `run_windows_changwon.bat` (창원) 을 두 번 누릅니다. **Linux·macOS:** `sh run_linux_mac.sh` (창원: `sh run_linux_mac.sh changwon`). 잠시 뒤 브라우저가 3D 지도 화면을 엽니다. 저절로 열리지 않으면 주소창에 `http://127.0.0.1:8765/3d` 를 넣습니다. **켜 둔 검은 창(터미널)은 닫지 마십시오** – 닫으면 프로그램이 꺼집니다.", { size: 20 })], [], 900));
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
  { tip: "기본 기체는 실증 기체 DJI Matrice 4T(탑재 최대 0.2 kg)입니다. 전력 모델은 DJI 공식 사양(무탑재 호버 42분, 9 m/s 정속 49분)에 맞춘 값이며, 탑재·고속 비행은 실측 비행 기록으로 확인 전입니다. 시험비행 기체 Holybro X650, 비교용 DJI M100도 고를 수 있습니다." },
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
  `버튼 옆에 '계산 중…'이 나옵니다. 이 예(판교, M4T, 탑재 0.2 kg, 15시, 왕복 약 5.4 km)에서는 **${LOGC.plan_s.toFixed(0)}초** 걸렸습니다.`,
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
  { tip: "Matrice 4T 는 ArduPilot 미션이 아니라 DJI 경로 파일로 올립니다. 아래 5.4 를 따르십시오." },
  { tip: "고도는 출발지(이륙점) 기준 상대 고도입니다. 실제 이륙 위치가 계획의 출발지와 같아야 합니다. DJI 경로 파일은 DJI 기체·소프트웨어에서 확인하지 못했습니다. 실제 비행 전에는 현장 확인과 비행승인이 필요합니다." },
], { brk: true }));

c.push(...step("5.4 실증 기체 Matrice 4T 의 경로 파일", "10b_export_m4t", [
  { n: "①", t: "기종에서 **'Matrice 4T 등 – Pilot 2 파일로 지정'** 을 고릅니다." },
  { n: "②", t: "M4T 의 **DJI Pilot 2** 에서 아무 경로(웨이포인트 경로)나 하나 만들어 내보낸 **.kmz** 를 고른 뒤 'DJI 경로 파일 (.kmz)' 을 누릅니다." },
  { tip: "M4T 의 기종 번호를 DJI 문서로 확인하지 못해, 그 기체의 Pilot 2 가 쓴 기종·카메라 번호와 선회 방식을 그대로 가져와 씁니다. 처음 한 번은 내보낸 파일을 Pilot 2 로 다시 불러와 경로점 고도·속도·복귀 고도가 맞는지 확인하십시오. 명령줄: `dpo-plan --wpml-template 파일.kmz --out missions`" },
], { brk: true }));

// 6. 끄기·명령줄
c.push(H1("6. 끄기와 명령줄 사용 (선택)"));
c.push(P("**끄기:** 켜 둔 검은 창을 닫거나, 그 창에서 **Ctrl+C**. `[platform] 종료`가 나오면 꺼진 것입니다. 다시 켤 때는 2장의 실행 파일만 누릅니다 (설치는 다시 하지 않음).", { size: 20 }));
c.push(...wide("6.1 화면 없이 명령 한 줄로", "t4_cli", [P("같은 계획을 화면 없이 할 수 있습니다. 배포판 폴더에서 터미널(Windows: 주소창에 `cmd`)을 열고 가상환경을 켠 뒤 실행합니다. 결과 요약이 출력되고 미션 파일이 `--out` 폴더에 생깁니다.", { size: 20 })],
  [...code(["Windows:      dpo-env\\Scripts\\activate", "Linux·macOS:  source dpo-env/bin/activate"])], 860));
c.push(...wide(null, "t5_cli_wpml", [P("지역을 바꾸려면 `--site changwon`, 편도는 `--one-way`, DJI 경로 파일까지 만들려면 `--wpml m30`(문서로 확인한 기종) 또는 `--wpml-template 파일.kmz`(M4T: Pilot 2 에서 내보낸 파일)를 붙입니다.", { size: 20 })], [], 960));
c.push(table(["자주 쓰는 인자", "뜻"], [
  ["`--site pangyo` / `changwon`", "지역 (서버도 `dpo-server --site changwon`)"],
  ["`--drone m4t` / `x650` / `m100`, `--payload KG`", "기체 (기본 m4t), 탑재중량 (m4t 최대 0.2 kg)"],
  ["`--dep-hour H`, `--reserve %`", "출발 시각 (9–20시), 예비 SOC"],
  ["`--one-way`, `--kind pickup`", "편도, 회수 임무"],
  ["`--confidence 0.95 --forecast-age H`", "예보 오차를 감안한 출발 판정 (예보 시점 H시간 전)"],
  ["`--wpml MODEL`", "DJI 경로 파일도 만듦 (m30, m300, m350, m3e 등)"],
  ["`--wpml-template 파일.kmz`", "M4T 등: Pilot 2 에서 내보낸 파일의 기종 번호로 DJI 경로 파일을 만듦"],
], [5, 9], { size: 19 }));

// 7. 문제 해결
c.push(H1("7. 문제가 생기면"));
c.push(table(["증상", "원인과 조치"], [
  ["[오류] Python 3.10 이상을 찾지 못했습니다 (Windows)", "python.org 에서 설치하며 'Add python.exe to PATH' 를 켜고, install_windows.bat 를 다시 누릅니다."],
  ["[오류] 프로그램 설치에 실패했습니다", "인터넷 연결 또는 회사 프록시 때문입니다. 연결을 확인하고 설치 파일을 다시 누릅니다 (이어서 설치됨)."],
  ["'Windows의 PC 보호' 창 / 파일이 열리지 않음", "'추가 정보' → '실행'. 메일로 받은 zip 은 풀기 전에 속성의 '차단 해제'를 켭니다."],
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
const accRow = (site, lab) => {
  const A = ACC[site]; if (!A) return [lab, "시험 안 함"];
  const n = A.steps.filter(x => x.ok).length;
  return [lab, `${n}/${A.steps.length} 통과 · 브라우저 오류 ${A.errors.length}건 · ` + A.steps.map(x => x.id).join(", ")];
};
c.push(table(["항목", "내용"], [
  ["방법", `배포판 DronePathOpt_v${VER}.zip 을 빈 폴더에 풀어 받은 사람과 같은 순서로 설치·실행 (Linux, Python 3.13, Chromium 자동 조작). 시험 파일: scripts/release_test.py`],
  ["파일 확인·설치", `SHA-256 ${INST.sha.out} · 설치 파일 ${INST.install.sec} s (종료 코드 ${INST.install.rc}) · Windows 실행 파일 정적 검사 (줄바꿈·글자 코드·명령) 통과`],
  ["켜기", `판교 ${INST.server_pangyo.sec} s · 창원 ${INST.server_changwon.sec} s (서버 준비까지)`],
  accRow("pangyo", "화면 시험 (판교)"),
  accRow("changwon", "화면 시험 (창원)"),
  ["명령줄", `경로 계획 ${INST.plan.sec} s · DJI 경로 파일 ${INST.plan_wpml.sec} s · 잘못된 입력은 안내 문구와 종료 코드 ${INST.bad_input.rc}`],
], [2, 10], { size: 19 }));
c.push(P("화면 시험 항목: T2-0 화면 열림, T2 경로 생성(M4T 0.2 kg 왕복 15시), T3 기존 경로와 비교, T2-1 미션 파일, T5-0/T5 Pilot 2 파일로 DJI 경로 파일, T2-2 탑재 한도 입력 거부, T6 X650 미션, L·L2 실시간 운용 화면과 모의 비행 (시험안내서와 같은 번호).", { size: 18, color: GRAY }));
c.push(table(["시험하면서 발견한 점 (v1.3.1)", "고친 것"], [
  ["창원 지역의 실시간 운용 화면에서 스크립트 오류 (원형 비행 금지 구역이 없는 지역)", "없는 경우를 처리, 두 지역에서 모의 비행 완료까지 시험"],
  ["Windows 실행 파일을 UTF-8 로 저장 – 한국어 Windows 에서 줄이 합쳐져 명령이 빠질 위험", "명령은 영문, 안내 글은 한국어 Windows 기본 글자 코드로 저장, 단계 번호와 [오류] 안내 추가"],
  ["Windows 에서 Python 판(3.10 미만) 확인 없음, 자료 풀기에 PowerShell 사용", "Python 3.10 이상인지 먼저 확인, 자료는 Python 으로 풂"],
  ["일부 자료 파일을 글자 코드 지정 없이 읽음 (한국어 Windows 기본값 cp949 에서 오류 가능)", "모두 UTF-8 로 읽고 씀"],
  ["매뉴얼이 명령어로 설치하는 방법만 설명", "배포판의 설치·실행 파일 순서로 다시 씀"],
], [6, 6], { size: 19 }));
c.push(P("", { after: 80 }));
c.push(P("**시험하지 못한 것:** 실제 Windows·macOS PC, 인터넷이 없는 PC에서의 설치 (기본 라이브러리를 미리 받아 두는 방법이 필요합니다), 진짜 M4T 의 DJI Pilot 2 파일 (시험에는 번호만 바꾼 흉내 파일을 씀), 여러 사용자가 동시에 쓰는 경우.", { size: 20 }));

const doc = new Document({
  creator: "drone-path-opt", title: `드론 SoC 최대화 경로계획 – 사용자 매뉴얼 v${VER}`,
  styles: { default: { document: { run: { font: FONT, size: 21 } },
    heading1: { run: { font: FONT, size: 30, bold: true, color: NAVY }, paragraph: { spacing: { before: 0, after: 160 }, keepNext: true } },
    heading2: { run: { font: FONT, size: 24, bold: true, color: TEAL }, paragraph: { spacing: { before: 120, after: 100 }, keepNext: true } } } },
  numbering: { config: [{ reference: "bul", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 400, hanging: 240 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: 11906, height: 16838, orientation: PageOrientation.LANDSCAPE }, margin: { top: 900, bottom: 800, left: 800, right: 800 } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, spacing: { after: 0 },
      children: [new TextRun({ text: `드론 SoC 최대화 경로계획 · 사용자 매뉴얼 v${VER}`, font: FONT, size: 16, color: GRAY })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 },
      children: [new TextRun({ children: [PageNumber.CURRENT], font: FONT, size: 18, color: GRAY })] })] }) },
    children: c,
  }],
});
const out = process.argv[2] || path.join(ROOT, "docs", "drone_soc_user_manual.docx");
Packer.toBuffer(doc).then((buf) => { fs.writeFileSync(out, buf); console.log(`saved ${out} (${Math.round(buf.length / 1024)} KB)`); });
