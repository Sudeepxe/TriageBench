#!/usr/bin/env python3
"""Deterministic dataset preparation: extract the Platform project CSV from
the upstream ZIP archive without downloading unrelated projects.

The full Eclipse_dataset.zip stores each of the nine Eclipse projects as an
independently DEFLATE-compressed ZIP member. Since Zenodo's file server
supports HTTP range requests (verified against the live record), this
script reads the ZIP's central directory and streams-decompresses only the
Platform member directly to disk — never downloading, or holding in memory,
the other eight projects (BIRT, CDT, Equinox, JDT, Mylyn, Papyrus, PDE,
TPTP).

If you already downloaded the full archive locally (`make download-full`),
pass --local-archive to read from disk instead of over HTTP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from triagebench.data.remote_zip import HTTPRangeFile  # noqa: E402

ZIP_URL = "https://zenodo.org/api/records/15348468/files/Eclipse_dataset.zip/content"
PLATFORM_MEMBER = "Eclipse/P_Platform/Platform_dataset_issues.csv"

LOCAL_HEADER_FIXED_SIZE = 30  # bytes, per ZIP spec, before filename/extra


def _progress_path(dest: Path) -> Path:
    return dest.with_suffix(dest.suffix + ".progress.json")


def _load_progress(dest: Path) -> dict | None:
    p = _progress_path(dest)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_progress(dest: Path, state: dict) -> None:
    _progress_path(dest).write_text(json.dumps(state))


def build_cd_record(info: zipfile.ZipInfo, new_local_header_offset: int) -> bytes:
    """Serialize a single ZIP central-directory file header for `info`,
    pointing at `new_local_header_offset` instead of its original
    position. Sizes exceeding 4GB go in a ZIP64 extra field (with the
    classic 4-byte fields set to the 0xFFFFFFFF sentinel); the new offset
    is written directly in the classic 4-byte field since placing the
    entry at/near the start of a compact container keeps it small.
    """
    filename = info.filename.encode("utf-8")
    needs_zip64_sizes = info.compress_size > 0xFFFFFFFF or info.file_size > 0xFFFFFFFF
    csize_field, usize_field = info.compress_size, info.file_size
    extra = b""
    if needs_zip64_sizes:
        zip64_extra = struct.pack("<QQ", info.file_size, info.compress_size)
        extra = struct.pack("<HH", 1, len(zip64_extra)) + zip64_extra
        csize_field = usize_field = 0xFFFFFFFF

    dt = info.date_time
    dosdate = ((dt[0] - 1980) << 9) | (dt[1] << 5) | dt[2]
    dostime = (dt[3] << 11) | (dt[4] << 5) | (dt[5] // 2)
    version_needed = 45 if needs_zip64_sizes else 20

    header = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50, 45, version_needed, info.flag_bits, info.compress_type,
        dostime, dosdate, info.CRC, csize_field, usize_field,
        len(filename), len(extra), 0, 0, 0, info.external_attr, new_local_header_offset,
    )
    return header + filename + extra


def build_zip64_eocd_tail(entry_count: int, cd_size: int, cd_offset: int) -> bytes:
    """ZIP64 end-of-central-directory record + locator + classic EOCD,
    describing a central directory of `entry_count` entries at `cd_offset`.
    """
    record = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50, 44, 45, 45, 0, 0, entry_count, entry_count, cd_size, cd_offset,
    )
    locator = struct.pack("<IIQI", 0x07064B50, 0, cd_offset + cd_size, 1)
    classic = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, entry_count, entry_count, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return record + locator + classic


def start_minimal_container(rf: HTTPRangeFile, zf: zipfile.ZipFile, member: str, dest: Path) -> tuple[int, int, int]:
    """Start (or resume) building a *minimal*, single-entry local ZIP
    container: this member's local header at local offset 0, followed by
    its compressed data, followed (once complete) by a freshly-built
    central directory + EOCD referencing local offset 0 -- never the
    member's original (multi-GB, for Platform) absolute offset.

    This deliberately does NOT preserve the remote archive's original
    layout via a sparse file (an earlier version of this function did).
    That approach was found to make p7zip 17.05 fail outright
    ("Can't open as archive") whenever the real entry sits behind a
    multi-GB leading gap of unwritten bytes -- reproduced independently
    with a synthetic sparse file, isolated from compression method,
    ZIP64 usage, or trailing gaps (all of which 7z handles fine). See
    docs/EXPERIMENT_LOG.md. Placing the entry at offset 0 sidesteps the
    bug entirely: there is no leading gap for 7z's offset-correction
    logic to (mis)compute.

    Returns (remote_data_start, local_data_start, already_written).
    """
    info = zf.getinfo(member)
    header_probe = rf_read_at(rf, info.header_offset, 4096)
    if header_probe[:4] != b"PK\x03\x04":
        raise ValueError(f"unexpected local header signature at offset {info.header_offset}")
    fname_len, extra_len = struct.unpack("<HH", header_probe[26:30])
    remote_data_start = info.header_offset + LOCAL_HEADER_FIXED_SIZE + fname_len + extra_len
    local_header_bytes = header_probe[: LOCAL_HEADER_FIXED_SIZE + fname_len + extra_len]
    local_data_start = len(local_header_bytes)

    existing = _load_progress(dest)
    fresh_state = {
        "member": member,
        "remote_data_start": remote_data_start,
        "local_data_start": local_data_start,
        "compress_size": info.compress_size,
        "uncompressed_size": info.file_size,
        "bytes_written": 0,
    }
    # The progress sidecar is deleted once finalize_container() completes,
    # so its mere existence means a prior run got interrupted mid-transfer
    # -- safe to resume as long as the metadata matches exactly.
    if (
        existing is not None
        and dest.exists()
        and all(existing.get(k) == fresh_state[k] for k in fresh_state if k != "bytes_written")
        and dest.stat().st_size == local_data_start + existing.get("bytes_written", 0)
    ):
        print(
            f"  resuming existing container at {dest} "
            f"({existing.get('bytes_written', 0) / 1e9:.2f} GB of compressed data already written)"
        )
        return remote_data_start, local_data_start, existing.get("bytes_written", 0)

    if existing is not None:
        print("  prior progress found but is stale/inconsistent with the current remote archive -- starting fresh")

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:  # intentional fresh-start truncate; only reached above when NOT resuming
        out.write(local_header_bytes)
    _save_progress(dest, fresh_state)

    print(
        f"  started minimal container at {dest} "
        f"(local header written, {info.compress_size/1e9:.2f} GB of data to append)"
    )
    return remote_data_start, local_data_start, 0


def finalize_container(dest: Path, zf: zipfile.ZipFile, member: str, local_data_start: int) -> None:
    """Append a freshly-built central directory + EOCD to a container
    whose local header + full compressed data have already been written,
    making it a complete, valid, minimal single-entry ZIP file."""
    info = zf.getinfo(member)
    actual_size = dest.stat().st_size
    expected_size = local_data_start + info.compress_size
    if actual_size != expected_size:
        raise OSError(
            f"container size mismatch before finalizing: got {actual_size} bytes, "
            f"expected {expected_size} (local header + full compressed data)"
        )
    cd_record = build_cd_record(info, new_local_header_offset=0)
    tail = build_zip64_eocd_tail(entry_count=1, cd_size=len(cd_record), cd_offset=actual_size)
    with dest.open("ab") as out:
        out.write(cd_record)
        out.write(tail)


def rf_read_at(rf: HTTPRangeFile, offset: int, n: int) -> bytes:
    rf.seek(offset)
    return rf.read(n)


def stream_compressed_data_to_file(
    rf: HTTPRangeFile, offset: int, length: int, dest: Path, position: int, already_written: int = 0
) -> None:
    """Stream `length` bytes starting at `offset` from the remote archive
    directly into `dest` at file position `position`, in chunks, with
    progress output and a resumable checkpoint after every chunk.

    If `already_written` bytes were persisted from a prior run (see
    build_sparse_single_member_zip's progress sidecar), this resumes from
    exactly that point instead of re-downloading from the start -- opening
    the destination with "r+b" (never "wb") so bytes already on disk are
    preserved. This is the only large network transfer this script
    performs (~compress_size bytes for the target member), and the one
    most likely to be interrupted by a transient network/server issue
    (observed directly: a Zenodo outage killed an in-progress run).
    """
    chunk = 24 * 1024 * 1024  # Zenodo returns intermittent 504s regardless of request size;
    # HTTPRangeFile retries with backoff, so this trades off total request count vs retry cost.
    if already_written >= length:
        print(f"  download already complete from a prior run ({already_written/1e9:.2f} GB)")
        return
    written = already_written
    last_pct = -1
    t0 = time.time()
    with dest.open("r+b") as out:
        out.seek(position + written)
        remaining = length - written
        pos = offset + written
        while remaining > 0:
            n = min(chunk, remaining)
            data = rf_read_at(rf, pos, n)
            if not data:
                raise OSError("short read from remote archive")
            out.write(data)
            out.flush()
            written += len(data)
            pos += len(data)
            remaining -= len(data)
            # Persist the checkpoint after every chunk, not only at the end,
            # so a crash/interrupt mid-transfer loses at most one chunk.
            progress = _load_progress(dest) or {}
            progress["bytes_written"] = written
            _save_progress(dest, progress)
            pct = int(100 * written / length)
            if pct != last_pct and pct % 2 == 0:
                elapsed = time.time() - t0
                rate = (written - already_written) / elapsed / 1e6 if elapsed > 0 else 0
                print(f"  download {pct}% ({written/1e9:.2f}/{length/1e9:.2f} GB compressed, {rate:.1f} MB/s)")
                last_pct = pct
    print(f"  download complete: {written/1e9:.2f} GB total ({time.time()-t0:.0f}s this run)")


def extract_member(zf: zipfile.ZipFile, member: str, dest: Path, expected_size: int | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    info = zf.getinfo(member)
    total = info.file_size
    written = 0
    last_pct = -1
    start = time.time()
    chunk = 32 * 1024 * 1024
    with zf.open(member) as src, tmp.open("wb") as out:
        while True:
            data = src.read(chunk)
            if not data:
                break
            out.write(data)
            written += len(data)
            pct = int(100 * written / total) if total else 0
            if pct != last_pct and pct % 2 == 0:
                elapsed = time.time() - start
                rate = written / elapsed / 1e6 if elapsed > 0 else 0
                print(f"  {pct}% ({written / 1e9:.2f} / {total / 1e9:.2f} GB uncompressed, {rate:.1f} MB/s)")
                last_pct = pct
    if expected_size is not None and written != expected_size:
        tmp.unlink(missing_ok=True)
        print(
            f"ERROR: integrity check failed. Extracted {written} bytes, expected exactly "
            f"{expected_size} bytes (from the ZIP's own uncompressed-size field). "
            f"Deleted the incomplete/corrupt partial output.",
            file=sys.stderr,
        )
        sys.exit(1)
    tmp.rename(dest)
    print(f"Wrote {dest} ({written / 1e9:.2f} GB)")


def cmd_extract_platform(args: argparse.Namespace) -> None:
    member = args.member
    dest = Path(args.output)

    if args.local_archive:
        print(f"Reading local archive {args.local_archive}")
        with zipfile.ZipFile(args.local_archive) as zf:
            extract_member(zf, member, dest, None)
        return

    print(f"Opening remote ZIP central directory over HTTP range requests: {ZIP_URL}")
    rf = HTTPRangeFile(ZIP_URL)
    print(f"  remote archive size: {rf.size / 1e9:.2f} GB")
    with zipfile.ZipFile(rf) as zf:
        names = zf.namelist()
        print(f"  {len(names)} entries found in archive")
        if member not in names:
            print(f"ERROR: expected member {member!r} not found. Entries: {names}", file=sys.stderr)
            sys.exit(1)
        info = zf.getinfo(member)
        print(
            f"  {member}: compressed={info.compress_size/1e9:.2f}GB "
            f"uncompressed={info.file_size/1e9:.2f}GB method={info.compress_type}"
        )

        if info.compress_type == zipfile.ZIP_DEFLATED or info.compress_type == zipfile.ZIP_STORED:
            extract_member(zf, member, dest, info.file_size)
            print(f"  HTTP range requests issued: {rf.request_count}, bytes fetched: {rf.bytes_fetched/1e9:.2f} GB")
            return

        # Method 9 (Deflate64) isn't supported by Python's zipfile. Rebuild
        # a *minimal* single-entry local container (this member's local
        # header + data at local offset 0, plus a freshly-built central
        # directory/EOCD) and hand it to 7z, which does support Deflate64.
        if info.compress_type != 9:
            print(f"ERROR: unsupported compression method {info.compress_type}", file=sys.stderr)
            sys.exit(1)

        container = dest.with_suffix(".container.zip")
        print(f"  compression method 9 (Deflate64) — not supported by Python zipfile; using 7z via {container}")
        remote_data_start, local_data_start, already_written = start_minimal_container(rf, zf, member, container)
        stream_compressed_data_to_file(
            rf, remote_data_start, info.compress_size, container, local_data_start, already_written=already_written
        )
        print(f"  HTTP range requests issued so far: {rf.request_count}, bytes fetched: {rf.bytes_fetched/1e9:.2f} GB")
        finalize_container(container, zf, member, local_data_start)
        print(f"  finalized container (appended central directory + EOCD) -> {container}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"  running 7z to decompress {member} from {container}")
        t0 = time.time()
        result = subprocess.run(
            ["7z", "x", str(container), f"-o{dest.parent}", "-y", member],
            capture_output=True, text=True,
        )
        print(result.stdout[-3000:])
        if result.returncode != 0:
            print(result.stderr[-3000:], file=sys.stderr)
            print(
                f"  7z extraction FAILED. The downloaded container at {container} is kept "
                "(not deleted) so the run can be diagnosed or retried without re-downloading.",
                file=sys.stderr,
            )
            sys.exit(1)
        extracted_path = dest.parent / member
        extracted_path.rename(dest)

        # Integrity check: the decompressed output size must exactly match
        # the uncompressed size recorded in the ZIP's own metadata. 7z
        # verifies CRC internally during extraction (and would normally
        # exit non-zero on a CRC failure), but this is a cheap, explicit
        # second check against silent truncation/corruption before we
        # delete the only copy of the ~8GB compressed download.
        actual_size = dest.stat().st_size
        if actual_size != info.file_size:
            print(
                f"ERROR: integrity check failed. Extracted {dest} is {actual_size} bytes, "
                f"expected exactly {info.file_size} bytes (from the ZIP's own uncompressed-size "
                f"field). NOT deleting {container} -- do not trust {dest} for measurement.",
                file=sys.stderr,
            )
            sys.exit(1)

        # Only reached on a verified-correct extraction: safe to clean up
        # the now-empty extracted directory tree, the compressed
        # container, and its progress sidecar.
        for p in sorted(dest.parent.glob("Eclipse/**/*"), reverse=True):
            if p.is_dir():
                p.rmdir()
        (dest.parent / "Eclipse").rmdir() if (dest.parent / "Eclipse").exists() else None
        container.unlink(missing_ok=True)
        _progress_path(container).unlink(missing_ok=True)
        print(
            f"  7z extraction complete and size-verified in {time.time()-t0:.0f}s "
            f"-> {dest} ({actual_size/1e9:.2f} GB, matches expected uncompressed size exactly)"
        )


def md5sum(path: Path, chunk_size: int = 64 * 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_checksum(args: argparse.Namespace) -> None:
    print(md5sum(Path(args.path)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("extract-platform", help="Extract a single project CSV from the archive")
    p1.add_argument("--output", required=True)
    p1.add_argument("--member", default=PLATFORM_MEMBER, help="ZIP member path to extract")
    p1.add_argument("--local-archive", default=None, help="Path to a locally downloaded Eclipse_dataset.zip")
    p1.set_defaults(func=cmd_extract_platform)

    p2 = sub.add_parser("checksum", help="Compute MD5 of a file")
    p2.add_argument("path")
    p2.set_defaults(func=cmd_checksum)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
