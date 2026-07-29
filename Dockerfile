# Kostolany Watch API — Cloud Run
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# No compiler toolchain: every runtime dep resolves to a cp311 manylinux wheel
# (numpy/scipy/pandas/scikit-learn/lightgbm/hmmlearn/pyarrow/uvloop/httptools/
# watchfiles) and the Korea connectors are pure Python. build-essential added
# ~290 MB of dead weight to the final layer.
# libgomp1 (~150 kB) is still required: lightgbm's libpath.py dlopen's
# libgomp.so.1 at import time, and it used to arrive only as a gcc dependency.
#
# Note: matplotlib (36 MB) + plotly (70 MB) + fontTools/PIL/contourpy/kiwisolver
# (~53 MB) are still in this image. They are NOT ours — pykrx imports matplotlib
# and FinanceDataReader imports plotly at module import time, so `[korea]` pulls
# them. Dropping them requires dropping or vendoring those connectors.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --upgrade pip && pip install ".[korea]"

EXPOSE 8080

# Cloud Run injects PORT
CMD exec uvicorn kostolany.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
