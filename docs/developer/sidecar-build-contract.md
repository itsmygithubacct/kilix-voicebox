# Sidecar provenance consumer contract

This document fixes `kilix.voicebox.sidecar-provenance/candidate-v2`. It is a
candidate build/consumer contract, not release approval. The collector's
separate `kilix.voicebox.sidecar-output/candidate-v1` handoff stays unchanged.
Its build_id, binary relative path/hash/size and provenance relative path/hash
must all identify the same invocation and actual files. A consumer must not
infer approval from a schema string or reuse a previous invocation's files.

## Envelope and parsing

Provenance is UTF-8 JSON, at most 16 MiB, depth at most 32. Reject duplicate
object keys at every depth, unknown or missing keys at every defined object,
NaN/infinities/floats, noncanonical digests, malformed UTF-8 and trailing data.
The producer serializes with sorted keys, separators `,` and `:`, ASCII JSON
escaping and one final LF. Whenever this contract hashes a JSON object, it
hashes that exact canonical encoding including the LF; ordinary JSON textual
reformatting is not a second valid digest interpretation.

The top-level object has exactly these eleven keys:

| Key | Required value/type |
| --- | --- |
| schema | `kilix.voicebox.sidecar-provenance/candidate-v2` |
| build_id | 8–64 lowercase hexadecimal characters; exact handoff build_id |
| source_commit | 40 lowercase hex characters; clean exact product commit |
| source_tree | 40 lowercase hex characters; that commit's complete Git tree |
| uv_lock_sha256 | 64 lowercase hex; exact product uv.lock bytes |
| sidecar_sha256 | 64 lowercase hex; actual sidecar file bytes |
| sidecar_bytes | Positive integer; exact actual sidecar length |
| contains_model_runtime | The JSON boolean `false`, not 0 or a string |
| provider_endpoints | Exactly `["kilix-qwen-tts.sock","kilix-transcribe.sock"]` in this order |
| inputs | The exact staged-input object below |
| build | The complete build-input and consumed-archive object below |

Integers are strict JSON integers, never booleans, in 0..2^53-1; counts/sizes
are additionally subject to the limits below. Digest strings use lowercase
hex only. Logical file paths are nonempty POSIX relative paths without an
empty component, `.`, `..`, backslash or NUL, at most 1,024 UTF-8 bytes.
Arrays described as sorted use bytewise UTF-8 ordering of the stated key,
have no duplicate keys, and have at most 40,000 members unless narrowed below.
Name/path/digest rows must identify actual selected bytes, not just filenames
or version labels. No local checkout/cache/HOME paths belong in provenance.

## Staged inputs and plaintext

`inputs` has exactly `completion_sha256`, `completion`, and `plaintext`.

`completion` is the entire unchanged C19 `kilix.voicebox.sidecar-stage/v1`
record. It has exactly `schema`, `input_manifest_sha256`, `sources`, `wheel`,
`observed_source_modes`, `observed_wheel_mode`, and `files`, with their exact
selected population from `.kilix-import/sidecar-inputs.json`. Its own canonical
encoding remains C19's sorted/indented-two-spaces UTF-8 JSON plus LF, **not**
the outer compact encoding. `completion_sha256` hashes that C19 encoding.
The selected input manifest digest must be
`63a8498f47b1c8c1daf78a4d62b3f98bb6ba53a29f8caeb9929395163b5f2d8e`.

The sources object contains exactly voice/transcribe; each has exactly
repository/commit/tree, matching the manifest. Voice commit is
228ac7ae5b1ceb6b8065f3bc1342f90bbc740345 and transcribe commit is
0c61eb4edcdc75e1aca993ef7c8673b3b9aed6a9. The wheel object has exactly
filename/bytes/sha256, matching kilix_transcribe-0.1.0-py3-none-any.whl,
46,454 bytes and
5d6ffab4c5237a45107eff9a23a177ae23759018e3cea60c19f7509bc592f2fb.
Observed source-mode keys are exactly the 19 selected source names prefixed
by voice/ or transcribe/; values are `0600` or `0644`. Observed wheel mode is
one of those two strings. `files` has the exact 19 selected rows, sorted by
path, each with exactly path/bytes/sha256/mode/role. Mode is `0600`; each
other value must match the checked-in input manifest. No additional worker,
CLI, Qwen, Content or model input may be added through completion metadata.

`inputs.plaintext` has exactly four rows, sorted by path, each with exactly
path/bytes/sha256. The paths inside the extracted sidecar are:

- `VERSION`
- `licenses/kilix-transcribe/LICENSE`
- `licenses/kilix-voice/LICENSE`
- `voicelib/microphone_supervisor.py`

Their bytes/digests equal the corresponding completion entries (strip only
the `python/` prefix for VERSION/helper). The helper is a real plaintext file
beside the frozen microphone module's `__file__` location; its unchanged
`/usr/bin/python3 -I -B -S` contract must work. VERSION is at voicelib's parent.
Both licenses are the complete selected source license bytes, not summaries.

## Build population

`build` has exactly `python`, `pyinstaller`, `distributions`, `tools`,
`snapshot`, and `archive`.

`snapshot` has exactly `sha256` and `files`. sha256 binds the actual sealed
build-input bundle consumed by the isolated build. files is the complete
sorted-by-path population of that bundle, with exact path/bytes/sha256/mode
rows. Mode is a four-digit octal string (`0600` or `0700`). Individual files
are at most 128 MiB, the complete bundle at most 512 MiB. Prefixes distinguish
`source/`, `projection/`, `python/`, `env/` and `host/`; there are no undeclared
files or arbitrary extraction paths. These rows bind all consumed product
source, the selected projection, interpreter/runtime, selected package files
and bootstrap/build tools, rather than relying on ambient site packages.

`python` has exactly implementation/version/executable/members.
implementation is `CPython`; version is `3.12.8`; executable is the logical
snapshot path `python/bin/python3.12`. members is the complete sorted list
of snapshot paths under python/, including that executable and its selected
standard-library/shared-library files. Each path resolves to one snapshot
file row; none may be outside that population.

`pyinstaller` has exactly distribution/version, values `pyinstaller` and
`6.15.0`. Its code population is the corresponding distributions row and
snapshot file rows, not an ambient imported package with matching metadata.

`distributions` is sorted by normalized name. Each row has exactly
name/version/record_sha256/members. Names are lowercase package names with
hyphens, matching the exact Linux x86_64 CPython3.12 runtime plus build-group
dependency population resolved from the unchanged uv.lock. No dev/provider/
model distributions are implicitly accepted. Version must match that lock
selection. record_sha256 identifies that distribution's actual RECORD bytes;
members is the complete sorted list of its files under env/, each present
once in snapshot.files. Missing, extra, duplicate, changed or shared members
refuse. Installed RECORD identities are not represented as proof of an
unretained original wheel archive. Any archive identity claim needs separate
actual archive bytes/hash verification.

`tools` is sorted by name. Each row has exactly name/member. Name identifies
the selected bootstrap or native build tool, and member references its exact
host/ snapshot file row. Every selected host/ file has one such tool row;
libraries use a stable logical path as their name. Tool/library inputs are
bound as bytes, including the actual bootstrap executable and native shared
dependencies. Kernel/OS release approval is not inferred from these rows.

## Consumed binary archive

`archive` has exactly `selected_modules`, `plaintext`, `entries`, and `options`.

selected_modules has the exact 15 projection module names, sorted by name.
Each row has exactly name/source_sha256/code_sha256. source_sha256 matches
the completion row; code_sha256 identifies the Python3.12 compiled code
extracted by the selected PyInstaller archive reader and checked against the
verified source. Code binding excludes only relocation-dependent filenames,
not bytecode, constants, exception/line tables or nested code objects.
No independent listening/capture or provider execution follows from this.

archive.plaintext is byte-for-byte the same four-row value as inputs.plaintext,
checked by actual archive extraction. entries is the complete sorted list of
extractable CArchive entries, each with exactly path/kind/bytes/sha256; kind
is the actual single ASCII type character. Individual entries are at most
128 MiB. The PYZ entry is included as bytes; its selected modules are also
checked individually above. options is the sorted, unique list of CArchive
option names (type `o`), at most128 strings of at most1,024 UTF-8 bytes. These
are reported separately because options have no extractable file payload.
The actual complete archive population, binary digest and byte length must
agree. Unknown metadata keys never stand in for an uninspected entry.

## Consumer obligations and remaining gates

The consumer must parse strictly, bind the actual binary and provenance to
the exact handoff build_id/digests/length, validate every field above, and
preserve the selected plaintext/notice identities wherever resources are
shipped. A missing/old-v1/unknown-v3 record must refuse; there is no silent
schema downgrade. Changing these fields or their identity rules requires a
new schema version and coordinated producer/consumer review.

This contract does not select a Tauri resource layout or a release pin. Tauri
standalone-resource/binary selection, actual provider delivery, physical
capture and installed OS/media/upgrade gates remain separate. A real build
requires a locally complete, verified pinned closure; absent inputs are a
reported prerequisite, not permission to acquire packages or substitute
versions. Controlled producer tests are never labeled actual frozen builds.
