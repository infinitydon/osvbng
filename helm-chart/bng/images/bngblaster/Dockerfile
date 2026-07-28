FROM docker.io/library/ubuntu:24.04

ARG BNG_BLASTER_VERSION=0.9.37

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      iproute2 \
      iputils-ping \
      libjansson4 \
      libncurses6 \
      libssl3 \
      python3-minimal \
    && curl -fsSLo /tmp/bngblaster.deb \
      "https://github.com/rtbrick/bngblaster/releases/download/${BNG_BLASTER_VERSION}/bngblaster-${BNG_BLASTER_VERSION}-ubuntu-24.04_amd64.deb" \
    && dpkg -i /tmp/bngblaster.deb \
    && rm -f /tmp/bngblaster.deb \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN bngblaster -v

WORKDIR /config
CMD ["sleep", "infinity"]
