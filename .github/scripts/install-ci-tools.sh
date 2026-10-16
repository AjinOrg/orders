#!/usr/bin/env bash
# Installs pinned versions of the CI tools, checking each download against the project's official SHA256 file.
# Pinned (not "latest"): a compromised or broken new release cannot enter the pipeline unnoticed.
# Usage (in a workflow step):  bash .github/scripts/install-ci-tools.sh gitleaks trivy kubeconform kyverno
set -euo pipefail

GITLEAKS_VERSION=8.30.1
TRIVY_VERSION=0.75.0
KUBECONFORM_VERSION=0.8.0
KYVERNO_VERSION=1.19.1

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
[ -n "${GITHUB_PATH:-}" ] && echo "$BIN" >> "$GITHUB_PATH"
DL="$(mktemp -d)"
cd "$DL"

# get <base-url> <file> <checksums-file>: download, verify, leave the file in the current folder
get() {
  curl -fsSLO "$1/$2"
  curl -fsSL "$1/$3" -o sums.txt
  grep -E "[[:space:]]\*?$2\$" sums.txt | sha256sum -c -
}

for tool in "$@"; do
  case "$tool" in
    gitleaks)
      f="gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz"
      get "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}" "$f" "gitleaks_${GITLEAKS_VERSION}_checksums.txt"
      tar -xzf "$f" gitleaks && install -m 755 gitleaks "$BIN/" ;;
    trivy)
      f="trivy_${TRIVY_VERSION}_Linux-64bit.tar.gz"
      get "https://github.com/aquasecurity/trivy/releases/download/v${TRIVY_VERSION}" "$f" "trivy_${TRIVY_VERSION}_checksums.txt"
      tar -xzf "$f" trivy && install -m 755 trivy "$BIN/" ;;
    kubeconform)
      f="kubeconform-linux-amd64.tar.gz"
      get "https://github.com/yannh/kubeconform/releases/download/v${KUBECONFORM_VERSION}" "$f" "CHECKSUMS"
      tar -xzf "$f" kubeconform && install -m 755 kubeconform "$BIN/" ;;
    kyverno)
      f="kyverno-cli_v${KYVERNO_VERSION}_linux_x86_64.tar.gz"
      get "https://github.com/kyverno/kyverno/releases/download/v${KYVERNO_VERSION}" "$f" "checksums.txt"
      tar -xzf "$f" kyverno && install -m 755 kyverno "$BIN/" ;;
    *)
      echo "unknown tool: $tool" >&2; exit 1 ;;
  esac
done

rm -rf "$DL"
