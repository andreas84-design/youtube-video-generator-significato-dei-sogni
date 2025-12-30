FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    wget \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz \
    && tar -xJf ffmpeg-release-amd64-static.tar.xz \
    && mv ffmpeg-*-amd64-static/ffmpeg /usr/local/bin/ffmpeg \
    && mv ffmpeg-*-amd64-static/ffprobe /usr/local/bin/ffprobe \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && rm -rf ffmpeg-*-amd64-static ffmpeg-release-amd64-static.tar.xz \
    && ffmpeg -version | head -n 1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 900"]
