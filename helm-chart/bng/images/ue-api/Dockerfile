FROM python:3.13.11-slim@sha256:25ec71a3df55517ab5f6f2fcd0e45b8dba3d197faf0ef2e5611a9a8bbd495c1e

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server.py /app/server.py

EXPOSE 8081
ENTRYPOINT ["python", "/app/server.py"]
