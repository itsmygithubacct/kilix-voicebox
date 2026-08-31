# Changelog

## F104 candidate

- Reimplemented synthesis and transcription as bounded clients of the
  independently supervised `kilix-qwen-tts` and `kilix-transcribe` providers.
- Removed embedded inference engines, model installation, remote-server mode,
  self-update, and unowned process discovery from the product boundary.
- Added private XDG storage for consent, generation provenance, and recoverable
  deletion.
- Restricted the desktop sidecar lifecycle, webview capabilities, network bind,
  and browser Origin population.
- Added locked Python, JavaScript, and Rust dependency graphs plus source-tree
  and sidecar inventory controls.

This candidate does not select, install, or qualify a model runtime. Those
decisions remain in the prerequisite F104 provider and content phases.
