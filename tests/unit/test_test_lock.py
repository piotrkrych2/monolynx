"""Testy plugin/scripts/test_lock.py - kolejka testow miedzy worktree (MON-251).

Skrypt bierze wylaczny `flock` na `<git-common-dir>/monolynx/test.lock` i dopiero potem uruchamia
komende (argv po `--`, bez powloki). Wszystkie worktree jednego repo dziela ten sam plik, wiec
dwa przebiegi testow z roznych worktree nie biegna naraz na wspolnej bazie.

Obraz Dockera aplikacji (python:3.12-slim) nie ma prawdziwego `git`, wiec kluczowa logika
(sciezka blokady, kolejnosc, timeout, kody wyjscia, holder) jest testowana na atrapie `git`
(wykonywalny skrypt na poczatku PATH subprocessu), tak jak `sync-base` w test_sprint_run_tick.py.
Jedyny test na prawdziwym repo i worktree ma `skipif(shutil.which("git") is None)`.
Dwa "worktree" atrapy to dwa rozne cwd z ta sama sciezka common-dir.
"""

import contextlib
import fcntl
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "test_lock.py"

assert SCRIPT.exists(), f"Brak skryptu: {SCRIPT}"

_spec = importlib.util.spec_from_file_location("test_lock_under_test", SCRIPT)
assert _spec is not None and _spec.loader is not None
test_lock = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(test_lock)

FAKE_GIT = """#!/usr/bin/env bash
if [ "${FAKE_GIT_NOT_REPO:-0}" = "1" ]; then
  echo "fatal: not a git repository (or any of the parent directories): .git" >&2
  exit 128
fi
if [ "$1" = "-C" ]; then
  cd "$2" || exit 128
  shift 2
fi
if [ "$1" != "rev-parse" ]; then
  echo "git-stub: nieobslugiwane argumenty: $*" >&2
  exit 128
fi
shift
for arg in "$@"; do
  case "$arg" in
    --git-common-dir) printf '%s\\n' "$FAKE_GIT_COMMON_DIR" ;;
    --show-toplevel) pwd ;;
    *) echo "git-stub: nieobslugiwane argumenty: $arg" >&2; exit 128 ;;
  esac
done
"""

# Komenda testowa: dopisuje `<name>-start`/`<name>-end` do wspolnego logu, opcjonalnie zaznacza,
# ze wystartowala (marker), czeka na plik release i wychodzi zadanym kodem.
WORKER_SOURCE = """
import os
import sys
import time

name, log, marker, release, code = sys.argv[1:6]
with open(log, "a") as fh:
    fh.write(name + "-start\\n")
if marker != "-":
    open(marker, "w").close()
if release != "-":
    deadline = time.time() + 30
    while not os.path.exists(release) and time.time() < deadline:
        time.sleep(0.05)
with open(log, "a") as fh:
    fh.write(name + "-end\\n")
sys.exit(int(code))
"""

WAITING_RE = re.compile(r"^TEST-LOCK: waiting holder=(\d+)/(\S+)$")


@pytest.fixture
def sb(tmp_path):
    root = tmp_path.resolve()
    fakebin = root / "fakebin"
    fakebin.mkdir()
    git = fakebin / "git"
    git.write_text(FAKE_GIT)
    git.chmod(0o755)
    main = root / "main"
    common = main / ".git"
    common.mkdir(parents=True)
    (main / "sub").mkdir()
    alpha = root / "wt-alpha"
    beta = root / "wt-beta"
    outside = root / "outside"
    for directory in (alpha, beta, outside):
        directory.mkdir()
    worker = root / "worker.py"
    worker.write_text(WORKER_SOURCE)
    return SimpleNamespace(
        root=root,
        fakebin=fakebin,
        main=main,
        common=common,
        alpha=alpha,
        beta=beta,
        outside=outside,
        worker=worker,
        lock_file=common / "monolynx" / "test.lock",
        ran=root / "ran.marker",
    )


def make_env(sb, extra=None):
    env = {
        "PATH": f"{sb.fakebin}:/usr/bin:/bin:/usr/local/bin",
        "FAKE_GIT_COMMON_DIR": str(sb.common),
    }
    env.update(extra or {})
    return env


def run_lock(sb, cwd, args, env_extra=None, timeout=30):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd),
        env=make_env(sb, env_extra),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def py(code, *args):
    return [sys.executable, "-c", code, *args]


def worker_cmd(sb, name, log, marker="-", release="-", code=0):
    return [sys.executable, str(sb.worker), name, str(log), str(marker), str(release), str(code)]


def touch_cmd(path):
    return py(f"open({str(path)!r}, 'w').close()")


def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class HeldLock:
    """Proces test_lock.py trzymajacy blokade, dopoki test nie zwolni pliku `release`."""

    def __init__(self, sb, cwd, name, log):
        self.sb = sb
        self.cwd = cwd
        self.name = name
        self.log = log
        self.marker = sb.root / f"{name}.marker"
        self.release_file = sb.root / f"{name}.release"
        self.err_path = sb.root / f"{name}.err"
        self.proc = None

    def __enter__(self):
        cmd = worker_cmd(self.sb, self.name, self.log, self.marker, self.release_file)
        with self.err_path.open("w") as err:
            self.proc = subprocess.Popen(
                [sys.executable, str(SCRIPT), "--", *cmd],
                cwd=str(self.cwd),
                env=make_env(self.sb),
                stdout=subprocess.DEVNULL,
                stderr=err,
            )
        assert wait_for(lambda: self.marker.exists()), f"holder nie zdobyl blokady: {self.err_path.read_text()!r}"
        return self

    def release(self):
        self.release_file.touch()

    def __exit__(self, *exc):
        self.release()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


@contextmanager
def foreign_lock(sb, content):
    """Test sam trzyma flock jak inny proces, z dowolna trescia pliku holdera (bez udzialu skryptu)."""
    sb.lock_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(sb.lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.ftruncate(fd, 0)
        os.write(fd, content)
        yield sb.lock_file
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def lock_lines(stderr):
    return [line for line in stderr.splitlines() if line.startswith("TEST-LOCK:")]


@pytest.mark.unit
class TestLockPath:
    def test_main_checkout_and_worktree_share_one_lock_file(self, sb, monkeypatch):
        monkeypatch.setenv("PATH", make_env(sb)["PATH"])
        expected = str(sb.common / "monolynx" / "test.lock")

        monkeypatch.setenv("FAKE_GIT_COMMON_DIR", ".git")
        from_main = test_lock.lock_path(str(sb.main))
        monkeypatch.setenv("FAKE_GIT_COMMON_DIR", str(sb.common))
        from_worktree = test_lock.lock_path(str(sb.alpha))

        assert from_main == expected
        assert from_worktree == expected

    def test_relative_common_dir_from_subdirectory_resolves_against_cwd(self, sb):
        result = run_lock(sb, sb.main / "sub", ["--", *touch_cmd(sb.ran)], {"FAKE_GIT_COMMON_DIR": "../.git"})

        assert result.returncode == 0, result.stderr
        assert sb.ran.exists()
        assert sb.lock_file.is_file()

    def test_script_creates_missing_monolynx_directory_and_lock_file(self, sb):
        assert not (sb.common / "monolynx").exists()

        result = run_lock(sb, sb.alpha, ["--", *py("pass")])

        assert result.returncode == 0, result.stderr
        assert sb.lock_file.is_file()

    def test_holder_file_records_pid_and_worktree_of_the_running_process(self, sb):
        log = sb.root / "order.log"
        with HeldLock(sb, sb.alpha, "A", log) as held:
            data = json.loads(sb.lock_file.read_text())

            assert data == {"pid": held.proc.pid, "worktree": str(sb.alpha)}

    @pytest.mark.skipif(shutil.which("git") is None, reason="brak binarki git (obraz Dockera aplikacji)")
    def test_real_worktree_and_main_checkout_share_the_lock_file(self, tmp_path):
        repo = (tmp_path / "repo").resolve()
        worktree = (tmp_path / "linked").resolve()
        repo.mkdir()
        env = {**os.environ, "HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

        def git(*args, cwd):
            subprocess.run(["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True, timeout=30)

        git("init", "-q", cwd=repo)
        git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "--allow-empty", "-q", "-m", "x", cwd=repo)
        git("worktree", "add", "-q", "-b", "linked-branch", str(worktree), cwd=repo)

        expected = str(repo / ".git" / "monolynx" / "test.lock")
        assert test_lock.lock_path(str(repo)) == expected
        assert test_lock.lock_path(str(worktree)) == expected
        for cwd in (repo, worktree):
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--", *py("pass")],
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert result.stderr.splitlines() == ["TEST-LOCK: acquired"]


@pytest.mark.unit
class TestRunsCommand:
    @pytest.mark.parametrize("code", [0, 1, 7])
    def test_propagates_command_exit_code(self, sb, code):
        result = run_lock(sb, sb.alpha, ["--", *py(f"import sys; sys.exit({code})")])

        assert result.returncode == code, result.stderr

    def test_command_stdout_passes_through_unchanged(self, sb):
        result = run_lock(sb, sb.alpha, ["--", *py("import sys; print('hello-out'); print('cmd-err', file=sys.stderr)")])

        assert result.returncode == 0
        assert result.stdout == "hello-out\n"
        assert "cmd-err" in result.stderr.splitlines()
        assert "TEST-LOCK: acquired" in result.stderr.splitlines()

    def test_command_runs_without_shell(self, sb):
        tricky = "a b; $HOME *.md `x` | y"
        result = run_lock(sb, sb.alpha, ["--", *py("import sys; print(sys.argv[1])", tricky)])

        assert result.returncode == 0, result.stderr
        assert result.stdout == tricky + "\n"

    def test_free_lock_prints_acquired_and_no_waiting_line(self, sb):
        result = run_lock(sb, sb.alpha, ["--", *py("pass")])

        assert result.returncode == 0
        assert result.stderr.splitlines() == ["TEST-LOCK: acquired"]

    def test_holder_entry_is_cleared_after_the_lock_is_released(self, sb):
        result = run_lock(sb, sb.alpha, ["--", *py("pass")])

        assert result.returncode == 0, result.stderr
        assert sb.lock_file.read_bytes() == b""

    @pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP], ids=["SIGTERM", "SIGINT", "SIGHUP"])
    def test_signal_sent_to_wrapper_is_forwarded_to_child(self, sb, sig):
        child_pid_file = sb.root / "child.pid"
        code = (
            "import os, signal, sys, time\n"
            "for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):\n"
            "    signal.signal(s, signal.SIG_DFL)\n"
            "with open(sys.argv[1], 'w') as fh:\n"
            "    fh.write(str(os.getpid()))\n"
            "time.sleep(30)\n"
        )
        wrapper = subprocess.Popen(
            [sys.executable, str(SCRIPT), "--", *py(code, str(child_pid_file))],
            cwd=str(sb.alpha),
            env=make_env(sb),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        child_pid = None
        try:
            assert wait_for(lambda: child_pid_file.exists() and child_pid_file.read_text() != ""), "komenda nie wystartowala"
            child_pid = int(child_pid_file.read_text())
            wrapper.send_signal(sig)

            assert wrapper.wait(timeout=20) == 128 + int(sig)

            def child_gone():
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    return True
                return False

            assert wait_for(child_gone, timeout=5), "dziecko nie dostalo sygnalu"
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait()
            if child_pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(child_pid, signal.SIGKILL)

    def test_missing_command_exits_127(self, sb):
        result = run_lock(sb, sb.alpha, ["--", "definitely-not-a-real-binary-xyz"])

        assert result.returncode == 127

    def test_missing_command_with_path_exits_127(self, sb):
        result = run_lock(sb, sb.alpha, ["--", str(sb.root / "no-such-dir" / "tool")])

        assert result.returncode == 127

    def test_command_without_execute_permission_exits_126(self, sb):
        plain = sb.root / "not-executable"
        plain.write_text("#!/bin/sh\nexit 0\n")
        plain.chmod(0o644)

        result = run_lock(sb, sb.alpha, ["--", str(plain)])

        assert result.returncode == 126

    def test_command_killed_by_signal_exits_128_plus_signal(self, sb):
        result = run_lock(sb, sb.alpha, ["--", *py("import os, signal; os.kill(os.getpid(), signal.SIGTERM)")])

        assert result.returncode == 128 + 15

    def test_command_returning_75_is_not_reported_as_lock_timeout(self, sb):
        result = run_lock(sb, sb.alpha, ["--", *py("import sys; sys.exit(75)")])

        assert result.returncode == 75
        assert "TEST-LOCK: timeout" not in result.stderr
        assert result.stderr.splitlines() == ["TEST-LOCK: acquired"]

    def test_lock_is_free_again_after_previous_runs_from_both_worktrees(self, sb):
        for cwd in (sb.alpha, sb.beta, sb.alpha):
            result = run_lock(sb, cwd, ["--", *py("pass")])

            assert result.returncode == 0, result.stderr
            assert result.stderr.splitlines() == ["TEST-LOCK: acquired"]


@pytest.mark.unit
class TestQueueing:
    def test_second_worktree_waits_and_runs_only_after_first_finishes(self, sb):
        log = sb.root / "order.log"
        b_err = sb.root / "b.err"
        with HeldLock(sb, sb.alpha, "A", log) as held:
            with b_err.open("w") as err:
                b = subprocess.Popen(
                    [sys.executable, str(SCRIPT), "--", *worker_cmd(sb, "B", log)],
                    cwd=str(sb.beta),
                    env=make_env(sb),
                    stdout=subprocess.DEVNULL,
                    stderr=err,
                )
            try:
                assert wait_for(lambda: "TEST-LOCK: waiting" in b_err.read_text()), "B nie zglosil oczekiwania"
                assert log.read_text().splitlines() == ["A-start"]
                assert json.loads(sb.lock_file.read_text())["pid"] == held.proc.pid
                held.release()
                assert b.wait(timeout=30) == 0
            finally:
                if b.poll() is None:
                    b.kill()
                    b.wait()

        assert log.read_text().splitlines() == ["A-start", "A-end", "B-start", "B-end"]
        lines = b_err.read_text().splitlines()
        assert len(lines) == 2
        match = WAITING_RE.match(lines[0])
        assert match is not None, lines[0]
        assert int(match.group(1)) == held.proc.pid
        assert match.group(2) == "wt-alpha"
        assert lines[1] == "TEST-LOCK: acquired"

    def test_waiting_line_reads_holder_from_lock_file(self, sb):
        content = json.dumps({"pid": 4242, "worktree": "/some/where/wt-gamma"}).encode()
        with foreign_lock(sb, content):
            result = run_lock(sb, sb.beta, ["--timeout", "1", "--", *touch_cmd(sb.ran)], timeout=20)

        assert result.returncode == 75
        waiting = [line for line in lock_lines(result.stderr) if "waiting" in line]
        assert waiting == ["TEST-LOCK: waiting holder=4242/wt-gamma"]

    @pytest.mark.parametrize(
        "content",
        [b"", b"{not json", b"[1, 2]", b"{}", b'{"pid": "x", "worktree": 1}', b'{"pid": true, "worktree": "/a/wt"}'],
        ids=["empty", "garbage", "not-an-object", "empty-object", "wrong-field-types", "bool-pid"],
    )
    def test_unreadable_holder_file_reports_unknown_holder_without_traceback(self, sb, content):
        with foreign_lock(sb, content):
            result = run_lock(sb, sb.beta, ["--timeout", "1", "--", *touch_cmd(sb.ran)], timeout=20)

        assert result.returncode == 75
        assert "Traceback" not in result.stderr
        waiting = [line for line in lock_lines(result.stderr) if "waiting" in line]
        assert waiting == ["TEST-LOCK: waiting holder=unknown"]


@pytest.mark.unit
class TestTimeout:
    def test_timeout_exits_75_with_message_and_does_not_run_command(self, sb):
        log = sb.root / "order.log"
        with HeldLock(sb, sb.alpha, "A", log):
            result = run_lock(sb, sb.beta, ["--timeout", "1", "--", *touch_cmd(sb.ran)], timeout=20)

        assert result.returncode == 75
        assert "TEST-LOCK: timeout" in result.stderr.splitlines()
        assert "TEST-LOCK: acquired" not in result.stderr
        assert not sb.ran.exists()

    def test_timeout_zero_gives_up_at_once_when_lock_is_busy(self, sb):
        log = sb.root / "order.log"
        with HeldLock(sb, sb.alpha, "A", log):
            result = run_lock(sb, sb.beta, ["--timeout", "0", "--", *touch_cmd(sb.ran)], timeout=15)

        assert result.returncode == 75
        assert "TEST-LOCK: timeout" in result.stderr.splitlines()
        assert not sb.ran.exists()

    def test_default_timeout_is_one_hour(self):
        timeout, command = test_lock._parse_args(["--", "x"])

        assert timeout == 3600
        assert command == ["x"]

    def test_explicit_timeout_overrides_the_default(self):
        timeout, command = test_lock._parse_args(["--timeout", "7", "--", "x", "y"])

        assert timeout == 7
        assert command == ["x", "y"]

    def test_timeout_at_upper_limit_is_accepted_when_lock_is_free(self, sb):
        result = run_lock(sb, sb.beta, ["--timeout", "86400", "--", *touch_cmd(sb.ran)])

        assert result.returncode == 0, result.stderr
        assert sb.ran.exists()

    def test_timeout_zero_runs_command_when_lock_is_free(self, sb):
        result = run_lock(sb, sb.beta, ["--timeout", "0", "--", *touch_cmd(sb.ran)])

        assert result.returncode == 0, result.stderr
        assert sb.ran.exists()
        assert result.stderr.splitlines() == ["TEST-LOCK: acquired"]

    def test_waiter_within_timeout_still_runs_after_holder_finishes(self, sb):
        log = sb.root / "order.log"
        b_err = sb.root / "b.err"
        with HeldLock(sb, sb.alpha, "A", log) as held:
            with b_err.open("w") as err:
                b = subprocess.Popen(
                    [sys.executable, str(SCRIPT), "--timeout", "60", "--", *touch_cmd(sb.ran)],
                    cwd=str(sb.beta),
                    env=make_env(sb),
                    stdout=subprocess.DEVNULL,
                    stderr=err,
                )
            try:
                assert wait_for(lambda: "TEST-LOCK: waiting" in b_err.read_text())
                held.release()
                assert b.wait(timeout=30) == 0
            finally:
                if b.poll() is None:
                    b.kill()
                    b.wait()

        assert sb.ran.exists()
        assert "TEST-LOCK: timeout" not in b_err.read_text()


def _no_dashdash(cmd):
    return list(cmd)


USAGE_ERROR_ARGS = {
    "missing-dashdash": _no_dashdash,
    "missing-dashdash-after-timeout": lambda cmd: ["--timeout", "5", *cmd],
    "empty-command": lambda cmd: ["--"],
    "empty-command-after-timeout": lambda cmd: ["--timeout", "5", "--"],
    "timeout-not-a-number": lambda cmd: ["--timeout", "abc", "--", *cmd],
    "timeout-negative": lambda cmd: ["--timeout", "-5", "--", *cmd],
    "timeout-fractional": lambda cmd: ["--timeout", "1.5", "--", *cmd],
    "timeout-above-limit": lambda cmd: ["--timeout", "86401", "--", *cmd],
    "timeout-huge": lambda cmd: ["--timeout", "1000000000000", "--", *cmd],
    "timeout-without-value": lambda cmd: ["--timeout"],
    "timeout-value-is-dashdash": lambda cmd: ["--timeout", "--", *cmd],
    "unknown-flag": lambda cmd: ["--bogus", "--", *cmd],
    "unknown-flag-after-timeout": lambda cmd: ["--timeout", "5", "--bogus", "--", *cmd],
}


@pytest.mark.unit
class TestUsageErrors:
    @pytest.mark.parametrize("build", list(USAGE_ERROR_ARGS.values()), ids=list(USAGE_ERROR_ARGS))
    def test_usage_error_exits_64_with_message_and_runs_nothing(self, sb, build):
        result = run_lock(sb, sb.alpha, build(touch_cmd(sb.ran)))

        assert result.returncode == 64, result.stderr
        assert any(line.startswith("test_lock:") for line in result.stderr.splitlines()), result.stderr
        assert "TEST-LOCK: acquired" not in result.stderr
        assert not sb.ran.exists()

    def test_directory_outside_git_repository_exits_64(self, sb):
        result = run_lock(sb, sb.outside, ["--", *touch_cmd(sb.ran)], {"FAKE_GIT_NOT_REPO": "1"})

        assert result.returncode == 64, result.stderr
        assert any(line.startswith("test_lock:") for line in result.stderr.splitlines()), result.stderr
        assert not sb.ran.exists()
