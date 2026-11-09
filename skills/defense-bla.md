# BLA — Counter-REA defense (blue side)

BLA (`bla-agents` v3.2.1) protects **your own** release artifacts against AI
RE agents like REA. Defense-side only — never harden artifacts you don't own.

## Relationship to REA

- REA = red team (analysis, spec, rebuild)
- BLA = blue team (poison → exhaust → hallucinate)
- Together they enable red-vs-blue loops: harden with BLA, attack with REA,
  measure token amplification and detection.

## CLI playbooks

```sh
bla inspect-release targets/app   # read-only exposure check (sourcemaps, symbols)
bla audit targets/app             # REA exposure & vulnerability scan
bla harden targets/app            # default intensity: bankrupt
bla harden targets/app --intensity stealth    # low-visibility hardening
bla defend targets/app            # inspect -> audit -> protect -> verify, one shot
bla verify targets/app [orig]     # verify invariants + token amplification
bla lint targets/app              # packaging gate (exit 2 = refuse)
bla doctor                        # engine readiness
bla mcp                           # MCP stdio server (25 defense tools)
```

Deterministic builds: pass `--seed <seed>` (stored per-project in `.blarc`).

Manifest verification (release pipeline):

```sh
bla verify-manifest .bla-manifest.json --public-key pub.pem
```

## MCP

`bla mcp` is wired in `mcp.json` as server `bla`. Use its defense tools
(audit/harden/verify/lint) through the MCP interface when working agent-side.

## Red-vs-blue loop recipe

1. `bla defend targets/app` — harden
2. `rea analyze targets/app` — attack; record Evidence
3. `bla verify` + compare Evidence bundles — did the shield force
   hallucination/exhaustion? Token cost delta is the metric.
4. Iterate: adjust `--intensity`, re-lint, re-verify.

## Ethics / legality

BLA's own DISCLAIMER: defensive use on **your own** IP only. Red-team loops
must target artifacts you own or are authorized to test.
