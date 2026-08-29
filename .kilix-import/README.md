# Kilix curated-import boundary

This directory is the only Kilix-owned addition in the initial Voicebox
snapshot. Everything outside `.kilix-import/` must reconstruct the exact
accepted upstream tree:

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

Run the exact import check from any directory inside this repository:

```sh
python3 .kilix-import/verify_import.py
python3 -m unittest discover -s .kilix-import -p 'test_*.py' -v
```

The checker reconstructs a temporary Git index from every tracked path outside
this directory and requires its tree identity to equal the accepted upstream
tree. It also checks provenance, clean worktree state, risk-path inventory,
licence bytes, absent upstream history, absent remotes, and product commit
identity.

## Status boundary

This is a PREP7 clean-import candidate. It establishes the fork boundary and
import hygiene only. It does not enter P8, remove embedded inference, freeze a
provider protocol, select a public product name, create a remote, or claim
implementation, integration, qualification, or release admission.
