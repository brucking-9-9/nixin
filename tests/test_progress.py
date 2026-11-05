"""The build tracker (apply/progress.py) on canned `@nix` lines, the status line and the
reporter, and the process seam's stop path: the `Cancel` token, the stderr sink, the
process groups and the SIGINT scope (apply/proc.py). No nix, no network."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import threading
import time

import pytest
from conftest import FIXTURES

from nixin.apply import proc, progress
from nixin.configrepo import NixinError

EVENTS = FIXTURES / "apply" / "build-events.jsonl"
SYSTEM_PATH = "/nix/store/0a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p-system-path.drv"
DBUS = "/nix/store/9z8y7x6w5v4u3t2s1r0q9p8o7n6m5l4k-unit-dbus.service.drv"


def events() -> list[str]:
    return EVENTS.read_text().splitlines(keepends=True)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def feed(tracker: progress.Tracker, lines, clock: Clock | None = None, step: float = 1.0):
    """Feed lines, the clock advancing `step` per event; returns the readable text."""
    out = []
    for ln in lines:
        text = tracker.feed_line(ln)
        if text:
            out.append(text)
        if clock is not None:
            clock.t += step
    return "".join(out)


# --- the parser -------------------------------------------------------------------------------


def test_will_be_built_list_and_fetch_list():
    t = progress.Tracker(local_host="19BK")
    feed(t, events()[:10])
    snap = t.snapshot()
    assert snap["total"] == 3 and snap["counts"]["queued"] == 3
    assert [d["name"] for d in snap["drvs"]] == [
        "system-path",
        "unit-dbus.service",
        "nixos-system-mbp13-26.11.20261023.3ec6363",
    ]
    assert snap["downloads"]["expected"] == 2
    assert snap["downloads"]["bytes_expected"] == int(1.5 * 2**20)
    assert snap["focus"] is None and snap["eta_s"] is None


def test_build_start_log_phase_and_stop():
    clock = Clock()
    t = progress.Tracker(local_host="19BK", clock=clock)
    lines = events()
    i = next(i for i, ln in enumerate(lines) if '"id":20,"level":3' in ln)
    feed(t, lines[: i + 5], clock)  # through the log lines of system-path
    snap = t.snapshot()
    sp = snap["drvs"][0]
    assert sp["status"] == "building" and sp["host"] == "19BK" and sp["phase"] == "buildPhase"
    assert sp["last_log_line"] == "building the system path"  # ANSI and \r gone
    assert sp["seconds"] == 5.0
    assert snap["counts"] == {"queued": 2, "building": 1, "done": 0, "failed": 0, "skipped": 0}
    assert snap["progress"] == [0, 3, 1, 0]
    assert snap["focus"]["name"] == "system-path"
    assert snap["focus"]["lines"] == [
        "created 1234 symlinks in user environment",
        "building the system path",
    ]
    j = next(j for j, ln in enumerate(lines) if ln.startswith('@nix {"action":"stop","id":20}'))
    feed(t, lines[i + 5 : j + 2], clock)
    snap = t.snapshot()
    assert snap["drvs"][0]["status"] == "done" and snap["counts"]["done"] == 1
    assert snap["progress"] == [1, 3, 0, 0]
    assert t.durations.get("system-path") is not None


def test_failed_build_and_skipped_dependents():
    t = progress.Tracker(local_host="19BK")
    text = feed(t, events())
    t.finish("failed")
    snap = t.snapshot()
    by = {d["name"]: d for d in snap["drvs"]}
    dbus = by["unit-dbus.service"]
    assert dbus["status"] == "failed" and dbus["host"] == "mbp13"
    assert dbus["last_log_line"] == "make: *** [all] Error 2"
    assert by["nixos-system-mbp13-26.11.20261023.3ec6363"]["status"] == "skipped"
    assert snap["counts"] == {"queued": 0, "building": 0, "done": 1, "failed": 1, "skipped": 1}
    assert snap["focus"]["name"] == "unit-dbus.service" and snap["focus"]["status"] == "failed"
    assert snap["focus"]["lines"] == ["configuring", "make: *** [all] Error 2"]
    assert snap["errors"] == 2 and snap["state"] == "failed"
    # the readable form: what nix prints without the JSON log format
    assert text.startswith("these 3 derivations will be built:\n  /nix/store/0a1b")
    assert f"building '{SYSTEM_PATH}'\n" in text
    assert "copying path '/nix/store/c0w5ay" in text
    assert "evaluating file" not in text and "created 1234 symlinks" not in text
    err = [ln for ln in text.splitlines() if "builder for" in ln]
    assert err and err[0].startswith("\x1b[31;1merror:\x1b[0m builder for")
    summary = t.summary()
    assert summary["failed"] == ["unit-dbus.service"] and summary["state"] == "failed"
    assert summary["downloads"] == {"done": 2, "bytes_done": 1048576 + 524288}


def test_downloads_count_paths_and_bytes():
    t = progress.Tracker(local_host="19BK")
    lines = events()
    i = next(i for i, ln in enumerate(lines) if '"fields":[262144,524288,0,0]' in ln)
    feed(t, lines[: i + 1])
    d = t.snapshot()["downloads"]
    assert d == {
        "expected": 2,
        "done": 1,
        "running": 1,
        "bytes_done": 1048576 + 262144,
        "bytes_expected": 1572864,
    }


def test_eta_from_durations_history(tmp_path):
    hist = progress.Durations(tmp_path / "durations.json")
    hist.note("system-path-1.0", 20.0)
    hist.note("unit-dbus.service", 10.0)
    hist.save()
    clock = Clock()
    t = progress.Tracker(durations=progress.Durations(tmp_path / "durations.json"), clock=clock)
    lines = events()
    i = next(i for i, ln in enumerate(lines) if '"id":20,"level":3' in ln)
    feed(t, lines[: i + 1], clock)  # system-path just started
    snap = t.snapshot()
    # known: system-path 20 s left (minus 1 s elapsed), dbus 10 s; the toplevel at the
    # average of the known (14.5); one job running
    assert snap["eta_known"] == [2, 3]
    assert snap["eta_s"] == round(19 + 10 + 14.5)
    assert snap["drvs"][0]["expected_s"] == 20
    j = next(j for j, ln in enumerate(lines) if ln.startswith('@nix {"action":"stop","id":20}'))
    feed(t, lines[i + 1 : j + 1], clock)  # system-path done after j - i seconds
    t.finish("done")
    saved = json.loads((tmp_path / "durations.json").read_text())
    assert saved["system-path"] == pytest.approx(0.7 * (j - i) + 0.3 * 20, abs=0.1)
    assert saved["unit-dbus.service"] == 10.0


def test_durations_key_and_drv_name():
    assert progress.drv_name(SYSTEM_PATH) == "system-path"
    assert progress.drv_name("/nix/store/abc-x.drv") == "abc-x"
    assert progress.duration_key("linux-7.2.14") == "linux"
    assert progress.duration_key("system-path") == "system-path"
    assert progress.duration_key("hello-2.12.3") == "hello"
    assert progress.host_of("ssh-ng://master@mbp13:22?x=1", "here") == "mbp13"
    assert progress.host_of("", "here") == "here"
    assert progress.host_of("[::1]", "here") == "::1"


def test_non_event_lines_pass_through():
    t = progress.Tracker(local_host="19BK")
    assert t.feed_line("building…\n") == "building…\n"
    assert t.feed_line("warning: Git tree is dirty") == "warning: Git tree is dirty\n"
    assert t.feed_line("@nix not json\n") == "@nix not json\n"
    assert t.feed_line('@nix {"action":"result","id":1,"type":105,"fields":[1,2,3,4]}\n') is None
    assert t.snapshot()["total"] == 0


def test_finish_cancelled_requeues_the_building_ones():
    t = progress.Tracker(local_host="19BK")
    lines = events()
    i = next(i for i, ln in enumerate(lines) if '"id":20,"level":3' in ln)
    feed(t, lines[: i + 1])
    t.finish("cancelled")
    s = t.summary()
    assert s["cancelled"] and s["state"] == "cancelled"
    assert s["counts"]["building"] == 0 and s["counts"]["queued"] == 3
    assert t.snapshot()["elapsed_s"] == t.summary()["duration_s"]  # the clock stopped


# --- the status line and the reporter -----------------------------------------------------------


def test_status_line():
    clock = Clock()
    t = progress.Tracker(local_host="19BK", clock=clock)
    lines = events()
    i = next(i for i, ln in enumerate(lines) if '"fields":["buildPhase"]' in ln)
    feed(t, lines[: i + 1], clock)
    line = progress.status_line(t.snapshot(), 200)
    assert line.startswith("[0/3 built, 1 building, 1/2 fetched 1.2 MiB] system-path: buildPhase")
    assert line.endswith(f" · {int(t.snapshot()['elapsed_s'])}s")
    assert len(progress.status_line(t.snapshot(), 40)) <= 40
    assert progress.status_line(progress.Tracker().snapshot()) == "[waiting for nix] · 0s"
    assert progress.human_time(3725) == "1h02m" and progress.human_time(65) == "1m05s"
    assert progress.human_bytes(1536) == "1.5 KiB"


def test_reporter_on_a_terminal_redraws_in_place():
    t = progress.Tracker(local_host="19BK")
    out = io.StringIO()
    r = progress.Reporter(t, tty=True, stream=out, tick=0)
    for ln in events()[:21]:  # through the second "copying path"
        r.line(t.feed_line(ln))
    text = out.getvalue()
    assert text.startswith("these 3 derivations will be built:\n")
    assert "\r\x1b[K[0/3 built, 0/2 fetched]" in text
    # a readable line after a status line erases it first
    assert "\r\x1b[Kcopying path '/nix/store/c0w5ayc0w5ayc0w5ayc0w5ayc0w5ay11" in text
    r.close()
    assert out.getvalue().endswith("\r\x1b[K")


def test_reporter_plain_prints_a_status_line_on_change():
    t = progress.Tracker(local_host="19BK")
    out = io.StringIO()
    r = progress.Reporter(t, tty=False, stream=out, tick=0, plain_every=0)
    for ln in events():
        r.line(t.feed_line(ln))
    r.close()
    lines = out.getvalue().splitlines()
    assert "\r" not in out.getvalue()
    status = [ln for ln in lines if ln.startswith("[")]
    assert status[0].startswith("[0/3 built, 0/2 fetched]")  # after the lists, not inside
    assert lines.index(status[0]) > lines.index(
        "  /nix/store/c0w5ayc0w5ayc0w5ayc0w5ayc0w5ay11-cowsay-3.8.4-man"
    )
    assert any(ln.startswith("[1/3 built") for ln in status)
    assert status[-1].startswith("[1/3 built, 1 failed, 2/2 fetched 1.5 MiB]")  # the final one
    assert f"building '{SYSTEM_PATH}'" in lines


def test_reporter_ticker_refreshes_without_events():
    t = progress.Tracker(local_host="19BK")
    feed(t, events()[:11])  # the lists and the first activity
    out = io.StringIO()
    r = progress.Reporter(t, tty=True, stream=out, tick=0.05)
    r.start()
    time.sleep(0.3)
    r.close()
    assert out.getvalue().count("[0/3 built") >= 1


# --- the process seam: sink, groups, cancel ------------------------------------------------------


def test_run_process_feeds_stderr_lines_through_the_sink():
    seen = []

    def sink(line: str):
        seen.append(line)
        return None if line.startswith("drop") else line.upper()

    p = proc.run_process(
        ["sh", "-c", "echo keep >&2; echo drop me >&2; echo out; echo more >&2"], on_stderr=sink
    )
    assert p.ok and p.out == "out\n"
    assert seen == ["keep\n", "drop me\n", "more\n"]
    assert p.err == "KEEP\nMORE\n"


def test_run_process_with_a_sink_and_stream_tees_what_the_sink_keeps(capsys):
    p = proc.run_process(
        ["sh", "-c", "echo a >&2; echo b >&2"],
        stream="stderr",
        on_stderr=lambda ln: ln if ln == "b\n" else None,
    )
    assert p.err == "b\n" and capsys.readouterr().err == "b\n"


def test_cancel_kills_the_process_group():
    tok = proc.Cancel()
    assert tok.stoppable and not tok.requested
    result = {}

    def run() -> None:
        result["p"] = proc.run_process(["sh", "-c", "sleep 60; echo late"], cancel=tok)

    th = threading.Thread(target=run)
    th.start()
    deadline = time.monotonic() + 5
    while not tok._procs and time.monotonic() < deadline:
        time.sleep(0.01)
    assert tok._procs, "the child never registered"
    child = tok._procs[0]
    assert os.getpgid(child.pid) == child.pid != os.getpgrp()  # its own group
    t0 = time.monotonic()
    assert tok.request()
    th.join(timeout=5)
    assert not th.is_alive() and time.monotonic() - t0 < 5
    p = result["p"]
    assert p.rc < 0 and tok.requested and p.out == ""
    with pytest.raises(NixinError) as e:
        tok.check()
    assert e.value.code == "cancelled"


def test_cancel_requested_before_the_start_kills_at_attach():
    tok = proc.Cancel()
    assert tok.request()
    t0 = time.monotonic()
    p = proc.run_process(["sleep", "60"], cancel=tok)
    assert p.rc < 0 and time.monotonic() - t0 < 5


def test_cancel_is_refused_while_shielded():
    tok = proc.Cancel()
    with tok.shielded("switch"):
        assert tok.step == "switch" and not tok.stoppable
        assert not tok.request()
        assert "switch" in tok.refused and not tok.requested
        with tok.shielded("inner"):
            pass
        assert tok.shield == "switch"  # the outer shield survives a nested one
    assert tok.stoppable
    assert tok.request()
    with pytest.raises(NixinError) as e, tok.shielded("pull"):
        pass
    assert e.value.code == "cancelled" and "before pull" in e.value.message


SHIELD_CHECK = """
import os, signal, subprocess, sys, time
from nixin.apply import proc
got = []
signal.signal(signal.SIGINT, lambda *_: got.append(1))
with proc.shielded():
    child = subprocess.Popen(["sleep", "5"], **({"process_group": 0} if proc._own_group(False, None) else {}))
try:
    assert os.getpgid(child.pid) == child.pid, "the child is not in its own group"
    os.killpg(os.getpgrp(), signal.SIGINT)  # Ctrl-C at the terminal: the whole foreground group
    time.sleep(0.3)
    assert got == [1], "our handler did not run"
    assert child.poll() is None, "the shielded child got the SIGINT"
finally:
    child.kill()
    child.wait()
print("shield ok")
"""


def test_shielded_children_get_their_own_group_and_survive_sigint():
    """The shield for apply's root steps: a Ctrl-C at the terminal (SIGINT to the foreground
    group) does not reach the child; nixin's own handler decides. Run in a process of its
    own group, so the SIGINT stays away from pytest."""
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in __import__("sys").path if p)}
    r = subprocess.run(
        [__import__("sys").executable, "-c", SHIELD_CHECK],
        capture_output=True,
        text=True,
        env=env,
        process_group=0,
        check=False,
    )
    assert r.returncode == 0 and r.stdout.strip() == "shield ok", r.stdout + r.stderr
    assert not proc._own_group(False, None)
    assert proc._own_group(False, proc.Cancel())
    assert not proc._own_group(True, proc.Cancel())  # a prompt stays in the foreground


def test_interrupts_turns_sigint_into_a_request(capsys):
    tok = proc.Cancel()
    with proc.interrupts(tok, "nixin plan"):
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
        assert tok.requested
    assert "nixin plan: stopping (Ctrl-C)" in capsys.readouterr().err
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_interrupts_says_why_when_refused(capsys):
    tok = proc.Cancel()
    with proc.interrupts(tok, "nixin apply"), tok.shielded("switch"):
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.05)
        assert not tok.requested
    assert "switch is running and cannot be stopped" in capsys.readouterr().err


def test_interrupts_is_a_no_op_off_the_main_thread():
    tok = proc.Cancel()
    before = signal.getsignal(signal.SIGINT)
    seen = {}

    def run() -> None:
        with proc.interrupts(tok):
            seen["handler"] = signal.getsignal(signal.SIGINT)

    th = threading.Thread(target=run)
    th.start()
    th.join()
    assert seen["handler"] is before


# --- the remote builder in the snapshot -------------------------------------------------------


def test_remote_block_in_the_snapshot_and_the_summary_and_the_host_alias():
    remote = {
        "mode": "big",
        "name": "mbp13",
        "builders": "ssh-ng://nixremote@100.97.50.80 x86_64-linux - 2 1 big-parallel ; "
        "ssh-ng://master@mbp13 x86_64-linux",
        "command": "mbp13-builders",
        "fell_back": False,
    }
    t = progress.Tracker(local_host="19BK", remote=remote)
    feed(t, events())
    snap = t.snapshot()
    assert snap["remote"] == remote and t.summary()["remote"] == remote
    # the fixture's remote build (ssh-ng://master@mbp13) is named after the remote block
    hosts = {d["name"]: d["host"] for d in snap["drvs"]}
    assert hosts["unit-dbus.service"] == "mbp13" and hosts["system-path"] == "19BK"
    t = progress.Tracker(local_host="19BK")
    feed(t, events())
    assert t.snapshot()["remote"] is None and t.summary()["remote"] is None


def test_where_phrases():
    from nixin.apply import builders

    base = {"name": "mbp13", "builders": "x", "command": "c", "fell_back": False}
    assert builders.where(None) == ""
    assert builders.where({**base, "mode": "big"}) == "building on mbp13 · big jobs"
    assert builders.where({**base, "mode": "all"}) == "building on mbp13 · everything"
    assert builders.where({**base, "mode": "split"}) == (
        "building on both (mbp13 takes any job) · split"
    )
    assert builders.where({**base, "mode": "big", "builders": None, "fell_back": True}) == (
        "mbp13 unreachable · building locally"
    )
    assert builders.spec_hosts("ssh://a@[fe80::1]:22 x ; @/etc/nix/machines ; ssh-ng://b") == [
        "fe80::1",
        "b",
    ]
