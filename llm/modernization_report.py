"""Holistic application modernization report generator.

Produces a single cohesive Markdown document describing the entire CardDemo
application — architecture, cross-program call graph, shared data model,
business rules, JCL job chains, and a Java package blueprint — all derived
from ANTLR-parsed artifacts in the database.
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
from collections import defaultdict
from typing import Any

OUTPUT_DIR = pathlib.Path(__file__).parent.parent / "output" / "specs"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _rows(con: sqlite3.Connection, sql: str, *args) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def _one(con: sqlite3.Connection, sql: str, *args) -> dict | None:
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def pic_to_java(pic: str | None, usage: str | None) -> str:
    if not pic:
        return "Object"
    p = pic.upper().replace(" ", "")
    u = (usage or "DISPLAY").upper()
    if "V" in p and "COMP" in u:
        m = re.match(r"S?9+\((\d+)\)V9*\((\d+)\)", p)
        if m:
            return f"BigDecimal  // p={int(m.group(1))+int(m.group(2))}, s={m.group(2)}"
        return "BigDecimal"
    if "COMP-3" in u or "PACKED" in u:
        return "long"
    if "COMP" in u:
        m = re.match(r"S?9+\((\d+)\)", p)
        return "long" if m and int(m.group(1)) > 9 else "int"
    if re.match(r"X$|X\(1\)$", p):
        return "char"
    if p.startswith("X"):
        return "String"
    if "INDEX" in u or "POINTER" in u:
        return "int"
    if re.match(r"S?9", p):
        return "String  // numeric display"
    return "String"


def camel(cobol: str) -> str:
    parts = cobol.lower().replace("-", "_").split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def pascal(cobol: str) -> str:
    return "".join(p.capitalize() for p in cobol.lower().replace("-", "_").split("_"))


# ── Domain classifier ─────────────────────────────────────────────────────────

_DOMAIN_RULES: list[tuple[str, str]] = [
    (r"^CBACT",  "Account Management (Batch)"),
    (r"^CBCUS",  "Customer Management (Batch)"),
    (r"^CBTRAN|^CBSTM", "Transaction Processing (Batch)"),
    (r"^CBDACU", "Utility / DayCycle (Batch)"),
    (r"^COACTU|^COACTVW", "Account Inquiry (Online)"),
    (r"^COCRD",  "Credit Card Management (Online)"),
    (r"^COTRN",  "Transaction Management (Online)"),
    (r"^COUSR",  "User Administration (Online)"),
    (r"^COBIL",  "Billing (Online)"),
    (r"^COSGN",  "Security / Sign-On (Online)"),
    (r"^COMEN",  "Main Menu (Online)"),
]

def domain_of(name: str) -> str:
    n = name.upper()
    for pat, label in _DOMAIN_RULES:
        if re.match(pat, n):
            return label
    return "Shared / Utility"


# ── Section builders ──────────────────────────────────────────────────────────

def _section_overview(con: sqlite3.Connection, doc: list[str]) -> None:
    n_prog = con.execute("SELECT COUNT(DISTINCT name) FROM nodes WHERE kind='Program'").fetchone()[0]
    n_para = con.execute("SELECT COUNT(*) FROM nodes WHERE kind='Paragraph'").fetchone()[0]
    n_di   = con.execute("SELECT COUNT(*) FROM data_items").fetchone()[0]
    n_br   = con.execute("SELECT COUNT(*) FROM business_rules").fetchone()[0]
    n_call = con.execute("SELECT COUNT(*) FROM call_graph").fetchone()[0]
    n_cfg  = con.execute("SELECT COUNT(*) FROM control_flow").fetchone()[0]
    n_jcl  = con.execute("SELECT COUNT(DISTINCT job_name) FROM jcl_job").fetchone()[0]
    n_bms  = con.execute("SELECT COUNT(*) FROM screen_map").fetchone()[0]
    n_cov  = con.execute("SELECT COUNT(*) FROM parse_coverage WHERE status='OK'").fetchone()[0]
    n_tot  = con.execute("SELECT COUNT(*) FROM parse_coverage").fetchone()[0]

    doc += [
        "## 1. Application Overview",
        "",
        "**CardDemo** is an AWS mainframe modernization reference application implementing "
        "a full credit-card management system with online (CICS) and batch components.",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| COBOL Programs (ANTLR-parsed) | **{n_prog}** |",
        f"| Paragraphs | **{n_para}** |",
        f"| Data Dictionary Entries | **{n_di}** |",
        f"| Business Rules Extracted | **{n_br}** |",
        f"| Call Graph Edges | **{n_call}** |",
        f"| CFG Edges | **{n_cfg}** |",
        f"| JCL Jobs | **{n_jcl}** |",
        f"| BMS Screen Maps | **{n_bms}** |",
        f"| Parse Coverage | **{n_cov}/{n_tot} ({round(100*n_cov/max(n_tot,1))}%)** |",
        "",
    ]


def _section_architecture(con: sqlite3.Connection, doc: list[str]) -> None:
    programs = _rows(con,
        "SELECT MIN(uuid) AS uuid, name, source_file, MIN(start_line) AS start_line, "
        "MAX(end_line) AS end_line "
        "FROM nodes WHERE kind='Program' GROUP BY name ORDER BY name")

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for p in programs:
        by_domain[domain_of(p["name"])].append(p)

    doc += ["## 2. Application Architecture by Domain", ""]
    for domain in sorted(by_domain):
        progs = by_domain[domain]
        doc.append(f"### {domain}")
        doc.append("")
        doc.append("| Program | Source | LoC | Paragraphs | Data Items |")
        doc.append("|---------|--------|-----|-----------|-----------|")
        for p in progs:
            loc = ((p["end_line"] or 0) - (p["start_line"] or 0) + 1)
            src = pathlib.Path(p["source_file"] or "?").name
            n_para = con.execute(
                "SELECT COUNT(*) FROM nodes WHERE parent_uuid=? AND kind='Paragraph'",
                (p["uuid"],)
            ).fetchone()[0]
            n_di = con.execute(
                "SELECT COUNT(*) FROM data_items WHERE program_uuid=?",
                (p["uuid"],)
            ).fetchone()[0]
            doc.append(f"| `{p['name']}` | `{src}` | {loc} | {n_para} | {n_di} |")
        doc.append("")


def _section_call_graph(con: sqlite3.Connection, doc: list[str]) -> None:
    edges = _rows(con,
        """SELECT n.name AS caller, cg.callee_name, cg.call_type, cg.is_resolved
           FROM call_graph cg JOIN nodes n ON n.uuid=cg.caller_uuid
           ORDER BY caller, callee_name""")

    doc += ["## 3. Cross-Program Call Graph (ANTLR-extracted)", ""]
    if not edges:
        doc += ["_No inter-program CALL statements detected._", ""]
        return

    doc.append("| Caller | Callee | Type | Resolved |")
    doc.append("|--------|--------|------|---------|")
    for e in edges:
        resolved = "✓" if e["is_resolved"] else "dynamic"
        doc.append(f"| `{e['caller']}` | `{e['callee_name']}` | {e['call_type'] or 'CALL'} | {resolved} |")

    # Hub programs (most callers)
    callee_counts: dict[str, int] = defaultdict(int)
    for e in edges:
        callee_counts[e["callee_name"]] += 1
    hubs = sorted(callee_counts.items(), key=lambda x: -x[1])[:5]
    if hubs:
        doc += ["", "**Most-called programs (integration hubs):**"]
        for name, cnt in hubs:
            doc.append(f"- `{name}` — called {cnt} time(s)")
    doc.append("")


def _section_transaction_flow(con: sqlite3.Connection, doc: list[str]) -> None:
    flows = _rows(con,
        """SELECT tf.verb, n1.name AS from_prog, tf.trans_id, tf.to_program AS to_prog
           FROM transaction_flow tf
           LEFT JOIN nodes n1 ON n1.uuid=tf.from_uuid
           ORDER BY tf.trans_id, from_prog""")

    doc += ["## 4. CICS Transaction Flow (ANTLR-extracted)", ""]
    if not flows:
        doc += ["_No EXEC CICS LINK/XCTL/RETURN chains detected._", ""]
        return

    by_trans: dict[str, list[dict]] = defaultdict(list)
    for f in flows:
        by_trans[f["trans_id"] or "?"].append(f)
    for tid, flist in sorted(by_trans.items()):
        doc.append(f"**Transaction `{tid}`**")
        for f in flist:
            doc.append(f"  - `{f['from_prog']}` →`{f['verb']}` → `{f['to_prog'] or '?'}`")
    doc.append("")


def _section_jcl_chain(con: sqlite3.Connection, doc: list[str]) -> None:
    jobs = _rows(con,
        "SELECT DISTINCT job_name, program FROM jcl_job ORDER BY job_name")
    deps = _rows(con,
        "SELECT producer_job, consumer_job, dataset FROM jcl_dependency ORDER BY producer_job")

    doc += ["## 5. JCL Batch Job Chain (ANTLR-extracted)", ""]
    if jobs:
        doc.append(f"**{len(jobs)} JCL jobs parsed.**")
        doc.append("")
        doc.append("| Job Name | Program |")
        doc.append("|----------|---------|")
        for j in jobs[:30]:
            prog = j["program"] or "?"
            doc.append(f"| `{j['job_name']}` | `{prog}` |")
        if len(jobs) > 30:
            doc.append(f"| … | ({len(jobs) - 30} more) |")
        if deps:
            doc.append("")
            doc.append("**Dataset dependencies:**")
            doc.append("")
            doc.append("| Producer Job | Consumer Job | Dataset |")
            doc.append("|-------------|-------------|---------|")
            for d in deps[:20]:
                doc.append(f"| `{d['producer_job']}` | `{d['consumer_job']}` | `{d['dataset']}` |")
    else:
        doc.append("_JCL parsed but no job-level data detected._")
    doc.append("")


def _section_shared_data(con: sqlite3.Connection, doc: list[str]) -> None:
    copybooks = _rows(con,
        """SELECT cu.copybook_name, COUNT(DISTINCT cu.program_uuid) AS consumers
           FROM copybook_use cu
           GROUP BY cu.copybook_name
           ORDER BY consumers DESC""")

    doc += ["## 6. Shared Data Model via Copybooks (ANTLR-extracted)", ""]
    if not copybooks:
        doc += ["_No copybook usage tracked._", ""]
        return

    doc.append("| Copybook | Programs Using It | Role (inferred) |")
    doc.append("|----------|-------------------|-----------------|")
    for cb in copybooks:
        role = _infer_copybook_role(cb["copybook_name"])
        doc.append(f"| `{cb['copybook_name']}` | {cb['consumers']} | {role} |")
    doc.append("")

    # Key entity types from high-use copybooks
    doc.append("### Key Shared Data Structures → Java Entities")
    doc.append("")
    for cb in copybooks[:8]:
        cb_name = cb["copybook_name"]
        fields = _rows(con,
            """SELECT DISTINCT di.name, di.level, di.pic, di.usage
               FROM data_items di
               JOIN copybook_use cu ON cu.program_uuid=di.program_uuid
               WHERE cu.copybook_name=? AND di.level BETWEEN 2 AND 10
               ORDER BY di.level, di.name
               LIMIT 15""",
            cb_name)
        if not fields:
            continue
        entity = pascal(cb_name.replace(".cpy", "").replace(".CPY", ""))
        doc.append(f"**`{cb_name}` → `{entity}.java`**")
        doc.append("```java")
        doc.append(f"public class {entity} {{")
        for f in fields:
            if (f["name"] or "").upper() == "FILLER":
                continue
            jtype = pic_to_java(f["pic"], f["usage"]).split("//")[0].strip()
            doc.append(f"    private {jtype} {camel(f['name'])};")
        doc.append("}")
        doc.append("```")
        doc.append("")


def _infer_copybook_role(name: str) -> str:
    n = name.upper()
    if "ACCT" in n or "ACT" in n:
        return "Account record"
    if "CUST" in n or "CUSR" in n:
        return "Customer record"
    if "TRAN" in n:
        return "Transaction record"
    if "CARD" in n:
        return "Card record"
    if "USR" in n or "USER" in n:
        return "User/security record"
    if "MSG" in n or "ERR" in n:
        return "Messages / error codes"
    return "Shared structure"


def _section_business_rules(con: sqlite3.Connection, doc: list[str]) -> None:
    rules = _rows(con,
        """SELECT br.kind, br.predicate_raw, br.then_summary, br.else_summary,
                  br.line, n.name AS program
           FROM business_rules br JOIN nodes n ON n.uuid=br.program_uuid
           ORDER BY program, br.line
           LIMIT 80""")

    doc += ["## 7. Application-wide Business Rules (ANTLR-extracted)", ""]
    if not rules:
        doc += ["_No business rules extracted._", ""]
        return

    by_prog: dict[str, list[dict]] = defaultdict(list)
    for r in rules:
        by_prog[r["program"]].append(r)

    for prog, prules in sorted(by_prog.items()):
        doc.append(f"### `{prog}` — {len(prules)} rule(s)")
        for i, r in enumerate(prules, 1):
            pred = (r["predicate_raw"] or "").replace("\n", " ")[:120]
            then = (r["then_summary"] or "").replace("\n", " ")[:80]
            else_ = (r["else_summary"] or "").replace("\n", " ")[:80]
            doc.append(f"**{i}. `{r['kind']}`** (line {r['line']}): `{pred}`")
            if then:
                doc.append(f"   - THEN: {then}")
            if else_:
                doc.append(f"   - ELSE: {else_}")
        doc.append("")


def _section_risk_register(con: sqlite3.Connection, doc: list[str]) -> None:
    risks = _rows(con,
        """SELECT r.kind, r.severity, r.note, r.line, n.name AS program
           FROM risk_register r JOIN nodes n ON n.uuid=r.program_uuid
           ORDER BY r.severity DESC, program, r.line""")

    doc += ["## 8. Application Risk Register (ANTLR-extracted)", ""]
    if not risks:
        doc += ["_No migration risks detected._", ""]
        return

    by_sev: dict[str, list[dict]] = defaultdict(list)
    for r in risks:
        by_sev[r["severity"] or "LOW"].append(r)

    for sev in ["HIGH", "MEDIUM", "LOW"]:
        group = by_sev.get(sev, [])
        if not group:
            continue
        doc.append(f"### {sev} severity — {len(group)} issue(s)")
        doc.append("| Program | Kind | Note | Line |")
        doc.append("|---------|------|------|------|")
        for r in group:
            doc.append(f"| `{r['program']}` | `{r['kind']}` | {r['note'] or ''} | {r['line'] or ''} |")
        doc.append("")


def _section_java_blueprint(con: sqlite3.Connection, doc: list[str]) -> None:
    programs = _rows(con,
        "SELECT MIN(uuid) AS uuid, name FROM nodes WHERE kind='Program' GROUP BY name ORDER BY name")

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for p in programs:
        by_domain[domain_of(p["name"])].append(p)

    doc += [
        "## 9. Java Migration Blueprint",
        "",
        "### Proposed Package Structure",
        "```",
        "com.ust.carddemo",
        "├── model/            ← JPA entities (from copybook data structures)",
        "├── repository/       ← Spring Data repositories per entity",
        "├── service/          ← Business logic (one service per domain)",
        "│   ├── account/",
        "│   ├── customer/",
        "│   ├── transaction/",
        "│   └── admin/",
        "├── batch/            ← Spring Batch jobs (from JCL job chains)",
        "├── web/controller/   ← REST controllers (from CICS online programs)",
        "└── util/             ← Shared utilities, type converters",
        "```",
        "",
        "### Service Class Mapping (ANTLR-derived)",
        "",
    ]

    domain_to_service = {
        "Account Management (Batch)":       ("account", "AccountBatchService"),
        "Customer Management (Batch)":      ("customer", "CustomerBatchService"),
        "Transaction Processing (Batch)":   ("transaction", "TransactionBatchService"),
        "Utility / DayCycle (Batch)":       ("batch", "DayCycleJob"),
        "Account Inquiry (Online)":         ("account", "AccountService"),
        "Credit Card Management (Online)":  ("card", "CardService"),
        "Transaction Management (Online)":  ("transaction", "TransactionService"),
        "User Administration (Online)":     ("admin", "UserAdminService"),
        "Billing (Online)":                 ("billing", "BillingService"),
        "Security / Sign-On (Online)":      ("security", "AuthenticationService"),
        "Main Menu (Online)":               ("web", "MenuController"),
        "Shared / Utility":                 ("util", "UtilityService"),
    }

    doc.append("| COBOL Programs | Domain | Java Service | Package |")
    doc.append("|---------------|--------|-------------|---------|")
    for domain in sorted(by_domain):
        progs = by_domain[domain]
        pkg, svc = domain_to_service.get(domain, ("util", "UtilityService"))
        names = ", ".join(f"`{p['name']}`" for p in progs[:4])
        if len(progs) > 4:
            names += f" +{len(progs)-4}"
        doc.append(f"| {names} | {domain} | `{svc}` | `com.ust.carddemo.{pkg}` |")
    doc.append("")

    # Key data type mapping table
    doc += [
        "### COBOL → Java Type Mapping (PIC clause analysis)",
        "",
        "| COBOL PIC | USAGE | Java Type | Notes |",
        "|-----------|-------|-----------|-------|",
        "| `S9(n)V9(m)` | COMP-3 | `BigDecimal` | Preserve precision/scale |",
        "| `9(n)` | COMP / BINARY | `int` / `long` | n≤9→int, n>9→long |",
        "| `9(n)` | DISPLAY | `String` | Preserve leading zeros |",
        "| `X(n)` | DISPLAY | `String` | Trim trailing spaces |",
        "| `X(1)` | DISPLAY | `char` | Single character |",
        "| `S9(n)` | COMP-3 | `long` | Packed decimal integer |",
        "| `POINTER` | — | `int` | Address → index |",
        "",
    ]


def _section_file_io(con: sqlite3.Connection, doc: list[str]) -> None:
    fio = _rows(con,
        """SELECT fi.file_name, fi.operation, n.name AS program
           FROM file_io fi JOIN nodes n ON n.uuid=fi.program_uuid
           ORDER BY fi.file_name, program""")

    doc += ["## 10. File I/O Inventory (ANTLR-extracted)", ""]
    if not fio:
        doc += ["_No file I/O operations detected._", ""]
        return

    by_file: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for row in fio:
        by_file[row["file_name"]][row["program"]].add(row["operation"] or "?")

    doc.append("| File / Dataset | Programs | Operations | Spring Data Mapping |")
    doc.append("|---------------|----------|-----------|---------------------|")
    for fname, prog_ops in sorted(by_file.items()):
        progs = ", ".join(f"`{p}`" for p in sorted(prog_ops)[:3])
        ops   = ", ".join(sorted({op for ops in prog_ops.values() for op in ops}))
        entity = pascal(re.sub(r"[^A-Z0-9]", "", fname.upper())[:12] or "File")
        doc.append(f"| `{fname}` | {progs} | {ops} | `{entity}Repository` |")
    doc.append("")


# ── Entry point ───────────────────────────────────────────────────────────────

def generate_holistic_report(
    con: sqlite3.Connection,
    out_dir: pathlib.Path = OUTPUT_DIR,
    use_llm: bool = False,
) -> dict:
    """Generate one holistic Markdown report for the entire CardDemo application."""
    out_dir.mkdir(parents=True, exist_ok=True)

    doc: list[str] = [
        "# CardDemo — Holistic Application Modernization Specification",
        "",
        "> Generated from ANTLR-parsed artifacts (COBOL, JCL, BMS, CSD).",
        "> All data items, business rules, call graphs and type mappings are",
        "> extracted deterministically from the parsed source — no manual annotation.",
        "",
        "---",
        "",
    ]

    _section_overview(con, doc)
    _section_architecture(con, doc)
    _section_call_graph(con, doc)
    _section_transaction_flow(con, doc)
    _section_jcl_chain(con, doc)
    _section_shared_data(con, doc)
    _section_business_rules(con, doc)
    _section_risk_register(con, doc)
    _section_java_blueprint(con, doc)
    _section_file_io(con, doc)

    if use_llm:
        _enhance_with_llm(doc, con)

    markdown = "\n".join(doc)
    out_file = out_dir / "MODERNIZATION_REPORT.md"
    out_file.write_text(markdown, encoding="utf-8")

    return {
        "event":        "done",
        "output_file":  str(out_file),
        "size_kb":      round(out_file.stat().st_size / 1024, 1),
        "sections":     10,
    }


def _enhance_with_llm(doc: list[str], con: sqlite3.Connection) -> None:
    try:
        from llm.llm_client import call_llm
        summary = call_llm(
            "You are a mainframe modernization expert. "
            "Given this CardDemo COBOL application spec, write a 3-paragraph executive summary "
            "covering: (1) application purpose and scope, (2) top 3 modernization challenges, "
            "(3) recommended Java/Spring migration approach.\n\n"
            + "\n".join(doc[:60]),
            max_tokens=500,
        )
        doc.insert(5, f"## Executive Summary (LLM-generated)\n\n{summary}\n\n---\n")
    except Exception as exc:
        doc.insert(5, f"## Executive Summary\n\n_LLM unavailable: {exc}_\n\n---\n")
