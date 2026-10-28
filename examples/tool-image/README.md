# Declared tool image

`research-package/v3` example. The runner builds `tools/demo/Containerfile` before
the worker starts, or reuses a local image whose `research.input_hash` label
matches. The tag is derived from that hash. The resulting image id is recorded
in `outputs/tool-images.json` and is not a portable pin.

`required_checks` lists `tool-image-provisioned`. The runner marks that check
passed from the receipt. This package has no fetch and no workload-specific
tooling. Assemble it with `research-runner assemble examples/tool-image job.rrpkg`.
