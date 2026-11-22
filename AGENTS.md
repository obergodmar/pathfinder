# Pathfinder

Keep runtime dependencies in the Python standard library and packaged system tools.
Use `devenv shell -- pathfinder-check` and `devenv shell -- nix build`.
Run tests hermetically; never probe real hosts from tests.
Use two-space indentation, Ruff for Python and nixfmt for Nix.
Keep real inventories, addresses, measurements and credentials out of this public repository.
Never change routes, firewall rules or tunnel peers from diagnostics.
Do not equate endpoint ICMP reachability with AWG connectivity or measured legs with verified transit.
Use master for published releases; infrastructure pins exact commits.
