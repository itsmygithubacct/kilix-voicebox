# Kilix curated-import boundary

This directory is the only Kilix-owned addition in the root Voicebox snapshot.
At product root commit `08e763fa8f734fe519b14cf6e9810404a7aa2617`, everything
outside `.kilix-import/` reconstructs the exact accepted upstream tree:

```text
commit  eb2cd861b19baa16720fd31747071c187c054bc5
tree    d047f7de60574c99815ddd8179a3b5a079cb2567
files   406/406
bytes   54,600,537/54,600,537
```

The upstream commit history is intentionally absent. The accepted P0 hygiene
record found 8/8 upstream Voicebox author/committer identities, while product
publication permits only the project pseudonym. This repository therefore
starts from a new snapshot commit authored and committed by that project
identity. [`PROVENANCE.json`](PROVENANCE.json) retains the upstream URL,
commit, tree, archive digest, licence digest, and accepted evidence identities.

The contaminated prototype was not read, copied, archived, or used as an
import source. The only accepted source was the detached clean P0 object. The
upstream source-risk population is exactly 1/1 path:
`data/cache/.gitkeep`, a 27-byte empty placeholder. The expected populations
for audio/model/database artifacts and private/generated material are each
0/1 allowed populations.

The bounded historical hygiene delta is frozen at commit
`f76181896488583bc0d1abb9b37752e075e99cf3`, tree
`6e02708895e7dc1c5ede3331afb847be6d596b1c`. From the immutable import root to
that boundary, the fork normalizes the IDAT encoding of 4/4 PNG paths. Their
4/4 decoded scanline streams and every 4/4 non-IDAT chunk populations remain
byte-identical to the imported objects. This removes 4/4 incidental matches
from the publication secret-pattern arm without changing pixels or metadata.

The imported root also contains 1/1 generated Apple asset catalog with 1/1
binary match in the PII/host-pattern arm. The current fork does not track that
generated binary. A 1/1 exact `.gitignore` rule keeps a macOS rebuild
from turning it into an untracked publication input, and the imported 3/3
resource mappings move unchanged from the cross-platform Tauri config into a
macOS-only config. The unchanged upstream `build.rs` still declares the macOS
`xcrun actool` regeneration rule. No scanner exception was accepted or added;
the current candidate requires 0/0 scanner exceptions. Application builds
executed by this import-boundary record are 0/2 across macOS and Linux.

CONT2 strengthens the historical evidence without adjudicating it. The checker
reproduces the 1/1 case-insensitive ASCII word match at byte offset
694,170/3,847,048, the exact 1/1 scanner-unit digest, and a 67/67-byte context
digest. The matched bytes form an isolated 3/3-byte printable run bounded by
2/2 non-printable adjacent bytes. It also measures the exact 3/3 accepted asset
catalog generator inputs (8,490,356/8,490,356 bytes): 0/3 inputs contain the
word match and 0/1 matching tokens occur across that input population. These
are reproducible measurements, not a conclusion about content. Independent
historical-blob adjudication remains 0/1.

Before trusting the checked-out Makefile or any worktree control, run the
verifier blob directly from `HEAD` at the repository root. `pipefail` is
required so a failed object read cannot become an empty successful script:

```sh
set -o pipefail
env -u GIT_ALTERNATE_OBJECT_DIRECTORIES -u GIT_COMMON_DIR \
  -u GIT_CONFIG_COUNT -u GIT_CONFIG_PARAMETERS -u GIT_DIR \
  -u GIT_INDEX_FILE -u GIT_NAMESPACE -u GIT_OBJECT_DIRECTORY \
  -u GIT_QUARANTINE_PATH -u GIT_REPLACE_REF_BASE -u GIT_SHALLOW_FILE \
  -u GIT_WORK_TREE GIT_NO_REPLACE_OBJECTS=1 \
  git -C "$PWD" show HEAD:.kilix-import/verify_import.py | python3 -
```

Only after that bootstrap passes, run the complete checks:

```sh
make check
python3 -m unittest discover -s .kilix-import -p 'test_*.py' -v
```

The checker reconstructs a temporary Git index from the root product snapshot
and requires its 406/406 source paths to equal the accepted upstream tree. It
then requires the exact hygiene boundary to remain an ancestor 1/1 and limits
the import-to-boundary source delta to 8/8 exact paths: 4/4 normalized PNGs,
1/1 removed generated binary, 1/1 exact ignore rule, and 2/2 Tauri config paths.
It proves PNG decoded/metadata identity, the removed binary's imported identity,
the 1/1 historical match and its bounded context, the 3/3 generator-input
measurements, the unchanged regeneration rule, and exact relocation of all 3/3
resources at that historical boundary. It separately audits the current product
tree from publishable Git objects for forbidden tracked/untracked artifact paths,
the removed generated binary, licence bytes, absent upstream history, and
complete project identity. Provenance, licence, and ignore-policy evidence comes
from `HEAD`, not a possibly hidden worktree view, and every tracked worktree file
must byte-match its `HEAD` blob before any worktree-based control runs.
Replacement refs, grafts, shallow history, hidden index flags, clean/smudge
substitutions, gitlinks, symlinks, and other non-file tree leaves are refused;
ignore semantics are reconstructed only from tracked `HEAD` ignore files in
isolated Git configuration.
Local archives may configure remotes 0/1; a published clone may configure only
1/1 remote, and both its effective fetch and push URLs must equal
`https://github.com/itsmygithubacct/kilix-voicebox.git`. Transport-helper,
mirror, implicit push-refspec, and effective follow-tag configuration are
refused. An authorized publication must still use an explicit full `work/*`
refspec with `git push --no-follow-tags`; this verifier grants no authority to
publish another namespace.

## Status boundary

The frozen `f761818…` object remains the F104CONT2 import-hygiene proof
candidate. Its boundary and measurements do not retroactively claim P8 or
stream acceptance. Later product implementation and work-ref publication are
audited as descendants rather than being mistaken for the original 8/8-path
hygiene delta. This verifier still grants release admission 0/1 and historical
content adjudication 0/1.
