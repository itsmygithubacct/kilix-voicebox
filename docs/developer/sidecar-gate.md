# Sidecar identity gate and Tauri resources

The desktop bundle must never carry a sidecar that the current build did not
produce and verify. Three standalone tools enforce that, and the Tauri build
hook puts them in front of every release-build route.

None of them builds, stages, downloads or runs anything. They read files that
already exist and either refuse or publish.

## What each tool does

`tools/verify_sidecar_provenance.py` validates one
`kilix.voicebox.sidecar-provenance/candidate-v3` record strictly: exact key
sets at every level, no duplicate keys, no floats, no trailing data, canonical
encoding, and every digest, count, path, ordering and membership rule in the
consumer contract. It binds the record to the **actual** sidecar bytes and
length, to the product `uv.lock` bytes, and to the checked-in input manifest
`.kilix-import/sidecar-inputs.json`. Both earlier `candidate-v1` and
`candidate-v2` records refuse, as do unknown future schemas. A schema string
alone conveys no acceptance.

The exact selected manifest is `kilix.voicebox.sidecar-inputs/v2`, SHA256
`fa26ea1c27217be6f9eb4509bd84e3c0b8f4c02a95913d5cd2cadd0c352b59d2`.
It selects 20 files and 16 modules, adding only the existing transcribe
`output.py` needed by its package initializer. Source commits/trees, the whole
transcribe wheel and the four plaintext resources stay fixed. Completion must
be `kilix.voicebox.sidecar-stage/v2`, with every selected row and exact
repository/commit/tree identity. Its digest uses the original sorted,
two-space-indented JSON plus LF; the outer provenance uses sorted compact
ASCII-escaped JSON plus LF. The two encodings are not interchangeable.

The selected `kilix_transcribe.output` archive module must have the source
digest bound by that completion. Missing output, extra modules/providers,
old manifest/completion hashes or mixed versions refuse. These checks read
metadata and actual supplied files; they do not run the selected package.

The consumer requires exactly the 35 normalized distribution names and versions
selected for CPython 3.12.8 on Linux x86_64 by the unchanged lock's runtime
requirements and `build` group. The selection includes `uvicorn[standard]`
(httptools, python-dotenv, pyyaml, uvloop, watchfiles and websockets), greenlet
for x86_64 and typing-extensions for Python below 3.13. It excludes the dev
group, other-platform dependencies and packages absent from the lock.

`LOCKED_DISTRIBUTIONS` is an explicit selection bound to the exact raw lock
SHA256 `c32006e700bde9801f6e427d2c1370f6ff3a1bb6b7259e2468c414345de89aa6`.
The CLI hashes the actual `--uv-lock` file; a record's self-reported digest is
insufficient. Another lock refuses even if its digest agrees with the record.
The library `validate()` interface still expects the caller's independently
computed actual-file digest, as it does for the binary. A lock update needs a
new reviewed selection; this consumer has no permissive generic resolver or
ambient packaging import. The focused lock-selection tests reproduce the
complete 48-record lock traversal, including active markers and extras.

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

This isolated consumer successor retains the older producer, stager, import
controls and build bridge. Their coherent migration is a separate integration;
they are not a complete normal-build route on this branch. The producer must
emit `candidate-v3` with completion-v2 and the exact manifest-v2 population
before the consumer can accept it. The seven-field
`kilix.voicebox.sidecar-output/candidate-v1` handoff stays unchanged. There is
no fallback to an older record or an earlier invocation's artifacts.

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
claim about the sealed build bundle, distribution RECORD/member bytes, the build
tools and the contents of the frozen archive — including each module's compiled
code digest — is a **producer-bound claim** that this consumer checks for
internal consistency and shape only. The separate real-build and archive review
is what settles those. `--report` writes the split out explicitly, under
`verified_from_actual_files` and `producer_bound_claims`.

Distribution name/version equality with the selected lock is checked locally;
it is not deferred to the producer. The controlled 35-distribution test fixture
uses synthetic member/RECORD payloads, including a retained legacy first-row
duplicate-member sentinel. Its validity proves consumer behavior, never actual
installed package, wheel archive or sidecar build identities.

The focused controls support both tests-directory imports and the repository's
package discovery semantics. A scoped invocation is
`python3 -I -m unittest discover -s kilix_voicebox_backend/tests -t . -p 'test_sidecar_[glv]*.py' -v`.
It retains the Makefile's isolated Python and explicit top-level directory;
only the filename pattern narrows the lane. The lock/v3 controls use relative
imports under package discovery and direct imports under a tests-directory
runner, with the same assertions in both cases. This scoped command is not the
full `check-backend` dependency, lint or installed-environment gate. The scoped
pattern selects gate, lock-selection and v3 controls; producer/collector output
tests require their separate execution scope.
