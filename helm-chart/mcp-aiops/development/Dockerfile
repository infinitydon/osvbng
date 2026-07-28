FROM python:3.13.11-slim@sha256:25ec71a3df55517ab5f6f2fcd0e45b8dba3d197faf0ef2e5611a9a8bbd495c1e

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --requirement requirements.txt
COPY server.py .

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin mcp
USER 10001
EXPOSE 8000
ENTRYPOINT ["python", "/app/server.py"]
