# Windows tools under Wine

## Environment

```sh
export PATH="$HOME/tools/wine/bin:$PATH"
export WINEPREFIX="$HOME/wine-prefix"
export WINEDEBUG=-all
export LD_LIBRARY_PATH="$HOME/tools/x11env/lib"   # X11/freetype from conda-forge
```

## x64dbg (headless CLI — the agent path)

```sh
x64dbg-headless -c "init <path-to-exe>"            # load binary
x64dbg-headless -c "init; bp 0x401000; run" bin.exe
x64dbg-headless -cf script.txt bin.exe             # script file
```

Common commands: `init`, `run`, `bp <addr|sym>`, `StepInto`, `StepOver`,
`r` (registers), `d <addr>` (dump memory), `dis.sel()`, `Stop`.

GUI variant: `wine C:\\x64dbg\\release\\x96dbg.exe` (needs display; use
`headless.exe` for automation).

## raddebugger

Staged at `$WINEPREFIX/drive_c/raddbg/raddbg.exe` (v0.9.29-alpha). Windows-only
upstream — usable only against Windows binaries under Wine; no Linux build.
For native Linux debugging use gdb-style workflows via radare2/rizin or
Ghidra; for .NET use netcoredbg.

## dnSpy

Not installable (needs Windows .NET Framework 4.7.2 + ICorDebug). Use ILSpy
(decompile) + netcoredbg (debug).

## Wine quirks

- No X server on this host: GUI apps fail unless a display server is added;
  headless/CLI modes are the reliable path.
- Install Windows software by unzipping into `$WINEPREFIX/drive_c/`.
- Registry edits: `wine regedit` (GUI) or `wine reg add ...`.
- If fonts render wrong, ensure `LD_LIBRARY_PATH` includes `~/tools/x11env/lib`.
