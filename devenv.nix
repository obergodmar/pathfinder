{ pkgs, ... }:
{
  packages = with pkgs; [
    python3
    python3Packages.setuptools
    ruff
    nixfmt
    statix
    deadnix
  ];
  scripts = {
    pathfinder-test.exec = "python -m unittest discover -s tests -v";
    pathfinder-check.exec = ''
      set -eu
      pathfinder-test
      ruff check .
      ruff format --check .
      nixfmt --check flake.nix devenv.nix nix/module.nix
      statix check .
      deadnix --fail .
    '';
    pathfinder-format.exec = ''
      ruff format .
      nixfmt flake.nix devenv.nix nix/module.nix
    '';
  };
}
