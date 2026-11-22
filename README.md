# Pathfinder

On-demand mesh diagnostics, declared route explanations, and ranked relay candidates.
Python standard library, standard system tools, JSON-first output, optional curses TUI.
No daemon, route changes, firewall changes, or automatic failover.

```sh
pathfinder topology --json
pathfinder explain alpha delta --json
pathfinder probe delta --json
pathfinder scan --sources alpha beta gamma delta --output observations.json --json
pathfinder recommend alpha delta --measurements observations.json --json
pathfinder tui --measurements observations.json
```

Provide inventory with `--config topology.json` or `PATHFINDER_CONFIG`.
See [Usage and evidence](docs/usage.md).

## Development

```sh
devenv shell -- pathfinder-check
devenv shell -- nix build
```

The flake exports packages, apps, checks and an optional installation-only NixOS module.
All examples and fixtures use fictional networks. Publish releases to `master`.
