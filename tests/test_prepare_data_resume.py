"""Verifies the Platform extraction pipeline's resumability and integrity
guarantees using a small local ZIP standing in for the remote archive's
structure (a genuine Deflate64 archive can't be produced by Python's
zipfile, but the resume/checkpoint logic under test operates purely on
byte offsets and doesn't depend on which compression method is in play).

This directly targets the bug found during Phase 1 review: an earlier
version of build_sparse_single_member_zip opened its container with mode
"wb", which truncates the file to zero on open -- so retrying after an
interrupted download would silently destroy whatever compressed data had
already been written. That must never happen again.
"""

import importlib.util
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

_spec = importlib.util.spec_from_file_location("prepare_data", REPO_ROOT / "scripts" / "prepare_data.py")
prepare_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepare_data)


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


def _make_test_zip(path: Path, member_name: str, content: bytes) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, content)


def test_fresh_extraction_downloads_and_reconstructs_correctly(tmp_path):
    member = "payload.csv"
    content = b"row1,row2\n" * 5000  # a few tens of KB, enough to span multiple chunks conceptually
    src_zip = tmp_path / "source.zip"
    _make_test_zip(src_zip, member, content)

    remote = FakeRemote(src_zip)
    dest_container = tmp_path / "out.container.zip"
    with zipfile.ZipFile(remote) as zf:
        data_start, already_written = prepare_data.build_sparse_single_member_zip(remote, zf, member, dest_container)
        assert already_written == 0
        info = zf.getinfo(member)
        prepare_data.stream_compressed_data_to_file(
            remote, data_start, info.compress_size, dest_container, data_start, already_written=0
        )

    # The reconstructed container must itself be a valid, openable ZIP
    # containing the same member with the same content.
    with zipfile.ZipFile(dest_container) as zf2:
        assert zf2.read(member) == content


def test_retry_after_interruption_does_not_destroy_already_downloaded_bytes(tmp_path):
    member = "payload.csv"
    content = b"abcdefghij" * 100_000  # 1,000,000 bytes of compressible content
    src_zip = tmp_path / "source.zip"
    _make_test_zip(src_zip, member, content)

    remote = FakeRemote(src_zip)
    dest_container = tmp_path / "out.container.zip"
    with zipfile.ZipFile(remote) as zf:
        info = zf.getinfo(member)
        data_start, already_written = prepare_data.build_sparse_single_member_zip(remote, zf, member, dest_container)
        assert already_written == 0

        # Simulate a run that was interrupted partway through the
        # compressed-data download: manually stop after writing only the
        # first half, as stream_compressed_data_to_file would if killed
        # mid-transfer (it checkpoints after every chunk).
        half = info.compress_size // 2
        with dest_container.open("r+b") as out:
            out.seek(data_start)
            remote.seek(data_start)
            out.write(remote.read(half))
        progress = prepare_data._load_progress(dest_container)
        progress["bytes_written"] = half
        prepare_data._save_progress(dest_container, progress)

        bytes_at_data_start_before_retry = dest_container.read_bytes()[data_start:data_start + half]

        # Now simulate the retry: a fresh process re-invokes
        # build_sparse_single_member_zip against the SAME remote state.
        # This must recognize the existing progress and NOT truncate/wipe
        # the container (the actual bug being regression-tested).
        remote2 = FakeRemote(src_zip)
        with zipfile.ZipFile(remote2) as zf2:
            data_start2, already_written2 = prepare_data.build_sparse_single_member_zip(
                remote2, zf2, member, dest_container
            )
        assert data_start2 == data_start
        assert already_written2 == half, "resume did not detect the correct already-written byte count"

        # The bytes already on disk before the retry must be byte-for-byte
        # unchanged after build_sparse_single_member_zip runs again.
        bytes_after_rebuild = dest_container.read_bytes()[data_start:data_start + half]
        assert bytes_after_rebuild == bytes_at_data_start_before_retry, (
            "build_sparse_single_member_zip destroyed already-downloaded compressed data on retry"
        )

        # Complete the download via the resume path and verify the
        # remaining bytes were fetched from the correct offset onward
        # (i.e. it didn't re-fetch the first half from byte 0 again).
        remote3 = FakeRemote(src_zip)
        prepare_data.stream_compressed_data_to_file(
            remote3, data_start, info.compress_size, dest_container, data_start, already_written=half
        )
        assert all(r_start >= data_start + half for r_start, _ in remote3.read_ranges), (
            "resumed download re-fetched bytes that were already written in a prior run"
        )

    with zipfile.ZipFile(dest_container) as zf3:
        assert zf3.read(member) == content


def test_progress_sidecar_is_ignored_if_inconsistent_with_current_remote_metadata(tmp_path):
    member = "payload.csv"
    content = b"x" * 50_000
    src_zip = tmp_path / "source.zip"
    _make_test_zip(src_zip, member, content)

    dest_container = tmp_path / "out.container.zip"
    remote = FakeRemote(src_zip)
    with zipfile.ZipFile(remote) as zf:
        prepare_data.build_sparse_single_member_zip(remote, zf, member, dest_container)

    # Corrupt the progress sidecar so it no longer matches reality (as if
    # a different archive version, or a bug, had written a bogus offset).
    progress = prepare_data._load_progress(dest_container)
    progress["compress_size"] = 999_999_999
    prepare_data._save_progress(dest_container, progress)

    remote2 = FakeRemote(src_zip)
    with zipfile.ZipFile(remote2) as zf2:
        _data_start, already_written = prepare_data.build_sparse_single_member_zip(remote2, zf2, member, dest_container)
    assert already_written == 0, "stale/inconsistent progress must trigger a fresh start, not a bogus resume"
