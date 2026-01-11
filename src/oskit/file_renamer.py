# src/oskit/file_renamer.py
from pathlib import Path
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")


# ---------------- Pure functions ----------------

def parse_mapping(mapping_list):
    """Return ordered list of (old, new) rules."""
    return [_parse_rule(item) for item in mapping_list]


def _parse_rule(item):
    """Parse single old:new rule."""
    if ":" not in item:
        raise ValueError(f"Invalid mapping format: {item}. Use old:new")
    old, new = item.split(":", 1)
    if not old:
        raise ValueError("Empty 'old' pattern is not allowed")
    return old, new


def apply_rules(name, rules):
    """Return new name after applying first matching rule (case-sensitive)."""
    for old, new in rules:
        if old in name:
            return name.replace(old, new, 1)
    return name


def build_plan(files, rules):
    """Compute rename plan for a list of files (pure)."""
    plan = []
    for file in files:
        new_name = apply_rules(file.name, rules)
        dst = file.with_name(new_name)
        plan.append((file, dst))
    return plan


def validate_plan(plan, existing_paths=None):
    """
    Validate rename plan (pure logic).
    existing_paths: set of Path objects considered to exist (optional)
    """
    targets = {}
    sources = {src for src, _ in plan}
    existing_paths = existing_paths or set()

    for src, dst in plan:
        if dst in targets:
            raise ValueError(f"Collision: {src.name} and {targets[dst].name} both map to {dst.name}")
        if dst in existing_paths and dst not in sources:
            raise ValueError(f"Target already exists: {dst}")
        targets[dst] = src


def compute_final_plan(plan):
    """
    Return final plan, unmatched count, and skipped count (pure).
    Skipped here means collision with other files in plan.
    """
    sources_set = {s for s, _ in plan}
    final_plan = []
    unmatched_count = 0
    skipped_count = 0

    for src, dst in plan:
        if src == dst:
            unmatched_count += 1
        elif dst.exists() and dst not in sources_set:
            skipped_count += 1
        else:
            final_plan.append((src, dst))

    return final_plan, unmatched_count, skipped_count


# ---------------- Impure / side-effect functions ----------------

def commit_plan(plan, rename_func=None, verbose=True):
    """
    Execute a swap-safe rename plan.
    rename_func(src, dst) can be injected for testing.
    """
    rename_func = rename_func or Path.rename
    tmp_map = {}

    for src, dst in plan:
        if src == dst:
            continue
        tmp = src.with_name(f"__tmp__{src.name}")
        rename_func(src, tmp)
        tmp_map[tmp] = dst
        if verbose:
            logging.info(f"Staged: {src} -> {tmp}")

    for tmp, dst in tmp_map.items():
        dst.parent.mkdir(parents=True, exist_ok=True)
        rename_func(tmp, dst)
        if verbose:
            logging.info(f"Renamed: {tmp} -> {dst}")


def batch_rename(folder, rules, apply=False, recursive=False, log_path=None, verbose=True):
    """
    High-level batch rename.
    Returns a summary dict.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"Invalid folder: {folder}")

    # Gather files
    files = list(folder.rglob("*") if recursive else folder.iterdir())
    files = [f for f in files if f.is_file()]

    plan = build_plan(files, rules)
    validate_plan(plan)

    final_plan, unmatched_count, skipped_count = compute_final_plan(plan)
    log_entries = [{"original": str(s), "renamed": str(d)} for s, d in final_plan]

    if verbose:
        logging.info(f"Dry run: {not apply}, {len(final_plan)} files to rename")
        for s, d in final_plan:
            logging.info(f"{'[DRY RUN]' if not apply else 'Renaming'}: {s} -> {d}")

    if apply and final_plan:
        commit_plan(final_plan, verbose=verbose)
        if log_path:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log_entries, f, indent=2)
            logging.info(f"Log saved to: {log_path}")

    return {
        "renamed": len(final_plan) if apply else 0,
        "skipped": skipped_count,
        "unmatched": unmatched_count,
        "dry_run": not apply,
        "plan": [{"original": str(s), "renamed": str(d)} for s, d in final_plan],
    }


def undo_rename(log_file, rename_func=None, verbose=True):
    """Undo a previous batch rename."""
    log_file = Path(log_file)
    if not log_file.exists():
        raise ValueError(f"Log file not found: {log_file}")

    rename_func = rename_func or Path.rename
    with open(log_file, "r", encoding="utf-8") as f:
        entries = json.load(f)

    restored_count = 0
    skipped_count = 0

    for entry in reversed(entries):
        src = Path(entry["renamed"])
        dst = Path(entry["original"])
        if not src.exists():
            skipped_count += 1
            if verbose:
                logging.warning(f"⚠️ Missing: {src}")
            continue
        if dst.exists():
            skipped_count += 1
            if verbose:
                logging.warning(f"⚠️ Cannot restore, target exists: {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        rename_func(src, dst)
        restored_count += 1
        if verbose:
            logging.info(f"Restored: {src} -> {dst}")

    if verbose:
        logging.info(f"\nUndo completed. Restored {restored_count}/{len(entries)}, skipped {skipped_count}.")
