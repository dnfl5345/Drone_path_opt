"""운용에 필요한 자료만 묶어 배포용 zip 을 만든다 (프로그램 wheel 과 따로 전달).

사용: python scripts/make_data_bundle.py [출력 폴더]   → dist/dpo_data_<판>.zip
받은 쪽: zip 을 풀고 그 폴더를 DPO_HOME 으로 지정한다 (사용자 매뉴얼 1장).
"""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dpo import __version__  # noqa: E402

FILES = [
    "data/processed/site_grid.npz", "data/processed/site_grid_changwon.npz",             # 지형·건물 격자 (필수)
    "data/raw/gfs_20261027_00z.npz", "data/raw/gfs_20261027_06z.npz",                    # 판교 예보 바람 (필수 / 실시간 모의용)
    "data/raw/gfs_changwon_20261027_00z.npz", "data/raw/gfs_changwon_20261027_06z.npz",  # 창원
    "results/adsb_error_model.json", "results/wind_error_model.json", "results/seasonal_table.json",
]
# 있으면 넣는다: 3D 화면의 건물 모양 (원본이 커서 저장소에는 없다. 없으면 화면이 격자 높이로 상자를 그린다)
OPTIONAL = ["data/raw/overture_buildings.json", "data/raw/overture_buildings_changwon.json"]
DIRS = ["data/raw/adsb", "scenarios"]


def main(out_dir=None):
    out = Path(out_dir or ROOT / "dist")
    out.mkdir(parents=True, exist_ok=True)
    z_path = out / f"dpo_data_{__version__}.zip"
    paths = [ROOT / f for f in FILES] + [p for d in DIRS for p in sorted((ROOT / d).rglob("*")) if p.is_file()]
    missing = [str(p.relative_to(ROOT)) for p in paths if not p.exists()]
    if missing:
        sys.exit("없는 파일: " + ", ".join(missing))
    opt = [ROOT / f for f in OPTIONAL]
    paths += [p for p in opt if p.exists()]
    for p in opt:
        if not p.exists():
            print(f"참고: {p.relative_to(ROOT)} 이 없어 넣지 않았습니다 (3D 화면의 건물이 상자 모양으로 그려집니다)")
    with zipfile.ZipFile(z_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, Path("dpo_data") / p.relative_to(ROOT))
    print(f"{z_path} ({z_path.stat().st_size / 1e6:.1f} MB, 파일 {len(paths)}개)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
