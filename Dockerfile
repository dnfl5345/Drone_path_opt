# 드론 SoC 경로계획 플랫폼 (dpo-server). 빌드: docker build -t drone-path-opt .
# 실행: docker run --rm -p 8765:8765 drone-path-opt            → http://localhost:8765/live
#       docker run --rm -p 8765:8765 -e DPO_SITE=changwon drone-path-opt
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DPO_HOME=/opt/dpo \
    DPO_HOST=0.0.0.0 \
    DPO_PORT=8765 \
    NUMBA_CACHE_DIR=/tmp/numba \
    MPLCONFIGDIR=/tmp/mpl

WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[mavlink]"

# 운용에 필요한 자료만 넣는다 (지형 격자, 예보 바람, 실측 오차 모델, 예시 장애물)
COPY data/processed /opt/dpo/data/processed
COPY data/raw/gfs_20261027_00z.npz data/raw/gfs_20261027_06z.npz \
     data/raw/gfs_changwon_20261027_00z.npz data/raw/gfs_changwon_20261027_06z.npz /opt/dpo/data/raw/
COPY data/raw/adsb /opt/dpo/data/raw/adsb
COPY results/adsb_error_model.json results/wind_error_model.json results/seasonal_table.json /opt/dpo/results/
COPY scenarios /opt/dpo/scenarios

RUN useradd --create-home dpo && chown -R dpo /opt/dpo
USER dpo
WORKDIR /opt/dpo
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('DPO_PORT','8765'), timeout=4).status == 200 else 1)"
CMD ["dpo-server"]
