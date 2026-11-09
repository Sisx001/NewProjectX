# .NET analysis

## Decompile (ILSpy)

```sh
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
ilspy target.dll                     # decompile to stdout
ilspy -o outdir/ target.dll          # generate project
ilspy -t Namespace.Class target.dll  # single type
```

Works on .exe/.dll assemblies (Framework and Core). GUI variant:
`~/tools/ilspy/ILSpy` (Avalonia; needs a display or null-driver).

## Debug (.NET Core, DAP)

```sh
export DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1
netcoredbg --server=4711 -- dotnet app.dll
```

Attach with any DAP client (VS Code coreclr adapter, nvim-dap, or agent DAP
bridge). Breakpoints, stepping, variable inspection over the DAP protocol.

## dnSpy status

dnSpy requires Windows .NET Framework 4.7.2 + ICorDebug — not installable in
this sandbox (no Microsoft download access, wine-mono insufficient). ILSpy
covers decompilation; netcoredbg covers Core debugging. For full dnSpy-style
edit-and-continue on Framework binaries, use a Windows host.

## Quick triage recipe

```sh
ilspy target.dll | head -100          # entry point + main types
ilspy -l target.dll                   # list types (if supported by build)
strings -el target.dll | head -40     # UTF-16 strings (C# literals)
```
