# Build the security-fixed upstream release because the project did not publish a container for it.
FROM golang:1.25-alpine@sha256:56961d79ea8129efddcc0b8643fd8a5416b4e6228cfd477e3fd61deb2672c587 AS builder

ARG MINIO_VERSION=RELEASE.2025-10-15T17-29-55Z
ENV CGO_ENABLED=0
RUN go install github.com/minio/minio@${MINIO_VERSION}

FROM alpine:3.22@sha256:14358309a308569c32bdc37e2e0e9694be33a9d99e68afb0f5ff33cc1f695dce

RUN addgroup -S -g 10001 minio \
    && adduser -S -D -H -u 10001 -G minio minio \
    && mkdir -p /data \
    && chown minio:minio /data
COPY --from=builder /go/bin/minio /usr/local/bin/minio

USER minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
