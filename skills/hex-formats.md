# Hex / file formats (ImHex)

## Binary location

`~/tools/imhex/usr/bin/imhex` (GUI; needs a display — this sandbox is
headless, so prefer the pattern-language workflow below or run under a
virtual display if one is added).

## Pattern language (headless-friendly)

ImHex patterns (`.hexpat`) describe binary structures. Keep reusable patterns
in `patterns/` in this repo.

```c
// patterns/example.hexpat
struct Header {
    u32 magic[[color("FF0000")]];
    u16 version;
    u16 numEntries;
};

struct Entry {
    u32 id;
    u32 offset;
    u32 size;
};

Header header @ 0x00;
Entry entries[header.numEntries] @ 0x08;
```

Documentation: https://docs.werwolv.net/imhex/pattern-language

## CLI alternatives for the same job

```sh
xxd target.bin | head -20           # quick hex dump
file target.bin                     # type detection
python3 - <<'PY'                    # scripted struct parsing
import struct
data = open("target.bin","rb").read()
magic, ver, n = struct.unpack_from("<IHH", data, 0)
print(hex(magic), ver, n)
PY
```

## Rule

When a format must be understood (not just parsed), document the structure in
SPEC.md with byte offsets as Evidence before writing the parser.
