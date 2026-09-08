# Selected sidecar inputs

`tools/stage_sidecar_inputs.py` is an offline, standard-library build-stage
component. It validates explicit local software inputs and writes a private
projection for later sidecar integration. It does not import those inputs,
install packages, resolve dependencies, launch providers or build a sidecar.

The adjacent `.kilix-import/sidecar-inputs.json` is bound by SHA256 in the tool.
There is no alternate-manifest or source-override switch. It selects:

| Input | Exact identity |
| --- | --- |
| kilix-voice | `228ac7ae5b1ceb6b8065f3bc1342f90bbc740345` |
| kilix-transcribe | `0c61eb4edcdc75e1aca993ef7c8673b3b9aed6a9` |
| kilix_transcribe-0.1.0-py3-none-any.whl | 46,454 bytes, SHA256 `5d6ffab4c5237a45107eff9a23a177ae23759018e3cea60c19f7509bc592f2fb` |

Prepare those exact local checkouts and wheel out of band. The tool never
fetches a missing Git object. Use an existing, owned 0700 output parent and a
new destination leaf. All arguments must be absolute paths without symbolic
links or `.`/`..` components. For example, after assigning the four absolute
paths in the invoking shell:

```sh
/usr/bin/python3 -I -B -S tools/stage_sidecar_inputs.py \
  --voice-source "$voice_source" \
  --transcribe-source "$transcribe_source" \
  --transcribe-wheel "$transcribe_wheel" \
  --destination "$sidecar_inputs_destination"
```

For an already isolated trusted build process, import this tool by its
reviewed file path and call
`stage(voice_source=..., transcribe_source=..., transcribe_wheel=..., destination=...)`.
It returns the completion record or raises `StageError`. Interruption still
propagates. Do not add the supplied source directories or wheel to Python's
search path to call this API. The CLI's `-I -B -S` invocation excludes ambient
Python path and site initialization; Git plumbing has a separate fixed
executable and sanitized environment. Linux no-follow directory descriptors
and `/proc/self/fd` are required. The original staging budget is 30 seconds;
Git output and each input are bounded to 1 MiB, wheel count to 64 members and
total expanded wheel bytes to 1 MiB.

The manifest binds raw repository commits, trees, Git blob identities,
selected source paths, sizes and SHA256 digests. Every selected Git file has
mode 100644. Git does not retain umask-derived permission differences, so an
owned, single-link regular source file may be 0600 or 0644; observed modes
are recorded. Executable, shared-writable, special and linked inputs refuse.
The whole wheel digest and all 20 members' names, bytes, exact Unix modes,
compression and RECORD identities are checked without extraction. Only the
selected eight wheel members are compared with their source counterparts;
unused wheel code and metadata are validated but never staged or executed.
The complete tracked LICENSE/LICENCE/NOTICE/COPYING population of each selected
commit is enumerated: these two revisions each contain one root LICENSE.

The output has exactly 19 payload files and one `completion.json`:

| Location | Population |
| --- | --- |
| `python/voicelib/` | Eight importable modules: `__init__`, `audio`, `microphone`, `settings`, `util`, `models`, `paths`, `device_leases` |
| `python/kilix_transcribe/` | Seven importable modules: `__init__`, `service`, `protocol`, `runtime`, `results`, `surface`, `owned` |
| `python/voicelib/microphone_supervisor.py` | The unchanged plaintext helper, beside `microphone.py` |
| `python/VERSION` | The actual selected Voice version file |
| `licenses/kilix-voice/LICENSE` | Complete selected Voice license bytes |
| `licenses/kilix-transcribe/LICENSE` | Complete selected transcribe license bytes, also verified against the wheel |

There are 15 importable projection modules plus the separate helper: 16
Python files physically present. The helper must remain a real plaintext
file at that relative location in the eventual extracted sidecar layout.
The unchanged microphone code invokes it with `/usr/bin/python3 -I -B -S`.
This stage does not rewrite that contract or turn the helper into a module
inside an inaccessible binary archive. `VERSION` must likewise remain next
to the `voicelib` directory. These are required integration constraints.

No transcribe worker/CLI/recording/content module, Qwen package, Content
package, model or executable entry point is staged. The selected service and
runtime modules support the reviewed client imports; the projection is not
a standalone provider installation. Its required runtime dependencies and
the separate provider launch path remain outside this component.

All output directories are 0700 and payload files are 0600. Existing outputs,
including incomplete directories and dangling links, refuse without changes.
All selected inputs are validated before allocation, then held by descriptors
and verified again after staging. Written bytes, modes and directory/file
population are checked. The completion record is prepared privately and
published last without overwrite. It binds selected commits/trees, the whole
wheel, observed source permissions and every staged file's path, role, size,
mode and SHA256. It contains no local input paths or timestamps. Identical
inputs and modes produce identical records.

Failure after allocation leaves an incomplete private directory for
inspection; there is no automatic cleanup or reuse. A consumer must require
the final completion record, verify its expected manifest identity and every
file row, and bind the consumed bytes into its own build evidence. A success
record is input-stage evidence at completion, not a durable claim that a
writable directory cannot later be changed. The later builder must preserve
that identity through actual consumption.

Run the new controls with explicit selected inputs, without package acquisition:

```sh
VOICEBOX_STAGE_TEST_VOICE_SOURCE="$voice_source" \
VOICEBOX_STAGE_TEST_TRANSCRIBE_SOURCE="$transcribe_source" \
VOICEBOX_STAGE_TEST_TRANSCRIBE_WHEEL="$transcribe_wheel" \
  /usr/bin/python3 -I -B -S -m unittest discover \
    -s .kilix-import -p test_sidecar_inputs.py -v
```

Without all three variables, the actual-input class is explicitly skipped;
structural controls still run. The selected-input tests use local disposable
Git copies for mutations, never edit the selected originals, never execute
input code, and check the descriptor count after each method. They exercise
the production CLI/API, exact population, notice/helper refusal, archive
structure, source/wheel substitution, poisoned environment, output refusal,
interruption and mutation during staging. Test process fixtures are synthetic.

Remaining integration includes the sidecar output-path correction (G1),
PyInstaller/tool/dependency closure and consumption of this manifest (G2/G3),
shipping the helper/VERSION/licenses and extending final binary provenance
(G3), catalog and host installation/provider staging (G4), and the exact
private provider interpreter/dependency environment plus OS installation (G5).
The original curated-import gate also fixes its control population at four
files: these two assigned new `.kilix-import` files require a separately
reviewed integration update to that gate. This component leaves its existing
assertions unchanged. No model, physical capture, built-sidecar, media,
upgrade or release qualification follows from a successful stage.
