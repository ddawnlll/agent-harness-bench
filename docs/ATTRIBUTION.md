# Attribution and licenses

## This repository

Benchmark code, documentation, charts, task fixtures and measurement records are
released under the MIT license (`docs/LICENSE`, copyright 2026 ddawnlll). Task
fixtures and derived data are released for research reproducibility; they are not
production code and carry no warranty.

No source code from the benchmarked agents is redistributed. Binaries are
identified by version and SHA-256, so provenance is checkable without shipping
third-party software.

## Previous benchmark and chart philosophy

| Project | Role | License |
|---|---|---|
| [`ddawnlll/goal-extension-benchmark`](https://github.com/ddawnlll/goal-extension-benchmark) | chart philosophy, light/dark token mechanism, and the `frame` / `scribble` / `hand_arrow` / `hand_circle` primitives reimplemented in `tools/chartkit.py` | MIT |

The adaptation is attributed in the module docstring and in
[DESIGN.md](DESIGN.md). The previous repository was **not modified**: this is a
new codebase with a new palette, canvas, type scale and figure set. The
`check_contrast.py` and `check_publish.py` check designs also originate there.

## Benchmarked agents

| Agent | Version recorded in the run | Binary SHA-256 | Upstream |
|---|---|---|---|
| Pi-Bolt | `1.5.0 (Pi-Bolt 1.1.4, linux-x64-baseline, JIT off)` | `1eecc82d40ad38e57bc8ebb68a04976cf08cd1366e132ae778cb77f6e894e363` | [`opensec-git/Pi-Bolt`](https://github.com/opensec-git/Pi-Bolt) (MIT) |
| JCode | `jcode v0.99.1 (7206efd0c)` | `059d05f0a167b5496bc5a7bdf963edff8f8a483d61f290847b5765104c476298` | repository URL not recorded in the sealed evidence |
| Hax | `hax v0.6.1` | `377d315e5a1cd75811d5022a82db5ea028c5ee361cb2abf0869ef4e1542ecef5` | repository URL not recorded in the sealed evidence |

JCode and Hax were provided as pre-built binaries. Their upstream repositories
and licenses were not captured in the sealed evidence and are stated as
**unverified** rather than guessed. Anyone redistributing those binaries is
responsible for their licenses; this repository does not.

## Model, provider and third-party code

The coding model was `deepseek-v4.1-flash` at `reasoning_effort=low`, served
through OpenCode Go (`opencode-go`). Names are those the relay observed; no claim
is made about the upstream model's training data or license.

Task repositories, public tests, hidden graders, oracle patches and negative
controls were written for this benchmark using only the Python standard library.
No third-party code was copied verbatim, except the chart primitives noted above,
adapted with attribution from the author's MIT-licensed previous repository.

## No affiliation

This is an independent evaluation. It is not affiliated with, endorsed by or
sponsored by the authors of Pi-Bolt, JCode, Hax or OpenCode Go. Agents are
described from their public behaviour in these nine trials and from the version
strings they reported.
