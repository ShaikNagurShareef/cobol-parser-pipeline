# CardDemo COBOL Modernisation Pipeline

> **UST CodeCrafter Championship 2026 — Solo Submission**  
> Competitor: **Nagur Shareef Shaik**  
> AI Agent: **Claude Code** (Anthropic) — primary and sole agent  
> IDE: Claude Code VSCode Extension  
> Submission deadline: May 24, 2026

---

## What This Is

A production-grade, deterministic ANTLR-based COBOL analysis and forward-engineering pipeline targeting the **AWS CardDemo corpus** (~60 K lines of COBOL, JCL, BMS, CSD, Assembler, and copybooks). The pipeline produces a fully cross-linked, UUID-addressable 8-layer artifact bundle that powers:

- **LLM specification generation** — grounded, evidence-linked natural-language specs for every paragraph
- **Java forward engineering** — type-correct Java with BigDecimal/RoundingMode from COMP-3 fields
- **Interactive web dashboard** — real-time pipeline execution, Mermaid diagrams, risk register, coverage reports

---

## Championship Rubric Coverage

| # | Criterion | Status | How demonstrated |
|---|-----------|--------|-----------------|
| 1 | COBOL parsing (ProLeap / ANTLR4) | ✅ Done | `cobol-exporter/` fat JAR → Layer 1 typed AST |
| 2 | Cross-file UUID addressability | ✅ Done | Deterministic uuid5 across all 8 layers |
| 3 | Data dictionary + type system | ✅ Done | Layer 2, `data_items` table, PIC → canonical type lowering |
| 4 | Control / data-flow graphs | ✅ Done | Layer 3 CFG + def-use chains in `control_flow`, `def_use` |
| 5 | Inter-program analysis (call graph, JCL, CICS) | ✅ Done | Layer 4, `call_graph`, `transaction_flow`, `jcl_dependency` |
| 6 | Business rule extraction | ✅ Done | Layer 5, `business_rules` table with predicate resolution |
| 7 | Migration risk register | ✅ Done | Layer 7, `risk_register` with severity scoring |
| 8 | LLM specification & Java emit | ✅ Done | `llm/`, `ir/` — multi-provider (OpenAI / Gemini) |

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
- Installs Maven (if missing via Homebrew)
- Creates Python virtual environment and installs all dependencies
- Clones the AWS CardDemo corpus and ProLeap COBOL parser
- Builds the Java fat JAR
- Runs the full 8-layer analysis pipeline
- Starts the FastAPI REST API with the web dashboard at **http://localhost:8000**

### Targeted modes

```bash
./run.sh --setup      # Environment bootstrap only (Maven, venv, clone repos, build JAR)
./run.sh --pipeline   # Run analysis pipeline only
./run.sh --api        # Start API + web dashboard
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
```

---

## Architecture

```
cobol-parser-pipeline/
├── cobol-exporter/            Java fat JAR — ProLeap wrapper, emits compact JSON CST
├── parsers/
│   ├── cobol/                 Python AST normaliser (CST → typed nodes + UUIDs)
│   ├── jcl/                   ANTLR4 JCL grammar + parser
│   ├── bms/                   ANTLR4 BMS grammar + parser
│   ├── csd/                   ANTLR4 CSD grammar + parser
│   ├── sql/                   EXEC SQL block extractor
│   └── cics/                  EXEC CICS verb recogniser
├── pipeline/
│   ├── preprocessor.py        COPY/REPLACE expander with provenance tracking
│   ├── ingest.py              Single-file orchestrator
│   └── batch.py               Full-corpus parallel runner
├── artifacts/
│   ├── layer1_ast.py          Typed AST → DB + JSON
│   ├── layer2_symbols.py      Symbol table, data dictionary, type lowering
│   ├── layer3_intra.py        CFG, def-use chains, cyclomatic complexity
│   ├── layer4_inter.py        Call graph, CICS tx flow, file I/O, JCL dependency
│   ├── layer5_business.py     Business rules, arithmetic specs, data lineage
│   ├── layer6_resources.py    CSD catalog, BMS screen maps, copybook catalog
│   └── layer7_quality.py      Coverage report, migration risk register
├── storage/
│   ├── schema.sql             SQLite DDL (20 tables, WAL mode)
│   ├── db.py                  Connection factory + upsert helpers
│   └── uuid_gen.py            Deterministic uuid5
├── ir/
│   ├── canonical_ir.py        AST + symbols → language-neutral IR
│   └── java_emitter.py        IR → Java (BigDecimal, long, String, switch)
├── llm/
│   ├── llm_client.py          Multi-provider client (OpenAI, Gemini)
│   ├── retrieval.py           UUID-anchored artifact slice assembler
│   ├── grounding.py           LLM output → UUID evidence mapper
│   ├── langgraph_agent.py     LangGraph: retrieve → generate → ground-check → emit
│   └── prompts/               Jinja2 templates (paragraph, program, job chain)
├── api/
│   ├── main.py                FastAPI app (15+ endpoints, SSE streaming)
│   └── routers/               Modular route handlers
├── diagrams/
│   └── mermaid_gen.py         SQL → Mermaid (call graph, tx flow, JCL, file I/O)
├── ui/
│   └── index.html             Full-stack SPA dashboard (Tailwind, Chart.js, Mermaid.js)
├── tests/
│   ├── test_uuid_stability.py
│   ├── test_preprocessor.py
│   ├── test_layer1.py
│   └── test_api.py
└── run.sh                     One-command bootstrap + pipeline + API
```

---

## 8-Layer Artifact Bundle

| Layer | Description | Key DB Tables |
|-------|-------------|---------------|
| **L1** | Typed AST nodes: Program → Paragraph → Statement → DataItem | `nodes` |
| **L2** | Symbol table, data dictionary, canonical PIC type lowering | `data_items`, `conditions_88`, `copybook_use` |
| **L3** | Intra-program CFG, def-use chains, cyclomatic complexity | `control_flow`, `def_use`, `complexity_metrics` |
| **L4** | Inter-program call graph, CICS tx flow, file/DB I/O, JCL dependency | `call_graph`, `transaction_flow`, `file_io`, `db_io`, `jcl_job`, `jcl_dependency` |
| **L5** | Business rules (IF/EVALUATE), arithmetic specs, data lineage | `business_rules`, `arithmetic_specs` |
| **L6** | CSD catalog, BMS screen maps, copybook consumer index | `csd_catalog`, `screen_map` |
| **L7** | Parse coverage report, migration risk register | `parse_coverage`, `risk_register` |
| **IR** | Language-neutral canonical IR → Java class generation | `output/ir/`, `output/java/` |

---

## UUID Stability

Every artifact node has a **deterministic uuid5** derived from its source coordinates:

```python
key = f"{source_file}:{start_line}:{start_col}:{end_line}:{end_col}:{kind}:{name}"
uuid = str(uuid.uuid5(NAMESPACE, key))
```

Running the pipeline twice on identical input produces byte-identical UUID sets. Verify:

```bash
python pipeline/batch.py --corpus ... --db /tmp/run1.db
python pipeline/batch.py --corpus ... --db /tmp/run2.db
diff <(sqlite3 /tmp/run1.db "SELECT uuid FROM nodes ORDER BY uuid") \
     <(sqlite3 /tmp/run2.db "SELECT uuid FROM nodes ORDER BY uuid")
# → no output (identical)
```

---

## REST API

All endpoints return JSON. The web UI at `http://localhost:8000` is the recommended interface.

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Health check + DB status |
| GET | `/stats` | Dashboard metrics (programs, paragraphs, coverage %, risks) |
| GET | `/programs` | Paginated program list with search |
| GET | `/programs/{name}` | Program metadata + node UUID |
| GET | `/programs/{name}/detail` | Full program view (paragraphs, call graph, business rules, risks) |
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
| GET | `/copybooks/{name}/consumers` | Programs that include this copybook |
| GET | `/reports/coverage` | Parse coverage by source type |
| GET | `/reports/risk-register` | Migration risk register |
| GET | `/diagrams/{name}` | Mermaid diagram source (call_graph, transaction_flow, jcl_job_chain, file_io_graph) |
| POST | `/generate-spec` | LLM specification generation (OpenAI / Gemini) |
| GET | `/emit-java/{program_name}` | Java class source generation |
| POST | `/pipeline/run` | SSE-streamed full pipeline execution |

Interactive API docs: **http://localhost:8000/docs**

---

## Web Dashboard

The single-page application at `http://localhost:8000` provides:

| Page | Features |
|------|----------|
| **Dashboard** | 8 stat cards, coverage donut chart, layer progress bar, championship rubric tracker |
| **Run Pipeline** | Real-time SSE log stream, configurable corpus/copybook paths |
| **Programs** | Searchable program table → detail panel with 6 tabs (paragraphs, data items, call graph, business rules, file I/O, risks) |
| **Diagrams** | Live Mermaid.js rendering of call graph, transaction flow, JCL job chain, file I/O |
| **Spec Generator** | Program/paragraph selector → LLM-generated grounded specification |
| **Java Emitter** | One-click Java generation for User Admin bounded context (COUSR01C/02C/03C) |
| **LangGraph** | Visual state machine diagram of the agentic spec-generation workflow |
| **Coverage** | Per-file parse success/failure with error class breakdown |
| **Risk Register** | Severity-weighted migration risks filterable by kind |

---

## LLM Specification Generation

```bash
# List all programs in the database
python llm/demo_spec.py --list-programs

# Generate a grounded program-level specification
python llm/demo_spec.py --program COTRN02C --scope program

# Generate a paragraph-level specification
python llm/demo_spec.py --program COTRN02C --scope paragraph

# Generate from a specific paragraph UUID
python llm/demo_spec.py --uuid <paragraph-uuid>
```

Output saved to `output/specs/`. Every sentence in the generated spec is grounded to a specific artifact UUID.

**LangGraph workflow:**

```
retrieve_artifacts(uuid)
       ↓
build_prompt(slice)
       ↓
generate_spec(prompt)     ←── OpenAI gpt-4o  or  Gemini gemini-1.5-pro
       ↓
ground_check(spec, slice) ←── maps each sentence → supporting UUID evidence
       ↓
emit_report(spec, grounding)
```

---

## Java Forward Engineering

```bash
# User Admin bounded context (COUSR01C, COUSR02C, COUSR03C)
python ir/demo_emit.py

# Specific programs
python ir/demo_emit.py --program COUSR01C --program COUSR02C

# All ingested programs
python ir/demo_emit.py --all
```

**Type mapping (COBOL → Java):**

| COBOL PIC / USAGE | Java type |
|-------------------|-----------|
| `S9(m)V9(n)` COMP-3 | `BigDecimal` (precision m+n, scale n, `RoundingMode.HALF_EVEN`) |
| `9(n)` COMP / COMP-4 | `long` or `int` |
| `X(n)` DISPLAY | `String` |
| `9(n)` DISPLAY | `String` (zoned decimal, preserved as-is) |

Output: `output/java/{ClassName}.java`

---

## Tests

```bash
pytest tests/ -v
```

| Test file | What it covers |
|-----------|----------------|
| `test_uuid_stability.py` | UUID determinism — same input → identical UUID set |
| `test_preprocessor.py` | COPY/REPLACE expansion, provenance tracking, nested copybooks |
| `test_layer1.py` | AST normalisation, type lowering, parent_uuid linkage |
| `test_api.py` | FastAPI endpoint smoke tests against seeded in-memory SQLite |

---

## Mermaid Diagrams

```bash
# Generate all four diagrams from the database
python diagrams/mermaid_gen.py --db artifacts/pipeline.db

# Output:
output/diagrams/call_graph.mmd        # Program-to-program call relationships
output/diagrams/transaction_flow.mmd  # CICS XCTL/LINK state machine
output/diagrams/jcl_job_chain.mmd     # JCL job dataset dependency graph
output/diagrams/file_io_graph.mmd     # Program ↔ file READ/WRITE operations
```

Diagrams are also renderable live in the web dashboard (Diagrams page).

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| COBOL parsing | ProLeap ANTLR4 COBOL85 grammar (Java, Apache 2.0) |
| JCL / BMS / CSD parsing | Hand-written ANTLR4 grammars (Python runtime) |
| Pipeline orchestration | Python 3.13, multiprocessing |
| Graph store | SQLite 3 (WAL mode, 20 tables) |
| UUID addressing | Python `uuid.uuid5` (deterministic) |
| REST API | FastAPI + Uvicorn |
| Web UI | Vanilla JS + Tailwind CSS + Chart.js + Mermaid.js + Highlight.js |
| LLM integration | LangGraph state machine, OpenAI gpt-4o / Gemini gemini-1.5-pro |
| Java emission | Custom canonical IR + BigDecimal/RoundingMode emitter |
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
