"""`nixin outdated`, offline: recorded evals of 19BK, a fake nix seam, a fake search.nixos.org.

Fixtures (tests/fixtures/outdated/, recorded on mbp13 on 2026-11-02 against ~/nixos):

    19BK-lock.json     outdated.nix on 19BK as locked (nixpkgs 3ec6363, 2026-10-23)
    19BK-target.json   the same with --override-input nixpkgs at nixos-unstable 0bb2eed
                       (2026-11-01); both trimmed to the packages that change, the pnames
                       search.nixos.org has several attributes for, and a sample of the rest
                       (the full lists give the same diff: 13 changed, 1 removed)
    metadata-nixos-unstable.json   `nix flake metadata --json github:nixos/nixpkgs/nixos-unstable`
    es-hits.json       search.nixos.org's hits for those pnames + `linux` (index 1c89083)

Live tests (NIXIN_LIVE=1) run against $NIXIN_CONFIG, read only.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from conftest import fixture

from nixin import configrepo
from nixin.cli import main
from nixin.configrepo import ConfigRepo, NixinError, trim_nix_error
from nixin.outdated import compare, render
from nixin.outdated import target as tgt

LOCK_REV = "3ec636362e1816bc2f0888fa0fa2c69f1bc9e504"
TARGET_REV = "0bb2eedc20e4739f29e8045034f2828ad7054f8f"
CONFIG_STORE = "/nix/store/4naz5g774cwbir62rw4fgdzrjv1m8rgc-source"

NETWORK_STDERR = """warning: unable to download 'https://api.github.com/repos/nixos/nixpkgs/commits/nixos-24.05': Could not connect to server (7) Failed to connect to api.github.com:443 over proxy 127.0.0.1 after 0 ms: Could not connect to server; retrying in 285 ms (attempt 1/5)
error:
       … while fetching the input 'github:nixos/nixpkgs/nixos-24.05'

       error: unable to download 'https://api.github.com/repos/nixos/nixpkgs/commits/nixos-24.05': Could not connect to server (7) Failed to connect to api.github.com:443 over proxy 127.0.0.1 after 0 ms: Could not connect to server
"""
BAD_REF_STDERR = """error:
       … while fetching the input 'github:nixos/nixpkgs/nixos-unstabel'

       error: unable to download 'https://api.github.com/repos/nixos/nixpkgs/commits/nixos-unstabel': HTTP error 422

       response body:

       {
         "message": "No commit found for SHA: nixos-unstabel",
         "status": "422"
       }
"""
# 19BK against nixos-26.05, live: a package the config lists is not in that release
TUXEDO_STDERR = """error:
       … while evaluating the attribute 'system'

       error: undefined variable 'tuxedo'
       at /home/master/nixos/configuration.nix:420:5:
          419|     python3
          420|     tuxedo
             |     ^
          421|     ffmpeg
"""


def recorded(name: str):
    return fixture(f"outdated/{name}")


class FakeNix:
    """The nix calls `outdated` makes, answered from the fixtures; records them.

    `metadata` maps a target flakeref to the `nix flake metadata` answer (a dict) or the
    stderr of its failure (a str); `target_error` makes the --override-input eval fail.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.metadata: dict[str, dict | str] = {
            "github:nixos/nixpkgs/nixos-unstable": recorded("metadata-nixos-unstable.json"),
        }
        self.target_error: str | None = None

    def evals(self) -> list[list[str]]:
        return [c for c in self.calls if "--apply" in c and "cfg:" in c[c.index("--apply") + 1]]

    def __call__(self, args: list[str], **kw) -> str:
        self.calls.append(args)
        code = kw.get("code", "eval-failed")
        if args[:3] == ["flake", "metadata", "--json"] and args[3].startswith("/"):
            return json.dumps({"path": CONFIG_STORE, "fingerprint": "fp-1", "revision": "abc"})
        if args[:3] == ["flake", "metadata", "--json"]:
            ans = self.metadata.get(args[3])
            if ans is None:
                raise AssertionError(f"no metadata recorded for {args[3]}")
            if isinstance(ans, str):
                raise NixinError(code, trim_nix_error(ans), stderr=ans)
            return json.dumps(ans)
        if args[-1] == "builtins.attrNames":
            return '["19BK","mbp13"]'
        if args[0] == "eval" and "--apply" in args:
            if "--override-input" in args:
                if self.target_error:
                    raise NixinError(
                        code, trim_nix_error(self.target_error), stderr=self.target_error
                    )
                return json.dumps(recorded("19BK-target.json"))
            return json.dumps(recorded("19BK-lock.json"))
        raise AssertionError(f"unexpected nix call: {args}")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "flake.nix").write_text("{ }")
    (tmp_path / "flake.lock").write_text(json.dumps(fixture("flake.lock.json")))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    r = ConfigRepo(tmp_path, nix=FakeNix())
    monkeypatch.setattr(
        configrepo.ConfigRepo, "locate", classmethod(lambda cls, explicit=None, nix=None: r)
    )
    return r


def run(capsys, *argv: str, json_: bool = True) -> tuple[int, dict | str]:
    rc = main([*(["--json"] if json_ else []), "--host", "19BK", "outdated", *argv])
    out, err = capsys.readouterr()
    if not json_:
        return rc, out
    assert err == ""
    return rc, json.loads(out)


# --- version order and inventories ---------------------------------------------------

# (a, b, builtins.compareVersions a b), checked against nix 2.34.9
NIX_ORDER = [
    ("1.0", "1.0.1", -1),
    ("2.3a", "2.3.1", -1),
    ("1.0pre", "1.0", -1),
    ("1.0pre1", "1.0pre2", -1),
    ("2.1.308", "2.1.316", -1),
    ("158.0.1", "159.0", -1),
    ("0.8.2-unstable-2026-09-09", "0.8.3", -1),
    ("1.2", "1-2", 0),
    ("26.08.2", "26.04.3", 1),
    ("1.0", "1.0.0", -1),
    ("2026.08.19", "2026.10.21", -1),
    ("153.6.0esr", "159.0", -1),
    ("1.0a", "1.0b", -1),
    ("3.16.0a1", "3.14.8", 1),
    ("2.36pre20261020_f3f18483", "2.34.9", 1),
    ("", "1", -1),
    ("6.12.0", "5.15.19", 1),
    ("1.0", "1.0a", -1),
    ("r12", "r9", 1),
    ("0.0.0-unstable", "0.0.0", 1),
]


@pytest.mark.parametrize("a, b, want", NIX_ORDER)
def test_compare_versions_is_nix(a, b, want):
    assert compare.compare_versions(a, b) == want
    assert compare.compare_versions(b, a) == -want


def test_inventory_counts_a_twice_listed_package_once():
    inv = compare.inventory(
        [
            {"name": "btop-1.4.7", "pname": "btop", "version": "1.4.7"},
            {"name": "btop-1.4.7", "pname": "btop", "version": "1.4.7"},
            {"name": "fuse-2.9.9", "pname": "fuse", "version": "2.9.9"},
            {"name": "fuse-3.18.2", "pname": "fuse", "version": "3.18.2"},
            {"name": "nixos-rebuild", "pname": None, "version": None},
        ]
    )
    assert inv == {
        "btop": {"1.4.7"},
        "fuse": {"2.9.9", "3.18.2"},
        "nixos-rebuild": {"nixos-rebuild"},
    }


# --- lock mode: the diff ------------------------------------------------------------


def test_diff_of_the_recorded_bump():
    d = compare.diff(recorded("19BK-lock.json"), recorded("19BK-target.json"))
    rows = {(r["scope"], r["user"], r["pname"]): (r["from"], r["to"]) for r in d["changed"]}
    assert len(rows) == 13
    assert rows[("system", None, "firefox")] == ("158.0.1", "159.0")
    assert rows[("system", None, "wireplumber")] == ("0.5.18", "0.5.19")
    assert rows[("home", "brucking", "harper")] == ("2.13.0", "3.0.0")
    assert rows[("home", "brucking", "yt-dlp")] == ("2026.08.19", "2026.10.21")
    assert all(r["direction"] == "up" for r in d["changed"])
    # fuse 2.9.9 + 3.18.2 -> 3.18.2: one removal, 3.18.2 itself is not a change
    assert d["removed"] == [{"pname": "fuse", "version": "2.9.9", "scope": "system", "user": None}]
    assert d["added"] == []
    assert [(s["scope"], s["user"], s["changed"], s["removed"]) for s in d["scopes"]] == [
        ("system", None, 4, 1),
        ("home", "brucking", 9, 0),
    ]
    assert d["total"] == sum(s["total"] for s in d["scopes"])


def test_diff_for_one_home_user():
    d = compare.diff(recorded("19BK-lock.json"), recorded("19BK-target.json"), home="brucking")
    assert {r["scope"] for r in d["changed"]} == {"home"} and len(d["changed"]) == 9
    assert d["removed"] == [] and [s["user"] for s in d["scopes"]] == ["brucking"]


def test_diff_pairs_versions_and_reports_renames():
    def pkg(pname, version):
        return {"name": f"{pname}-{version}", "pname": pname, "version": version}

    lock = {"system": [pkg("python3", "3.13.9"), pkg("python3", "3.12.12"), pkg("exa", "0.10")]}
    new = {"system": [pkg("python3", "3.14.8"), pkg("python3", "3.12.13"), pkg("eza", "0.23")]}
    d = compare.diff(lock, new)
    assert [(r["from"], r["to"]) for r in d["changed"]] == [
        ("3.12.12", "3.12.13"),
        ("3.13.9", "3.14.8"),
    ]
    assert [r["pname"] for r in d["removed"]] == ["exa"]
    assert [r["pname"] for r in d["added"]] == ["eza"]
    down = compare.diff({"system": [pkg("zsh", "5.9.2")]}, {"system": [pkg("zsh", "5.9.1")]})
    assert down["changed"][0]["direction"] == "down"


# --- --published: picking the channel's attribute ------------------------------------


def _hit(attr, pname, version, position):
    return {
        "package_attr_name": attr,
        "package_pname": pname,
        "package_pversion": version,
        "package_position": position,
    }


def test_pick_prefers_the_same_file_then_major_then_attr():
    # live on unstable (index 1c89083): an unrelated top-level `ark`, and qt5 next to qt6
    ark = [
        _hit("ark", "ark", "0.1.252", "pkgs/by-name/ar/ark/package.nix:50"),
        _hit("kdePackages.ark", "ark", "26.08.2", "pkgs/kde/gear/ark/default.nix:7"),
    ]
    mine = {compare.position_file("/nix/store/6nwz-source/pkgs/kde/gear/ark/default.nix:7")}
    assert mine == {"pkgs/kde/gear/ark/default.nix"}
    assert compare.pick("ark", ark, "26.08.2", mine)["package_attr_name"] == "kdePackages.ark"
    assert compare.pick("ark", ark, "26.08.2")["package_attr_name"] == "kdePackages.ark"  # major
    assert compare.pick("ark", ark)["package_attr_name"] == "ark"  # nothing known: the name
    qt = [
        _hit(
            "qt5.qtbase",
            "qtbase",
            "5.15.19",
            "pkgs/development/libraries/qt-5/modules/qtbase.nix:597",
        ),
        _hit(
            "qt6.qtbase",
            "qtbase",
            "6.12.0",
            "pkgs/development/libraries/qt-6/modules/qtbase/default.nix:379",
        ),
    ]
    assert compare.pick("qtbase", qt, "6.12.0")["package_attr_name"] == "qt6.qtbase"
    assert compare.pick("nope", qt) is None


def test_published_against_the_recorded_index():
    lock, es = recorded("19BK-lock.json"), recorded("es-hits.json")
    res = compare.published(
        lock, es["hits"], "unstable", "1c89083e451e891152365b799595cf7790a43325"
    )
    rows = {r["pname"]: r for r in res["changed"]}
    assert len(rows) == 13
    assert (rows["firefox"]["installed"], rows["firefox"]["published"]) == ("158.0.1", "159.0")
    assert rows["firefox"]["attr"] == "firefox" and rows["firefox"]["alternatives"] == 3
    assert rows["xwayland-satellite"]["published"] == "0.8.3"
    assert rows["harper"]["scope"] == "home" and rows["harper"]["user"] == "brucking"
    # newer-looking versions under other attributes are not "updates":
    # python3 3.16.0a1 (python316), git 20140128 (emacsPackages.git), nix 2.36pre…
    for pname in ("python3", "git", "ripgrep", "nix", "curl"):
        assert pname not in rows
    assert res["unpublished"] == ["casual-pre-loader", "csvmusic", "demmanager", "demoman"]
    linux = [h for h in es["hits"] if h["package_pname"] == "linux"]
    assert compare.kernel_published("7.2.12", linux) == {
        "from": "7.2.12",
        "to": "7.2.13",
        "latest": "7.3",
        "changed": True,
    }


# --- the target ------------------------------------------------------------------------


def test_nixpkgs_input_resolves_the_node(repo):
    np = tgt.nixpkgs_input(repo)
    assert (np.input, np.locked.node, np.locked.rev) == ("nixpkgs", "nixpkgs_2", LOCK_REV)
    assert np.original_ref == "github:nixos/nixpkgs/nixos-unstable"


def test_nixpkgs_input_by_repo_when_not_called_nixpkgs(tmp_path):
    lock = {
        "root": "root",
        "version": 7,
        "nodes": {
            "root": {"inputs": {"hm": "hm", "pkgs": "nixpkgs_3"}},
            "hm": {"locked": {"type": "github", "owner": "nix-community", "repo": "home-manager"}},
            "nixpkgs_3": {
                "locked": {"type": "github", "owner": "NixOS", "repo": "nixpkgs", "rev": "a" * 40},
                "original": {
                    "type": "github",
                    "owner": "NixOS",
                    "repo": "nixpkgs",
                    "ref": "nixos-26.05",
                },
            },
        },
    }
    (tmp_path / "flake.lock").write_text(json.dumps(lock))
    np = tgt.nixpkgs_input(ConfigRepo(tmp_path, nix=FakeNix()))
    assert (np.input, np.locked.node, np.original_ref) == (
        "pkgs",
        "nixpkgs_3",
        "github:NixOS/nixpkgs/nixos-26.05",
    )


@pytest.mark.parametrize(
    "original, ref",
    [
        (
            {"type": "github", "owner": "nixos", "repo": "nixpkgs", "ref": "nixos-unstable"},
            "github:nixos/nixpkgs/nixos-unstable",
        ),
        ({"type": "github", "owner": "nixos", "repo": "nixpkgs"}, "github:nixos/nixpkgs"),
        (
            {"type": "github", "owner": "o", "repo": "r", "ref": "feature/x"},
            "github:o/r?ref=feature%2Fx",
        ),
        (
            {"type": "tarball", "url": "https://channels.nixos.org/nixos-unstable/nixexprs.tar.xz"},
            "https://channels.nixos.org/nixos-unstable/nixexprs.tar.xz",
        ),
        ({"type": "indirect", "id": "nixpkgs"}, "flake:nixpkgs"),
        (
            {"type": "git", "url": "https://example.org/nixpkgs", "ref": "main"},
            "git+https://example.org/nixpkgs?ref=main",
        ),
    ],
)
def test_render_ref(original, ref):
    assert tgt.render_ref(original) == ref


def test_requested_targets(repo):
    np = tgt.nixpkgs_input(repo)
    assert tgt.requested(np, None, None) == ("github:nixos/nixpkgs/nixos-unstable", "lock")
    assert tgt.requested(np, "3ec6363", None) == ("github:nixos/nixpkgs/3ec6363", "nixpkgs")
    assert tgt.requested(np, "github:nixos/nixpkgs/nixos-26.05", None)[0].endswith("26.05")
    assert tgt.requested(np, None, tgt.channel_branch("26.05")) == (
        "github:nixos/nixpkgs/nixos-26.05",
        "channel",
    )
    assert tgt.channel_branch("unstable") == "nixos-unstable"
    assert tgt.channel_branch("nixos-unstable-small") == "nixos-unstable-small"
    assert tgt.channel_branch("stable", "26.05") == "nixos-26.05"
    with pytest.raises(NixinError) as e:
        tgt.channel_branch("bogus")
    assert e.value.code == "bad-channel"


def test_resolve_reads_the_locked_commit(repo):
    np = tgt.nixpkgs_input(repo)
    t = tgt.resolve(repo, np, "github:nixos/nixpkgs/nixos-unstable", "lock")
    assert (t.rev, t.short_rev, t.date) == (TARGET_REV, "0bb2eed", "2026-11-01")
    assert t.locked.startswith(f"github:nixos/nixpkgs/{TARGET_REV}?narHash=")
    tgt.resolve(repo, np, "github:nixos/nixpkgs/nixos-unstable", "lock", refresh=True)
    assert repo.nix.calls[-1][-1] == "--refresh"


@pytest.mark.parametrize(
    "stderr, code", [(NETWORK_STDERR, "network"), (BAD_REF_STDERR, "bad-channel")]
)
def test_resolve_failures(repo, stderr, code):
    repo.nix.metadata["github:nixos/nixpkgs/x"] = stderr
    with pytest.raises(NixinError) as e:
        tgt.resolve(repo, tgt.nixpkgs_input(repo), "github:nixos/nixpkgs/x", "nixpkgs")
    assert e.value.code == code


# --- through the CLI --------------------------------------------------------------------


def test_lock_mode_document(repo, capsys):
    rc, doc = run(capsys)
    assert rc == 0 and doc["ok"] and doc["verb"] == "outdated" and doc["host"] == "19BK"
    assert doc["mode"] == "lock" and doc["up_to_date"] is False
    assert doc["lock"]["node"] == "nixpkgs_2" and doc["lock"]["rev"] == LOCK_REV
    t = doc["target"]
    assert (t["from"], t["flakeref"], t["rev"], t["date"]) == (
        "lock",
        "github:nixos/nixpkgs/nixos-unstable",
        TARGET_REV,
        "2026-11-01",
    )
    assert t["days_newer"] == 8.9
    assert doc["count"] == len(doc["changed"]) == 13 and len(doc["removed"]) == 1
    assert doc["kernel"] == {"from": "7.2.12", "to": "7.2.14", "changed": True}
    assert set(doc["changed"][0]) == {"pname", "from", "to", "scope", "user", "direction"}
    assert doc["commands"] == [
        f"nix flake update nixpkgs --flake {repo.path}",
        f"nixin plan --config {repo.path} --host 19BK",
        f"nixin apply --config {repo.path} --host 19BK",
    ]
    assert doc["notes"] == []
    # two package-list evals, never the toplevel; the target one overrides nixpkgs only
    lock_eval, target_eval = repo.nix.evals()
    assert "--override-input" not in lock_eval
    i = target_eval.index("--override-input")
    assert target_eval[i + 1] == "nixpkgs"
    assert target_eval[i + 2].startswith(f"github:nixos/nixpkgs/{TARGET_REV}?narHash=")
    assert "--no-write-lock-file" in target_eval
    assert all(c[2].endswith('#nixosConfigurations."19BK"') for c in repo.nix.evals())
    assert doc["cache"]["misses"] == ["outdated-lock", "outdated-target"]


def test_second_run_is_cached_and_refresh_is_not(repo, capsys):
    run(capsys)
    n = len(repo.nix.evals())
    _rc, doc = run(capsys)
    assert len(repo.nix.evals()) == n and doc["cache"]["hits"] == [
        "outdated-lock",
        "outdated-target",
    ]
    assert doc["timings"]["lock"] == doc["timings"]["target"] == 0.0
    _rc, doc = run(capsys, "--refresh")
    assert len(repo.nix.evals()) == n + 2 and doc["cache"]["hits"] == []
    assert any(c[-1] == "--refresh" for c in repo.nix.calls if c[:2] == ["flake", "metadata"])


def test_the_lock_already_at_the_target(repo, capsys):
    ref = "github:nixos/nixpkgs/3ec6363"
    repo.nix.metadata[ref] = {
        "locked": {
            "type": "github",
            "owner": "nixos",
            "repo": "nixpkgs",
            "rev": LOCK_REV,
            "lastModified": 1792773339,
        },
        "url": f"github:nixos/nixpkgs/{LOCK_REV}",
    }
    rc, doc = run(capsys, "--nixpkgs", "3ec6363")
    assert rc == 0 and doc["up_to_date"] is True and doc["changed"] == doc["removed"] == []
    assert doc["kernel"]["changed"] is False and doc["target"]["from"] == "nixpkgs"
    assert len(repo.nix.evals()) == 1  # no target eval
    assert doc["commands"] == [] and doc["notes"] == []
    _rc, text = run(capsys, "--nixpkgs", "3ec6363", json_=False)
    assert "the lock already has 3ec6363: nothing would change" in text


def test_another_branch_is_pinned_with_a_note(repo, capsys):
    rev = "9bb52223f4f6a0cc606fd0852d8784f896c137d5"  # nixos-26.05 on 2026-11-01
    repo.nix.metadata["github:nixos/nixpkgs/nixos-26.05"] = {
        "locked": {
            "type": "github",
            "owner": "nixos",
            "repo": "nixpkgs",
            "rev": rev,
            "lastModified": 1793520000,
        },
        "url": f"github:nixos/nixpkgs/{rev}?narHash=sha256-x",
    }
    rc, doc = run(capsys, "--channel", "26.05")
    assert rc == 0 and doc["target"]["from"] == "channel"
    assert doc["target"]["flakeref"] == "github:nixos/nixpkgs/nixos-26.05"
    assert doc["commands"][0] == (
        f"nix flake lock {repo.path} --override-input nixpkgs github:nixos/nixpkgs/{rev}"
    )
    assert 'set inputs.nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05"' in doc["notes"][0]
    _rc, text = run(capsys, "--channel", "26.05", json_=False)
    assert "  # pin nixpkgs to exactly the commit compared\n  nix flake lock " in text


def test_a_target_the_config_does_not_evaluate_with(repo, capsys):
    repo.nix.target_error = TUXEDO_STDERR
    rc, doc = run(capsys)
    assert rc == 1 and doc["error"]["code"] == "eval-failed"
    assert "would break it" in doc["error"]["message"]
    assert "undefined variable 'tuxedo'" in doc["error"]["message"]


def test_network_failures(repo, capsys):
    repo.nix.target_error = NETWORK_STDERR  # an input fetch during the target eval
    rc, doc = run(capsys)
    assert rc == 1 and doc["error"]["code"] == "network"
    repo.nix.metadata["github:nixos/nixpkgs/nixos-unstable"] = NETWORK_STDERR
    rc, doc = run(capsys)
    assert rc == 1 and doc["error"]["code"] == "network"
    assert "cannot fetch github:nixos/nixpkgs/nixos-unstable" in doc["error"]["message"]


@pytest.mark.parametrize(
    "argv, code, rc",
    [
        (["--nixpkgs", "x", "--channel", "unstable"], "usage", 2),
        (["--published", "--nixpkgs", "3ec6363"], "usage", 2),
        (["extra"], "usage", 2),
        (["--home", "nobody"], "unknown-user", 1),
        (["--channel", "bogus"], "bad-channel", 1),
    ],
)
def test_errors(repo, capsys, argv, code, rc):
    got, doc = run(capsys, *argv)
    assert got == rc and doc["ok"] is False and doc["error"]["code"] == code


def test_unknown_host(repo, capsys):
    rc = main(["--json", "--host", "nope", "outdated"])
    doc = json.loads(capsys.readouterr().out)
    assert rc == 1 and doc["error"]["code"] == "unknown-host"


def test_lock_mode_text(repo, capsys):
    rc, text = run(capsys, json_=False)
    assert rc == 0
    lines = text.splitlines()
    assert lines[0] == (
        "19BK: nixpkgs (github:nixos/nixpkgs/nixos-unstable) 3ec6363 (2026-10-23) → "
        "0bb2eed (2026-11-01), 8.9 days newer"
    )
    assert "system: 4 of " in text and "would change (1 removed)" in text
    assert "home brucking: 9 of " in text
    assert re.search(r"^  firefox +158\.0\.1 → 159\.0$", text, re.MULTILINE)
    assert re.search(r"^  fuse +2\.9\.9 +\(removed\)$", text, re.MULTILINE)
    assert re.search(
        r"^13 of \d+ packages would change; kernel 7\.2\.12 → 7\.2\.14$", text, re.MULTILINE
    )
    assert f"  nix flake update nixpkgs --flake {repo.path}" in text and "next:" in text
    # the arrows line up within a table
    table = [ln for ln in lines if " → " in ln and ln.startswith("  ") and "# " not in ln]
    assert len({ln.index("→") for ln in table[:4]}) == 1


# --- --published through the CLI -------------------------------------------------------


@pytest.fixture
def es_hits(transport):
    """search.nixos.org: the recorded aliases and count; each terms query answered with the
    recorded hits for the pnames it asks for."""
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    transport.add("POST", r"/_count$", fixture_file="es/count.json")
    hits = recorded("es-hits.json")["hits"]
    lock = recorded("19BK-lock.json")
    names = sorted(set(compare.pnames(lock)) | {"linux"})
    for i in range(0, len(names), 50):
        batch = names[i : i + 50]
        payload = {"hits": {"hits": [{"_source": h} for h in hits if h["package_pname"] in batch]}}
        transport.add(
            "POST",
            r"/_search$",
            payload,
            when=lambda b, batch=batch: (
                b["query"]["bool"]["filter"][1]["terms"]["package_pname"] == batch
            ),
        )
    return transport


def test_published_document(repo, capsys, es_hits):
    rc, doc = run(capsys, "--published")
    assert rc == 0 and doc["mode"] == "published", doc
    t = doc["target"]
    assert (t["source"], t["channel"], t["short_rev"]) == (
        "search.nixos.org",
        "unstable",
        "1c89083",
    )
    names = len(set(compare.pnames(recorded("19BK-lock.json"))) | {"linux"})
    assert t["queries"] == -(-names // 50) == es_hits.count("/_search")
    assert doc["count"] == 13 and doc["added"] == doc["removed"] == []
    assert set(doc["changed"][0]) >= {"pname", "installed", "published", "channel", "rev"}
    assert doc["kernel"]["to"] == "7.2.13" and doc["kernel"]["latest"] == "7.3"
    assert len(repo.nix.evals()) == 1  # the lock side only; no target, no metadata
    assert not any(
        c[:2] == ["flake", "metadata"] and not c[3].startswith("/") for c in repo.nix.calls
    )
    assert doc["commands"][0] == f"nixin outdated --config {repo.path} --host 19BK"
    assert t["cached"] is False
    _rc, again = run(capsys, "--published")
    assert again["target"]["cached"] is True and again["changed"] == doc["changed"]


def test_published_text(repo, capsys, es_hits):
    rc, text = run(capsys, "--published", json_=False)
    assert rc == 0
    assert text.splitlines()[0] == (
        "19BK: installed (nixpkgs 3ec6363 (2026-10-23)) vs search.nixos.org unstable (index 1c89083)"
    )
    assert re.search(r"^  harper +2\.13\.0 → 3\.0\.0$", text, re.MULTILINE)
    assert "kernel 7.2.12 → 7.2.13, latest 7.3; 4 not on search.nixos.org" in text


def test_published_without_network(repo, capsys, transport):
    rc, doc = run(capsys, "--published")
    assert rc == 1 and doc["error"]["code"] == "network"


def test_channel_stable_asks_the_index_for_the_newest_release(repo, capsys, transport):
    transport.add("GET", r"/_cat/aliases", fixture_file="es/aliases.json")
    repo.nix.metadata["github:nixos/nixpkgs/nixos-26.05"] = BAD_REF_STDERR  # stop after resolving
    _rc, doc = run(capsys, "--channel", "stable")
    assert doc["error"]["code"] == "bad-channel" and "nixos-26.05" in doc["error"]["message"]


def test_render_is_total_on_a_minimal_document():
    doc = {
        "mode": "lock",
        "lock": {"input": "nixpkgs", "short_rev": None, "date": None},
        "target": {"flakeref": "x", "short_rev": None, "date": None},
        "scopes": [],
        "changed": [],
        "added": [],
        "removed": [],
        "kernel": {"from": None},
        "count": 0,
        "total": 0,
        "commands": [],
    }
    assert "0 of 0 packages would change; kernel unknown" in render.outdated(doc, "h")


# --- live ---------------------------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("NIXIN_LIVE") != "1", reason="set NIXIN_LIVE=1 to run nix")


@live
@pytest.mark.parametrize("mode", [[], ["--published"]], ids=["lock", "published"])
def test_live_outdated(capsys, mode):
    repo = ConfigRepo.locate()
    lock_before = (repo.path / "flake.lock").read_text()
    for host in repo.hosts():
        rc = main(["--json", "--host", host, "outdated", "--config", str(repo.path), *mode])
        doc = json.loads(capsys.readouterr().out)
        assert rc == 0, doc
        assert doc["kernel"]["from"] and doc["total"] > 0
        assert doc["count"] == len(doc["changed"])
    assert (repo.path / "flake.lock").read_text() == lock_before  # never writes the lock
    assert Path(repo.path / "flake.nix").is_file()
