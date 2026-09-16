from pathlib import Path

from log_tee import RotatingByteLog


def test_rotating_byte_log_keeps_current_file_and_numbered_backup(tmp_path: Path):
    path = tmp_path / "ebo-engine.log"
    output = RotatingByteLog(path, max_bytes=10, backup_count=2)
    try:
        output.write(b"12345678")
        output.write(b"abcd")
    finally:
        output.close()

    assert path.read_bytes() == b"abcd"
    assert (tmp_path / "ebo-engine.log.1").read_bytes() == b"12345678"


def test_rotating_byte_log_discards_backups_beyond_limit(tmp_path: Path):
    path = tmp_path / "ebo-engine.log"
    output = RotatingByteLog(path, max_bytes=4, backup_count=2)
    try:
        output.write(b"1111")
        output.write(b"2222")
        output.write(b"3333")
        output.write(b"4444")
    finally:
        output.close()

    assert path.read_bytes() == b"4444"
    assert (tmp_path / "ebo-engine.log.1").read_bytes() == b"3333"
    assert (tmp_path / "ebo-engine.log.2").read_bytes() == b"2222"
    assert not (tmp_path / "ebo-engine.log.3").exists()
