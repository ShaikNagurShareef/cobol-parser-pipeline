# CardDemo COBOL Modernisation Pipeline

> **UST CodeCrafter Championship 2026 — Solo Submission**  
> Competitor: **Nagur Shareef Shaik**  
> AI Agent: **Claude Code** (Anthropic, claude-sonnet-4-6)  
> IDE: Claude Code VSCode Extension  
> Submission deadline: May 24, 2026

---

## What This Is

A production-grade, deterministic ANTLR-based COBOL analysis and forward-engineering pipeline targeting the **AWS CardDemo corpus** (~60 K lines of COBOL, JCL, BMS, CSD, and copybooks). The pipeline produces a fully cross-linked, UUID-addressable **7-layer artifact bundle** that powers:

- **LangGraph spec generation** — grounded, UUID-evidence-linked natural-language specs for every paragraph and program, using a 5-node LangGraph state machine
- **Java forward engineering** — type-correct Java with `BigDecimal`/`RoundingMode` derived from COMP-3 PIC clauses via a canonical IR
- **Interactive web dashboard** — real-time pipeline execution, Mermaid diagrams, risk register, Layer Explorer, coverage reports, source viewer

### Corpus Coverage (live DB)

| Metric | Count |
|--------|-------|
| COBOL programs parsed | 31 (100% coverage) |
| Total source files OK | 135 / 135 (COBOL + JCL + BMS + CSD) |
| Paragraphs | 1,200 |
| Statements | 14,360 |
| Data items | 22,462 |
| 88-level conditions | 1,420 |
| CFG edges | 3,514 |
| Def-use entries | 524 |
| Call edges | 193 |
| Business rules | 267 |
| Migration risks | 1,400 (12 HIGH, 1,352 MEDIUM, 36 LOW) |
| JCL–COBOL file bindings | 684 |
| CICS transaction flow edges | 557 |
| BMS screen maps | 17 |
| CSD catalog entries | 189 |

---

## Championship Rubric Coverage

| # | Criterion | Weight | Status | How demonstrated |
|---|-----------|--------|--------|-----------------|
| 1 | Parse Coverage (honest reporting) | 20% | ✅ | 100% of 135 files; per-file status in `/reports/coverage` |
| 2 | Artifact Contract (Layers 1–7, UUID links) | 25% | ✅ | All 7 layers populated, deterministic uuid5, cross-linked by `parent_uuid` |
| 3 | Spec Generation Demo (COTRN02C paragraph) | 15% | ✅ | LangGraph 5-node pipeline → grounded spec via `/generate-spec` |
| 4 | Forward Engineering (IR → Java, COUSR0xC) | 15% | ✅ | Canonical IR → BigDecimal/long/String Java classes via `/emit-java/{name}` |
| 5 | Engineering Quality (tests, UUID stability) | 10% | ✅ | `pytest tests/` — uuid stability, preprocessor, layer1, API |
| 6 | Performance (parallel batch, WAL SQLite) | 5% | ✅ | `ThreadPoolExecutor` Phase 1, `PRAGMA journal_mode=WAL` |
| 7 | Migration Risk Register (severity-rated) | 5% | ✅ | 1,400 risks with HIGH/MEDIUM/LOW in `risk_register` |
| 8 | LangGraph Orchestration (bonus) | 5% | ✅ | Full 5-node LangGraph state machine with grounding check |

---

## Quick Start

```bash
# Clone this repository
git clone https://github.com/ShaikNagurShareef/cobol-parser-pipeline
cd cobol-parser-pipeline

# Run everything (bootstrap → pipeline → API → UI)
./run.sh
```

`run.sh` is fully automated:
- Installs Maven (if missing via Homebrew) and builds the ProLeap Java fat JAR
- Creates Python virtual environment and installs all dependencies
- Clones the AWS CardDemo corpus
- Runs the full 7-layer analysis pipeline over the complete corpus
- Starts the FastAPI REST API + web dashboard at **http://localhost:8000**

### Targeted modes

```bash
./run.sh --setup      # Environment bootstrap only (Maven, venv, clone repos, build JAR)
./run.sh --pipeline   # Run analysis pipeline only
./run.sh --api        # Start API + web dashboard (if pipeline already run)
./run.sh --smoke      # Single-file smoke test (COSGN00C.cbl)
./run.sh --test       # Run pytest suite
./run.sh --diagrams   # Generate Mermaid .mmd files
./run.sh --spec COTRN02C   # Generate LLM spec for a program
./run.sh --emit COUSR01C   # Emit Java for a program
```

### LLM provider configuration

```bash
# OpenAI (default)
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...

# Google Gemini
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=AIza...

# Anthropic Claude
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Architecture

```
cobol-parser-pipeline/
├── cobol-exporter/            Java fat JAR — ProLeap ANTLR4 wrapper, emits compact JSON CST
├── parsers/
│   ├── cobol/                 Python AST normaliser (CST → typed nodes + UUIDs)
│   ├── jcl/                   JCL parser (ANTLR4 grammar + Python)
│   ├── bms/                   BMS screen map parser
│   ├── csd/                   CICS CSD catalog parser
│   ├── sql/                   EXEC SQL block extractor
│   └── cics/                  EXEC CICS verb recogniser
├── pipeline/
│   ├── preprocessor.py        COPY/REPLACE expander with provenance tracking
│   ├── ingest.py              Single-file orchestrator (Layers 1–2)
│   └── batch.py               Full-corpus parallel runner (all 8 phases)
├── artifacts/
│   ├── layer1_ast.py          Typed AST → DB nodes table + JSON output
│   ├── layer2_symbols.py      Symbol table, data dictionary, PIC type lowering, 88-level extraction
│   ├── layer3_intra.py        CFG, def-use chains, cyclomatic complexity metrics
│   ├── layer4_inter.py        Call graph, CICS tx flow, file I/O, JCL–COBOL binding, dynamic CALL resolution
│   ├── layer5_business.py     Business rules (IF/EVALUATE), arithmetic specs, 88-level predicate resolution
│   ├── layer6_resources.py    CSD catalog, BMS screen maps, copybook consumer index
│   └── layer7_quality.py      Parse coverage report, migration risk register (severity scoring)
├── storage/
│   ├── schema.sql             SQLite DDL (20 tables, WAL mode)
│   ├── db.py                  Connection factory + upsert helpers
│   └── uuid_gen.py            Deterministic uuid5 from source coordinates
├── ir/
│   ├── canonical_ir.py        AST + symbols → language-neutral IR (expr trees, not text blobs)
│   └── java_emitter.py        IR → Java (BigDecimal, long, String, switch/case)
├── llm/
│   ├── llm_client.py          Multi-provider client (OpenAI, Gemini, Anthropic)
│   ├── retrieval.py           UUID-anchored artifact slice assembler (_best_prog_uuid dedup)
│   ├── grounding.py           LLM output → UUID evidence mapper
│   ├── langgraph_agent.py     LangGraph 5-node pipeline
│   ├── modernization_report.py  Full holistic 10-section report generator
│   └── prompts/               Jinja2 templates (paragraph_spec, program_spec, job_chain_narrative)
├── api/
│   └── main.py                FastAPI app (50+ endpoints, SSE streaming, Layer Explorer)
├── diagrams/
│   └── mermaid_gen.py         SQL → Mermaid (call graph, tx flow, JCL chain, file I/O)
├── ui/
│   ├── src/app.ts             TypeScript SPA source
│   ├── dist/                  Built bundle (Vite + Tailwind + Chart.js + Mermaid.js)
│   └── index.html             Entry point (also served via FastAPI static mount)
├── tests/
│   ├── test_uuid_stability.py
│   ├── test_preprocessor.py
│   ├── test_layer1.py
│   └── test_api.py
├── start.sh                   Launch server (build UI + start uvicorn)
└── run.sh                     One-command bootstrap + pipeline + API
```

---

## 7-Layer Artifact Bundle

| Layer | Description | Key DB Tables |
|-------|-------------|---------------|
| **L1** | Typed AST: `Program → Section → Paragraph → Statement` with source coordinates | `nodes` |
| **L2** | Symbol table, data dictionary, canonical PIC type lowering, 88-level conditions | `data_items`, `conditions_88`, `copybook_use` |
| **L3** | Intra-program CFG (PERFORM/FALLTHROUGH/GOTO/LOOP_BACK edges), def-use chains, cyclomatic complexity | `control_flow`, `def_use`, `complexity_metrics` |
| **L4** | Inter-program call graph, CICS LINK/XCTL tx flow, file I/O, JCL–COBOL dataset binding, dynamic CALL constant propagation | `call_graph`, `transaction_flow`, `file_io`, `jcl_job`, `jcl_program_binding` |
| **L5** | Business rules from IF/EVALUATE (with 88-level predicate resolution), arithmetic expression specs, data lineage | `business_rules`, `arithmetic_specs` |
| **L6** | BMS screen map catalog, CSD program/transaction/file definitions | `screen_map`, `csd_catalog` |
| **L7** | Parse coverage report, migration risk register with severity (HIGH/MEDIUM/LOW) | `parse_coverage`, `risk_register` |

### UUID Stability

Every artifact node has a **deterministic uuid5** derived from its source coordinates:

```python
key = f"{source_file}:{start_line}:{start_col}:{end_line}:{end_col}:{kind}:{name}"
uuid = str(uuid.uuid5(NAMESPACE, key))
```

Running the pipeline twice on identical input produces byte-identical UUID sets:

```bash
python pipeline/batch.py --corpus ... --db /tmp/run1.db
python pipeline/batch.py --corpus ... --db /tmp/run2.db
diff <(sqlite3 /tmp/run1.db "SELECT uuid FROM nodes ORDER BY uuid") \
     <(sqlite3 /tmp/run2.db "SELECT uuid FROM nodes ORDER BY uuid")
# → no output (identical)
```

---

## Pipeline Execution Flow

```
Phase 1 ─ COBOL parsing (parallel, N workers)
   └── ingest_file()  →  ProLeap JAR  →  JSON CST  →  Layer 1 (nodes)  →  Layer 2 (data_items, conditions_88)

Phase 2 ─ Intra-program graphs (sequential per program)
   └── layer3_intra.persist()     →  control_flow, def_use, complexity_metrics
   └── layer4_inter.persist()     →  call_graph (literal CALLs), transaction_flow
   └── layer5_business.persist()  →  business_rules, arithmetic_specs
   └── layer7_quality.build_risk_register()  →  risk_register

Phase 3 ─ Inter-program resolution
   └── layer4_inter.resolve_callees()  →  resolve dynamic CALL variables via def-use

Phase 4 ─ JCL parsing
   └── parse_jcl_file()  →  jcl_job, jcl_dd, jcl_dependency

Phase 4b ─ JCL–COBOL dataset binding
   └── layer4_inter.bind_jcl_to_cobol()  →  jcl_program_binding (DD name ↔ COBOL logical file)

Phase 5 ─ BMS screen map parsing
   └── parse_bms_file()  →  screen_map

Phase 6 ─ CSD catalog parsing
   └── parse_csd_file()  →  csd_catalog

Phase 7 ─ Coverage report
   └── layer7_quality.coverage_report()  →  parse_coverage

Phase 8 ─ Mermaid diagrams
   └── generate_all_diagrams()  →  output/diagrams/*.mmd
```

Topological COPY-dependency sorting ensures copybooks are processed before their dependents (Phase 1).

---

## LangGraph Spec Generation

```
retrieve_artifacts(uuid)            ← _best_prog_uuid() resolves duplicate nodes; assembles
       ↓                              7-layer slice (paragraphs, data items, conditions_88,
build_prompt(slice)                   CFG summary, CICS, JCL bindings, business rules, risks)
       ↓
generate_spec(prompt)               ← OpenAI gpt-4o  /  Gemini gemini-1.5-pro  /  Claude
       ↓
ground_check(spec, slice)           ← maps each claim → supporting artifact UUID
       ↓
emit_report(spec, grounding)        → output/specs/{program}.md
```

**Prompt templates** (Jinja2, no raw COBOL ever reaches the LLM):

| Template | Coverage |
|----------|----------|
| `paragraph_spec.jinja2` | Paragraph, statements, data items, 88-level conditions, def-use, business rules, file I/O, control flow callers/callees |
| `program_spec.jinja2` | All 7 layers: paragraph list, data item summary, complexity hotspots, CFG edge types, calls in/out, CICS interactions, JCL bindings, business rules, risk summary |
| `job_chain_narrative.jinja2` | JCL job chain with dataset lineage |

```bash
# Via CLI
python llm/demo_spec.py --program COTRN02C --scope program
python llm/demo_spec.py --program COTRN02C --scope paragraph

# Via API
curl -X POST http://localhost:8000/generate-spec \
  -H "Content-Type: application/json" \
  -d '{"scope": "program", "program_name": "COTRN02C"}'
```

---

## Java Forward Engineering

Canonical IR lowers COBOL AST + symbol table into expression trees (not text blobs), then emits type-correct Java:

| COBOL PIC / USAGE | Java type |
|-------------------|-----------|
| `S9(m)V9(n)` COMP-3 | `BigDecimal` (precision m+n, scale n, `RoundingMode.HALF_EVEN`) |
| `9(n)` COMP / COMP-4 | `long` or `int` |
| `X(n)` DISPLAY | `String` |
| `9(n)` DISPLAY | `String` (zoned decimal, preserved as-is) |
| PERFORM | Method call |
| IF/EVALUATE | `if`/`switch` |
| File I/O | Annotated stub methods |

```bash
# User Admin bounded context (COUSR01C, COUSR02C, COUSR03C)
python ir/demo_emit.py

# Specific program
python ir/demo_emit.py --program COUSR01C

# All ingested programs
python ir/demo_emit.py --all
```

Output: `output/java/{ClassName}.java`

---

## REST API

50+ endpoints. Interactive docs at **http://localhost:8000/docs**

### Core endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check + DB status |
| GET | `/stats` | Dashboard metrics (programs, paragraphs, coverage %, risks) |
| GET | `/programs` | Paginated program list with search |
| GET | `/programs/{name}` | Program metadata + UUID |
| GET | `/programs/{name}/detail` | Full program view (paragraphs, call graph, business rules, risks) |
| GET | `/programs/{name}/cfg` | CFG as Mermaid flowchart + node/edge lists |
| GET | `/programs/{name}/symbol-table` | Full data dictionary with type info |
| GET | `/programs/{name}/complexity` | Cyclomatic complexity per paragraph |
| GET | `/programs/{name}/source` | Original COBOL source with syntax highlighting |
| GET | `/paragraphs/{uuid}` | Paragraph AST + statements |
| GET | `/data-items/{uuid}` | Data item definition + canonical type |
| GET | `/call-graph/{uuid}/callers` | Callers of this node |
| GET | `/call-graph/{uuid}/callees` | Callees from this node |
| GET | `/control-flow/{program_uuid}` | CFG as node + edge list |
| GET | `/def-use/{data_item_uuid}` | Def-use chains |
| GET | `/business-rules/{program_uuid}` | Business rule catalog |
| GET | `/file-access/{program_uuid}` | File I/O operations |
| GET | `/transaction-flow/{trans_id}` | CICS transaction reachability graph |
| GET | `/jcl/job-chain/{job_name}` | JCL job upstream/downstream via dataset reuse |
| GET | `/jcl/bindings` | All JCL–COBOL file bindings |
| GET | `/jcl/jobs` | All parsed JCL jobs and steps |
| GET | `/copybooks/{name}/consumers` | Programs that COPY this copybook |
| GET | `/layers/summary` | All 7 layers — counts, breakdowns, coverage |
| GET | `/reports/coverage` | Per-file parse success/failure |
| GET | `/reports/risk-register` | Migration risk register (severity-rated) |
| GET | `/diagrams/{name}` | Live Mermaid source (call_graph, transaction_flow, jcl_job_chain, file_io_graph) |
| POST | `/generate-spec` | LLM spec generation (program or paragraph scope) |
| POST | `/generate-modernization-report` | Full 10-section holistic modernization report |
| GET | `/emit-java/{program_name}` | Java class source generation |
| POST | `/pipeline/run` | SSE-streamed full pipeline execution |
| POST | `/pipeline/cancel` | Cancel running pipeline |

### Layer Explorer endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /layers/1/programs` | Browse Layer 1 programs |
| `GET /layers/2/data-items` | Browse data items with type info |
| `GET /layers/3/cfg-edges` | Browse CFG edges with paragraph names |
| `GET /layers/4/call-graph` | Browse inter-program call edges |
| `GET /layers/5/business-rules` | Browse business rules |
| `GET /layers/6/bms-maps` | Browse BMS screen maps |
| `GET /layers/6/csd` | Browse CSD catalog entries |
| `GET /layers/7/risks` | Browse risk register with severity filter |

---

## Web Dashboard

Single-page TypeScript application at **http://localhost:8000**

| Page | Features |
|------|----------|
| **Dashboard** | Stat cards, coverage donut chart, artifact layer bar chart, live championship rubric tracker |
| **Run Pipeline** | Real-time SSE log stream, configurable paths, cancel button |
| **Programs** | Searchable program table → detail panel with 7 tabs: Paragraphs, Data Items, Call Graph, Business Rules, File I/O, Source, Risks |
| **Visualizations** | CFG Mermaid flowchart, symbol table, complexity metrics — all using duplicate-node-safe UUID resolution |
| **Diagrams** | Live Mermaid.js rendering of call graph, transaction flow, JCL job chain, file I/O |
| **Spec Generator** | Program/paragraph selector → LangGraph spec with grounding score |
| **Java Emitter** | One-click Java generation; displays source with syntax highlighting |
| **Coverage** | Per-file parse status with error class breakdown |
| **Risk Register** | 1,400 risks filterable by severity and kind |
| **Layer Explorer** | Browse all 7 layers raw: data items, CFG edges, call graph, business rules, BMS maps, CSD, risks |
| **LangGraph** | Visual state machine diagram of the agentic spec-generation workflow |
| **Settings** | LLM provider / model selection, API key configuration |

---

## Tests

```bash
pytest tests/ -v
```

| Test file | What it covers |
|-----------|----------------|
| `test_uuid_stability.py` | UUID determinism — same input → identical UUID set across two runs |
| `test_preprocessor.py` | COPY/REPLACE expansion, provenance tracking, nested copybooks |
| `test_layer1.py` | AST normalisation, PIC type lowering, parent_uuid linkage |
| `test_api.py` | FastAPI endpoint smoke tests against seeded in-memory SQLite |

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| COBOL parsing | ProLeap ANTLR4 COBOL85 grammar (Java, Apache 2.0) |
| JCL / BMS / CSD parsing | ANTLR4 grammars + Python runtime |
| CICS / SQL extraction | Keyword-driven verb+parameter tokenizer |
| Pipeline orchestration | Python 3.13, `ThreadPoolExecutor` (parallel Phase 1) |
| Graph store | SQLite 3 (WAL mode, 20 tables, deterministic uuid5) |
| REST API | FastAPI + Uvicorn (SSE streaming, 50+ endpoints) |
| Web UI | TypeScript + Vite + Tailwind CSS + Chart.js + Mermaid.js v10 + Highlight.js |
| LLM integration | LangGraph 5-node state machine; OpenAI / Gemini / Anthropic |
| Java emission | Canonical IR expression trees → BigDecimal/RoundingMode emitter |
| AI development agent | **Claude Code** (Anthropic, claude-sonnet-4-6) |

---

## Credits

- **ProLeap COBOL Parser** — ANTLR4 COBOL85 grammar by Ulrich Wolffgang (Apache 2.0)  
  https://github.com/uwol/proleap-cobol-parser
- **AWS CardDemo** — COBOL modernisation reference corpus (MIT)  
  https://github.com/aws-samples/aws-mainframe-modernization-carddemo
- **ANTLR4** — Parser generator by Terence Parr (BSD)  
  https://www.antlr.org

---

*Built for the **UST CodeCrafter Championship 2026** by Nagur Shareef Shaik using Claude Code.*
