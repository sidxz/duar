#!/usr/bin/env bash
set -euo pipefail

# Internal CA + server cert for Postgres and Redis TLS.
#
# Usage: scripts/gen-tls-certs.sh <out-dir>   (writes ca.crt ca.key server.crt server.key)
#
# The certs pass strict X.509 verification (`openssl verify -x509_strict`), which
# Python 3.13+ applies by default: the CA carries keyUsage, the server cert carries
# keyUsage, extendedKeyUsage and an authority key identifier.

OUT="${1:?Usage: $0 <out-dir>}"
mkdir -p "$OUT"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

openssl req -x509 -newkey rsa:3072 -nodes \
  -keyout "$OUT/ca.key" -out "$OUT/ca.crt" -days 3650 \
  -subj "/CN=Duar Internal CA" \
  -addext "basicConstraints=critical,CA:TRUE" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null

openssl req -newkey rsa:2048 -nodes \
  -keyout "$OUT/server.key" -out "$TMP/server.csr" \
  -subj "/CN=duar-internal" 2>/dev/null

cat > "$TMP/server.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,DNS:postgres,DNS:redis,IP:127.0.0.1
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid,issuer
EOF

openssl x509 -req -in "$TMP/server.csr" \
  -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" -CAserial "$TMP/ca.srl" -CAcreateserial \
  -days 3650 -extfile "$TMP/server.ext" -out "$OUT/server.crt" 2>/dev/null

chmod 600 "$OUT/server.key" "$OUT/ca.key"
openssl verify -x509_strict -CAfile "$OUT/ca.crt" "$OUT/server.crt" >/dev/null
