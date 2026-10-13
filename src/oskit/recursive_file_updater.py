#!/usr/bin/env python3
"""Safely replace same-named files in a tree, with preview, journal, and undo.

Python >= 3.10. Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any
from uuid import uuid4

DEFAULT_STATE_DIR = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    / "file-updater"
)
Progress = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class Plan:
    source: Path
    root: Path
    targets: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class Result:
    run_id: str
    changed: int
    skipped: int
    errors: tuple[str, ...]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover(dst: Path, src: Path, state_dir: Path = DEFAULT_STATE_DIR) -> Plan:
    src = src.expanduser().resolve(strict=True)
    dst = dst.expanduser().resolve(strict=True)
    state_dir = state_dir.expanduser().resolve()
    if not src.is_file():
        raise ValueError(f"Not a regular source file: {src}")
    if not dst.is_dir():
        raise ValueError(f"Not a destination directory: {dst}")

    targets = tuple(
        sorted(
            path
            for path in dst.rglob(src.name)
            if path.is_file()
            and not path.is_symlink()
            and not path.is_relative_to(state_dir)
            and not os.path.samefile(path, src)
        )
    )
    return Plan(src, dst, targets)


def file_row(path: Path, role: str) -> tuple[str, str, str, str]:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return role, f"{stat.st_size:,}", modified, str(path)


def show_plan(plan: Plan) -> None:
    print(f"{'ROLE':<7} {'BYTES':>12} {'MODIFIED':<19} PATH")
    rows = [(plan.source, "SOURCE"), *((path, "TARGET") for path in plan.targets)]
    for path, role in rows:
        a, b, c, d = file_row(path, role)
        print(f"{a:<7} {b:>12} {c:<19} {d}")
    print(f"\nMatching targets: {len(plan.targets)}")


def make_logger(state_dir: Path) -> logging.Logger:
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "posix":
        state_dir.chmod(0o700)  # Backups may contain sensitive information.
    log = logging.getLogger(f"file-updater:{state_dir.resolve()}")
    if not log.handlers:
        handler = logging.FileHandler(state_dir / "operations.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically replace the manifest; do not leave a half-written JSON file."""
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def copy_replace(src: Path, dst: Path) -> None:
    """Copy metadata and bytes, then atomically replace one destination path."""
    fd, name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    os.close(fd)
    temp = Path(name)
    try:
        shutil.copy2(src, temp)
        with temp.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temp, dst)
    finally:
        temp.unlink(missing_ok=True)


def _notify(message: str, log: logging.Logger, progress: Progress | None) -> None:
    log.info(message)
    if progress:
        progress(message)


def update_files_recursively(
    dst: Path,
    src: Path,
    state_dir: Path = DEFAULT_STATE_DIR,
    progress: Progress | None = None,
    *,
    plan: Plan | None = None,
) -> Result:
    """Journal every prepared change before modifying its destination."""
    plan = plan if plan is not None else discover(dst, src, state_dir)
    log = make_logger(state_dir)
    if not plan.targets:
        _notify("No matching destination files.", log, progress)
        return Result("", 0, 0, ())

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + f"_{uuid4().hex[:8]}"
    )
    run_dir = state_dir / "runs" / run_id
    backup_dir = run_dir / "backups"
    backup_dir.mkdir(parents=True)

    snapshot = run_dir / "source.bin"
    shutil.copy2(plan.source, snapshot)  # Immutable input for the entire run.
    with snapshot.open("rb") as stream:
        os.fsync(stream.fileno())
    source_hash = sha256(snapshot)
    manifest_path = run_dir / "manifest.json"
    manifest: dict[str, Any] = {
        "version": 1,
        "run_id": run_id,
        "source": str(plan.source),
        "root": str(plan.root),
        "source_sha256": source_hash,
        "status": "running",
        "entries": [],
    }
    write_json(manifest_path, manifest)
    _notify(
        f"BEGIN {run_id}: {plan.source} -> {plan.root} ({len(plan.targets)} matches)",
        log,
        progress,
    )

    changed = skipped = 0
    errors: list[str] = []
    for index, target in enumerate(plan.targets, 1):
        backup_rel = f"backups/{index:06d}.bin"
        backup = run_dir / backup_rel
        try:
            if (
                not target.is_file()
                or target.is_symlink()
                or os.path.samefile(target, plan.source)
            ):
                raise RuntimeError("Destination is no longer a safe regular file")

            # A pre-existing identical file needs neither replacement nor backup.
            if target.stat().st_nlink > 1:
                raise RuntimeError(
                    "Hard-linked file; cannot safely restore the link relationship"
                )
            if sha256(target) == source_hash:
                skipped += 1
                _notify(f"UNCHANGED {target}", log, progress)
                continue

            shutil.copy2(target, backup)
            with backup.open("rb") as stream:
                os.fsync(stream.fileno())
            original_hash = sha256(backup)
            if sha256(target) != original_hash:
                raise RuntimeError("Destination changed while being backed up")

            entry = {
                "path": str(target),
                "backup": backup_rel,
                "before": original_hash,
                "after": source_hash,
                "state": "prepared",
            }
            manifest["entries"].append(entry)
            write_json(
                manifest_path, manifest
            )  # Write-ahead journal for crash recovery.

            if not target.is_file() or target.is_symlink():
                raise RuntimeError("Destination changed type before replacement")
            if sha256(target) != original_hash:
                raise RuntimeError("Destination changed before replacement")
            copy_replace(snapshot, target)
            entry["state"] = "applied"
            write_json(manifest_path, manifest)
            changed += 1
            _notify(f"UPDATED {target}", log, progress)
        except (OSError, RuntimeError) as exc:
            error = f"FAILED {target}: {exc}"
            errors.append(error)
            log.error(error)
            if progress:
                progress(error)

    manifest["status"] = "partial" if errors else "completed"
    write_json(manifest_path, manifest)
    _notify(
        f"END {run_id}: updated={changed}, unchanged={skipped}, failed={len(errors)}",
        log,
        progress,
    )
    return Result(run_id, changed, skipped, tuple(errors))


def load_manifest(state_dir: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    # Never permit run IDs to navigate outside the state directory.
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise ValueError("Invalid run ID")
    run_dir = state_dir / "runs" / run_id
    path = run_dir / "manifest.json"
    with path.open(encoding="utf-8") as stream:
        manifest = json.load(stream)
    if manifest.get("run_id") != run_id or manifest.get("version") != 1:
        raise ValueError(f"Invalid manifest: {path}")
    return run_dir, manifest


def latest_run(state_dir: Path) -> str | None:
    runs_dir = state_dir / "runs"
    if not runs_dir.is_dir():
        return None
    for path in sorted(runs_dir.iterdir(), reverse=True):
        if not path.is_dir() or not (path / "manifest.json").is_file():
            continue
        try:
            _, manifest = load_manifest(state_dir, path.name)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if any(item["state"] != "undone" for item in manifest["entries"]):
            return path.name
    return None


def undo_run(
    state_dir: Path = DEFAULT_STATE_DIR,
    run_id: str | None = None,
    progress: Progress | None = None,
) -> Result:
    run_id = run_id or latest_run(state_dir)
    if run_id is None:
        raise ValueError("No runs available to undo")
    run_dir, manifest = load_manifest(state_dir, run_id)
    log = make_logger(state_dir)
    _notify(f"UNDO BEGIN {run_id}", log, progress)

    restored = skipped = 0
    errors: list[str] = []
    for entry in reversed(manifest["entries"]):
        if entry["state"] == "undone":
            skipped += 1
            continue
        target = Path(entry["path"])
        backup = run_dir / entry["backup"]
        try:
            if not backup.is_file() or sha256(backup) != entry["before"]:
                raise RuntimeError("Missing or altered backup")
            if not target.is_file() or target.is_symlink():
                raise RuntimeError("Destination is missing or is not a regular file")
            current = sha256(target)
            if current == entry["after"]:
                # Reapply the original metadata as well as the original bytes.
                copy_replace(backup, target)
                restored += 1
                _notify(f"RESTORED {target}", log, progress)
            elif current == entry["before"]:
                skipped += 1  # Prepared but not replaced, or restored externally.
                _notify(f"ALREADY ORIGINAL {target}", log, progress)
            else:
                raise RuntimeError(
                    "File was modified since update; refusing to overwrite"
                )
            entry["state"] = "undone"
            write_json(run_dir / "manifest.json", manifest)
        except (OSError, RuntimeError) as exc:
            error = f"UNDO FAILED {target}: {exc}"
            errors.append(error)
            log.error(error)
            if progress:
                progress(error)

    manifest["status"] = "partial_undo" if errors else "undone"
    write_json(run_dir / "manifest.json", manifest)
    _notify(
        f"UNDO END {run_id}: restored={restored}, skipped={skipped}, failed={len(errors)}",
        log,
        progress,
    )
    return Result(run_id, restored, skipped, tuple(errors))


def recent_log(state_dir: Path, max_lines: int = 100) -> str:
    path = state_dir / "operations.log"
    if not path.is_file():
        return "No operations logged yet."
    from collections import deque

    with path.open(encoding="utf-8") as stream:
        return "".join(deque(stream, maxlen=max_lines))


class UpdaterGUI(tk.Tk):
    def __init__(self, state_dir: Path) -> None:
        super().__init__()
        self.title("Recursive File Updater")
        self.geometry("1100x720")
        self.state_dir = state_dir
        self.source = tk.StringVar()
        self.destination = tk.StringVar()
        self.status = tk.StringVar(value="Choose a source file and destination folder.")
        self.plan: Plan | None = None
        self.path_controls: list[ttk.Widget] = []
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.on_done: Callable[[object], None] | None = None
        self._build()
        self.after(100, self._poll)

    def _build(self) -> None:
        form = ttk.Frame(self, padding=12)
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)
        for row, (label, variable, choose) in enumerate(
            (
                ("Source file", self.source, self._choose_source),
                ("Destination folder", self.destination, self._choose_destination),
            )
        ):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(form, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=8)
            button = ttk.Button(form, text="Browse…", command=choose)
            button.grid(row=row, column=2)
            self.path_controls.extend((entry, button))
            variable.trace_add("write", lambda *_: self._invalidate())

        buttons = ttk.Frame(self, padding=(12, 0, 12, 8))
        buttons.pack(fill="x")
        self.preview_button = ttk.Button(buttons, text="Preview", command=self.preview)
        self.preview_button.pack(side="left", padx=(0, 8))
        self.update_button = ttk.Button(
            buttons, text="Update files", command=self.apply_update, state="disabled"
        )
        self.update_button.pack(side="left", padx=(0, 8))
        self.undo_button = ttk.Button(buttons, text="Undo last run", command=self.undo)
        self.undo_button.pack(side="left")
        ttk.Label(buttons, textvariable=self.status).pack(side="right")

        files_frame = ttk.LabelFrame(self, text="Preview", padding=8)
        files_frame.pack(fill="both", expand=True, padx=12, pady=4)
        columns = ("role", "bytes", "modified", "path")
        self.tree = ttk.Treeview(files_frame, columns=columns, show="headings")
        for column, label, width in (
            ("role", "Type", 75),
            ("bytes", "Bytes", 110),
            ("modified", "Last modified", 160),
            ("path", "Path", 660),
        ):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, stretch=(column == "path"))
        scrollbar = ttk.Scrollbar(
            files_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        logs = ttk.LabelFrame(
            self,
            text=f"Operations log — {self.state_dir / 'operations.log'}",
            padding=8,
        )
        logs.pack(fill="both", padx=12, pady=(4, 12))
        self.log_text = tk.Text(logs, height=9, wrap="none", state="disabled")
        self.log_text.pack(fill="both", expand=True)
        self._refresh_log()

    def _choose_source(self) -> None:
        path = filedialog.askopenfilename(title="Select source file")
        if path:
            self.source.set(path)

    def _choose_destination(self) -> None:
        path = filedialog.askdirectory(title="Select destination directory")
        if path:
            self.destination.set(path)

    def _invalidate(self) -> None:
        self.plan = None
        self.update_button.configure(state="disabled")

    def _busy(self, busy: bool) -> None:
        for control in self.path_controls:
            control.configure(state="disabled" if busy else "normal")
        self.preview_button.configure(state="disabled" if busy else "normal")
        can_update = not busy and self.plan is not None and bool(self.plan.targets)
        self.update_button.configure(state="normal" if can_update else "disabled")
        self.undo_button.configure(state="disabled" if busy else "normal")

    def _start(self, fn: Callable[[], object], done: Callable[[object], None]) -> None:
        self.on_done = done
        self._busy(True)

        def worker() -> None:
            try:
                self.events.put(("done", fn()))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "status":
                    self.status.set(str(value))
                elif kind == "error":
                    self._busy(False)
                    self.status.set("Operation failed")
                    messagebox.showerror("File updater", str(value))
                    self._refresh_log()
                elif kind == "done":
                    self._busy(False)
                    if self.on_done:
                        self.on_done(value)
                    self._refresh_log()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def preview(self) -> None:
        source = Path(self.source.get())
        destination = Path(self.destination.get())
        self.status.set("Searching for matching files…")
        self._start(
            lambda: discover(destination, source, self.state_dir),
            self._show_preview,
        )

    def _show_preview(self, result: object) -> None:
        assert isinstance(result, Plan)
        self.plan = result
        self.tree.delete(*self.tree.get_children())
        rows = [
            (result.source, "SOURCE"),
            *((path, "TARGET") for path in result.targets),
        ]
        for path, role in rows:
            try:
                self.tree.insert("", "end", values=file_row(path, role))
            except OSError:
                self.tree.insert("", "end", values=(role, "?", "?", str(path)))
        self.status.set(f"{len(result.targets)} matching files")
        self._busy(False)

    def _report_progress(self, message: str) -> None:
        self.events.put(("status", message))

    def apply_update(self) -> None:
        if self.plan is None or not self.plan.targets:
            messagebox.showinfo("File updater", "No matching files to update.")
            return
        if not messagebox.askyesno(
            "Confirm update",
            f"Replace up to {len(self.plan.targets)} files?\n"
            "Originals will be backed up for undo.",
        ):
            return
        plan = self.plan
        self.status.set("Updating files…")
        self._start(
            lambda: update_files_recursively(
                plan.root, plan.source, self.state_dir, self._report_progress, plan=plan
            ),
            self._show_result,
        )

    def undo(self) -> None:
        run_id = latest_run(self.state_dir)
        if run_id is None:
            messagebox.showinfo("File updater", "No previous run available to undo.")
            return
        if not messagebox.askyesno(
            "Confirm undo", f"Undo run {run_id}?\nChanged files will be skipped."
        ):
            return
        self.status.set("Restoring files…")
        self._start(
            lambda: undo_run(self.state_dir, run_id, self._report_progress),
            self._show_result,
        )

    def _show_result(self, result: object) -> None:
        assert isinstance(result, Result)
        self.plan = None  # Require a fresh preview before another update.
        self._busy(False)
        self.status.set(
            f"Run {result.run_id}: changed/restored={result.changed}, errors={len(result.errors)}"
        )
        if result.errors:
            messagebox.showwarning(
                "Completed with errors", "\n".join(result.errors[:12])
            )
        else:
            messagebox.showinfo("Complete", self.status.get())

    def _refresh_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", recent_log(self.state_dir))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    commands = {"gui", "update", "undo"}
    if not argv:
        argv = ["gui"]
    elif argv[0] not in commands and not argv[0].startswith("-"):
        argv.insert(0, "update")  # Accept the original: script.py DST SRC

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    gui = sub.add_parser("gui", help="Launch the Tkinter GUI")
    update = sub.add_parser("update", help="Preview and replace same-named files")
    undo = sub.add_parser("undo", help="Restore files from a run's backups")
    for item in (gui, update, undo):
        item.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    update.add_argument("dst", type=Path)
    update.add_argument("src", type=Path)
    update.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    undo.add_argument("--run-id", help="Default: most recent undoable run")
    undo.add_argument("-y", "--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args(argv)
    try:
        if args.command == "gui":
            UpdaterGUI(args.state_dir).mainloop()
            return 0
        if args.command == "update":
            plan = discover(args.dst, args.src, args.state_dir)
            show_plan(plan)
            if not plan.targets:
                return 0
            if (
                not args.yes
                and input("Proceed with replacement? [y/N] ").strip().lower() != "y"
            ):
                print("Cancelled.")
                return 0
            result = update_files_recursively(
                args.dst, args.src, args.state_dir, print, plan=plan
            )
        else:
            run_id = args.run_id or latest_run(args.state_dir)
            if run_id is None:
                raise ValueError("No runs available to undo")
            print(f"Undo run {run_id}")
            if (
                not args.yes
                and input("Restore originals? [y/N] ").strip().lower() != "y"
            ):
                print("Cancelled.")
                return 0
            result = undo_run(args.state_dir, run_id, print)
        return 1 if result.errors else 0
    except (OSError, ValueError, tk.TclError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
