# deception-engine — data plane en Go de la red señuelo de NetPulse
# Build multi-stage, binario estático (CGO deshabilitado por defecto).
#
#   docker build -t netpulse/deception-engine .
#   docker run --network deception-net -v $PWD/config/deception:/cfg \
#     netpulse/deception-engine -config /cfg/network.yaml \
#     -ingest http://netpulse-api:8082/api/deception/ingest -token $TOKEN

FROM golang:1.27-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /engine ./cmd/engine

FROM alpine:3.20
RUN adduser -D -u 10001 engine
COPY --from=build /engine /usr/local/bin/engine
USER engine
ENTRYPOINT ["engine"]
