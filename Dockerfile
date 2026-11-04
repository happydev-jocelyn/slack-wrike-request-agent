# 배포판까지 고정한다(패치 버전은 다시 빌드할 때 보안 패치를 받도록 열어 둔다)
FROM python:3.12-slim-bookworm

# 로그가 바로 보이게 하고 .pyc를 만들지 않는다
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY request_agent ./request_agent

# 설정 저장소(data/)만 볼륨으로 남기고 root가 아닌 사용자로 실행한다
RUN useradd --create-home --uid 10001 agent && mkdir -p /app/data && chown agent /app/data
USER agent
VOLUME ["/app/data"]

CMD ["python", "app.py"]
