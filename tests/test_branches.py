"""`nixin branches` offline: list, merge (object-store merge, built from a detached
worktree, then main fast-forwarded) and prune, on test_apply's Machine (a real git
workspace, a bare hub, fake nix)."""

# ruff: noqa: F811 -- `m` is test_apply's fixture, imported; the tests take it by name

from __future__ import annotations

import subprocess

from test_apply import HOST, git, m, pytestmark, run  # noqa: F401

from nixin.apply import build


def commit(repo, name: str, text: str, msg: str | None = None) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", msg or f"{name}: {text.strip()}")
    return git(repo, "rev-parse", "HEAD")


def branch(m, name: str, *files: tuple[str, str], base: str = "main") -> str:
    """A branch off `base` with one commit per (file, text); the workspace goes back."""
    here = git(m.ws, "branch", "--show-current")
    git(m.ws, "switch", "-q", "-c", name, base)
    sha = ""
    for f, t in files:
        sha = commit(m.ws, f, t)
    git(m.ws, "switch", "-q", here)
    return sha


def hub(m, name: str = "origin"):
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(m.hub)], check=True)
    git(m.ws, "remote", "add", name, str(m.hub))
    git(m.ws, "push", "-q", name, "main")
    git(m.ws, "remote", "set-head", name, "main")
    git(m.ws, "branch", "-q", "--set-upstream-to", f"{name}/main", "main")


def builds(m) -> int:
    return m.count("nix", "build")


def worktrees(m) -> list[str]:
    out = git(m.ws, "worktree", "list", "--porcelain")
    return [ln.split(" ", 1)[1] for ln in out.splitlines() if ln.startswith("worktree ")]


# --- list ---------------------------------------------------------------------------------


def test_list(m, capsys):
    hub(m)
    old = branch(m, "old", ("o", "1\n"))
    git(m.ws, "merge", "-q", "--no-ff", "-m", "merge old", "old")
    feat = branch(m, "feat", ("a", "1\n"), ("b", "2\n"))
    branch(m, "clash", ("c", "x\n"))
    commit(m.ws, "c", "main's\n")  # main now conflicts with clash
    git(m.ws, "push", "-q", "origin", "feat")
    git(m.ws, "branch", "-q", "-D", "feat")  # feat is on the hub only now
    d = run(capsys, m, "branches")
    assert d["into"] == "main" and d["hub"] == "origin"
    assert d["counts"] == {"unmerged": 2, "merged": 1}
    by = {b["name"]: b for b in d["branches"]}
    assert set(by) == {"old", "feat", "clash"}
    assert [b["merged"] for b in d["branches"]] == [False, False, True]  # unmerged first
    f = by["feat"]
    assert f["where"] == "hub" and f["commit"] == feat and f["hub_commit"] == feat
    assert (f["ahead"], f["merged"], f["merges_clean"]) == (2, False, True)
    assert f["files"] == ["a", "b"] and f["files_total"] == 2
    c = by["clash"]
    assert c["merges_clean"] is False and c["conflicts"] == ["c"] and c["behind"] == 1
    o = by["old"]
    assert o["merged"] and o["commit"] == old and o["files"] == [] and o["merges_clean"] is None
    text = run(capsys, m, "branches", json_=False)
    assert "2 unmerged, 1 merged" in text
    assert "conflict  clash" in text and "conflicts" in text and "prunable  1: old" in text


def test_usage(m, capsys):
    assert run(capsys, m, "branches", "frob")["error"]["code"] == "usage"
    assert run(capsys, m, "branches", "merge")["error"]["code"] == "usage"
    assert run(capsys, m, "branches", "prune", "x")["error"]["code"] == "usage"
    assert run(capsys, m, "branches", "list", "--push")["error"]["code"] == "usage"
    assert run(capsys, m, "branches", "prune", "--no-build")["error"]["code"] == "usage"
    d = run(capsys, m, "branches", "merge", "nope", "--no-build")
    assert d["error"]["code"] == "unknown-branch"
    assert run(capsys, m, "branches", "--into", "nope")["error"]["code"] == "unknown-branch"


# --- merge --------------------------------------------------------------------------------


def test_merge_from_another_branch_with_a_dirty_tree(m, capsys):
    a = branch(m, "a", ("fa", "1\n"), ("fa2", "2\n"))
    b = branch(m, "b", ("fb", "1\n"))
    branch(m, "work", ("w", "1\n"))
    git(m.ws, "switch", "-q", "work")
    (m.ws / "w").write_text("uncommitted\n")
    old = git(m.ws, "rev-parse", "main")
    d = run(capsys, m, "branches", "merge", "a", "b")
    assert d["ok"] and d["old"] == old and d["new"] == git(m.ws, "rev-parse", "main")
    assert [x["branch"] for x in d["merged"]] == ["a", "b"]
    assert d["merged"][0]["commits"] == 2 and d["merged"][0]["files"] == ["fa", "fa2"]
    # two merge commits, the hub's --no-ff style, first parent along main
    first = git(m.ws, "rev-parse", "main^1")
    assert git(m.ws, "rev-parse", "main^2") == b and git(m.ws, "rev-parse", f"{first}^2") == a
    assert git(m.ws, "rev-parse", f"{first}^1") == old
    assert git(m.ws, "log", "-1", "--format=%s", first) == "Merge branch 'a'"
    assert "* fa: 1" in git(m.ws, "log", "-1", "--format=%b", first)
    # built once, from a worktree that is gone again; the commit cache knows the out path
    assert builds(m) == 1 and worktrees(m) == [str(m.ws)]
    assert d["build"]["host"] == HOST
    assert build.PlanCache().by_commit(HOST, d["new"])["out"] == d["build"]["out"]
    # the workspace is untouched
    assert git(m.ws, "branch", "--show-current") == "work"
    assert (m.ws / "w").read_text() == "uncommitted\n"
    assert any("git switch main" in w for w in d["warnings"])
    # again: nothing to do
    d = run(capsys, m, "branches", "merge", "a")
    assert d["merged"] == [] and d["skipped"][0]["reason"] == "main already contains it"


def test_merge_dry_run_and_no_build(m, capsys):
    branch(m, "a", ("fa", "1\n"))
    old = git(m.ws, "rev-parse", "main")
    d = run(capsys, m, "branches", "merge", "a", "--dry-run")
    assert d["dry_run"] and d["merged"][0]["merge"] is None and d["new"] == old
    assert git(m.ws, "rev-parse", "main") == old and builds(m) == 0
    text = run(capsys, m, "branches", "merge", "a", "--no-build", json_=False)
    assert "built     no (--no-build)" in text and builds(m) == 0
    assert git(m.ws, "rev-parse", "main") != old


def test_merge_on_main_moves_the_files(m, capsys):
    branch(m, "a", ("fa", "1\n"))
    (m.ws / "untracked").write_text("x")
    d = run(capsys, m, "branches", "merge", "a", "--no-build")
    assert d["ok"] and git(m.ws, "rev-parse", "HEAD") == d["new"]
    assert (m.ws / "fa").read_text() == "1\n" and git(
        m.ws, "status", "--porcelain"
    ) == "?? untracked"
    assert not any("git switch" in w for w in d["warnings"])


def test_conflict_changes_nothing(m, capsys):
    branch(m, "ok", ("fo", "1\n"))
    branch(m, "clash", ("c", "theirs\n"))
    commit(m.ws, "c", "ours\n")
    old = git(m.ws, "rev-parse", "main")
    d = run(capsys, m, "branches", "merge", "ok", "clash")
    assert d["error"]["code"] == "merge-conflict"
    assert d["error"]["branch"] == "clash" and d["error"]["files"] == ["c"]
    assert "(after ok)" in d["error"]["message"]
    assert git(m.ws, "rev-parse", "main") == old and builds(m) == 0


def test_failed_build_changes_nothing(m, capsys):
    branch(m, "a", ("fa", "1\n"))
    git(m.ws, "switch", "-q", "a")
    m.fail_build = True
    old = git(m.ws, "rev-parse", "main")
    d = run(capsys, m, "branches", "merge", "a")
    assert d["error"]["code"] == "build-failed" and d["error"]["build"]
    assert "nothing was changed" in d["error"]["message"]
    assert git(m.ws, "rev-parse", "main") == old and worktrees(m) == [str(m.ws)]


def test_main_checked_out_elsewhere(m, capsys, tmp_path):
    branch(m, "a", ("fa", "1\n"))
    git(m.ws, "switch", "-q", "a")
    other = tmp_path / "main-wt"
    git(m.ws, "worktree", "add", "-q", str(other), "main")
    old = git(m.ws, "rev-parse", "main")
    d = run(capsys, m, "branches", "merge", "a")
    assert d["error"]["code"] == "branch-checked-out" and d["error"]["path"] == str(other)
    assert git(m.ws, "rev-parse", "main") == old and builds(m) == 0  # refused before the build
    # a stale entry (its directory gone) does not block
    subprocess.run(["rm", "-rf", str(other)], check=True)
    d = run(capsys, m, "branches", "merge", "a", "--no-build")
    assert d["ok"] and git(m.ws, "rev-parse", "main") == d["new"]


def test_main_moving_meanwhile_is_diverged(m, capsys, monkeypatch):
    from nixin.apply import branches as br

    branch(m, "a", ("fa", "1\n"))
    git(m.ws, "switch", "-q", "a")
    real = br._build

    def build_then_someone_commits(ctx, tip):
        git(m.ws, "switch", "-q", "main")
        commit(m.ws, "late", "1\n")
        git(m.ws, "switch", "-q", "a")
        return real(ctx, tip)

    monkeypatch.setattr(br, "_build", build_then_someone_commits)
    d = run(capsys, m, "branches", "merge", "a")
    assert d["error"]["code"] == "diverged"
    assert git(m.ws, "log", "-1", "--format=%s", "main") == "late: 1"


def test_push(m, capsys):
    hub(m)
    branch(m, "a", ("fa", "1\n"))
    git(m.ws, "switch", "-q", "a")
    d = run(capsys, m, "branches", "merge", "a", "--no-build", "--push")
    assert d["pushed"] and git(m.hub, "rev-parse", "main") == d["new"]


# --- prune --------------------------------------------------------------------------------


def test_prune(m, capsys, tmp_path):
    hub(m)
    other = tmp_path / "mbp13.git"  # another remote: never pruned
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(other)], check=True)
    git(m.ws, "remote", "add", "mbp13", str(other))
    for name in ("done1", "done2", "here", "held"):
        branch(m, name, (name, "1\n"))
        git(m.ws, "push", "-q", "origin", name)
        git(m.ws, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)
    git(m.ws, "push", "-q", "origin", "main")
    git(m.ws, "push", "-q", "mbp13", "main", "done1")
    git(m.ws, "fetch", "-q", "mbp13")
    branch(m, "wip", ("wip", "1\n"))
    git(m.ws, "push", "-q", "origin", "wip")
    git(m.ws, "worktree", "add", "-q", str(tmp_path / "held-wt"), "held")
    git(m.ws, "switch", "-q", "here")
    d = run(capsys, m, "branches", "prune", "--dry-run", "--hub")
    assert d["dry_run"] and git(m.ws, "rev-parse", "--verify", "-q", "done1")
    gone = {(x["branch"], x["where"]) for x in d["deleted"]}
    assert gone == {("done1", "local"), ("done2", "local"), ("done1", "hub"), ("done2", "hub")}
    d = run(capsys, m, "branches", "prune")  # local only
    assert {x["branch"] for x in d["deleted"]} == {"done1", "done2"} and d["unmerged"] == 1
    reasons = {x["branch"]: x["reason"] for x in d["skipped"]}
    assert reasons["here"] == "the workspace has it checked out"
    assert reasons["held"].startswith("checked out in ")
    left = git(m.ws, "branch", "--format=%(refname:short)").split()
    assert sorted(left) == ["held", "here", "main", "wip"]
    assert "done1" in git(m.hub, "branch", "--format=%(refname:short)")
    d = run(capsys, m, "branches", "prune", "--hub")
    assert {(x["branch"], x["where"]) for x in d["deleted"]} == {("done1", "hub"), ("done2", "hub")}
    assert {x["branch"] for x in d["skipped"] if x["where"] == "hub"} == {"here", "held"}
    on_hub = git(m.hub, "branch", "--format=%(refname:short)").split()
    assert sorted(on_hub) == ["held", "here", "main", "wip"]
    assert git(m.ws, "rev-parse", "--verify", "-q", "refs/remotes/mbp13/done1")
    assert "done1" in git(other, "branch", "--format=%(refname:short)")
    text = run(capsys, m, "branches", "prune", "--dry-run", json_=False)
    assert "0 would delete, 1 unmerged kept" in text
