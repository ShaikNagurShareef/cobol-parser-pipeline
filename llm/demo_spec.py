"""Demo: generate a grounded specification for a COBOL paragraph or program.

Usage examples:
  python llm/demo_spec.py --program COTRN02C --scope program
  python llm/demo_spec.py --uuid <para-uuid> --scope paragraph
  python llm/demo_spec.py --list-programs          # show available programs
  python llm/demo_spec.py --list-paragraphs COTRN02C
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from storage.db import get_connection
from llm.langgraph_agent import generate_spec_for


def _list_programs(con) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT name FROM nodes WHERE kind='Program' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def _list_paragraphs(con, program_name: str) -> list[dict]:
    prog = con.execute(
        "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
        (program_name,),
    ).fetchone()
    if not prog:
        return []
    rows = con.execute(
        "SELECT uuid, name, start_line FROM nodes "
        "WHERE parent_uuid=? AND kind='Paragraph' ORDER BY start_line",
        (prog[0],),
    ).fetchall()
    return [{"uuid": r[0], "name": r[1], "start_line": r[2]} for r in rows]


def _resolve_uuid(con, program_name: str, scope: str) -> str | None:
    if scope == "program":
        row = con.execute(
            "SELECT uuid FROM nodes WHERE kind='Program' AND UPPER(name)=UPPER(?)",
            (program_name,),
        ).fetchone()
        return row[0] if row else None
    # paragraph scope — pick the first paragraph of the program
    paras = _list_paragraphs(con, program_name)
    return paras[0]["uuid"] if paras else None


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM spec generation demo")
    ap.add_argument("--uuid", help="Paragraph or program UUID")
    ap.add_argument("--program", help="Program name (e.g. COTRN02C)")
    ap.add_argument("--scope", choices=["paragraph", "program"], default="paragraph")
    ap.add_argument("--list-programs", action="store_true")
    ap.add_argument("--list-paragraphs", metavar="PROGRAM")
    ap.add_argument(
        "--db", default="artifacts/pipeline.db", help="SQLite database path"
    )
    args = ap.parse_args()

    with get_connection(args.db) as con:
        if args.list_programs:
            programs = _list_programs(con)
            print(f"Programs in database ({len(programs)}):")
            for p in programs:
                print(f"  {p}")
            return

        if args.list_paragraphs:
            paras = _list_paragraphs(con, args.list_paragraphs)
            if not paras:
                print(f"Program '{args.list_paragraphs}' not found in database.")
                sys.exit(1)
            print(f"Paragraphs in {args.list_paragraphs} ({len(paras)}):")
            for p in paras:
                print(f"  [{p['uuid'][:8]}] {p['name']} (line {p['start_line']})")
            return

        uuid = args.uuid
        if not uuid:
            if not args.program:
                print("Provide --uuid or --program.", file=sys.stderr)
                sys.exit(1)
            uuid = _resolve_uuid(con, args.program, args.scope)
            if not uuid:
                print(
                    f"Program '{args.program}' not found in database. "
                    "Run the pipeline first.",
                    file=sys.stderr,
                )
                sys.exit(1)

    print(f"Generating {args.scope} specification for UUID: {uuid}")
    print("(Calling Claude claude-sonnet-4-6 via Anthropic API...)\n")

    spec = generate_spec_for(uuid, scope=args.scope)
    print(spec)

    # Save to output
    out_dir = pathlib.Path("output/specs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{uuid[:8]}_{args.scope}.md"
    out_file.write_text(spec, encoding="utf-8")
    print(f"\nSpec saved to: {out_file}")


if __name__ == "__main__":
    main()
