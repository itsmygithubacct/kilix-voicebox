# Security policy

This is a local-only release candidate. It does not claim a public security
contact, signed release channel, or supported public version. Report suspected
issues through the release owner's approved private channel.

The intended boundary is deliberately narrow:

- the HTTP control plane binds only to loopback and enforces an exact Origin
  allowlist plus an unguessable per-process session credential;
- provider traffic uses private Unix sockets and same-user peer checks;
- application data, consent records, and provenance records are private to the
  current OS user;
- generation requires a valid stored consent record and records provider and
  source provenance;
- preview deletion uses a private recoverable trash area; and
- self-update, remote-server mode, model installation, and embedded inference
  are outside this repository's authority.

Session credentials are passed to the sidecar through its environment, retained
only in process memory, omitted from access logs, and never persisted.

Do not include private media, credentials, local paths, raw provider errors, or
generated databases in issue material or commits.
