#!/usr/bin/env bash
# 수집 기록(DB)을 state 브랜치에 보관·복원한다 (GitHub Actions 에서 사용).
#   bash scripts/state_db.sh restore   state 브랜치의 postings.db → data/postings.db (없으면 첫 실행)
#   bash scripts/state_db.sh save      data/postings.db → state 브랜치 (커밋 하나로 덮어씀, GITHUB_TOKEN 필요)
set -euo pipefail
db=data/postings.db

case "${1:-}" in
  restore)
    mkdir -p data
    if git fetch --depth=1 origin state 2>/dev/null; then
      git show FETCH_HEAD:postings.db > "$db"
      echo "기존 수집 기록 복원 ($(stat -c %s "$db") bytes)"
    else
      echo "state 브랜치 없음: 첫 실행"
    fi
    ;;
  save)
    tmp=$(mktemp -d)
    cp "$db" "$tmp/"
    cd "$tmp"
    git init -q -b state
    git add postings.db
    git -c user.name="github-actions[bot]" \
        -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
        commit -q -m "수집 기록 $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M')"
    git push -q -f "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" state
    ;;
  *)
    echo "사용법: $0 restore|save" >&2
    exit 2
    ;;
esac
