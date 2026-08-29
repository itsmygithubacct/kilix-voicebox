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

The bounded fork delta after that immutable root commit normalizes the IDAT
encoding of 4/4 PNG paths. Their 4/4 decoded scanline streams and every 4/4
non-IDAT chunk populations remain byte-identical to the imported objects. This
removes 4/4 incidental matches from the publication secret-pattern arm without
changing pixels or metadata.

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

Run the exact import and hygiene-delta check from any directory inside this
repository:

```sh
python3 .kilix-import/verify_import.py
python3 -m unittest discover -s .kilix-import -p 'test_*.py' -v
```

The checker reconstructs a temporary Git index from the root product snapshot
and requires its 406/406 source paths to equal the accepted upstream tree. It
then limits the current source delta to 8/8 exact paths: 4/4 normalized PNGs,
1/1 removed generated binary, 1/1 exact ignore rule, and 2/2 Tauri config paths.
It proves PNG decoded/metadata identity, the removed binary's imported identity,
the 1/1 historical match and its bounded context, the 3/3 generator-input
measurements, the unchanged regeneration rule, and exact relocation of all 3/3
resources. It also checks provenance, clean worktree state, risk-path inventory,
licence bytes, absent upstream history, absent remotes, and all 4/4
product-commit identities.

## Status boundary

This is an F104CONT2 import-hygiene proof candidate. It establishes the fork
boundary and reproducible measurements only. It does not independently
adjudicate the historical 1/1 match, enter P8, remove embedded inference,
freeze a provider protocol, select a public product name, create a remote, or
claim implementation, integration, qualification, build support, or release
admission.
