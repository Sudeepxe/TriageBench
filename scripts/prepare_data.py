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


def build_sparse_single_member_zip(rf: HTTPRangeFile, zf: zipfile.ZipFile, member: str, dest: Path) -> None:
    """Reconstruct a sparse local .zip file that is byte-identical to the
    remote archive in the two regions that matter (this member's local
    header + compressed data, and the full central directory + EOCD), with
    everything else left as an unwritten (sparse) hole.

    This lets standard ZIP tools (which need random access to the central
    directory + this member's data) work against a local file while only
    ~compress_size bytes are actually transferred and written to disk, not
    the full archive. Deflate64 (compression method 9) isn't decodable by
    Python's zipfile, so the actual decompression is done by an external
    tool (7z) against this reconstructed file.
    """
    info = zf.getinfo(member)

    # Parse the real local header to find the exact compressed-data start
    # offset (filename/extra field lengths can differ from the central
    # directory record).
    header_probe = rf_read_at(rf, info.header_offset, 4096)
    if header_probe[:4] != b"PK\x03\x04":
        raise ValueError(f"unexpected local header signature at offset {info.header_offset}")
    fname_len, extra_len = struct.unpack("<HH", header_probe[26:30])
    data_start = info.header_offset + LOCAL_HEADER_FIXED_SIZE + fname_len + extra_len

    central_dir_start = zf.start_dir
    tail = rf_read_at(rf, central_dir_start, rf.size - central_dir_start)

    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        out.truncate(rf.size)  # preallocate as a sparse file
        out.seek(info.header_offset)
        out.write(header_probe[: LOCAL_HEADER_FIXED_SIZE + fname_len + extra_len])
        out.seek(central_dir_start)
        out.write(tail)

    print(f"  reconstructed sparse container at {dest} (apparent size {rf.size/1e9:.2f} GB)")
    return data_start


def rf_read_at(rf: HTTPRangeFile, offset: int, n: int) -> bytes:
    rf.seek(offset)
    return rf.read(n)


def stream_compressed_data_to_file(rf: HTTPRangeFile, offset: int, length: int, dest: Path, position: int) -> None:
    """Stream `length` bytes starting at `offset` from the remote archive
    directly into `dest` at file position `position`, in chunks, with
    progress output. This is the only large network transfer this script
    performs (~compress_size bytes for the target member)."""
    chunk = 12 * 1024 * 1024  # kept modest: large ranges have hit transient 504s from Zenodo
    written = 0
    last_pct = -1
    t0 = time.time()
    with dest.open("r+b") as out:
        out.seek(position)
        remaining = length
        pos = offset
        while remaining > 0:
            n = min(chunk, remaining)
            data = rf_read_at(rf, pos, n)
            if not data:
                raise IOError("short read from remote archive")
            out.write(data)
            written += len(data)
            pos += len(data)
            remaining -= len(data)
            pct = int(100 * written / length)
            if pct != last_pct and pct % 2 == 0:
                elapsed = time.time() - t0
                rate = written / elapsed / 1e6 if elapsed > 0 else 0
                print(f"  download {pct}% ({written/1e9:.2f}/{length/1e9:.2f} GB compressed, {rate:.1f} MB/s)")
                last_pct = pct
    print(f"  download complete: {written/1e9:.2f} GB in {time.time()-t0:.0f}s")


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
        print(f"WARNING: extracted {written} bytes, expected {expected_size}", file=sys.stderr)
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
        print(f"  {member}: compressed={info.compress_size/1e9:.2f}GB uncompressed={info.file_size/1e9:.2f}GB method={info.compress_type}")

        if info.compress_type == zipfile.ZIP_DEFLATED or info.compress_type == zipfile.ZIP_STORED:
            extract_member(zf, member, dest, info.file_size)
            print(f"  HTTP range requests issued: {rf.request_count}, bytes fetched: {rf.bytes_fetched/1e9:.2f} GB")
            return

        # Method 9 (Deflate64) isn't supported by Python's zipfile. Rebuild
        # a sparse local container (real header+data for this member, real
        # central directory/EOCD, everything else a hole) and hand it to
        # 7z, which does support Deflate64.
        if info.compress_type != 9:
            print(f"ERROR: unsupported compression method {info.compress_type}", file=sys.stderr)
            sys.exit(1)

        container = dest.with_suffix(".container.zip")
        print(f"  compression method 9 (Deflate64) — not supported by Python zipfile; using 7z via {container}")
        data_start = build_sparse_single_member_zip(rf, zf, member, container)
        stream_compressed_data_to_file(rf, data_start, info.compress_size, container, data_start)
        print(f"  HTTP range requests issued so far: {rf.request_count}, bytes fetched: {rf.bytes_fetched/1e9:.2f} GB")

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
            sys.exit(1)
        extracted_path = dest.parent / member
        extracted_path.rename(dest)
        # clean up the now-empty extracted directory tree and the container
        for p in sorted(dest.parent.glob("Eclipse/**/*"), reverse=True):
            if p.is_dir():
                p.rmdir()
        (dest.parent / "Eclipse").rmdir() if (dest.parent / "Eclipse").exists() else None
        container.unlink(missing_ok=True)
        print(f"  7z extraction complete in {time.time()-t0:.0f}s -> {dest} ({dest.stat().st_size/1e9:.2f} GB)")


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
