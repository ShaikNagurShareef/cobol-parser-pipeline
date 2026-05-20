-- ============================================================
-- CardDemo COBOL Pipeline — SQLite Schema
-- All node references are stable uuid5 hashes (text).
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

-- ── Spine table (§8 non-optional) ───────────────────────────
CREATE TABLE IF NOT EXISTS nodes (
    uuid        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,           -- Program|Paragraph|Statement|DataItem|…
    name        TEXT,                    -- human-readable identifier (program/para/item name)
    source_file TEXT NOT NULL,
    start_line  INTEGER,
    end_line    INTEGER,
    start_col   INTEGER,
    end_col     INTEGER,
    parent_uuid TEXT REFERENCES nodes(uuid) ON DELETE CASCADE,
    payload_json TEXT                   -- full JSON blob for this node
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind        ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_source_file ON nodes(source_file);
CREATE INDEX IF NOT EXISTS idx_nodes_parent      ON nodes(parent_uuid);

-- ── Layer 2: symbol / data dictionary ───────────────────────
CREATE TABLE IF NOT EXISTS data_items (
    uuid            TEXT PRIMARY KEY REFERENCES nodes(uuid),
    program_uuid    TEXT NOT NULL REFERENCES nodes(uuid),
    name            TEXT NOT NULL,
    level           INTEGER NOT NULL,
    pic             TEXT,
    usage           TEXT,
    sign            TEXT,
    occurs_min      INTEGER,
    occurs_max      INTEGER,
    occurs_odo      TEXT,           -- name of ODO controlling variable
    redefines       TEXT,           -- name of redefined item
    value_raw       TEXT,
    canonical_kind  TEXT,           -- decimal|zoned|binary|alpha|group
    precision       INTEGER,
    scale           INTEGER,
    signed          INTEGER,        -- 0/1
    length          INTEGER,        -- for alpha
    copybook_origin TEXT,
    start_line      INTEGER,
    end_line        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_data_items_program ON data_items(program_uuid);
CREATE INDEX IF NOT EXISTS idx_data_items_name    ON data_items(name);

CREATE TABLE IF NOT EXISTS conditions_88 (
    uuid            TEXT PRIMARY KEY,
    parent_uuid     TEXT NOT NULL REFERENCES data_items(uuid),
    name            TEXT NOT NULL,
    value_raw       TEXT
);

-- ── Layer 3: control flow ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS control_flow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_uuid   TEXT NOT NULL REFERENCES nodes(uuid),
    to_uuid     TEXT NOT NULL REFERENCES nodes(uuid),
    edge_type   TEXT NOT NULL    -- PERFORM|PERFORM_THRU|GOTO|FALLTHROUGH|BRANCH_TRUE|BRANCH_FALSE|LOOP_BACK|CICS_XCTL|CICS_RETURN
);

CREATE INDEX IF NOT EXISTS idx_cf_from ON control_flow(from_uuid);
CREATE INDEX IF NOT EXISTS idx_cf_to   ON control_flow(to_uuid);

-- ── Layer 3: def-use chains ───────────────────────────────────
CREATE TABLE IF NOT EXISTS def_use (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    data_item_uuid  TEXT NOT NULL REFERENCES data_items(uuid),
    stmt_uuid       TEXT NOT NULL REFERENCES nodes(uuid),
    op              TEXT NOT NULL,  -- READ|WRITE
    line            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_du_item ON def_use(data_item_uuid);
CREATE INDEX IF NOT EXISTS idx_du_stmt ON def_use(stmt_uuid);

-- ── Layer 4: call graph ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_graph (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_uuid     TEXT NOT NULL REFERENCES nodes(uuid),
    callee_name     TEXT NOT NULL,
    callee_uuid     TEXT REFERENCES nodes(uuid),   -- null if unresolved
    call_site_uuid  TEXT REFERENCES nodes(uuid),
    call_type       TEXT NOT NULL,  -- CALL_LITERAL|CALL_DYNAMIC|CICS_LINK|CICS_XCTL
    is_resolved     INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_cg_caller ON call_graph(caller_uuid);
CREATE INDEX IF NOT EXISTS idx_cg_callee ON call_graph(callee_name);

-- ── Layer 4: file I/O graph ───────────────────────────────────
CREATE TABLE IF NOT EXISTS file_io (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uuid    TEXT NOT NULL REFERENCES nodes(uuid),
    file_name       TEXT NOT NULL,
    logical_name    TEXT,           -- COBOL SELECT name
    operation       TEXT NOT NULL,  -- READ|WRITE|REWRITE|DELETE|OPEN|CLOSE|START|STARTBR|READNEXT
    record_copybook TEXT,
    node_uuid       TEXT REFERENCES nodes(uuid),
    line            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_fio_program ON file_io(program_uuid);
CREATE INDEX IF NOT EXISTS idx_fio_file    ON file_io(file_name);

-- ── Layer 4: DB2 access graph ─────────────────────────────────
CREATE TABLE IF NOT EXISTS db_io (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uuid TEXT NOT NULL REFERENCES nodes(uuid),
    table_name  TEXT NOT NULL,
    columns     TEXT,           -- JSON array
    operation   TEXT NOT NULL,  -- SELECT|INSERT|UPDATE|DELETE|DECLARE|OPEN|FETCH
    node_uuid   TEXT REFERENCES nodes(uuid),
    line        INTEGER
);

-- ── Layer 4: CICS transaction flow graph ─────────────────────
CREATE TABLE IF NOT EXISTS transaction_flow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_uuid   TEXT NOT NULL REFERENCES nodes(uuid),
    to_uuid     TEXT REFERENCES nodes(uuid),
    to_program  TEXT,
    verb        TEXT NOT NULL,  -- XCTL|LINK|RETURN|SEND_MAP
    trans_id    TEXT,
    line        INTEGER
);

-- ── Layer 4: BMS screen-to-program map ───────────────────────
CREATE TABLE IF NOT EXISTS screen_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    map_name        TEXT NOT NULL,
    mapset_name     TEXT,
    field_name      TEXT NOT NULL,
    program_uuid    TEXT REFERENCES nodes(uuid),
    var_uuid        TEXT REFERENCES data_items(uuid),
    position_row    INTEGER,
    position_col    INTEGER,
    length          INTEGER,
    attributes      TEXT,
    pic             TEXT
);

-- ── Layer 4: JCL job graph ────────────────────────────────────
CREATE TABLE IF NOT EXISTS jcl_job (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uuid    TEXT NOT NULL REFERENCES nodes(uuid),
    job_name    TEXT NOT NULL,
    step_uuid   TEXT REFERENCES nodes(uuid),
    step_name   TEXT,
    program     TEXT,
    proc        TEXT,
    parm        TEXT
);

CREATE TABLE IF NOT EXISTS jcl_dd (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    step_uuid   TEXT NOT NULL REFERENCES nodes(uuid),
    dd_name     TEXT NOT NULL,
    dataset     TEXT,
    disposition TEXT,
    dsorg       TEXT,
    recfm       TEXT,
    lrecl       INTEGER
);

CREATE TABLE IF NOT EXISTS jcl_dependency (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    producer_job    TEXT NOT NULL,
    consumer_job    TEXT NOT NULL,
    dataset         TEXT NOT NULL,
    producer_disp   TEXT,
    consumer_disp   TEXT
);

-- G3: JCL DD → COBOL logical file binding
CREATE TABLE IF NOT EXISTS jcl_program_binding (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name            TEXT NOT NULL,
    step_name           TEXT,
    program_name        TEXT NOT NULL,
    dd_name             TEXT NOT NULL,
    dataset_name        TEXT,
    disposition         TEXT,
    cobol_logical_file  TEXT,
    program_uuid        TEXT REFERENCES nodes(uuid)
);

-- ── Layer 4: copybook usage ────────────────────────────────────
CREATE TABLE IF NOT EXISTS copybook_use (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uuid    TEXT NOT NULL REFERENCES nodes(uuid),
    copybook_name   TEXT NOT NULL,
    replacing_json  TEXT,           -- JSON [{from:.., to:..}]
    line            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cu_program  ON copybook_use(program_uuid);
CREATE INDEX IF NOT EXISTS idx_cu_copybook ON copybook_use(copybook_name);

-- ── Layer 5: business rules ────────────────────────────────────
CREATE TABLE IF NOT EXISTS business_rules (
    uuid                TEXT PRIMARY KEY,
    program_uuid        TEXT NOT NULL REFERENCES nodes(uuid),
    para_uuid           TEXT REFERENCES nodes(uuid),
    kind                TEXT NOT NULL,  -- IF|EVALUATE_WHEN
    predicate_raw       TEXT,
    predicate_resolved  TEXT,           -- JSON resolved form
    then_summary        TEXT,
    else_summary        TEXT,
    node_uuid           TEXT REFERENCES nodes(uuid),
    line                INTEGER
);

CREATE INDEX IF NOT EXISTS idx_br_program ON business_rules(program_uuid);

-- ── Layer 5: arithmetic specifications ────────────────────────
CREATE TABLE IF NOT EXISTS arithmetic_specs (
    uuid            TEXT PRIMARY KEY,
    program_uuid    TEXT NOT NULL REFERENCES nodes(uuid),
    stmt_uuid       TEXT REFERENCES nodes(uuid),
    kind            TEXT NOT NULL,  -- COMPUTE|ADD|SUBTRACT|MULTIPLY|DIVIDE
    expression_json TEXT,           -- canonical tree JSON
    result_var      TEXT,
    line            INTEGER
);

-- ── Layer 6: CSD catalog ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS csd_catalog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,  -- PROGRAM|TRANSACTION|MAPSET|FILE|LIBRARY
    name        TEXT NOT NULL,
    group_name  TEXT,
    attributes  TEXT            -- JSON of all key=value pairs
);

-- ── Layer 7: parse coverage ────────────────────────────────────
CREATE TABLE IF NOT EXISTS parse_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file     TEXT NOT NULL UNIQUE,
    source_type     TEXT NOT NULL,  -- COBOL|JCL|BMS|CSD|SQL|CICS
    status          TEXT NOT NULL,  -- OK|LEXER_ERROR|PARSER_ERROR|PREPROCESSOR_FAILURE|TIMEOUT|UNSUPPORTED
    parse_errors    INTEGER DEFAULT 0,
    error_messages  TEXT,           -- JSON array
    parse_time_ms   INTEGER
);

-- ── Layer 7: migration risk register ─────────────────────────
CREATE TABLE IF NOT EXISTS risk_register (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,  -- ALTER|GOTO_DEPENDING|DYNAMIC_CALL|ODO|REDEFINES_OVERLAP|COMP3_ROUNDING|HANDLE_CONDITION|PSEUDO_CONVERSATIONAL
    program_uuid    TEXT REFERENCES nodes(uuid),
    node_uuid       TEXT REFERENCES nodes(uuid),
    severity        TEXT NOT NULL,  -- HIGH|MEDIUM|LOW
    note            TEXT,
    line            INTEGER
);

CREATE INDEX IF NOT EXISTS idx_risk_program ON risk_register(program_uuid);
CREATE INDEX IF NOT EXISTS idx_risk_kind    ON risk_register(kind);

-- ── Layer 7: complexity metrics ────────────────────────────────
CREATE TABLE IF NOT EXISTS complexity_metrics (
    para_uuid           TEXT PRIMARY KEY REFERENCES nodes(uuid),
    program_uuid        TEXT NOT NULL,
    cyclomatic          INTEGER DEFAULT 1,
    statement_count     INTEGER DEFAULT 0,
    nesting_depth       INTEGER DEFAULT 0,
    fan_in              INTEGER DEFAULT 0,
    fan_out             INTEGER DEFAULT 0
);
