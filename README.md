# CardDemo COBOL Parser Pipeline
**UST CodeCrafter Championship** — ANTLR-based COBOL modernisation pipeline

## Quick Start (one command)

```bash
# 1. Bootstrap environment (first time only)
brew install maven
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Build the ProLeap fat JAR (first time only)
cd cobol-exporter && mvn clean package -q && cd ..

# 3. Run the full corpus pipeline
python pipeline/batch.py \
  --corpus     external/carddemo/app/cbl \
  --copybooks  external/carddemo/app/cpy \
  --jcl        external/carddemo/app/jcl \
  --bms        external/carddemo/app/bms \
  --csd        external/carddemo/app/csd \
  --db         artifacts/pipeline.db \
  --output     artifacts/

# 4. Start the REST API
uvicorn api.main:app --reload
```

After step 3 the database at `artifacts/pipeline.db` contains all 8 artifact layers, fully cross-linked by stable uuid5 UUIDs.

---

## Architecture

```
external/carddemo/      AWS CardDemo corpus (COBOL, JCL, BMS, CSD, copybooks)
external/proleap-cobol-parser/   ProLeap ANTLR4 COBOL grammar (dependency only)
cobol-exporter/         Java fat JAR — runs ProLeap, emits compact JSON
parsers/                Python parsers for JCL, BMS, CSD, SQL, CICS
pipeline/
  preprocessor.py       COPY/REPLACE expander with provenance tracking
  ingest.py             Single-file orchestrator
  batch.py              Full-corpus parallel runner
artifacts/
  layer1_ast.py         Typed AST nodes → DB + JSON
  layer2_symbols.py     Symbol table, data dictionary, type lowering
  layer3_intra.py       CFG, def-use chains, cyclomatic complexity
  layer4_inter.py       Call graph, CICS tx flow, file I/O, JCL dependency
  layer5_business.py    Business rules, arithmetic specs, data lineage
  layer6_resources.py   CSD catalog, BMS screen maps, copybook catalog
  layer7_quality.py     Coverage report, migration risk register
storage/
  schema.sql            SQLite DDL (20 tables)
  db.py                 Connection factory (WAL mode)
  uuid_gen.py           Deterministic uuid5
ir/
  canonical_ir.py       AST → language-neutral IR (BigDecimal, long, String)
  java_emitter.py       IR → Java class source
llm/
  retrieval.py          Assemble UUID-referenced artifact slice for LLM
  prompts/              Jinja2 templates (paragraph_spec, program_spec, job_chain)
  grounding.py          Map LLM output sentences → UUID evidence
  langgraph_agent.py    LangGraph: retrieve → generate → ground-check → emit
api/main.py             FastAPI REST API (12 endpoints)
diagrams/mermaid_gen.py SQL → Mermaid diagrams (call graph, tx flow, JCL, file I/O)
```

---

## Layer Reference

| Layer | What it stores | Key tables |
|-------|---------------|-----------|
| 1 | Typed AST nodes (Program → Paragraph → Statement → DataItem) | `nodes` |
| 2 | Symbol table, data dictionary, canonical types | `data_items`, `conditions_88`, `copybook_use` |
| 3 | Intra-program CFG, def-use chains, complexity | `control_flow`, `def_use`, `complexity_metrics` |
| 4 | Inter-program call graph, CICS tx flow, file/DB I/O, JCL | `call_graph`, `transaction_flow`, `file_io`, `db_io`, `jcl_job`, `jcl_dependency` |
| 5 | Business rules, arithmetic specs, data lineage | `business_rules`, `arithmetic_specs` |
| 6 | CSD catalog, BMS screen maps, copybook catalog | `csd_catalog`, `screen_map` |
| 7 | Parse coverage, migration risk register | `parse_coverage`, `risk_register` |

---

## REST API

| Method | Endpoint | Description |
|--------|---------|-------------|
| GET | `/programs/{name}` | Program metadata + UUID |
| GET | `/paragraphs/{uuid}` | Paragraph AST + statements |
| GET | `/data-items/{uuid}` | Data item definition + type |
| GET | `/call-graph/{uuid}/callers` | Who calls this paragraph/program |
| GET | `/call-graph/{uuid}/callees` | Who this calls |
| GET | `/control-flow/{program_uuid}` | CFG as node+edge list |
| GET | `/def-use/{data_item_uuid}` | Def-use chains |
| GET | `/business-rules/{program_uuid}` | Business rule catalog |
| GET | `/file-access/{program_uuid}` | File I/O list |
| GET | `/transaction-flow/{trans_id}` | CICS transaction reachability |
| GET | `/jcl/job-chain/{job_name}` | JCL job upstream/downstream |
| GET | `/copybooks/{name}/consumers` | Programs including this copybook |
| GET | `/reports/coverage` | Parse coverage by source type |
| GET | `/reports/risk-register` | Migration risk register |
| GET | `/health` | Health check |

Interactive docs: http://localhost:8000/docs

---

## LLM Specification Generation Demo

```bash
# List programs in the database
python llm/demo_spec.py --list-programs

# Generate a grounded paragraph specification
python llm/demo_spec.py --program COTRN02C --scope paragraph

# Generate a program-level specification
python llm/demo_spec.py --program COTRN02C --scope program
```

Requires `ANTHROPIC_API_KEY` environment variable.

---

## Java Forward Engineering Demo

```bash
# Emit Java for the User Admin bounded context
python ir/demo_emit.py --program COUSR01C --program COUSR02C --program COUSR03C

# Emit Java for all ingested programs
python ir/demo_emit.py --all
```

Output: `output/java/{ClassName}.java`

---

## Mermaid Diagrams

```bash
python diagrams/mermaid_gen.py --db artifacts/pipeline.db
```

Output: `output/diagrams/` — call_graph.mmd, transaction_flow.mmd, jcl_job_chain.mmd, file_io_graph.mmd

---

## Tests

```bash
pytest tests/ -v
```

Key tests:
- `test_uuid_stability.py` — UUID determinism across pipeline runs
- `test_preprocessor.py`  — COPY/REPLACE expansion correctness
- `test_layer1.py`        — AST normalization and type lowering
- `test_api.py`           — FastAPI endpoint smoke tests

---

## UUID Stability

Every node UUID is derived deterministically via uuid5:

```python
key = f"{source_file}:{start_line}:{start_col}:{end_line}:{end_col}:{kind}:{name}"
uuid = str(uuid.uuid5(NAMESPACE, key))
```

Running the pipeline twice on the same input produces identical UUID sets. Verify with:

```bash
python pipeline/batch.py --corpus ... --db /tmp/run1.db
python pipeline/batch.py --corpus ... --db /tmp/run2.db
diff <(sqlite3 /tmp/run1.db "SELECT uuid FROM nodes ORDER BY uuid") \
     <(sqlite3 /tmp/run2.db "SELECT uuid FROM nodes ORDER BY uuid")
```

---

## Credits

- **ProLeap COBOL Parser** — ANTLR4 COBOL85 grammar by Ulrich Wolffgang (Apache 2.0)  
  https://github.com/uwol/proleap-cobol-parser
- **AWS CardDemo** — COBOL modernisation reference corpus (MIT)  
  https://github.com/aws-samples/aws-mainframe-modernization-carddemo
- **ANTLR4** — Parser generator by Terence Parr (BSD)  
  https://www.antlr.org
