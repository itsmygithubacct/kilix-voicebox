# Sidecar identity gate and Tauri resources

The desktop bundle must never carry a sidecar that the current build did not
produce and verify. Three standalone tools enforce that, and the Tauri build
hook puts them in front of every release-build route.

None of them builds, stages, downloads or runs anything. They read files that
already exist and either refuse or publish.

## What each tool does

`tools/verify_sidecar_provenance.py` validates one
`kilix.voicebox.sidecar-provenance/candidate-v2` record strictly: exact key
sets at every level, no duplicate keys, no floats, no trailing data, canonical
encoding, and every digest, count, path, ordering and membership rule in the
consumer contract. It binds the record to the **actual** sidecar bytes and
length, to the product `uv.lock` bytes, and to the checked-in input manifest
`.kilix-import/sidecar-inputs.json`. There is no downgrade to `candidate-v1`
and no acceptance inferred from a filename or a schema string.

`tools/prepare_sidecar_resources.py` runs that validation and then publishes
the verified provenance and the complete selected Voice and transcribe licence
bytes as Tauri bundle resources, staged privately and swapped in atomically.

`tools/tauri_build_gate.py` is the fail-closed hook. It refuses before the
frontend build unless it is given explicit authority for the invocation.

## Running the gate

    make sidecar-gate

or directly:

    python3 -I tools/tauri_build_gate.py

The gate takes its authority from the environment. It will not invent it:

| Variable | Meaning |
| --- | --- |
| `VOICEBOX_SIDECAR_HANDOFF` | path to the collector's `sidecar-outputs` handoff (preferred) |
| `VOICEBOX_SIDECAR_BUILD_ID` | this invocation's identity, when no handoff is published |
| `VOICEBOX_SIDECAR_SOURCE_COMMIT` | expected clean product commit |
| `VOICEBOX_SIDECAR_SOURCE_TREE` | that commit's tree |
| `VOICEBOX_SIDECAR_STAGED_INPUTS` | the stager output directory holding the selected notices |
| `VOICEBOX_SIDECAR_PLATFORM` | Rust host tuple naming the installed sidecar |

Exit status is `0` when the gate passes, `2` when something was refused, and
`3` when no build authority was supplied.

## The bridge that is still missing

`scripts/build-server.sh` removes its staging directory when it exits, so the
`kilix.voicebox.sidecar-output/candidate-v1` handoff — and the `build_id` in it
— does not survive to Tauri build time. Until the collector publishes that
handoff beside the installed artifacts, or a build passes the identity through,
**the normal `make build` route stops at the gate with exit 3**. That is
deliberate: the alternative is adopting whatever provenance happens to sit next
to the binary, which is exactly how a stale pair gets shipped.

The producer also still emits `candidate-v1`. Until it emits `candidate-v2`,
the gate refuses with exit 2. Both are integrations for the owners of those
files, not something this consumer works around.

## Build ordering

`make build` runs `build-server` and `build-tauri` as separate serial
sub-makes. They were plain prerequisites before, so `make -j build` could
package while sidecar publication was still in flight. `bun run tauri build`
runs the configured build hook, so `make build-tauri`, a direct
`bun run tauri build` and `cargo tauri build` all reach the same gate exactly
once; the gate never invokes the producer, so running it costs no rebuild.

## Resources and failure

A successful preparation publishes exactly four files under
`tauri/src-tauri/resources/sidecar/`, and `tauri.conf.json` names each of them
explicitly rather than globbing a directory:

- `voicebox-server.provenance.json` — the verified record, byte for byte
- `licenses/kilix-voice/LICENSE` and `licenses/kilix-transcribe/LICENSE`
- `RESOURCES.json` — a receipt naming the build id and each published digest

A failed preparation moves any earlier complete set into
`tauri/src-tauri/resources/.retained/<timestamp>-failed/`. The bytes survive for
inspection, and the published path is left empty, so neither the gate nor Tauri
itself can package a set this build did not verify. All three paths are ignored
by Git: they are build outputs, not source.

## What the gate does not prove

The gate reads the binary, the provenance, the manifest and the lock. Every
claim about the sealed build bundle, the installed distributions, the build
tools and the contents of the frozen archive — including each module's compiled
code digest — is a **producer-bound claim** that this consumer checks for
internal consistency and shape only. The separate real-build and archive review
is what settles those. `--report` writes the split out explicitly, under
`verified_from_actual_files` and `producer_bound_claims`.
