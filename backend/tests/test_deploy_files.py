"""deploy/: the example operator files do what their comments claim."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
class TestBackupScript:
    def _run(self, tmp_path, *, keep_days="30"):
        data = tmp_path / "my data"  # a path with a space on purpose
        data.mkdir()
        (data / "journal.json").write_text('{"visits": []}')
        backups = tmp_path / "backups dir"
        env = {
            **os.environ,
            "TERRA_DATA_DIR": str(data),
            "TERRA_BACKUP_DIR": str(backups),
            "TERRA_BACKUP_KEEP_DAYS": keep_days,
        }
        result = subprocess.run(["bash", str(DEPLOY / "backup.sh")], env=env, capture_output=True, text=True, timeout=60)
        return result, backups

    def test_writes_an_archive_that_only_the_owner_can_read(self, tmp_path):
        result, backups = self._run(tmp_path)
        assert result.returncode == 0, result.stderr
        (archive,) = backups.glob("terra-data-*.tar.gz")
        assert stat.S_IMODE(archive.stat().st_mode) == 0o600  # journal data and GPS trails
        assert stat.S_IMODE(backups.stat().st_mode) == 0o700
        listing = subprocess.run(["tar", "-tzf", str(archive)], capture_output=True, text=True).stdout
        assert "my data/journal.json" in listing

    def test_old_archives_are_removed_and_recent_ones_kept(self, tmp_path):
        _, backups = self._run(tmp_path)
        old, recent = backups / "terra-data-20200101T000000Z.tar.gz", backups / "terra-data-20991231T000000Z.tar.gz"
        for f, age_days in ((old, 45), (recent, 3)):
            f.write_bytes(b"x")
            ts = time.time() - age_days * 86400
            os.utime(f, (ts, ts))
        data = tmp_path / "my data"
        env = {**os.environ, "TERRA_DATA_DIR": str(data), "TERRA_BACKUP_DIR": str(backups), "TERRA_BACKUP_KEEP_DAYS": "30"}
        subprocess.run(["bash", str(DEPLOY / "backup.sh")], env=env, check=True, capture_output=True, timeout=60)
        assert not old.exists() and recent.exists()


def test_the_systemd_unit_documents_its_setup_and_keeps_the_env_file_private():
    unit = (DEPLOY / "terra-incognita.service").read_text(encoding="utf-8")
    assert "EnvironmentFile=-" in unit  # optional: the app also reads .env itself
    assert "install -d" in unit  # ReadWritePaths= needs the directories to exist
    assert "-m 0640" in unit and "world-readable" in unit  # the .env will hold API keys
    assert "ProtectSystem=strict" in unit and "ReadWritePaths=" in unit
