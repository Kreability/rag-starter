# MinIO no longer publishes pullable images (quay.io/minio/minio returns 401 and
# Docker Hub has no minio/minio), so build the pinned release from source.
# Bump MINIO_VERSION together with the CI service in .github/workflows/ci.yml.
FROM golang:1.24-alpine AS build
ARG MINIO_VERSION=RELEASE.2025-04-22T22-12-26Z
ENV CGO_ENABLED=0 GOBIN=/out
RUN apk add --no-cache git \
 && go install -trimpath -ldflags "-s -w" "github.com/minio/minio@${MINIO_VERSION}"

FROM alpine:3.21
RUN apk add --no-cache ca-certificates
COPY --from=build /out/minio /usr/local/bin/minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
CMD ["server", "/data"]
