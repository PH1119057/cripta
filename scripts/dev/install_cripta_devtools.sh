#!/usr/bin/env bash
set -euo pipefail
root="${1:-/srv/cripta/source_checkout}"
for name in cripta-state cripta-gate cripta-diff-report cripta-docs-audit cripta-patch cripta-soak cripta-field-proof; do
  ln -sfn "$root/scripts/dev/cripta-tool" "/usr/local/bin/$name"
done
echo "installed=7 root=$root"

