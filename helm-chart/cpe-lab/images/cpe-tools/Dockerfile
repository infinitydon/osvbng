FROM docker.io/ispxhq/cpe-labs:v0.2.1 AS cpe
FROM docker.io/nicolaka/netshoot:v0.13
COPY --from=cpe /usr/local/bin/cpe-sim /usr/local/bin/cpe-sim
RUN /usr/local/bin/cpe-sim --version
