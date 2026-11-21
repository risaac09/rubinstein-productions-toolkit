"""Test helper: an MP4 whose stream says one size and whose decoder returns
another, as a file carrying Frame Cropping side data does (a 'clap' box in the
sample entry, which ffprobe lists as stream side data and ffmpeg applies)."""

import struct
from pathlib import Path


def add_clap(src, dst, top, bottom, left=0, right=0):
    data = bytearray(Path(src).read_bytes())

    def find(box, start, end):
        i = start
        while i < end:
            size, typ = struct.unpack(">I4s", data[i:i + 8])
            if typ == box:
                return i, size
            i += size
        raise KeyError(box)

    path, s, e = [], 0, len(data)
    for b in (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"):
        i, sz = find(b, s, e)
        path.append(i)
        s, e = i + (16 if b == b"stsd" else 8), i + sz
    entry, _ = find(b"avc1", s, e)
    w, h = struct.unpack(">HH", data[entry + 32:entry + 36])
    clap = struct.pack(">I4s8i", 40, b"clap", w - left - right, 1, h - top - bottom, 1,
                       left - right, 2, top - bottom, 2)
    end = entry + struct.unpack(">I", data[entry:entry + 4])[0]
    data[end:end] = clap
    for p in path + [entry]:
        data[p:p + 4] = struct.pack(">I", struct.unpack(">I", data[p:p + 4])[0] + len(clap))
    Path(dst).write_bytes(data)
