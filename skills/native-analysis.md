# Native binary analysis

## Ghidra headless (scriptable, no GUI)

```sh
export JAVA_HOME=$HOME/tools/jdk-25
mkdir -p ~/ghidra-projects
~/tools/ghidra_12.2_PUBLIC/support/analyzeHeadless \
  ~/ghidra-projects proj1 -import target.bin -analysisTimeoutPerFile 600
```

Useful flags: `-postScript <script.java|.py>`, `-scriptPath <dir>`,
`-recursive`, `-processor <name>`, `-cs <cspec>`.

Decompile a function via post-script (Python/Jython):

```python
# DecompileAll.py — dump decompiled C for all functions
from ghidra.app.decompiler import DecompInterface
di = DecompInterface()
di.openProgram(currentProgram)
f = currentProgram.getFunctionManager().getFunctions(True)
with open("/tmp/decomp.c", "w") as out:
    for fn in f:
        res = di.decompileFunction(fn, 60, monitor)
        if res.decompileCompleted():
            out.write(res.getDecompiledFunction().getC())
            out.write("\n\n")
```

## GhidraMCP (agent-driven)

```sh
python3 -m ghidra_mcp --headless --binary target.bin \
  --project ~/ghidra-projects/proj2 --project-name proj2
```

Runs an MCP stdio server exposing decompile/list-functions/xrefs tools.

## radare2 / rizin

```sh
radare2 -A target.bin        # analyze all
# inside r2:
#   afl        list functions
#   pdf @ main decompile-ish print of function
#   s sym.main; pdf
#   axT        xrefs to
#   iz         strings
rizin -A -c "afl; quit" target.bin   # one-shot
```

## angr (symbolic)

```python
import angr
# Note: unicorn emulation backend unavailable in this sandbox (unicornlib
# load failure); CFG + symbolic exploration work, concrete emulation doesn't.
proj = angr.Project("target.bin", auto_load_libs=False)
cfg = proj.analyses.CFGFast()
entry = proj.factory.entry_state()
sm = proj.factory.simulation_manager(entry)
sm.explore(find=lambda s: b"win" in s.posix.dumps(1))
print(sm.found[0].posix.dumps(0))
```
