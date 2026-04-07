FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Chromium + Xvfb + 동아시아 폰트.
# chromium 패키지를 설치하면 apt가 필요한 런타임 라이브러리(libnss3, libgbm1 등)를
# 의존성으로 함께 끌어오므로 별도로 나열하지 않는다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        xvfb \
        xauth \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.txt를 먼저 복사해 캐시 활용
COPY requirements.txt .
RUN pip install -r requirements.txt

# 나머지 소스 복사
COPY . .

# Render는 $PORT 환경변수를 주입한다 (기본 8000)
ENV PORT=8000
EXPOSE 8000

# xvfb-run이 가상 디스플레이를 띄우고 그 안에서 uvicorn 실행.
# DrissionPage가 실행하는 Chromium도 같은 DISPLAY를 공유하므로 headed 모드가 가능해진다.
CMD ["sh", "-c", "xvfb-run -a --server-args='-screen 0 1280x1024x24' uvicorn main:app --app-dir api --host 0.0.0.0 --port ${PORT}"]
