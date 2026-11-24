"""기관 보고서 양식(.hwpx)에서 서식만 남긴 빈 기본 파일을 만든다.

    python scripts/make_report_base_hwpx.py <양식.hwpx> [docs/templates/report_base.hwpx]

남기는 것: 글꼴·글자 모양·문단 모양·테두리/배경·스타일(header.xml), 쪽 설정·쪽 번호(첫 문단의 secPr·ctrl).
지우는 것: 본문 문단 전부, 그림(BinData), 미리보기 글·그림, 작성자 등 문서 정보.
양식 파일은 내용을 읽기만 하고 실행하지 않는다.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

from lxml import etree

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
OPF = "http://www.idpf.org/2007/opf/"
ROOT = Path(__file__).resolve().parents[1]

# 1×1 흰색 PNG (미리보기 그림 자리)
_BLANK_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f8ffff3f0005fe02fea7d5a4f60000000049454e44ae426082")


def _section(xml: bytes) -> bytes:
    root = etree.fromstring(xml)
    paras = root.findall(f"{{{HP}}}p")
    first = paras[0]
    for p in paras[1:]:
        root.remove(p)
    # 첫 문단: 구역 설정(secPr)과 조판 부호(ctrl)가 든 run만 남기고 글자 run은 비운다
    for run in first.findall(f"{{{HP}}}run"):
        keep = [c for c in run if etree.QName(c).localname in ("secPr", "ctrl")]
        if not keep:
            first.remove(run)
            continue
        for c in list(run):
            if c not in keep:
                run.remove(c)
    for lsa in first.findall(f"{{{HP}}}linesegarray"):
        first.remove(lsa)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _content_hpf(xml: bytes) -> bytes:
    root = etree.fromstring(xml)
    md = root.find(f"{{{OPF}}}metadata")
    for el in list(md):
        name = el.get("name")
        if etree.QName(el).localname == "title":
            el.text = "보고서"
        elif name in ("creator", "lastsaveby", "subject", "description", "keyword", "date"):
            el.text = ""
    man = root.find(f"{{{OPF}}}manifest")
    for it in list(man):
        if (it.get("href") or "").startswith("BinData/"):
            man.remove(it)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _manifest(xml: bytes) -> bytes:
    root = etree.fromstring(xml)
    for el in list(root):
        if "BinData/" in (el.get("{urn:oasis:names:tc:opendocument:xmlns:manifest:1.0}full-path") or el.get("full-path") or ""):
            root.remove(el)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def build(src: Path, out: Path) -> None:
    zin = zipfile.ZipFile(src)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            name = info.filename
            if name.startswith("BinData/"):
                continue
            data = zin.read(name)
            if name == "Contents/section0.xml":
                data = _section(data)
            elif name == "Contents/content.hpf":
                data = _content_hpf(data)
            elif name == "META-INF/manifest.xml":
                data = _manifest(data)
            elif name == "Preview/PrvText.txt":
                data = b""
            elif name == "Preview/PrvImage.png":
                data = _BLANK_PNG
            comp = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            zout.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), data, compress_type=comp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(buf.getvalue())
    print("저장:", out, f"({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "docs" / "templates" / "report_base.hwpx")
