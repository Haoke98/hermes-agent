"""A snapshot git killed holding the index lock must not wedge a project forever.

``git add -A`` takes ``<index>.lock``; a run that exceeds the checkpoint timeout is killed with
the lock still on disk, and git then refuses every later stage for that project with "Unable to
create '…lock': File exists" — permanently, because git never removes a lock it did not take.
The checkpoint store is a *bare* repo with one index per project, so
``gitlock.clear_stale_git_locks`` (which resolves a checkout's ``.git/``) never saw these.
"""

import os
import time
from pathlib import Path

import pytest

from hermes_cli.gitlock import clear_stale_index_lock
from tools.checkpoint_manager import (
    CheckpointManager,
    _index_path,
    _project_hash,
    _store_path,
)

_STALE = 3600.0
_FRESH = 5.0


@pytest.fixture()
def checkpoint_base(tmp_path):
    return tmp_path / "checkpoints"


@pytest.fixture()
def work_dir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    (d / "main.py").write_text("print('hello')\n")
    return d


@pytest.fixture()
def mgr(checkpoint_base, monkeypatch):
    monkeypatch.setattr("tools.checkpoint_manager.CHECKPOINT_BASE", checkpoint_base)
    return CheckpointManager(enabled=True, max_snapshots=50)


def _plant_lock(checkpoint_base: Path, work_dir: Path, *, age_seconds: float) -> Path:
    store = _store_path(checkpoint_base)
    lock = Path(f"{_index_path(store, _project_hash(str(work_dir)))}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    stamp = time.time() - age_seconds
    os.utime(lock, (stamp, stamp))
    return lock


class TestAStaleIndexLockDoesNotWedgeTheProject:
    def test_a_snapshot_recovers_from_a_lock_left_by_a_killed_stage(
            self, mgr, checkpoint_base, work_dir):
        assert mgr.ensure_checkpoint(str(work_dir), "first") is True
        lock = _plant_lock(checkpoint_base, work_dir, age_seconds=_STALE)

        mgr.new_turn()
        (work_dir / "main.py").write_text("print('modified')\n")

        # Red before the fix: git refuses every stage and the snapshot is lost forever.
        assert mgr.ensure_checkpoint(str(work_dir), "after a killed stage") is True
        assert not lock.exists()

    def test_the_project_keeps_checkpointing_afterwards(
            self, mgr, checkpoint_base, work_dir):
        mgr.ensure_checkpoint(str(work_dir), "first")
        _plant_lock(checkpoint_base, work_dir, age_seconds=_STALE)

        for turn in range(2):
            mgr.new_turn()
            (work_dir / "main.py").write_text(f"print({turn})\n")
            assert mgr.ensure_checkpoint(str(work_dir), f"turn {turn}") is True


class TestClearStaleIndexLock:
    def test_removes_a_lock_past_the_age_floor(self, tmp_path):
        index_file = tmp_path / "hash16"
        lock = Path(f"{index_file}.lock")
        lock.write_text("")
        stamp = time.time() - _STALE
        os.utime(lock, (stamp, stamp))

        assert clear_stale_index_lock(index_file) is True
        assert not lock.exists()

    def test_keeps_a_lock_a_live_stage_may_hold(self, tmp_path):
        index_file = tmp_path / "hash16"
        lock = Path(f"{index_file}.lock")
        lock.write_text("")

        # A freshly written lock belongs to a git that may still be running.
        assert clear_stale_index_lock(index_file) is False
        assert lock.exists()

    def test_keeps_the_lock_while_any_git_is_running(self, tmp_path, monkeypatch):
        index_file = tmp_path / "hash16"
        lock = Path(f"{index_file}.lock")
        lock.write_text("")
        stamp = time.time() - _STALE
        os.utime(lock, (stamp, stamp))
        monkeypatch.setattr("hermes_cli.gitlock._git_proc_running", lambda: True)

        assert clear_stale_index_lock(index_file) is False
        assert lock.exists()

    def test_is_a_no_op_when_there_is_no_lock(self, tmp_path):
        assert clear_stale_index_lock(tmp_path / "hash16") is False

    def test_is_a_no_op_when_the_store_has_no_indexes_dir(self, tmp_path):
        assert clear_stale_index_lock(tmp_path / "missing" / "hash16") is False

    def test_age_floor_is_overridable(self, tmp_path):
        index_file = tmp_path / "hash16"
        lock = Path(f"{index_file}.lock")
        lock.write_text("")
        stamp = time.time() - _FRESH
        os.utime(lock, (stamp, stamp))

        assert clear_stale_index_lock(index_file, min_age_seconds=0) is True
        assert not lock.exists()
