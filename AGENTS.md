# AGENTS.md — NewProjectX RE Workbench

Instructions for any agent session working in this repository.

## First action in a new session

Check if the workbench is installed; if not, bootstrap it:

```sh
[ -x ~/tools/bin/jadx ] && [ -x ~/tools/r2/bin/radare2 ] && [ -d ~/tools/ghidra_12.2_PUBLIC ] \
  && echo INSTALLED || sh setup.sh
```

`setup.sh` rebuilds everything user-space in a few minutes (no root needed;
uses `gh release download`, `pip`, `npm`, `git` — raw `curl`/`wget`/`apt` are
blocked in this sandbox). After it finishes, `source ~/.bashrc`.

## Environment (after bootstrap)

- `JAVA_HOME=$HOME/tools/jdk-25` — required by Ghidra 12.2 (min JDK 25)
- PATH additions: `~/.npm-global/bin`, `~/tools/bin`, `~/tools/r2/bin`, `~/tools/rizin/bin`, `~/.local/bin`
- `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` — required by ILSpy
- `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` also needed for netcoredbg
- mitmproxy CA: `~/.mitmproxy/mitmproxy-ca-cert.pem`
- Wine prefix: `~/wine-prefix` (x64dbg at `C:\x64dbg`, raddebugger at `C:\raddbg`)

## Tool quick reference

| Task | Command |
|---|---|
| App/binary overview | `rea analyze <target>` |
| Native decompile | `~/tools/ghidra_12.2_PUBLIC/support/analyzeHeadless <proj-dir> <proj> -import <bin> -postScript decompile.cs` |
| Agent-driven Ghidra | MCP server: `python3 -m ghidra_mcp --headless --binary <bin> --project <dir> --project-name <name>` |
| Quick disassembly | `radare2 -A <bin>` / `rizin -A <bin>` |
| APK → Java | `jadx -d out/ target.apk` |
| APK resources/manifest | `apktool d target.apk` |
| Runtime hooking (Android/native) | `frida -U -l script.js <app>` |
| .NET decompile | `ilspy <assembly.dll>` |
| .NET Core debug (DAP) | `netcoredbg --server` |
| Windows binary debug | `x64dbg-headless -c "<cmd>" <binary>` (Wine) |
| Hex + structure | `imhex` (GUI) or ImHex pattern language files |
| Traffic capture | `mitmproxy` / `mitmdump -w flow.mitm` |
| Symbolic execution | `python3 -c "import angr"` — see skills/symbolic.md |
| Rebuild/verify loop | `craft-anything-cli` |
| Evidence comparison | `rea compare <bundleA> <bundleB>` |

## Pipeline

Follow `SPEC.md` exactly: `binary → REA → SPEC.md → rebuild → oracle → retry`.

- Put targets in `targets/`
- Every claim in a SPEC must cite Evidence (address, capture, dump)
- The oracle decides "done"; never skip a failed check

## Skills

Detailed playbooks live in `skills/`:

- `skills/native-analysis.md` — Ghidra headless, r2/rizin workflows
- `skills/android.md` — jadx, apktool, frida
- `skills/dotnet.md` — ILSpy, netcoredbg
- `skills/network.md` — mitmproxy setup and capture
- `skills/symbolic.md` — angr scripts
- `skills/hex-formats.md` — ImHex patterns
- `skills/windows-wine.md` — x64dbg headless, Wine quirks

## Reference material

Cloned under `~/re-lab/`: `rea`, `craft-anything`, `GhidraMCP`,
`awesome-ai-reverse-engineering`, `awesome-reverse-engineering`.
