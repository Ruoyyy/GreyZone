FROM golang:1.27-alpine AS builder

WORKDIR /src
ADD https://gitea.com/gitea/gitea-mcp/archive/bfe0d4c9b06e71a60d22b7ce81a55f01ee0157b0.tar.gz /tmp/source.tar.gz
RUN tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src
COPY comment-prefix.patch /tmp/comment-prefix.patch
COPY comment_prefix_test.go operation/issue/comment_prefix_test.go
RUN apk add --no-cache patch && patch --fuzz=0 -p1 < /tmp/comment-prefix.patch
RUN --mount=type=cache,target=/go/pkg/mod \
    --mount=type=cache,target=/root/.cache/go-build \
    go test ./operation/issue && \
    CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.Version=1.7.0" -o /tmp/gitea-mcp

FROM docker.gitea.com/gitea-mcp-server@sha256:273827c5cf8fec3846c09bd35461b59bdd06941bdcfcfefd388cbc22f2343f6b
COPY --from=builder --chown=nonroot:nonroot /tmp/gitea-mcp /app/gitea-mcp
