"""Demo: emit Java source from canonical IR for the User Admin bounded context.

Usage:
  python ir/demo_emit.py --program COUSR01C
  python ir/demo_emit.py --program COUSR01C --program COUSR02C --program COUSR03C
  python ir/demo_emit.py --all        # emit Java for all programs with IR files
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from ir.canonical_ir import lower_program
from ir.java_emitter import emit_java, emit_java_from_file


IR_DIR = pathlib.Path("output/ir")
LAYER1_DIR = pathlib.Path("output/layer1")


def _emit_one(program_name: str, package: str) -> None:
    ir_path = IR_DIR / f"{program_name}.ir.json"
    if ir_path.exists():
        print(f"  Loading IR from {ir_path}")
        java_src = emit_java_from_file(ir_path, package=package)
    else:
        # Fall back to layer1 JSON if IR not precomputed
        layer1_path = LAYER1_DIR / f"{program_name}.json"
        if not layer1_path.exists():
            print(f"  ERROR: neither {ir_path} nor {layer1_path} found — run pipeline first")
            return
        import json
        nodes = json.loads(layer1_path.read_text(encoding="utf-8"))
        print(f"  Lowering IR from layer1 JSON ({len(nodes)} nodes)...")
        ir = lower_program(nodes, program_name)
        java_src = emit_java(ir, package=package)

    out_path = pathlib.Path("output/java") / f"{_to_class_name(program_name)}.java"
    print(f"  Written: {out_path}  ({len(java_src.splitlines())} lines)")


def _to_class_name(program: str) -> str:
    parts = program.replace("-", "_").split("_")
    return "".join(p.capitalize() for p in parts if p)


def main() -> None:
    ap = argparse.ArgumentParser(description="Emit Java from canonical IR")
    ap.add_argument(
        "--program", action="append", dest="programs",
        metavar="PROGRAM",
        help="Program name(s) to emit (may be repeated)",
    )
    ap.add_argument("--all", action="store_true", help="Emit Java for all *.ir.json files")
    ap.add_argument("--package", default="com.ust.carddemo", help="Java package name")
    args = ap.parse_args()

    programs: list[str] = args.programs or []

    if args.all:
        if IR_DIR.exists():
            programs = [p.stem.replace(".ir", "") for p in sorted(IR_DIR.glob("*.ir.json"))]
        else:
            print(f"IR directory not found: {IR_DIR}", file=sys.stderr)
            sys.exit(1)

    if not programs:
        # Default: User Admin bounded context
        programs = ["COUSR01C", "COUSR02C", "COUSR03C"]
        print(f"No programs specified — defaulting to User Admin context: {programs}")

    print(f"\nEmitting Java for {len(programs)} program(s), package={args.package}\n")
    for prog in programs:
        print(f"[{prog}]")
        _emit_one(prog, args.package)

    print(f"\nDone. Java files in: output/java/")


if __name__ == "__main__":
    main()
