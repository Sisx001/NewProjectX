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
| [x64dbg](https://github.com/x64dbg/x64dbg) | Windows debugger | `x64dbg-headless` (snapshot 2026-05-27, headless CLI under Wine 11.21) |
| [ImHex](https://github.com/WerWolv/ImHex) | Hex editor + pattern language | `~/tools/imhex/usr/bin/imhex` (v1.38.1) |
| [angr](https://github.com/angr/angr) | Symbolic execution | `python3 -c "import angr"` (v10.0.5) |

## [04] Network and execution

| Tool | Purpose | Install |
|---|---|---|
| [mitmproxy](https://github.com/mitmproxy/mitmproxy) | Traffic interception | `mitmproxy` (v12.3.1) |
| [dnSpy](https://github.com/dnSpyEx/dnSpy) | .NET debug/edit | Needs Windows .NET Framework (unavailable headless) — covered by ILSpy + netcoredbg |
| [rizin](https://github.com/rizinorg/rizin) | radare2 fork | `rizin` (v0.9.1, static) |
| [raddebugger](https://github.com/EpicGames/raddebugger) | Epic Games debugger | v0.9.29 staged in `~/wine-prefix/drive_c/raddbg` (Windows-only release; no Linux build) |

## [05] Rebuild and verify

- [awesome-ai-reverse-engineering](https://github.com/zhuyansen/awesome-ai-reverse-engineering) — 122 repos, rated
- [awesome-reverse-engineering](https://github.com/morluto/awesome-reverse-engineering) — curated by the REA creator

Both cloned under `~/re-lab/`.

## [Extra] .NET debugging (dnSpy substitute)

| Tool | Purpose | Install |
|---|---|---|
| [netcoredbg](https://github.com/Samsung/netcoredbg) | .NET Core debugger (DAP) | `netcoredbg` (v3.2.0) |

## [Extra] Defense — BLA (Counter-REA)

| Tool | Purpose | Install |
|---|---|---|
| [BLA](https://github.com/qybaihe/bla) | Adversarial shield protecting artifacts against RE agents | `bla` (v3.2.1) |

BLA is the **red team's opponent**, not a companion: REA attacks, BLA defends
(poison → exhaust → hallucinate). Both installed so the workbench can run
full red-vs-blue loops:

```sh
bla audit targets/app          # how exposed is the artifact to REA?
bla harden targets/app         # bankrupt-intensity hardening (default)
bla defend targets/app         # full pipeline: inspect -> audit -> protect -> verify
bla verify targets/app         # verify defense invariants + token amplification
rea analyze targets/app        # then attack it from the red side
```

BLA MCP server (25 defense tools) is wired in `mcp.json`. See
`skills/defense-bla.md` for the playbooks.

## Windows tools under Wine

Wine 11.21 Staging installed user-space (`~/tools/wine`), prefix at `~/wine-prefix`.
X11/FreeType libs come from a conda-forge env (`~/tools/x11env`) since the host
has none and apt is unavailable.

- **x64dbg**: `x64dbg-headless -c "<command>" <binary>` — scriptable debugger CLI,
  verified running (enters command loop). GUI also present at `C:\x64dbg`.
- **raddebugger**: staged at `C:\raddbg` (Windows-only upstream, no Linux build).
- **dnSpy**: NOT installable — requires real Windows .NET Framework 4.7.2+
  (Microsoft servers unreachable, wine-mono insufficient for ICorDebug).
  Decompile with ILSpy, debug .NET Core with netcoredbg.

## [06] Agent skills & Burp

| Tool | Purpose | Install |
|---|---|---|
| [hallmark](https://github.com/Nutlope/hallmark) | Anti-AI-slop design skill (50 themes, redesign/audit) | installed at `~/.claude/skills/hallmark` (v2.0.0) — invoke via `/hallmark <prompt>`, `/hallmark redesign <path>`, `/hallmark audit <path>` |
| [burp-sectivai](https://github.com/zack0x01/burp-sectivai) | Skill-driven AI security review for Burp Suite (IDOR/XSS/SQLi/SSRF dashboard) | JAR staged at `~/tools/burp-extensions/sectiv-1.0.0.jar` — load via Burp → Extensions → Add → Java, then set AI provider key in its Settings tab |

Sectiv notes: prebuilt JAR verified (Montoya API, Kotlin shadow jar). Burp Suite itself is a
proprietary GUI app — run it on your desktop and load the staged JAR; in this sandbox the
extension is ready but Burp cannot host it. Its AI review only reasons over traffic you select.

## Environment

- Host: Debian 13, x86_64, user-space install (no root)
- `JAVA_HOME=$HOME/tools/jdk-25` (Temurin 25 — required by Ghidra 12.2)
- PATH: `~/.npm-global/bin`, `~/tools/bin`, `~/tools/r2/bin`, `~/tools/rizin/bin`, `~/.local/bin`
- `DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1` (required by ILSpy, no libicu on host)
- mitmproxy CA generated at `~/.mitmproxy/mitmproxy-ca-cert.pem`
- Tool sources cloned under `~/re-lab/`

## [07] Forensics & data carving

| Tool | Purpose | Install |
|---|---|---|
| [YARA](https://github.com/VirusTotal/yara) | Pattern matching / rules | `yara-python` (v4.5.4) |
| [Unicorn](https://github.com/unicorn-engine/unicorn) | CPU emulation | `unicorn` (v2.1.4) |
| [Capstone](https://github.com/capstone-engine/capstone) | Disassembly framework | `capstone` (v5.0.7) |
| [pefile](https://github.com/erocarrera/pefile) | PE parsing | `pefile` |
| [z3](https://github.com/Z3Prover/z3) | Constraint solver | `z3-solver` |
| [Volatility 3](https://github.com/volatilityfoundation/volatility3) | Memory forensics | `volatility3` (`vol`) |
| [pwntools](https://github.com/Gallopsled/pwntools) | Exploit development | `pwntools` (`pwn`) |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | Adaptive web scraping | `scrapling` (v0.5.1; browser fetchers unavailable — CDN blocked) |
| [UPX](https://github.com/upx/upx) | Packer/unpacker | `upx` (v5.3.0) |
| [7-Zip](https://github.com/ip7z/7zip) | Archive engine | `7zz` (v26.06) |
| [Detect It Easy](https://github.com/horsicq/DIE-engine) | Compiler/packer detection | `diec` (v4.0.0, wrapper with Qt5 libs + `-D` db path) |
| [binwalk](https://github.com/ReFirmLabs/binwalk) | Firmware/data carving | `binwalk` (v3.1.0, cargo; built with `RUST_FONTCONFIG_DLOPEN=on`) |

Quick use:

```sh
vol -f memory.dmp windows.info          # memory forensics
binwalk firmware.bin                    # embedded content scan
upx -d packed.exe                       # unpack
diec /tmp/ls_test                       # detect compiler/packer
python3 -c "import yara, unicorn, capstone, pefile, z3"  # all importable
```

## [08] Agent-side skills & site tooling

| Tool | Purpose | Install |
|---|---|---|
| [unslop](https://github.com/heydamir/unslop) | Turns AI-looking UI into designed UI | `~/.claude/skills/unslop` |
| [ponytail](https://github.com/dietrichgebert/ponytail) | Laziest-senior-dev discipline (6 skills: review/audit/debt/gain) | `~/.claude/skills/ponytail*` |
| [api-anything](https://github.com/goodnight000/api-anything) | Turns websites into agent-callable APIs | configured via `npx api-anything setup` — **local-only mode** (nothing shared/reported) |
| [ai-website-cloner-template](https://github.com/JCodesMore/ai-website-cloner-template) | Clone a site's look into a fresh project | `~/re-lab/ai-website-cloner-template` (deps installed, ready to copy per project) |
| [Awesome-REA](https://github.com/reacher-z/Awesome-REA) | Curated RE list: web, games, apps, firmware | `~/re-lab/Awesome-REA` |

## Reference material

- [awesome-ai-reverse-engineering](https://github.com/zhuyansen/awesome-ai-reverse-engineering)
- [awesome-reverse-engineering](https://github.com/morluto/awesome-reverse-engineering)
- [Awesome-REA](https://github.com/reacher-z/Awesome-REA)

All cloned under `~/re-lab/`.
