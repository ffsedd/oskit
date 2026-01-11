# src/oskit/cli/file_renamer_cli.py
import sys
import argparse
from datetime import datetime
from oskit.file_renamer import parse_mapping, batch_rename, undo_rename
import logging

def main():
    parser = argparse.ArgumentParser(
        description="Batch rename files using mapping rules (dry-run by default)."
    )
    parser.add_argument("folder", nargs="?")
    parser.add_argument("--map", action="append", default=[], help="Rename rule old:new")
    parser.add_argument("--recursive", "-r", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", metavar="LOG_FILE")
    parser.add_argument(
        "--log",
        default=f"rename_log_{datetime.now():%Y%m%d_%H%M%S}.json"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")
    else:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")

    try:
        if args.undo:
            undo_rename(args.undo, verbose=args.verbose)
            return

        if not args.folder:
            parser.error("folder argument is required unless --undo is used")

        rules = parse_mapping(args.map)
        if not rules:
            parser.error("At least one --map rule is required")

        summary = batch_rename(
            folder=args.folder,
            rules=rules,
            apply=args.apply,
            recursive=args.recursive,
            log_path=args.log,
            verbose=args.verbose
        )

        print("\nSummary:")
        print(f"  Dry run: {summary['dry_run']}")
        print(f"  Renamed: {summary['renamed']}")
        print(f"  Skipped: {summary['skipped']}")
        print(f"  Unmatched: {summary.get('unmatched', 0)}")

    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
