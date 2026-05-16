"""Full-corpus batch ingestion pipeline.

Usage:
    python pipeline/batch.py [--corpus PATH] [--db PATH] [--workers N]

Runs Layers 1–7 across the complete CardDemo corpus in parallel,
then emits the final coverage report and Mermaid diagrams.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from pipeline.ingest import ingest_file, CPY_DIRS
from parsers.jcl.jcl_parser import parse_jcl_file
from parsers.bms.bms_parser import parse_bms_file
from parsers.csd.csd_parser import parse_csd_file
from artifacts import layer3_intra, layer4_inter, layer5_business, layer7_quality
from parsers.cobol.ast_normalizer import normalize
from parsers.cobol.proleap_wrapper import parse_cobol_file
from storage.db import init_db, transaction, get_connection
from diagrams.mermaid_gen import generate_all_diagrams
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

PROJECT_ROOT = pathlib.Path(__file__).parent.parent
DEFAULT_CORPUS = PROJECT_ROOT / "external" / "carddemo" / "app" / "cbl"
DEFAULT_JCL    = PROJECT_ROOT / "external" / "carddemo" / "app" / "jcl"
DEFAULT_BMS    = PROJECT_ROOT / "external" / "carddemo" / "app" / "bms"
DEFAULT_CSD    = PROJECT_ROOT / "external" / "carddemo" / "app" / "csd"
DEFAULT_DB     = PROJECT_ROOT / "artifacts" / "pipeline.db"
DEFAULT_OUT    = PROJECT_ROOT / "output"

console = Console()


def run_batch(
    corpus_dir: pathlib.Path = DEFAULT_CORPUS,
    jcl_dir: pathlib.Path = DEFAULT_JCL,
    bms_dir: pathlib.Path = DEFAULT_BMS,
    csd_dir: pathlib.Path = DEFAULT_CSD,
    db_path: pathlib.Path = DEFAULT_DB,
    workers: int = 4,
) -> dict:
    start = time.perf_counter()
    init_db(db_path)

    # ── Phase 1: COBOL files (parallel) ──────────────────────────────────────
    cbl_files = sorted(corpus_dir.glob("*.cbl")) + sorted(corpus_dir.glob("*.CBL"))
    console.print(f"\n[bold cyan]Phase 1: COBOL[/bold cyan] — {len(cbl_files)} files, {workers} workers")

    results_cobol: list[dict] = []
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  BarColumn(), TextColumn("{task.completed}/{task.total}"),
                  TimeElapsedColumn(), console=console) as progress:
        task = progress.add_task("Parsing COBOL...", total=len(cbl_files))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_ingest_one, f, db_path): f for f in cbl_files}
            for fut in as_completed(futures):
                res = fut.result()
                results_cobol.append(res)
                progress.advance(task)

    ok_cobol = sum(1 for r in results_cobol if r.get("status") == "OK")
    console.print(f"  COBOL: {ok_cobol}/{len(cbl_files)} OK")

    # ── Phase 2: Build Layers 3-5 per program ────────────────────────────────
    console.print(f"\n[bold cyan]Phase 2: Layers 3-5[/bold cyan] (CFG, def-use, business rules)")
    _build_graph_layers(cbl_files, db_path)

    # ── Phase 3: Layer 4 inter-program resolution ─────────────────────────────
    console.print("\n[bold cyan]Phase 3: Resolving call graph[/bold cyan]")
    with transaction() as con:
        resolved = layer4_inter.resolve_callees(con)
        layer4_inter.emit_artifact(con)
    console.print(f"  Resolved {resolved} dynamic callee UUIDs")

    # ── Phase 4: JCL ─────────────────────────────────────────────────────────
    jcl_files = sorted(jcl_dir.glob("*.jcl")) + sorted(jcl_dir.glob("*.JCL")) \
                + sorted(jcl_dir.glob("*.jcl.*"))
    console.print(f"\n[bold cyan]Phase 4: JCL[/bold cyan] — {len(jcl_files)} files")
    jcl_ok = 0
    for f in jcl_files:
        try:
            parse_jcl_file(f, db_path)
            jcl_ok += 1
        except Exception as exc:
            console.print(f"  [red]JCL FAIL[/red] {f.name}: {exc}")
    console.print(f"  JCL: {jcl_ok}/{len(jcl_files)} OK")

    # ── Phase 5: BMS ─────────────────────────────────────────────────────────
    bms_files = sorted(bms_dir.glob("*.bms")) + sorted(bms_dir.glob("*.BMS")) \
                + sorted(bms_dir.glob("*.MFS"))
    console.print(f"\n[bold cyan]Phase 5: BMS[/bold cyan] — {len(bms_files)} files")
    bms_ok = 0
    for f in bms_files:
        try:
            parse_bms_file(f, db_path)
            bms_ok += 1
        except Exception as exc:
            console.print(f"  [red]BMS FAIL[/red] {f.name}: {exc}")
    console.print(f"  BMS: {bms_ok}/{len(bms_files)} OK")

    # ── Phase 6: CSD ─────────────────────────────────────────────────────────
    csd_files = sorted(csd_dir.glob("*.csd")) + sorted(csd_dir.glob("*.CSD")) \
                + sorted(csd_dir.glob("*.txt")) + sorted(csd_dir.glob("*.TXT"))
    console.print(f"\n[bold cyan]Phase 6: CSD[/bold cyan] — {len(csd_files)} files")
    csd_ok = 0
    for f in csd_files:
        try:
            parse_csd_file(f, db_path)
            csd_ok += 1
        except Exception as exc:
            console.print(f"  [red]CSD FAIL[/red] {f.name}: {exc}")
    console.print(f"  CSD: {csd_ok}/{len(csd_files)} OK")

    # ── Phase 7: Coverage report + risk register ─────────────────────────────
    console.print("\n[bold cyan]Phase 7: Coverage report[/bold cyan]")
    with get_connection() as con:
        report = layer7_quality.coverage_report(con)
    console.print(
        f"  Overall parse coverage: [bold]{report['overall_coverage_pct']}%[/bold] "
        f"({report['ok_files']}/{report['total_files']} files)"
    )

    # ── Phase 8: Mermaid diagrams ──────────────────────────────────────────
    console.print("\n[bold cyan]Phase 8: Mermaid diagrams[/bold cyan]")
    with get_connection() as con:
        generate_all_diagrams(con, DEFAULT_OUT / "diagrams")
    console.print("  Diagrams written to output/diagrams/")

    elapsed = round(time.perf_counter() - start, 1)
    summary = {
        "cobol_files": len(cbl_files),
        "cobol_ok": ok_cobol,
        "jcl_ok": jcl_ok,
        "bms_ok": bms_ok,
        "csd_ok": csd_ok,
        "overall_coverage_pct": report["overall_coverage_pct"],
        "elapsed_s": elapsed,
    }
    console.print(f"\n[bold green]Done in {elapsed}s[/bold green]")
    console.print(json.dumps(summary, indent=2))
    return summary


def _ingest_one(cbl_file: pathlib.Path, db_path: pathlib.Path) -> dict:
    try:
        return ingest_file(cbl_file, db_path, verbose=False)
    except Exception as exc:
        return {"file": str(cbl_file), "status": "EXCEPTION", "error": str(exc)}


def _build_graph_layers(cbl_files: list[pathlib.Path], db_path: pathlib.Path) -> None:
    """Build Layers 3-5 for each program (requires Layer 1 already persisted)."""
    for cbl_file in cbl_files:
        try:
            raw = parse_cobol_file(cbl_file, CPY_DIRS)
            nodes = normalize(raw)
            if not nodes:
                continue
            prog_name = nodes[0]["name"] if nodes else cbl_file.stem
            with transaction() as con:
                layer3_intra.persist(nodes, prog_name, con)
                layer4_inter.persist_program(nodes, prog_name, con)
                layer5_business.persist(nodes, prog_name, con)
        except Exception:
            pass  # already logged in phase 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Full-corpus CardDemo pipeline")
    ap.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    ap.add_argument("--jcl",    default=str(DEFAULT_JCL))
    ap.add_argument("--bms",    default=str(DEFAULT_BMS))
    ap.add_argument("--csd",    default=str(DEFAULT_CSD))
    ap.add_argument("--db",     default=str(DEFAULT_DB))
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    run_batch(
        corpus_dir=pathlib.Path(args.corpus),
        jcl_dir=pathlib.Path(args.jcl),
        bms_dir=pathlib.Path(args.bms),
        csd_dir=pathlib.Path(args.csd),
        db_path=pathlib.Path(args.db),
        workers=args.workers,
    )
