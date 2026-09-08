# Building the sidecar: the normal-build bridge

`make build` produces the sidecar and packages the desktop bundle in one
sequence: a new invocation identity, one producer run, the consumer, and only
then packaging. Nothing on this path has a default. Every selected input is
named by the caller, because a workspace path baked into the build would
quietly decide what a release contains.

## What you must supply

| Flag | Variable | Meaning |
| --- | --- | --- |
| `--stage-root` | `VOICEBOX_SIDECAR_STAGE_ROOT` | existing directory **outside** the checkout to build in |
| `--python-executable` | `VOICEBOX_SIDECAR_PYTHON` | the selected CPython 3.12.8 executable |
| `--voice-source` | `VOICEBOX_SIDECAR_VOICE_SOURCE` | the selected Voice source checkout |
| `--transcribe-source` | `VOICEBOX_SIDECAR_TRANSCRIBE_SOURCE` | the selected transcribe source checkout |
| `--transcribe-wheel` | `VOICEBOX_SIDECAR_TRANSCRIBE_WHEEL` | the selected transcribe wheel |
| `--wheelhouse` | `VOICEBOX_SIDECAR_WHEELHOUSE` | the selected 35-wheel wheelhouse |
| `--python-home` | `VOICEBOX_SIDECAR_PYTHON_HOME` | the selected Python home |
| `--platform` | `VOICEBOX_SIDECAR_PLATFORM` | Rust host tuple naming the installed sidecar |

Omit any of them and the build stops with exit **3**, naming each one. It never
picks an ambient checkout, an installed interpreter or a previous record.

## What happens, in order

1. `tools/build_sidecar.py` generates the invocation identity — a fresh 32-hex
   nonce, created at the start of this build, outside the source tree. It is
   never adopted from a record that already exists.
2. It reads the **actual** product commit and tree from the checkout and
   refuses unless the checkout is clean, so the sidecar is bound to exact
   committed source.
3. `scripts/build-server.sh` runs the producer **once**, executing the selected
   interpreter by absolute path with `-I -B -S` in a constructed environment:
   `PATH=/usr/bin:/bin`, a private `HOME` and `TMPDIR` for this build,
   `PYTHONNOUSERSITE`, `PYTHONSAFEPATH` and `PYTHONDONTWRITEBYTECODE` set, and
   no ambient `PYTHONPATH`, `PYTHONHOME` or loader injection surviving. **No uv
   or pip is invoked and nothing is resolved or installed.**
4. The collector verifies the handoff against this build's identity and
   publishes the binary and provenance as a pair, keeping the previous pair
   until both replacements succeed. That rollback behaviour is unchanged.
5. The stage is **kept** at this point, because the consumer still needs the
   producer's real selected-input notices.
6. `tools/prepare_sidecar_resources.py` verifies the provenance against the
   same identity this build generated — handed straight through, not
   rediscovered — and publishes the Tauri resources.
7. Only a complete, verified build removes its stage. A build that fails at any
   step keeps it and prints where it is.
8. Packaging runs last, told which build the gate passed for.

## Routes

- `make build` — the whole sequence above.
- `make build-sidecar` — everything except packaging.
- `bun run tauri build` or `cargo tauri build` — the build hook reaches the same
  path. If the gate has already passed for this build it verifies that work
  rather than repeating the producer; otherwise it runs the whole bridge
  itself. Without explicit inputs it refuses, and **never** packages whatever
  binaries or resources happen to be lying in the tree.
- `make build-server BUILD_SERVER_ARGS='…'` — the collector alone, with inputs
  you name yourself. It produces and publishes a pair but runs **no consumer**
  and prepares **no** Tauri resources, so it is for producing and inspecting a
  sidecar, never for packaging a release.

## Failure and recovery

A failed producer keeps its stage, including `build.log`, for inspection; there
is no successful handoff in it and it is never reused. A failed publication
restores the previous pair and, if that restoration is itself incomplete,
prints the recovery directory rather than leaving an unknown state. A failed
resource preparation moves any earlier complete resource set aside intact, so
its bytes survive while nothing stale can be packaged.

These are failure-consistent, serialized private-builder guarantees. They are
not crash atomicity, and they assume one builder and one packager at a time.

## What this does not do

It does not run an actual PyInstaller build, open an archive, compile Rust or a
frontend, package a bundle, execute the sidecar, acquire or install anything,
or touch a model. The producer itself is owned elsewhere; this is the
invocation bridge to it.
