#!/usr/bin/env bash
# =============================================================================
#  run.sh — Single-script launcher for the CardDemo COBOL Parser Pipeline
#
#  Usage:
#    ./run.sh                        # full pipeline + API server
#    ./run.sh --setup                # bootstrap env only (venv + JAR)
#    ./run.sh --pipeline             # parse corpus, build all artifact layers
#    ./run.sh --api                  # start REST API server (port 8000)
#    ./run.sh --spec COTRN02C        # generate LLM spec for a program
#    ./run.sh --emit COUSR01C        # emit Java for a program
#    ./run.sh --diagrams             # generate Mermaid diagrams
#    ./run.sh --test                 # run test suite
#    ./run.sh --smoke                # quick smoke test on COSGN00C only
#
#  Environment variables (LLM):
#    LLM_PROVIDER=openai   (default) or gemini
#    LLM_MODEL=gpt-4o      override model for chosen provider
#    OPENAI_API_KEY=sk-... required for openai provider
#    GEMINI_API_KEY=AIza.. required for gemini provider
# =============================================================================
set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

CORPUS="$SCRIPT_DIR/external/carddemo/app/cbl"
COPYBOOKS="$SCRIPT_DIR/external/carddemo/app/cpy"
JCL_DIR="$SCRIPT_DIR/external/carddemo/app/jcl"
BMS_DIR="$SCRIPT_DIR/external/carddemo/app/bms"
CSD_DIR="$SCRIPT_DIR/external/carddemo/app/csd"
DB="$SCRIPT_DIR/artifacts/pipeline.db"
JAR="$SCRIPT_DIR/cobol-exporter/target/cobol-exporter-1.0.0-jar-with-dependencies.jar"

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[run.sh]${NC} $*"; }
warn()  { echo -e "${YELLOW}[run.sh]${NC} $*"; }
error() { echo -e "${RED}[run.sh]${NC} $*" >&2; exit 1; }

# ── Argument parsing ──────────────────────────────────────────────────────────
MODE="all"
SPEC_PROGRAM=""
EMIT_PROGRAM=""
SCOPE="program"
API_PORT=8000

while [[ $# -gt 0 ]]; do
  case "$1" in
    --setup)    MODE="setup";    shift ;;
    --pipeline) MODE="pipeline"; shift ;;
    --api)      MODE="api";      shift ;;
    --diagrams) MODE="diagrams"; shift ;;
    --test)     MODE="test";     shift ;;
    --smoke)    MODE="smoke";    shift ;;
    --spec)     MODE="spec";     SPEC_PROGRAM="${2:-}"; shift 2 ;;
    --emit)     MODE="emit";     EMIT_PROGRAM="${2:-}"; shift 2 ;;
    --scope)    SCOPE="${2:-program}"; shift 2 ;;
    --port)     API_PORT="${2:-8000}"; shift 2 ;;
    --db)       DB="${2:-$DB}"; shift 2 ;;
    -h|--help)
      sed -n '3,20p' "$0"; exit 0 ;;
    *) error "Unknown argument: $1" ;;
  esac
done

# =============================================================================
#  STEP 0 — Prerequisites check
# =============================================================================
check_prereqs() {
  info "Checking prerequisites..."
  command -v java >/dev/null 2>&1 || error "Java not found. Install: brew install openjdk"
  command -v python3 >/dev/null 2>&1 || error "Python 3 not found."
  JAVA_VER=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | cut -d. -f1)
  [[ "$JAVA_VER" -ge 11 ]] || error "Java 11+ required (found $JAVA_VER)"
  info "Java $JAVA_VER OK"
}

# =============================================================================
#  STEP 1 — Python virtual environment
# =============================================================================
setup_venv() {
  if [[ ! -d "$VENV" ]]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV"
  fi
  info "Installing Python dependencies..."
  "$PIP" install --quiet --upgrade pip
  "$PIP" install --quiet \
    fastapi uvicorn jinja2 networkx rich tqdm pytest \
    langgraph langchain langchain-core \
    openai google-generativeai
  if [[ -f "$SCRIPT_DIR/requirements.txt" ]]; then
    "$PIP" install --quiet -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null || true
  fi
  info "Python environment ready."
}

# =============================================================================
#  STEP 2 — Clone CardDemo corpus if missing
# =============================================================================
clone_corpus() {
  if [[ ! -d "$SCRIPT_DIR/external/carddemo/app/cbl" ]]; then
    info "Cloning AWS CardDemo corpus..."
    mkdir -p "$SCRIPT_DIR/external"
    git clone --depth 1 \
      https://github.com/aws-samples/aws-mainframe-modernization-carddemo \
      "$SCRIPT_DIR/external/carddemo"
    info "CardDemo corpus cloned."
  else
    info "CardDemo corpus already present."
  fi
}

# =============================================================================
#  STEP 3 — Clone ProLeap parser if missing
# =============================================================================
clone_proleap() {
  if [[ ! -d "$SCRIPT_DIR/external/proleap-cobol-parser" ]]; then
    info "Cloning ProLeap COBOL parser..."
    git clone --depth 1 \
      https://github.com/uwol/proleap-cobol-parser \
      "$SCRIPT_DIR/external/proleap-cobol-parser"
    info "ProLeap cloned."
  else
    info "ProLeap already present."
  fi
}

# =============================================================================
#  STEP 4 — Build ProLeap fat JAR
# =============================================================================
build_jar() {
  if [[ -f "$JAR" ]]; then
    info "COBOL exporter JAR already built: $JAR"
    return 0
  fi
  command -v mvn >/dev/null 2>&1 || {
    warn "Maven not found. Attempting: brew install maven"
    brew install maven
  }
  info "Building COBOL exporter JAR (this takes ~30 s)..."
  cd "$SCRIPT_DIR/cobol-exporter"
  mvn clean package -q -DskipTests
  cd "$SCRIPT_DIR"
  [[ -f "$JAR" ]] || error "JAR build failed. Check cobol-exporter/pom.xml"
  info "JAR built: $JAR"
}

# =============================================================================
#  STEP 5 — Create CICS system copybook stubs if missing
# =============================================================================
create_stubs() {
  local STUB_DIR="$SCRIPT_DIR/external/carddemo/app/cpy-stubs"
  mkdir -p "$STUB_DIR"

  if [[ ! -f "$STUB_DIR/DFHAID.cpy" ]]; then
    info "Creating DFHAID stub..."
    cat > "$STUB_DIR/DFHAID.cpy" <<'STUB'
       01 DFHAID.
           02 DFHNULL   PIC X VALUE ' '.
           02 DFHENTER  PIC X VALUE X'7D'.
           02 DFHCLEAR  PIC X VALUE X'6D'.
           02 DFHPA1    PIC X VALUE X'6C'.
           02 DFHPA2    PIC X VALUE X'6E'.
           02 DFHPA3    PIC X VALUE X'6B'.
           02 DFHPF1    PIC X VALUE X'F1'.
           02 DFHPF2    PIC X VALUE X'F2'.
           02 DFHPF3    PIC X VALUE X'F3'.
           02 DFHPF4    PIC X VALUE X'F4'.
           02 DFHPF5    PIC X VALUE X'F5'.
           02 DFHPF6    PIC X VALUE X'F6'.
           02 DFHPF7    PIC X VALUE X'F7'.
           02 DFHPF8    PIC X VALUE X'F8'.
           02 DFHPF9    PIC X VALUE X'F9'.
           02 DFHPF10   PIC X VALUE X'7A'.
           02 DFHPF11   PIC X VALUE X'7B'.
           02 DFHPF12   PIC X VALUE X'7C'.
           02 DFHPF13   PIC X VALUE X'C1'.
           02 DFHPF14   PIC X VALUE X'C2'.
           02 DFHPF15   PIC X VALUE X'C3'.
           02 DFHPF16   PIC X VALUE X'C4'.
           02 DFHPF17   PIC X VALUE X'C5'.
           02 DFHPF18   PIC X VALUE X'C6'.
           02 DFHPF19   PIC X VALUE X'C7'.
           02 DFHPF20   PIC X VALUE X'C8'.
           02 DFHPF21   PIC X VALUE X'C9'.
           02 DFHPF22   PIC X VALUE X'4A'.
           02 DFHPF23   PIC X VALUE X'4B'.
           02 DFHPF24   PIC X VALUE X'4C'.
STUB
  fi

  if [[ ! -f "$STUB_DIR/DFHBMSCA.cpy" ]]; then
    info "Creating DFHBMSCA stub..."
    cat > "$STUB_DIR/DFHBMSCA.cpy" <<'STUB'
       01 DFHBMSCA.
           02 DFHBMPEM  PIC X VALUE ' '.
           02 DFHBMUNP  PIC X VALUE X'40'.
           02 DFHBMUNN  PIC X VALUE X'41'.
           02 DFHBMPRO  PIC X VALUE X'F0'.
           02 DFHBMASIP PIC X VALUE X'42'.
           02 DFHBMASK  PIC X VALUE X'43'.
           02 DFHBMFSE  PIC X VALUE X'C8'.
           02 DFHBMEOF  PIC X VALUE X'20'.
           02 DFHBMCUR  PIC X VALUE X'F2'.
           02 DFHBMICC  PIC X VALUE X'04'.
           02 DFHBMPHI  PIC X VALUE X'F8'.
           02 DFHBMDET  PIC X VALUE X'80'.
           02 DFHGREEN  PIC X VALUE X'42'.
           02 DFHBLUE   PIC X VALUE X'F4'.
           02 DFHRED    PIC X VALUE X'F2'.
           02 DFHTURQ   PIC X VALUE X'F1'.
           02 DFHWHITE  PIC X VALUE X'F7'.
           02 DFHYELLO  PIC X VALUE X'F6'.
           02 DFHPINK   PIC X VALUE X'F5'.
           02 DFHNEUTR  PIC X VALUE X'40'.
           02 DFHHI     PIC X VALUE X'08'.
           02 DFHBLINK  PIC X VALUE X'10'.
           02 DFHREVERSE PIC X VALUE X'20'.
           02 DFHUNDLN  PIC X VALUE X'04'.
STUB
  fi
}

# =============================================================================
#  STEP 6 — Run the full batch pipeline
# =============================================================================
run_pipeline() {
  info "Initialising database at $DB ..."
  mkdir -p "$(dirname "$DB")"

  info "Running full corpus pipeline..."
  "$PYTHON" "$SCRIPT_DIR/pipeline/batch.py" \
    --corpus    "$CORPUS" \
    --copybooks "$COPYBOOKS" \
    --jcl       "$JCL_DIR" \
    --bms       "$BMS_DIR" \
    --csd       "$CSD_DIR" \
    --db        "$DB" \
    --output    "$SCRIPT_DIR/artifacts"

  info "Generating Mermaid diagrams..."
  "$PYTHON" "$SCRIPT_DIR/diagrams/mermaid_gen.py" --db "$DB" || warn "Diagram generation skipped."

  info "Pipeline complete. DB: $DB"
}

# =============================================================================
#  STEP 7 — Smoke test (single file only)
# =============================================================================
run_smoke() {
  local FILE="$CORPUS/COSGN00C.cbl"
  [[ -f "$FILE" ]] || error "Smoke test file not found: $FILE"
  info "Smoke test: ingesting $FILE ..."
  mkdir -p "$(dirname "$DB")"
  "$PYTHON" "$SCRIPT_DIR/pipeline/ingest.py" "$FILE" --db "$DB"
  info "Smoke test passed."
}

# =============================================================================
#  STEP 8 — API server
# =============================================================================
run_api() {
  [[ -f "$DB" ]] || warn "Database not found at $DB — run pipeline first."
  info "Starting REST API on http://localhost:$API_PORT  (Ctrl+C to stop)"
  info "Interactive docs: http://localhost:$API_PORT/docs"
  export PIPELINE_DB="$DB"
  "$VENV/bin/uvicorn" api.main:app \
    --host 0.0.0.0 \
    --port "$API_PORT" \
    --reload \
    --app-dir "$SCRIPT_DIR"
}

# =============================================================================
#  STEP 9 — LLM spec generation
# =============================================================================
run_spec() {
  [[ -n "$SPEC_PROGRAM" ]] || error "--spec requires a program name, e.g.: --spec COTRN02C"
  [[ -f "$DB" ]] || error "Database not found. Run: ./run.sh --pipeline"
  _check_llm_key
  info "Generating $SCOPE spec for $SPEC_PROGRAM ..."
  export PIPELINE_DB="$DB"
  "$PYTHON" "$SCRIPT_DIR/llm/demo_spec.py" \
    --program "$SPEC_PROGRAM" \
    --scope   "$SCOPE" \
    --db      "$DB"
}

# =============================================================================
#  STEP 10 — Java emit
# =============================================================================
run_emit() {
  [[ -n "$EMIT_PROGRAM" ]] || error "--emit requires a program name, e.g.: --emit COUSR01C"
  info "Emitting Java for $EMIT_PROGRAM ..."
  "$PYTHON" "$SCRIPT_DIR/ir/demo_emit.py" --program "$EMIT_PROGRAM"
}

# =============================================================================
#  STEP 11 — Mermaid diagrams
# =============================================================================
run_diagrams() {
  [[ -f "$DB" ]] || error "Database not found. Run: ./run.sh --pipeline"
  info "Generating Mermaid diagrams..."
  "$PYTHON" "$SCRIPT_DIR/diagrams/mermaid_gen.py" --db "$DB"
}

# =============================================================================
#  STEP 12 — Tests
# =============================================================================
run_tests() {
  info "Running test suite..."
  "$VENV/bin/pytest" "$SCRIPT_DIR/tests/" -v
}

# =============================================================================
#  Helper: check LLM key
# =============================================================================
_check_llm_key() {
  local provider="${LLM_PROVIDER:-openai}"
  if [[ "$provider" == "gemini" ]]; then
    [[ -n "${GEMINI_API_KEY:-}" ]] || error "Set GEMINI_API_KEY before running LLM features."
  else
    [[ -n "${OPENAI_API_KEY:-}" ]] || error "Set OPENAI_API_KEY before running LLM features."
  fi
}

# =============================================================================
#  MAIN
# =============================================================================
main() {
  check_prereqs

  case "$MODE" in
    setup)
      clone_corpus
      clone_proleap
      setup_venv
      build_jar
      create_stubs
      info "Setup complete. Now run: ./run.sh --pipeline"
      ;;
    pipeline)
      setup_venv
      build_jar
      create_stubs
      run_pipeline
      ;;
    smoke)
      setup_venv
      build_jar
      create_stubs
      run_smoke
      ;;
    api)
      setup_venv
      run_api
      ;;
    spec)
      setup_venv
      run_spec
      ;;
    emit)
      setup_venv
      run_emit
      ;;
    diagrams)
      setup_venv
      run_diagrams
      ;;
    test)
      setup_venv
      run_tests
      ;;
    all)
      clone_corpus
      clone_proleap
      setup_venv
      build_jar
      create_stubs
      run_pipeline
      run_tests
      info "All steps complete. Starting API server..."
      run_api
      ;;
    *)
      error "Unknown mode: $MODE"
      ;;
  esac
}

main
