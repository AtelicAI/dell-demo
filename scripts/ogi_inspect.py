"""Inspect data/ogi/source_clip.mp4 (no ffprobe needed) and record what the file itself says.

Writes resolution / frame_rate / duration_s into data/ogi/source_metadata.json and the start time
of every frame into data/ogi/frame_times.json, so the player can map time <-> frame exactly even
when the clip has a variable frame rate. Does not touch status, licence or any other field.

Usage: python3 scripts/ogi_inspect.py
"""
import json
import struct
from pathlib import Path

OGI = Path(__file__).resolve().parents[1] / "data" / "ogi"


def boxes(b, s, e):
    while s + 8 <= e:
        size, kind = struct.unpack(">I4s", b[s:s + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", b[s + 8:s + 16])[0], 16
        elif size == 0:
            size = e - s
        yield kind.decode("latin1"), s + head, s + size
        s += size


def find(b, s, e, path):
    for kind, a, z in boxes(b, s, e):
        if kind == path[0]:
            if len(path) == 1:
                yield a, z
            else:
                yield from find(b, a, z, path[1:])


def first(b, s, e, path):
    return next(find(b, s, e, path))


def video_track(b):
    for ta, tz in find(b, 0, len(b), ["moov", "trak"]):
        hdlr, _ = first(b, ta, tz, ["mdia", "hdlr"])
        if b[hdlr + 8:hdlr + 12] == b"vide":
            return ta, tz
    raise SystemExit("No video track found")


def main():
    b = (OGI / "source_clip.mp4").read_bytes()
    ta, tz = video_track(b)

    mdhd, _ = first(b, ta, tz, ["mdia", "mdhd"])
    if b[mdhd] == 0:
        timescale, duration = struct.unpack(">II", b[mdhd + 12:mdhd + 20])
    else:
        timescale, duration = struct.unpack(">IQ", b[mdhd + 20:mdhd + 32])

    _, tkhd_end = first(b, ta, tz, ["tkhd"])
    width, height = (v >> 16 for v in struct.unpack(">II", b[tkhd_end - 8:tkhd_end]))

    # stts: run-length list of (frame count, frame duration in timescale units).
    stts, _ = first(b, ta, tz, ["mdia", "minf", "stbl", "stts"])
    n = struct.unpack(">I", b[stts + 4:stts + 8])[0]
    times, t = [], 0
    for i in range(n):
        count, delta = struct.unpack(">II", b[stts + 8 + i * 8:stts + 16 + i * 8])
        for _ in range(count):
            times.append(round(t / timescale, 5))
            t += delta

    seconds = duration / timescale
    meta_path = OGI / "source_metadata.json"
    meta = json.loads(meta_path.read_text())
    meta.update(
        resolution=f"{width}x{height}",
        frame_rate=round(len(times) / seconds, 3),
        variable_frame_rate=n > 1,
        duration_s=round(seconds, 3),
        frame_count=len(times),
    )
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    (OGI / "frame_times.json").write_text(json.dumps({"frame_start_s": times, "end_s": round(seconds, 5)}))
    print(f"{width}x{height}, {len(times)} frames, {seconds:.2f}s, avg {len(times) / seconds:.3f} fps, VFR={n > 1}")


if __name__ == "__main__":
    main()
