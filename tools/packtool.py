#!/usr/bin/env python3
"""Minimal PFH4 .pack reader for Total War Attila: list and extract files."""
import struct, sys, os

def read_index(f):
    hdr = f.read(28)
    magic, flags, pf_count, pf_size, file_count, file_index_size, ts = struct.unpack("<4sIIIIII", hdr)
    if magic != b"PFH4":
        raise SystemExit(f"unsupported pack magic {magic}")
    has_ts = bool(flags & 0x40000000) or False  # extended header flag varies; detect below
    f.read(pf_size)  # skip dependent pack names
    index_data = f.read(file_index_size)
    data_offset = f.tell()

    def parse(with_ts):
        entries = []
        pos = 0
        for _ in range(file_count):
            size = struct.unpack_from("<I", index_data, pos)[0]
            pos += 4
            if with_ts:
                pos += 4
            end = index_data.index(b"\x00", pos)
            path = index_data[pos:end].decode("latin-1")
            pos = end + 1
            entries.append((path, size))
        if pos != len(index_data):
            raise ValueError("index size mismatch")
        return entries

    for with_ts in (False, True):
        try:
            entries = parse(with_ts)
            break
        except (ValueError, IndexError, struct.error):
            entries = None
    if entries is None:
        raise SystemExit("could not parse file index")
    return entries, data_offset

def main():
    pack, mode = sys.argv[1], sys.argv[2]
    with open(pack, "rb") as f:
        entries, data_offset = read_index(f)
        if mode == "list":
            pat = sys.argv[3].lower() if len(sys.argv) > 3 else ""
            for path, size in entries:
                if pat in path.lower():
                    print(f"{size:>10} {path}")
        elif mode == "extract":
            pat, outdir = sys.argv[3].lower(), sys.argv[4]
            off = data_offset
            for path, size in entries:
                if pat in path.lower():
                    f.seek(off)
                    data = f.read(size)
                    dest = os.path.join(outdir, path.replace("\\", "/"))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as o:
                        o.write(data)
                    print(dest)
                off += size

if __name__ == "__main__":
    main()
