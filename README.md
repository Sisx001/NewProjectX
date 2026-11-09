# NewProjectX — RE Workbench

Reverse-engineering workbench: analysis → spec → rebuild → verify, end to end.

## Pipeline

```
binary → REA → SPEC.md → rebuild → oracle → retry
```

See [SPEC.md](SPEC.md) for the pipeline contract and [mcp.json](mcp.json) for the MCP plugin wiring.

## [01] Analysis core

| Tool | Purpose | Install |
|---|---|---|
| [REA](https://github.com/morluto/rea) | One MCP connecting everything RE | `npm i -g rea-agents` → `rea` (v6.8.0) |
| [Craft Anything](https://github.com/storytold/craft-anything) | study → rebuild → verify loop | `craft-anything-cli` (v0.5.0) |
| [Ghidra](https://github.com/NationalSecurityAgency/ghidra) | Decompiler, free | `~/tools/ghidra_12.2_PUBLIC` (JDK 25) |
| [GhidraMCP](https://github.com/LaurieWired/GhidraMCP) | Agent control of Ghidra | extension in `Extensions/Ghidra/` + `ghidra-mcp` pip pkg |

## [02] Mobile and apps

| Tool | Purpose | Install |
|---|---|---|
| [jadx](https://github.com/skylot/jadx) | APK → Java | `jadx` (v1.5.6) |
| [Apktool](https://github.com/iBotPeaches/Apktool) | APK dis/reassembly | `apktool` (v3.0.3) |
| [Frida](https://github.com/frida/frida) | Runtime hooking | `frida` (v17.26.2) |
| [ILSpy](https://github.com/icsharpcode/ILSpy) | .NET decompiler | `ilspy` (v11.2.0) |

## [03] Binaries and low level

| Tool | Purpose | Install |
|---|---|---|
| [radare2](https://github.com/radareorg/radare2) | Terminal RE framework | `radare2` (v6.2.7) |
| [x64dbg](https://github.com/x64dbg/x64dbg) | Windows debugger | Windows-only — not installable on this host |
| [ImHex](https://github.com/WerWolv/ImHex) | Hex editor + pattern language | `~/tools/imhex/usr/bin/imhex` (v1.38.1) |
| [angr](https://github.com/angr/angr) | Symbolic execution | `python3 -c "import angr"` (v10.0.5) |

## [04] Network and execution

| Tool | Purpose | Install |
|---|---|---|
| [mitmproxy](https://github.com/mitmproxy/mitmproxy) | Traffic interception | `mitmproxy` (v12.3.1) |
| [dnSpy](https://github.com/dnSpyEx/dnSpy) | .NET debug/edit | Windows-only — use ILSpy here |
| [rizin](https://github.com/rizinorg/rizin) | radare2 fork | `rizin` (v0.9.1, static) |
| [raddebugger](https://github.com/EpicGames/raddebugger) | Epic Games debugger | Windows-only release (raddbg.exe) |

## [05] Rebuild and verify

- [awesome-ai-reverse-engineering](https://github.com/zhuyansen/awesome-ai-reverse-engineering) — 122 repos, rated
- [awesome-reverse-engineering](https://github.com/morluto/awesome-reverse-engineering) — curated by the REA creator

Both cloned under `~/re-lab/`.

## Environment

- Host: Debian 13, x86_64, user-space install (no root)
- `JAVA_HOME=$HOME/tools/jdk-25` (Temurin 25 — required by Ghidra 12.2)
- PATH: `~/.npm-global/bin`, `~/tools/bin`, `~/tools/r2/bin`, `~/tools/rizin/bin`, `~/.local/bin`
- `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` (required by ILSpy, no libicu on host)
- Tool sources cloned under `~/re-lab/`
