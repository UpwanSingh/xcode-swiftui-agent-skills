#!/usr/bin/env bash
# Install the skills in this repo into a coding agent's skills directory.
#
#   ./install.sh                                   # Claude Code, user scope (~/.claude/skills)
#   ./install.sh --agent codex                     # ~/.codex/skills
#   ./install.sh --agent cursor --scope project --project-dir ~/src/MyApp
#   ./install.sh --dest /custom/skills/dir         # any other agent
#   ./install.sh --link                            # symlink instead of copy (stays updated with git pull)
#   ./install.sh --skills swiftui-state-layout,xcode-build-signing
#   ./install.sh --uninstall                       # remove only the skills this repo provides
#
# Skill directory conventions differ between agents and change over time; check your agent's docs and
# use --dest if the default below is wrong for your version.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT="claude"; SCOPE="user"; PROJECT_DIR="$PWD"; DEST=""; MODE="copy"; ONLY=""; FORCE=0; DRY=0; UNINSTALL=0

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) AGENT="$2"; shift 2 ;;
    --scope) SCOPE="$2"; shift 2 ;;
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --link) MODE="link"; shift ;;
    --copy) MODE="copy"; shift ;;
    --skills) ONLY="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 2 ;;
  esac
done

if [[ -z "$DEST" ]]; then
  case "$AGENT:$SCOPE" in
    claude:user)    DEST="$HOME/.claude/skills" ;;
    claude:project) DEST="$PROJECT_DIR/.claude/skills" ;;
    codex:user)     DEST="${CODEX_HOME:-$HOME/.codex}/skills" ;;
    codex:project)  DEST="$PROJECT_DIR/.codex/skills" ;;
    cursor:user)    DEST="$HOME/.cursor/skills" ;;
    cursor:project) DEST="$PROJECT_DIR/.cursor/skills" ;;
    gemini:user)    DEST="$HOME/.gemini/skills" ;;
    gemini:project) DEST="$PROJECT_DIR/.gemini/skills" ;;
    vscode:user)    DEST="$HOME/.copilot/skills" ;;
    vscode:project) DEST="$PROJECT_DIR/.github/skills" ;;
    *) echo "unsupported --agent/--scope: $AGENT/$SCOPE (use --dest DIR)" >&2; exit 2 ;;
  esac
fi

run() { if [[ $DRY -eq 1 ]]; then echo "+ $*"; else "$@"; fi; }

SKILLS=()
for dir in "$REPO"/skills/*/; do
  name="$(basename "$dir")"
  if [[ -n "$ONLY" && ",$ONLY," != *",$name,"* ]]; then continue; fi
  SKILLS+=("$name")
done
[[ ${#SKILLS[@]} -gt 0 ]] || { echo "no matching skills (check --skills)" >&2; exit 2; }

if [[ $UNINSTALL -eq 1 ]]; then
  for name in "${SKILLS[@]}"; do
    if [[ -e "$DEST/$name" || -L "$DEST/$name" ]]; then run rm -rf "${DEST:?}/$name"; echo "removed $DEST/$name"; fi
  done
  exit 0
fi

run mkdir -p "$DEST"
for name in "${SKILLS[@]}"; do
  target="$DEST/$name"
  if [[ -e "$target" || -L "$target" ]]; then
    if [[ $FORCE -eq 0 ]]; then echo "skip $target (exists; use --force to replace)"; continue; fi
    run rm -rf "$target"
  fi
  if [[ "$MODE" == "link" ]]; then
    run ln -s "$REPO/skills/$name" "$target"
  else
    run cp -R "$REPO/skills/$name" "$target"
    run find "$target" -name '__pycache__' -type d -prune -exec rm -rf {} +
  fi
  echo "installed $name -> $target ($MODE)"
done

cat <<EOF

Next steps
  * Restart your agent so it discovers the skills.
  * MCP server (optional): point your client at
      python3 $REPO/mcp-server/server.py
    See README.md > "MCP server" for per-client config.
EOF
