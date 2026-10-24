# Standalone asset loader example

Read [the authoring guide](../../docs/asset-loaders.md) and
[wire schema](../../schemas/asset-loader.json) before adapting this example.
This is a fixture/reference implementation, not a private-source connector.

Copy this directory into your independent loader project. No Runner installation is
needed to execute `loader.py`; Python 3.11+ and the standard library are sufficient.
Edit the catalogue example to map an allowed ID to an absolute source directory
and immutable resolved ID. Register the script using the TOML example, replacing
all executable, script and catalogue paths with your installation's absolute paths.
Use Runner's `validate-loaders --config FILE` to check the handshake.

Keep real catalogues, downloaded inputs and credentials in your private project.
Treat IDs as catalogue keys. For large remote assets, stream progress while resolving
as well as retrieving, and implement your own private cache if resolving requires
an initial download. Validate with fixture data before accessing private sources.
