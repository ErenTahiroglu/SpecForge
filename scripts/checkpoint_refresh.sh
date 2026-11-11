#!/usr/bin/env bash
# SpecForge dual-graph checkpoint refresh (R1: content-based NO_CHANGE detection)
# Usage: bash scripts/checkpoint_refresh.sh <CHECKPOINT_ID>
#
# Deliberate, manual, idempotent. NOT a file-save hook.
# Never: git network ops, installs/upgrades, LLM calls, or signalling/stopping any process.
#
# Exit codes: 0 ok/NO_CHANGE | 1 tests failed | 2 index failure/partial | 3 PROCESS_CONFLICT_BLOCKED
#
# Test-only env hooks:
#   CHECKPOINT_STATE_DIR          override baseline directory (default outside the repo)
#   CHECKPOINT_FAULT_INJECT=cbm|graphify   simulate that index command failing (command is NOT run)
#   CHECKPOINT_PROBE_SYMBOLS="a b"         symbols that must be queryable after refresh (default: audit_documents)
#   CHECKPOINT_ABSENT_SYMBOLS="x"          symbols that must NOT be present after refresh
#   CHECKPOINT_PRIVATE_PATHS="/a:/b"       extra directories the code index must never reach
#   CHECKPOINT_ALLOW_PATHS="/c"            non-git sibling directories that may be indexed

set -uo pipefail

CHECKPOINT="${1:-MANUAL}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"
PROJECT_ID="$(echo "$PROJECT_DIR" | sed 's#^/##; s#/#-#g')"
STATE_DIR="${CHECKPOINT_STATE_DIR:-$HOME/.cache/specforge-checkpoint/specforge}"
LOG_DIR="$PROJECT_DIR/.checkpoint-logs"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/$TS-$CHECKPOINT.log"
PY="$PROJECT_DIR/.venv/bin/python"; [ -x "$PY" ] || PY=python3
HELPER="$PROJECT_DIR/scripts/checkpoint_state.py"
CBM_DB="$HOME/.cache/codebase-memory-mcp/$PROJECT_ID.db"
GRAPH="$PROJECT_DIR/graphify-out/graph.json"
PROBE="${CHECKPOINT_PROBE_SYMBOLS:-audit_documents}"
ABSENT="${CHECKPOINT_ABSENT_SYMBOLS:-}"
mkdir -p "$LOG_DIR"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

log() { echo "[$(date -u +%H:%M:%SZ)] $*" | tee -a "$LOG"; }
jget() { "$PY" "$HELPER" get "$1" "$2"; }

log "=== CHECKPOINT: $CHECKPOINT ==="
log "git_head=$(git rev-parse HEAD 2>/dev/null || echo none) branch=$(git branch --show-current 2>/dev/null || echo none)"
log "state_dir=$STATE_DIR"

# ---------------- STEP A: verification ----------------
log "=== STEP A: tests ==="
T0=$SECONDS
"$PY" -m pytest -q tests/test_core.py tests/test_api.py 2>&1 | tee -a "$LOG"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  log "RESULT: TESTS_FAILED (indexes untouched, baseline unchanged)"; exit 1
fi
log "tests_elapsed=$((SECONDS - T0))s"

# ---------------- PLAN: content fingerprint vs stored per-index baseline ----------------
VERS="cbm=$(codebase-memory-mcp --version 2>&1 | head -1);graphify=$(graphify --version 2>&1 | head -1)"
FLAGS="GRAPHIFY_BACKEND=none;graphify update . --no-cluster;cbm index_repository"
if ! "$PY" "$HELPER" plan --project-dir "$PROJECT_DIR" --state-dir "$STATE_DIR" \
      --versions "$VERS" --flags "$FLAGS" --cbm-artifact "$CBM_DB" \
      --graphify-artifact "$GRAPH" --scan-out "$WORK/scan.json" > "$WORK/plan.json" 2>>"$LOG"; then
  log "RESULT: PLAN_FAILED (cannot establish change state; NOT treated as NO_CHANGE)"; exit 2
fi
NEED_CBM="$(jget "$WORK/plan.json" need_cbm)"
NEED_GFY="$(jget "$WORK/plan.json" need_graphify)"
log "baseline=$(jget "$WORK/plan.json" baseline) files_scanned=$(jget "$WORK/plan.json" file_count)"
log "fingerprint=$(jget "$WORK/plan.json" fingerprint)"
log "cbm: need=$NEED_CBM reason=$(jget "$WORK/plan.json" indexes.cbm.reason)"
log "graphify: need=$NEED_GFY reason=$(jget "$WORK/plan.json" indexes.graphify.reason)"
log "changes: $(jget "$WORK/plan.json" changes)"
FORCE=""
if [ "$("$PY" -c "import json,sys;c=json.load(open('$WORK/plan.json'))['changes'];print(1 if c['removed'] or c['renamed'] else 0)")" = "1" ]; then
  FORCE="--force"   # graphify refuses to shrink graph.json after deletions unless forced
fi

record() { # index result detail [nodes edges]
  "$PY" "$HELPER" record --state-dir "$STATE_DIR" --index "$1" --scan-file "$WORK/scan.json" \
        --result "$2" --detail "$3" --checkpoint "$CHECKPOINT" ${4:+--nodes "$4"} ${5:+--edges "$5"} | tee -a "$LOG"
}
cbm_status() { codebase-memory-mcp cli index_status --project "$PROJECT_ID" 2>&1; }

CBM_RESULT="skipped"; GFY_RESULT="skipped"; CONFLICT=0

if [ "$NEED_CBM" = "True" ] || [ "$NEED_GFY" = "True" ]; then
  log "DECISION: INDEX_REFRESH_REQUIRED"
else
  log "DECISION: NO_CHANGE (source+config fingerprint equals last successful baseline; no rebuild)"
fi

# ---------------- STEP B: Codebase Memory ----------------
if [ "$NEED_CBM" = "True" ]; then
  log "=== STEP B: Codebase Memory refresh ==="
  S=$(date +%s); T0=$SECONDS
  if [ "${CHECKPOINT_FAULT_INJECT:-}" = "cbm" ]; then OUT="INJECTED_FAULT: simulated CBM failure"; RC=1
  else OUT="$(codebase-memory-mcp cli index_repository --repo_path "$PROJECT_DIR" 2>&1)"; RC=$?; fi
  echo "$OUT" | tail -3 | cut -c1-400 | tee -a "$LOG"
  if echo "$OUT" | grep -q "active pre-coordination or unverified CBM daemon"; then
    log "cbm: PROCESS_CONFLICT_BLOCKED (not touching any process)"; CONFLICT=1; CBM_RESULT="blocked"
    record cbm blocked "process conflict"
  elif [ $RC -ne 0 ] || ! echo "$OUT" | grep -q '"status":"indexed"'; then
    log "cbm: REFRESH_FAILED rc=$RC"; CBM_RESULT="failed"; record cbm failed "refresh failed rc=$RC"
  elif cbm_status | "$PY" "$HELPER" verify-cbm --project-dir "$PROJECT_DIR" --project "$PROJECT_ID" --scan-file "$WORK/scan.json" > "$WORK/cbm_v.json"; then
    CBM_RESULT="fresh"

    log "cbm: FRESH nodes=$(jget "$WORK/cbm_v.json" nodes) edges=$(jget "$WORK/cbm_v.json" edges) elapsed=$((SECONDS - T0))s"
  else
    log "cbm: STALE freshness check failed: $(cat "$WORK/cbm_v.json" 2>/dev/null)"; CBM_RESULT="stale"
    record cbm failed "freshness check failed"
  fi
fi

# ---------------- STEP C: Graphify ----------------
if [ "$NEED_GFY" = "True" ]; then
  log "=== STEP C: Graphify update (AST-only, no LLM) ==="
  S=$(date +%s); T0=$SECONDS
  if [ "${CHECKPOINT_FAULT_INJECT:-}" = "graphify" ]; then OUT="INJECTED_FAULT: simulated graphify failure"; RC=1
  else OUT="$(env GRAPHIFY_BACKEND=none OPENAI_API_KEY= graphify update . --no-cluster $FORCE 2>&1)"; RC=$?; fi
  echo "$OUT" | tail -4 | cut -c1-300 | tee -a "$LOG"
  if [ $RC -ne 0 ]; then
    log "graphify: REFRESH_FAILED rc=$RC"; GFY_RESULT="failed"; record graphify failed "refresh failed rc=$RC"
  elif "$PY" "$HELPER" verify-graphify --project-dir "$PROJECT_DIR" --graph "$GRAPH" \
         --scan-file "$WORK/scan.json" --since "$S" > "$WORK/gfy_v.json"; then
    GFY_RESULT="fresh"

    log "graphify: FRESH nodes=$(jget "$WORK/gfy_v.json" nodes) edges=$(jget "$WORK/gfy_v.json" edges) elapsed=$((SECONDS - T0))s"
  else
    log "graphify: STALE freshness check failed: $(cat "$WORK/gfy_v.json" 2>/dev/null)"; GFY_RESULT="stale"
    record graphify failed "freshness check failed"
  fi
fi

# ---------------- NO_CHANGE: re-verify recorded freshness cheaply ----------------
if [ "$NEED_CBM" != "True" ]; then
  if cbm_status | "$PY" "$HELPER" verify-cbm --project-dir "$PROJECT_DIR" --project "$PROJECT_ID" --scan-file "$WORK/scan.json" > "$WORK/cbm_v.json"; then CBM_RESULT="fresh(unchanged)"
  else CBM_RESULT="stale"; log "cbm: STALE on NO_CHANGE check: $(cat "$WORK/cbm_v.json")"; fi
fi
if [ "$NEED_GFY" != "True" ]; then
  if "$PY" "$HELPER" verify-graphify --project-dir "$PROJECT_DIR" --graph "$GRAPH" --scan-file "$WORK/scan.json" --since 0 > "$WORK/gfy_v.json"; then GFY_RESULT="fresh(unchanged)"
  else GFY_RESULT="stale"; log "graphify: STALE on NO_CHANGE check: $(cat "$WORK/gfy_v.json")"; fi
fi

# ---------------- STEP D: cross-check ----------------
log "=== STEP D: cross-check ==="
for sym in $PROBE; do
  if codebase-memory-mcp cli search_graph --project "$PROJECT_ID" --name_pattern "$sym" 2>&1 | grep -q "$sym"; then log "cbm has '$sym'"; else log "cbm MISSING '$sym'"; CBM_RESULT="stale"; fi
  if "$PY" -c "import json,sys;g=json.load(open('$GRAPH'));sys.exit(0 if any('$sym' in str(n.get('id',''))+str(n.get('label','')) for n in g['nodes']) else 1)"; then log "graphify has '$sym'"; else log "graphify MISSING '$sym'"; GFY_RESULT="stale"; fi
done
for sym in $ABSENT; do
  if codebase-memory-mcp cli search_graph --project "$PROJECT_ID" --name_pattern "$sym" 2>&1 | grep -q "$sym"; then log "cbm STILL HAS removed '$sym'"; CBM_RESULT="stale"; else log "cbm confirms '$sym' absent"; fi
  if "$PY" -c "import json,sys;g=json.load(open('$GRAPH'));sys.exit(1 if any('$sym' in str(n.get('id',''))+str(n.get('label','')) for n in g['nodes']) else 0)"; then log "graphify confirms '$sym' absent"; else log "graphify STILL HAS removed '$sym'"; GFY_RESULT="stale"; fi
done
# Scope: no indexed project and no symlink in the repository may reach a protected (private) directory.
# Protected = non-git directories next to this repository, plus CHECKPOINT_PRIVATE_PATHS, minus
# CHECKPOINT_ALLOW_PATHS (details: scripts/checkpoint_state.py, verify-scope). Unverifiable = stale.
"$PY" "$HELPER" verify-scope --project-dir "$PROJECT_DIR" > "$WORK/scope.json" 2>>"$LOG"; SCOPE_RC=$?
if [ "$SCOPE_RC" -eq 0 ]; then log "scope: private directories not indexed ($(jget "$WORK/scope.json" projects_checked) projects checked)"
elif [ "$SCOPE_RC" -eq 1 ]; then log "ERROR: a protected private directory is indexed or reachable: $(cat "$WORK/scope.json")"; CBM_RESULT="stale"
else log "scope: verification failed rc=$SCOPE_RC: $(cat "$WORK/scope.json" 2>/dev/null)"; CBM_RESULT="stale"; fi

# Reject a moving input set before any successful baseline write.
if ! "$PY" "$HELPER" plan --project-dir "$PROJECT_DIR" --state-dir "$STATE_DIR" \
      --versions "$VERS" --flags "$FLAGS" --cbm-artifact "$CBM_DB" \
      --graphify-artifact "$GRAPH" --scan-out "$WORK/final-scan.json" > "$WORK/final-plan.json" 2>>"$LOG"; then
  CBM_RESULT="stale"; GFY_RESULT="stale"; log "final input scan failed"
elif [ "$(jget "$WORK/scan.json" fingerprint)" != "$(jget "$WORK/final-scan.json" fingerprint)" ]; then
  CBM_RESULT="stale"; GFY_RESULT="stale"; log "inputs changed during checkpoint; baseline not advanced"
fi

# ---------------- STEP E: report ----------------
log "=== STEP E: summary ==="
log "cbm=$CBM_RESULT graphify=$GFY_RESULT llm_tokens=0(verified in graph.json) processes_signalled=0"
if [ $CONFLICT -eq 1 ]; then log "RESULT: PROCESS_CONFLICT_BLOCKED"; exit 3; fi
case "$CBM_RESULT/$GFY_RESULT" in
  fresh*/fresh*)
    # Advance only after BOTH verification and symbol probes have succeeded.
    record cbm ok "verified refresh or unchanged inputs" "$(jget "$WORK/cbm_v.json" nodes)" "$(jget "$WORK/cbm_v.json" edges)"
    record graphify ok "verified refresh or unchanged inputs" "$(jget "$WORK/gfy_v.json" nodes)" "$(jget "$WORK/gfy_v.json" edges)"
    if [ "$NEED_CBM" != "True" ] && [ "$NEED_GFY" != "True" ]; then log "RESULT: NO_CHANGE"; else log "RESULT: INDEX_REFRESHED"; fi
    exit 0 ;;
  *) log "RESULT: INDEX_STATUS_NOT_FRESH (cbm=$CBM_RESULT graphify=$GFY_RESULT) - baseline NOT advanced for failed index"; exit 2 ;;
esac
