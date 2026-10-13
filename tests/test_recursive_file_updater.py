from pathlib import Path

from oskit import recursive_file_updater as fu


def test_update_then_undo(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    src = tmp_path / "src" / "report.txt"
    src.parent.mkdir()
    src.write_text("NEW")
    old1, old2 = root / "a/report.txt", root / "b/report.txt"
    old1.write_text("old a")
    old2.write_text("old b")
    (root / "a" / "else.txt").write_text("untouched")
    state = tmp_path / "state"

    plan = fu.discover(root, src, state)
    assert plan.targets == (old1, old2)
    result = fu.update_files_recursively(root, src, state)
    assert (result.changed, result.skipped, result.errors) == (2, 0, ())
    assert old1.read_text() == old2.read_text() == "NEW"
    assert (state / "operations.log").exists()
    assert fu.latest_run(state) == result.run_id

    undo = fu.undo_run(state)
    assert (undo.changed, undo.errors) == (2, ())
    assert old1.read_text() == "old a"
    assert old2.read_text() == "old b"
    assert fu.latest_run(state) is None
    assert (root / "a/else.txt").read_text() == "untouched"


def test_undo_refuses_later_edits(tmp_path: Path) -> None:
    src = tmp_path / "file.txt"
    src.write_text("new")
    dest = tmp_path / "dst"
    dest.mkdir()
    target = dest / "file.txt"
    target.write_text("old")
    state = tmp_path / "state"
    result = fu.update_files_recursively(dest, src, state)
    target.write_text("externally edited")

    undo = fu.undo_run(state, result.run_id)
    assert undo.changed == 0
    assert len(undo.errors) == 1
    assert target.read_text() == "externally edited"
    assert (state / "runs" / result.run_id / "backups/000001.bin").read_text() == "old"


def test_skip_identical_source_and_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "file.txt"
    source.write_text("new")
    sub = root / "sub"
    sub.mkdir()
    existing = sub / "file.txt"
    existing.write_text("new")
    links = root / "links"
    links.mkdir()
    (links / "file.txt").symlink_to(existing)

    plan = fu.discover(root, source, tmp_path / "state")
    assert plan.targets == (existing,)
    result = fu.update_files_recursively(root, source, tmp_path / "state")
    assert (result.changed, result.skipped) == (0, 1)
    assert fu.latest_run(tmp_path / "state") is None


def test_failure_between_journal_and_replace_can_be_undone(
    tmp_path: Path, monkeypatch
) -> None:
    src = tmp_path / "source" / "file.txt"
    src.parent.mkdir()
    src.write_text("new")
    dst = tmp_path / "dest"
    dst.mkdir()
    target = dst / "file.txt"
    target.write_text("original")
    state = tmp_path / "state"

    def fail_copy_replace(src: Path, dst: Path) -> None:
        raise OSError("simulated failure")

    monkeypatch.setattr(fu, "copy_replace", fail_copy_replace)
    result = fu.update_files_recursively(dst, src, state)
    assert result.changed == 0
    assert len(result.errors) == 1
    assert fu.latest_run(state) == result.run_id
    undo = fu.undo_run(state)
    assert (undo.changed, undo.skipped, undo.errors) == (0, 1, ())
    assert target.read_text() == "original"
    assert fu.latest_run(state) is None


def test_original_cli_positional_compatibility(tmp_path: Path) -> None:
    src = tmp_path / "src" / "file.txt"
    src.parent.mkdir()
    src.write_text("new")
    dst = tmp_path / "dst"
    dst.mkdir()
    target = dst / "file.txt"
    target.write_text("old")
    state = tmp_path / "state"
    assert fu.main([str(dst), str(src), "--state-dir", str(state), "--yes"]) == 0
    assert target.read_text() == "new"
    assert fu.main(["undo", "--state-dir", str(state), "--yes"]) == 0
    assert target.read_text() == "old"


def test_preapproved_preview_does_not_touch_new_matches(tmp_path: Path) -> None:
    src = tmp_path / "src" / "file.txt"
    src.parent.mkdir()
    src.write_text("new")
    dst = tmp_path / "dst"
    dst.mkdir()
    original = dst / "file.txt"
    original.write_text("old")
    state = tmp_path / "state"

    plan = fu.discover(dst, src, state)
    late = dst / "late"
    late.mkdir()
    new_match = late / "file.txt"
    new_match.write_text("not approved")
    result = fu.update_files_recursively(dst, src, state, plan=plan)
    assert result.changed == 1
    assert original.read_text() == "new"
    assert new_match.read_text() == "not approved"
