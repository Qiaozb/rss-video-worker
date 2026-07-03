FROM docker.m.daocloud.io/library/node:20-bookworm

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    chromium \
    ffmpeg \
    fontconfig \
    fonts-noto-cjk \
    python3 \
    python3-pip \
    python3-venv \
  && fc-cache -f \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY package.json ./
RUN npm install --legacy-peer-deps --no-audit --no-fund

COPY requirements.txt ./
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

COPY app ./app
COPY remotion ./remotion
COPY tsconfig.json ./

ENV REMOTION_BROWSER_EXECUTABLE=/usr/bin/chromium
ENV REMOTION_ROOT=/app/remotion
ENV REMOTION_PUBLIC_DIR=/app/remotion/public
ENV OUTPUT_DIR=/data/output
ENV LANG=C.UTF-8

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
