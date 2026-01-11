# tests/test_file_renamer.py
import pytest
from pathlib import Path
from oskit.file_renamer import (
    parse_mapping,
    _parse_rule,
    apply_rules,
    build_plan,
    compute_final_plan,
    commit_plan,
)

# ---------------- Tests for pure functions ----------------

def test_parse_mapping():
    rules = parse_mapping(["a:b", "x:y"])
    assert rules == [("a", "b"), ("x", "y")]

def test_parse_rule_invalid():
    with pytest.raises(ValueError):
        _parse_rule("no-colon")
    with pytest.raises(ValueError):
        _parse_rule(":empty-old")

def test_apply_rules():
    rules = [("foo", "bar"), ("baz", "qux")]
    assert apply_rules("foo_file.txt", rules) == "bar_file.txt"
    assert apply_rules("baz_file.txt", rules) == "qux_file.txt"
    # No match returns original
    assert apply_rules("nochange.txt", rules) == "nochange.txt"

def test_build_plan(tmp_path):
    # tmp_path is a pytest fixture for temporary directories
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("dummy")
    f2.write_text("dummy")
    rules = [("a", "x"), ("b", "y")]
    files = [f1, f2]
    plan = build_plan(files, rules)
    expected = [(f1, tmp_path / "x.txt"), (f2, tmp_path / "y.txt")]
    assert plan == expected

def test_compute_final_plan(tmp_path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f3 = tmp_path / "c.txt"
    f1.write_text("x")
    f2.write_text("y")
    f3.write_text("z")
    plan = [(f1, f1), (f2, tmp_path / "b.txt"), (f3, tmp_path / "d.txt")]
    final_plan, unmatched, skipped = compute_final_plan(plan)
    assert final_plan == [(f3, tmp_path / "d.txt")]
    assert unmatched == 2  # f1->f1 + f2->b.txt (exists)
    assert skipped == 0


# ---------------- Tests for swap logic ----------------

def test_commit_plan_swap(tmp_path):
    # Create two files to swap
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A")
    b.write_text("B")

    plan = [(a, b), (b, a)]
    renamed_steps = []

    # Inject mock rename function
    def mock_rename(src, dst):
        renamed_steps.append((str(src), str(dst)))
        # Simulate rename by updating Path (in-memory)
        src.write_text(dst.name)  # dummy content update

    commit_plan(plan, rename_func=mock_rename, verbose=False)

    # Should first stage to tmp, then rename to target
    tmp_names = [step[1] for step in renamed_steps if "__tmp__" in step[1]]
    assert len(tmp_names) == 2
    targets = [step[1] for step in renamed_steps if "__tmp__" not in step[1]]
    assert set(targets) == {str(a), str(b)}


# ---------------- Dry-run test ----------------

def test_dry_run(tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_text("data")
    rules = [("a", "x")]
    files = [f1]
    plan = build_plan(files, rules)
    final_plan, unmatched, skipped = compute_final_plan(plan)
    # Dry-run doesn't change files
    assert f1.exists()
    assert unmatched == 0
    assert skipped == 0
    assert final_plan[0][1].name == "x.txt"

