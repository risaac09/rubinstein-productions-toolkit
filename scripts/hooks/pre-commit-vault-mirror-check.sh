#!/usr/bin/env bash
# pre-commit-vault-mirror-check.sh
#
# Versioned source for the pre-commit hook that blocks vault-mirror drift in
# this toolkit. The active hook at .git/hooks/pre-commit is a copy/symlink of
# this file so the check survives clean checkouts.
#
# Composition:
#   1. Delegate to the system-wide secret scanner if present
#      (~/scripts/git-hooks/pre-commit-secret-scan).
#   2. Run scripts/check-vault-mirror-drift.sh against the repo root.
#   3. Abort with a clear, drift-class-specific error if either fails.
#
# Install:
#   ln -sf ../../scripts/hooks/pre-commit-vault-mirror-check.sh \
#          .git/hooks/pre-commit
#
# Or copy:
#   cp scripts/hooks/pre-commit-vault-mirror-check.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit

set -eu

REPO_ROOT="$(git rev-parse --show-toplevel)"
SECRET_SCAN="$HOME/scripts/git-hooks/pre-commit-secret-scan"
DRIFT_CHECK="$REPO_ROOT/scripts/check-vault-mirror-drift.sh"

# 1. Secret scan (preserve the existing system-wide gate).
if [ -x "$SECRET_SCAN" ]; then
  "$SECRET_SCAN"
fi

# 2. Vault-mirror drift check.
if [ -x "$DRIFT_CHECK" ]; then
  if ! TOOLKIT_ROOT="$REPO_ROOT" "$DRIFT_CHECK"; then
    echo
    echo "============================================================"
    echo "COMMIT BLOCKED: vault-mirror drift detected in the toolkit."
    echo
    echo "Vault top-level directory names (e.g. '02 Practice/', '03 Projects/')"
    echo "must never appear inside ~/rubinstein-productions-toolkit/. The toolkit"
    echo "and the Obsidian vault are separate stacks."
    echo
    echo "This guard exists because the Arena V2 misroute on 2026-05-20 wrote"
    echo "vault content into the toolkit silently. See:"
    echo "  ~/rubinstein-productions-toolkit/docs/CANONICAL-STRUCTURE.md"
    echo
    echo "Fix the drift, or move the content into _archive/, then re-commit."
    echo "============================================================"
    exit 1
  fi
fi

exit 0
