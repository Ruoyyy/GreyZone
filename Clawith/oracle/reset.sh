#!/usr/bin/env bash
# Episode reset for the Clawith oracle.
#
#   bash oracle/reset.sh          # light: restore pre-episode state from backup
#   bash oracle/reset.sh --hard   # heavy: wipe volumes and rebuild via start.sh
#
# The prepare step of every episode writes its own restore points into
# .run/oracle/prepare-backup/ (Morty soul/memory, Meeseeks namespace, KB
# roots, and the active group's enterprise_info path), so the light reset
# restores exactly what the episode touched.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--hard" ]; then
    docker compose down -v
    rm -rf backend/agent_data
    rm -f .run/oracle/episode.json
    rm -rf .run/oracle/prepare-backup
    ./start.sh
    exit 0
fi

uv run --with httpx python - <<'EOF'
import shutil
from pathlib import Path
from oracle import common as C

backup = C.BACKUP_DIR
if not backup.exists():
    print("[reset] no prepare-backup found — nothing to restore")
    raise SystemExit(0)

# 1. restore Morty/Meeseeks backed-up files
for agent_dir in (backup.iterdir()):
    if not agent_dir.is_dir() or agent_dir.name in {"kb", "group-kb", "agent-ns"}:
        continue
    for rel in agent_dir.rglob("*"):
        if rel.is_file():
            dst = C.AGENT_DATA / agent_dir.name / rel.relative_to(agent_dir)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rel, dst)
            print(f"[reset] restored {dst.relative_to(C.AGENT_DATA)}")

# 2. restore agent namespaces that were backed up whole
ns_backup = backup / "agent-ns"
if ns_backup.exists():
    for ns in ns_backup.iterdir():
        dst = C.AGENT_DATA / ns.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(ns, dst)
        print(f"[reset] restored namespace {ns.name}")

# 3. restore KB roots
kb_backup = backup / "kb"
if kb_backup.exists():
    for root in C.kb_roots():
        src = kb_backup / root.name
        if src.exists():
            shutil.rmtree(root)
            shutil.copytree(src, root)
            print(f"[reset] restored KB root {root.name}")
        else:
            shutil.rmtree(root)
            print(f"[reset] removed post-episode KB root {root.name}")

# 4. restore the active group's enterprise_info path, then drop episode state
state = C.RUN_DIR / "episode.json"
if state.exists():
    episode = C.load_episode()
    group_id = episode.get("lab", {}).get("group_id") if episode else None
    if group_id:
        group_kb = C.group_kb_root(group_id)
        saved_group_kb = backup / "group-kb" / group_id
        if group_kb.exists():
            shutil.rmtree(group_kb)
        if saved_group_kb.exists():
            shutil.copytree(saved_group_kb, group_kb)
        print(f"[reset] restored group enterprise_info for {group_id}")
if state.exists():
    state.unlink()
print("[reset] light reset done")
EOF
