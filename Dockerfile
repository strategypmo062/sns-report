FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

# Chromium (Threads collector용 DrissionPage) + google-chrome-stable
# (DCard collector용 patchright) + Xvfb + 동아시아 폰트.
# - chromium: DrissionPage가 사용 (Threads collector)
# - google-chrome-stable: patchright가 channel="chrome"으로 사용 (DCard collector)
#   patchright는 정식 Chrome에서 stealth 패치가 가장 강력함.
# 패키지 설치 시 apt가 필요한 런타임 라이브러리(libnss3, libgbm1 등)도 같이 끌어옴.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        xvfb \
        xauth \
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        ca-certificates \
        wget \
        gnupg \
    && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# requirements.txt를 먼저 복사해 캐시 활용
COPY requirements.txt .
RUN pip install -r requirements.txt
# patchright fallback chromium (channel="chrome" 실패 시 대비)
RUN patchright install chromium

# 나머지 소스 복사
COPY . .

# Render는 $PORT 환경변수를 주입한다 (기본 8000)
ENV PORT=8000
# 컨테이너 환경 식별용 (Threads collector가 headless 모드 분기에 사용)
ENV DOCKER=1
EXPOSE 8000

# xvfb-run이 가상 디스플레이를 띄우고 그 안에서 uvicorn 실행.
# DrissionPage가 실행하는 Chromium도 같은 DISPLAY를 공유하므로 headed 모드가 가능해진다.
CMD ["sh", "-c", "xvfb-run -a --server-args='-screen 0 1280x1024x24' uvicorn main:app --app-dir api --host 0.0.0.0 --port ${PORT}"]
