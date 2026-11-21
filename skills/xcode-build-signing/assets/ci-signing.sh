#!/usr/bin/env bash
# Reference CI signing setup for macOS runners. TEMPLATE: review before use.
#
# This script MODIFIES the runner's keychain state, so agents must not run it on a developer
# machine. It is provided so the skill can point CI maintainers at a known-good sequence.
#
# All secrets come from environment variables supplied by your CI secret store:
#   P12_BASE64          base64 of the signing certificate + private key (.p12)
#   P12_PASSWORD        password of the .p12
#   PROFILE_BASE64      (optional) base64 of a .mobileprovision for manual signing
#   KEYCHAIN_PASSWORD   any random string for the temporary keychain
#
# Usage:  ci-signing.sh setup   # before xcodebuild
#         ci-signing.sh cleanup # in an always-run step
set -euo pipefail

KEYCHAIN="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/ci-signing.keychain-db"

require() { for v in "$@"; do [[ -n "${!v:-}" ]] || { echo "error: \$$v is not set" >&2; exit 2; }; done; }

setup() {
  require P12_BASE64 P12_PASSWORD KEYCHAIN_PASSWORD
  local work; work="$(mktemp -d)"
  trap 'rm -rf "$work"' RETURN
  local p12="$work/cert.p12"
  printf '%s' "$P12_BASE64" | base64 --decode > "$p12"

  cleanup  # idempotent: a previous job on a self-hosted runner may have left the keychain behind
  security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
  security set-keychain-settings -lut 21600 "$KEYCHAIN"          # auto-lock after 6h
  security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
  security import "$p12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign -T /usr/bin/security
  # Avoid errSecInternalComponent / UI prompts when codesign uses the key:
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
  # Prepend to the search list, keeping existing keychains (word splitting intended):
  # shellcheck disable=SC2046
  security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')

  if [[ -n "${PROFILE_BASE64:-}" ]]; then
    local dir="$HOME/Library/Developer/Xcode/UserData/Provisioning Profiles"   # Xcode 16+
    mkdir -p "$dir" "$HOME/Library/MobileDevice/Provisioning Profiles"
    local tmp="$work/profile.mobileprovision"
    printf '%s' "$PROFILE_BASE64" | base64 --decode > "$tmp"
    local uuid; uuid="$(security cms -D -i "$tmp" | plutil -extract UUID raw -o - -)"
    cp "$tmp" "$dir/$uuid.mobileprovision"
    cp "$tmp" "$HOME/Library/MobileDevice/Provisioning Profiles/$uuid.mobileprovision"  # Xcode 15
    echo "installed profile $uuid"
  fi
  security find-identity -v -p codesigning "$KEYCHAIN"
}

cleanup() {
  # delete-keychain also removes it from the search list
  security delete-keychain "$KEYCHAIN" 2>/dev/null || true
}

case "${1:-}" in
  setup) setup ;;
  cleanup) cleanup ;;
  *) echo "usage: $0 setup|cleanup" >&2; exit 2 ;;
esac
