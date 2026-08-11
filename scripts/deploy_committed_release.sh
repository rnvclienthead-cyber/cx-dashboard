#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="${CX_DEPLOY_SERVER:-root@72.56.240.22}"
REMOTE_ROOT="/root/cx-dashboard"

cd "$PROJECT_ROOT"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ОТКАЗ: есть незакоммиченные tracked-изменения. Сначала завершите и закоммитьте их." >&2
  exit 2
fi

TARGET_COMMIT="$(git rev-parse HEAD)"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/cx-deploy-${TARGET_COMMIT:0:12}.XXXXXX")"
cleanup() {
  rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

mkdir -p "$STAGE_DIR/repo"
git archive "$TARGET_COMMIT" | tar -x -C "$STAGE_DIR/repo"

if [[ ! -d "$PROJECT_ROOT/frontend/node_modules" ]]; then
  echo "ОТКАЗ: отсутствует frontend/node_modules" >&2
  exit 2
fi
if [[ ! -x "$PROJECT_ROOT/venv/bin/python" ]]; then
  echo "ОТКАЗ: отсутствует venv/bin/python" >&2
  exit 2
fi

ln -s "$PROJECT_ROOT/frontend/node_modules" "$STAGE_DIR/repo/frontend/node_modules"

(
  cd "$STAGE_DIR/repo"
  "$PROJECT_ROOT/venv/bin/python" -m unittest discover -s backend/tests -v
  "$PROJECT_ROOT/venv/bin/python" -m compileall -q backend
)
(
  cd "$STAGE_DIR/repo/frontend"
  npm run build
)

if rg -n "127\.0\.0\.1:8001|localhost:8001" "$STAGE_DIR/repo/frontend/dist"; then
  echo "ОТКАЗ: production bundle содержит локальный backend URL" >&2
  exit 2
fi

# За время тестов другой агент мог создать новый коммит. Старый снимок в таком
# случае больше не является полным HEAD и не должен уходить на сервер.
if [[ "$(git rev-parse HEAD)" != "$TARGET_COMMIT" ]]; then
  echo "ОТКАЗ: HEAD изменился во время сборки. Запустите деплой заново." >&2
  exit 3
fi

git archive --format=tar.gz -o "$STAGE_DIR/source.tgz" "$TARGET_COMMIT"
tar -czf "$STAGE_DIR/frontend-dist.tgz" -C "$STAGE_DIR/repo/frontend/dist" .
git rev-list "$TARGET_COMMIT" > "$STAGE_DIR/ancestors.txt"
printf '%s\n' "$TARGET_COMMIT" > "$STAGE_DIR/target_commit"
cp "$PROJECT_ROOT/scripts/deploy_remote_release.sh" "$STAGE_DIR/deploy_remote_release.sh"

REMOTE_INCOMING="$REMOTE_ROOT/_deploy_incoming/$TARGET_COMMIT"
ssh "$SERVER" "mkdir -p '$REMOTE_INCOMING'"
scp \
  "$STAGE_DIR/source.tgz" \
  "$STAGE_DIR/frontend-dist.tgz" \
  "$STAGE_DIR/ancestors.txt" \
  "$STAGE_DIR/target_commit" \
  "$STAGE_DIR/deploy_remote_release.sh" \
  "$SERVER:$REMOTE_INCOMING/"

ssh "$SERVER" "bash '$REMOTE_INCOMING/deploy_remote_release.sh' '$REMOTE_INCOMING'"
echo "Готово: $TARGET_COMMIT"
