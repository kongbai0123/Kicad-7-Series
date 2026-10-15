"""Tutor CLI: read progress, record confirmed steps or notes with revision checks."""
import argparse
import json
from pathlib import Path
import sys
import state

parser = argparse.ArgumentParser(description=__doc__)
sub = parser.add_subparsers(dest="command", required=True)
sub.add_parser("status")
migrate = sub.add_parser("migrate-intro")
migrate.add_argument("--revision", type=int, required=True)
record = sub.add_parser("record")
record.add_argument("--lesson", required=True)
record.add_argument("--action", choices=["activate_lesson", "confirm_step", "add_note"], required=True)
record.add_argument("--revision", type=int, required=True)
record.add_argument("--step")
record.add_argument("--note")
record.add_argument("--note-file", type=Path)
args = parser.parse_args()
try:
    if args.command == "status":
        result = state.progress()
    elif args.command == "migrate-intro":
        result = state.migrate_intro_workspace(args.revision)
    else:
        note = args.note_file.read_text(encoding="utf-8-sig") if args.note_file else args.note
        result = state.mutate({"lessonId": args.lesson, "action": args.action,
                               "expectedRevision": args.revision, "stepId": args.step, "note": note}, actor="tutor")
    print(json.dumps(result, ensure_ascii=True, indent=2))
except (state.StateError, OSError, ValueError) as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
