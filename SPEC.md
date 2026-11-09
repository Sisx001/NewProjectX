# SPEC — binary → REA → SPEC.md → rebuild → oracle → retry

The workbench contract. Every analysis session produces artifacts in this shape.

## Stages

### 1. binary (input)
Target placed under `targets/`. Record hash, format, origin.

### 2. REA (analysis)
Run REA to produce a structured overview, then deepen with the right specialist:

- Native ELF/Mach-O/PE → Ghidra headless + GhidraMCP, radare2/rizin
- Android APK → jadx (decompile), Apktool (resources/manifest), Frida (runtime)
- .NET assembly → ILSpy
- Network behavior → mitmproxy capture
- Symbolic queries / path constraints → angr
- Raw format / file structure → ImHex patterns

### 3. SPEC.md (understanding)
The written reconstruction spec. Required sections:

```markdown
# SPEC: <target>
## Facts
- format, entry points, imports, notable strings (Evidence-backed)
## Behavior
- observed/derived behavior, protocol or state machine
## Interfaces
- functions/methods/IPC/network surface worth rebuilding
## Rebuild plan
- ordered obligations, each with: source Evidence, target module, done-criteria
## Open questions
- what analysis could not resolve
```

### 4. rebuild (craft)
Implement the rebuild per the rebuild plan. `craft-anything-cli` drives the
study → rebuild → verify loop. One module per obligation; never skip the
done-criteria.

### 5. oracle (verify)
Differential check: rebuilt artifact vs original, on the surfaces claimed in
the SPEC (function outputs, protocol traces, resource outputs). Evidence
bundles from REA (`rea compare`) are the canonical comparison format.

### 6. retry (loop)
Any oracle failure becomes a new obligation: re-analyze the mismatched surface
(back to stage 2, scoped), amend SPEC.md, rebuild, re-verify. Loop until the
ledger is clean or the gap is documented.

## Rules

- Every claim in SPEC.md cites Evidence (address, capture, or dump).
- No rebuild step starts without a done-criterion.
- The oracle is the only "done". Human review is the only "true".
