#!/bin/sh
# Build tailscaled for Termux on Android (arm64), with the tailscale CLI inside it.
#
#   contrib/termux/build-tailscaled.sh [VERSION] [OUTPUT]
#
# The stock linux-arm64 binaries do not run in an Android app: the CLI dies with
# SIGSYS (seccomp blocks faccessat2) and tailscaled cannot list interfaces (netlink
# bind and RTM_GETLINK are denied). A GOOS=android build avoids the first, and
# termux_interfaces_android.go supplies the second. Taildrop is omitted because on
# Android it expects file access provided by the Tailscale app, and panics without it.
# Needs Go; the toolchain Tailscale asks for is fetched automatically.
set -e
version=${1:-1.102.4}
out=${2:-$PWD/tailscaled}
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
work=$(mktemp -d)
trap 'chmod -R u+w "$work"; rm -rf "$work"' EXIT

GOTOOLCHAIN=auto GOFLAGS=-mod=mod go mod download -json "tailscale.com@v$version" >/dev/null
cp -R "$(go env GOMODCACHE)/tailscale.com@v$version" "$work/src"
chmod -R u+w "$work/src"
cp "$here/termux_interfaces_android.go" "$work/src/cmd/tailscaled/"
cd "$work/src"
GOOS=android GOARCH=arm64 CGO_ENABLED=0 GOTOOLCHAIN=auto go build -trimpath -ldflags="-s -w" \
    -tags ts_include_cli,ts_omit_systray,ts_omit_ssh,ts_omit_tap,ts_omit_aws,ts_omit_kube,ts_omit_taildrop \
    -o "$out" ./cmd/tailscaled
echo "built $out (tailscale $version for android/arm64)"
