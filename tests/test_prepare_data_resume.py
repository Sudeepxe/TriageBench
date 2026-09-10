"""Verifies the Platform extraction pipeline's resumability and integrity
guarantees using a small local ZIP standing in for the remote archive's
structure (a genuine Deflate64 archive can't be produced by Python's
zipfile, but the resume/checkpoint logic under test operates purely on
byte offsets and doesn't depend on which compression method is in play).

Two real bugs were found and fixed during Phase 1 review, both
regression-tested here:

1. An earlier version of the container-builder opened its output file
   with mode "wb", which truncates to zero on open -- so retrying after
   an interrupted ~8GB download (exactly what happened once already, when
   a Zenodo outage killed an in-progress run) would have silently
   destroyed whatever compressed data had already been written.

2. An earlier design preserved the target entry at its *original*
   multi-GB absolute offset inside a sparse local file (mirroring the
   remote archive's full layout). This made real p7zip 17.05 fail
   outright ("Can't open as archive") whenever that offset was large
   (reproduced independently with a synthetic sparse file: works fine up
   to at least ~1KB of leading gap, fails completely at ~9GB of leading
   gap, isolated from compression method, ZIP64 usage, and trailing
   gaps). The fix places the entry at local offset 0 in a compact,
   non-sparse container instead.
"""

import importlib.util
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_spec = importlib.util.spec_from_file_location("prepare_data", REPO_ROOT / "scripts" / "prepare_data.py")
prepare_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare_data)

HAS_7Z = subprocess.run(["which", "7z"], capture_output=True).returncode == 0


class FakeRemote:
    """Minimal stand-in for HTTPRangeFile, backed by a local file's bytes.
    Tracks every byte range actually read, so tests can assert a resumed
    run doesn't re-fetch bytes it already has."""

    def __init__(self, path: Path):
        self._data = path.read_bytes()
        self.size = len(self._data)
        self.pos = 0
        self.read_ranges: list[tuple[int, int]] = []

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def read(self, n=-1):
        if n < 0:
            n = self.size - self.pos
        end = min(self.pos + n, self.size)
        self.read_ranges.append((self.pos, end))
        data = self._data[self.pos:end]
        self.pos = end
        return data


def _make_multi_entry_zip64_source(path: Path, target_name: str, target_content: bytes) -> None:
    # force_zip64=True makes even small entries use ZIP64 extra fields /
    # sentinels, matching the real archive's structure (whose sizes
    # genuinely require ZIP64) closely enough to exercise the same code
    # paths in build_cd_record / build_zip64_eocd_tail.
    with zipfile.ZipFile(path, "w") as zf:
        with zf.open("other1.txt", "w", force_zip64=True) as f:
            f.write(b"x" * 5000)
        with zf.open(target_name, "w", force_zip64=True) as f:
            f.write(target_content)
        with zf.open("other2.txt", "w", force_zip64=True) as f:
            f.write(b"y" * 5000)


def test_fresh_extraction_produces_container_with_data_at_offset_zero(tmp_path):
    member = "payload.csv"
    content = b"row1,row2\n" * 5000
    src_zip = tmp_path / "source.zip"
    _make_multi_entry_zip64_source(src_zip, member, content)

    remote = FakeRemote(src_zip)
    dest_container = tmp_path / "out.container.zip"
    with zipfile.ZipFile(remote) as zf:
        remote_data_start, local_data_start, already_written = prepare_data.start_minimal_container(
            remote, zf, member, dest_container
        )
        assert already_written == 0
        assert local_data_start < remote_data_start, "local offset should be much smaller (no leading gap)"
        info = zf.getinfo(member)
        prepare_data.stream_compressed_data_to_file(
            remote, remote_data_start, info.compress_size, dest_container, local_data_start, already_written=0
        )
        prepare_data.finalize_container(dest_container, zf, member, local_data_start)

    # The reconstructed container is a valid, minimal, single-entry ZIP
    # whose data starts essentially at the beginning of the file.
    assert local_data_start < 200
    with zipfile.ZipFile(dest_container) as zf2:
        assert zf2.namelist() == [member]
        assert zf2.getinfo(member).header_offset == 0
        assert zf2.read(member) == content


def test_retry_after_interruption_does_not_destroy_already_downloaded_bytes(tmp_path):
    member = "payload.csv"
    content = b"abcdefghij" * 100_000  # 1,000,000 bytes
    src_zip = tmp_path / "source.zip"
    _make_multi_entry_zip64_source(src_zip, member, content)

    remote = FakeRemote(src_zip)
    dest_container = tmp_path / "out.container.zip"
    with zipfile.ZipFile(remote) as zf:
        info = zf.getinfo(member)
        remote_data_start, local_data_start, already_written = prepare_data.start_minimal_container(
            remote, zf, member, dest_container
        )
        assert already_written == 0

        # Simulate an interrupted run: write only half the compressed data
        # (stream_compressed_data_to_file checkpoints after every chunk,
        # so a kill mid-transfer leaves exactly this kind of partial state).
        half = info.compress_size // 2
        prepare_data.stream_compressed_data_to_file(
            remote, remote_data_start, half, dest_container, local_data_start, already_written=0
        )
        bytes_before_retry = dest_container.read_bytes()

        # Retry: a fresh process re-invokes start_minimal_container against
        # the SAME remote state. Must recognize existing progress and NOT
        # truncate/wipe the container (the bug this regression-tests).
        remote2 = FakeRemote(src_zip)
        with zipfile.ZipFile(remote2) as zf2:
            _rds2, _lds2, already_written2 = prepare_data.start_minimal_container(remote2, zf2, member, dest_container)
        assert already_written2 == half, "resume did not detect the correct already-written byte count"
        assert dest_container.read_bytes() == bytes_before_retry, (
            "start_minimal_container destroyed already-downloaded compressed data on retry"
        )

        # Complete the download via the resume path; verify it fetched
        # only the remaining bytes, not from the start again.
        remote3 = FakeRemote(src_zip)
        prepare_data.stream_compressed_data_to_file(
            remote3, remote_data_start, info.compress_size, dest_container, local_data_start, already_written=half
        )
        assert all(r_start >= remote_data_start + half for r_start, _ in remote3.read_ranges), (
            "resumed download re-fetched bytes that were already written in a prior run"
        )
        prepare_data.finalize_container(dest_container, zf, member, local_data_start)

    with zipfile.ZipFile(dest_container) as zf3:
        assert zf3.read(member) == content


def test_progress_sidecar_ignored_if_inconsistent_with_current_remote_metadata(tmp_path):
    member = "payload.csv"
    content = b"x" * 50_000
    src_zip = tmp_path / "source.zip"
    _make_multi_entry_zip64_source(src_zip, member, content)

    dest_container = tmp_path / "out.container.zip"
    remote = FakeRemote(src_zip)
    with zipfile.ZipFile(remote) as zf:
        prepare_data.start_minimal_container(remote, zf, member, dest_container)

    progress = prepare_data._load_progress(dest_container)
    progress["compress_size"] = 999_999_999
    prepare_data._save_progress(dest_container, progress)

    remote2 = FakeRemote(src_zip)
    with zipfile.ZipFile(remote2) as zf2:
        _rds, _lds, already_written = prepare_data.start_minimal_container(remote2, zf2, member, dest_container)
    assert already_written == 0, "stale/inconsistent progress must trigger a fresh start, not a bogus resume"


def test_finalize_rejects_incomplete_data():
    tmp_dir = Path(__file__).parent / "_tmp_finalize_check"
    tmp_dir.mkdir(exist_ok=True)
    try:
        src_zip = tmp_dir / "source.zip"
        _make_multi_entry_zip64_source(src_zip, "payload.csv", b"z" * 10_000)
        remote = FakeRemote(src_zip)
        dest_container = tmp_dir / "out.container.zip"
        with zipfile.ZipFile(remote) as zf:
            remote_data_start, local_data_start, _ = prepare_data.start_minimal_container(
                remote, zf, "payload.csv", dest_container
            )
            info = zf.getinfo("payload.csv")
            # write only half the data, then try to finalize anyway
            half = info.compress_size // 2
            prepare_data.stream_compressed_data_to_file(
                remote, remote_data_start, half, dest_container, local_data_start, already_written=0
            )
            with pytest.raises(OSError):
                prepare_data.finalize_container(dest_container, zf, "payload.csv", local_data_start)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@pytest.mark.skipif(not HAS_7Z, reason="7z not installed")
def test_minimal_container_is_actually_openable_by_real_7z(tmp_path):
    member = "Eclipse/P_Platform/Platform_dataset_issues.csv"
    content = b"Issue URL,ID\nhttps://example,1\n" * 2000
    src_zip = tmp_path / "source.zip"
    _make_multi_entry_zip64_source(src_zip, member, content)

    remote = FakeRemote(src_zip)
    dest_container = tmp_path / "out.container.zip"
    with zipfile.ZipFile(remote) as zf:
        info = zf.getinfo(member)
        remote_data_start, local_data_start, _ = prepare_data.start_minimal_container(
            remote, zf, member, dest_container
        )
        prepare_data.stream_compressed_data_to_file(
            remote, remote_data_start, info.compress_size, dest_container, local_data_start, already_written=0
        )
        prepare_data.finalize_container(dest_container, zf, member, local_data_start)

    out_dir = tmp_path / "extracted"
    result = subprocess.run(
        ["7z", "x", str(dest_container), f"-o{out_dir}", "-y", member],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"7z failed:\n{result.stdout}\n{result.stderr}"
    assert (out_dir / member).read_bytes() == content


def test_build_cd_record_and_eocd_are_internally_consistent_offsets():
    # A focused check on the raw byte layout, independent of 7z/zipfile,
    # of the exact fields the earlier bug hinged on: no dependency on the
    # original (multi-GB) offset anywhere in the rebuilt structures.
    class FakeInfo:
        filename = "Eclipse/P_Platform/Platform_dataset_issues.csv"
        compress_size = 9_000_000_000  # exceeds 4GB -> forces ZIP64 extra
        file_size = 15_000_000_000
        CRC = 0x12345678
        compress_type = 9
        flag_bits = 0
        external_attr = 0
        date_time = (2020, 1, 1, 0, 0, 0)

    cd = prepare_data.build_cd_record(FakeInfo(), new_local_header_offset=0)
    sig, _, _, _, _, _, _, _, csize, usize = struct.unpack("<IHHHHHHIII", cd[:28])
    assert sig == 0x02014B50
    assert csize == 0xFFFFFFFF and usize == 0xFFFFFFFF  # sentinels, real values in zip64 extra
    local_offset = struct.unpack("<I", cd[42:46])[0]
    assert local_offset == 0

    tail = prepare_data.build_zip64_eocd_tail(entry_count=1, cd_size=len(cd), cd_offset=123)
    assert tail[:4] == b"PK\x06\x06"
    z64_offset = struct.unpack("<Q", tail[48:56])[0]
    assert z64_offset == 123
