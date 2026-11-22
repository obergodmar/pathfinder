{
  description = "Pathfinder: on-demand mesh diagnostics and relay recommendations";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/release-26.05";
  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      forSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forSystems (
        system:
        let
          pkgs = import nixpkgs { inherit system; };
        in
        {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "pathfinder";
            version = "0.1.0";
            pyproject = true;
            src = nixpkgs.lib.cleanSourceWith {
              src = ./.;
              filter =
                path: type:
                let
                  name = baseNameOf path;
                in
                nixpkgs.lib.cleanSourceFilter path type
                && !(builtins.elem name [
                  ".devenv"
                  ".direnv"
                  ".ruff_cache"
                  "__pycache__"
                  "build"
                  "dist"
                ])
                && !(nixpkgs.lib.hasSuffix ".egg-info" name)
                && !(nixpkgs.lib.hasPrefix "result" name);
            };
            build-system = [ pkgs.python3Packages.setuptools ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            nativeCheckInputs = [ pkgs.ruff ];
            checkPhase = ''
              runHook preCheck
              python -m unittest discover -s tests -v
              ruff check .
              ruff format --check .
              runHook postCheck
            '';
            postFixup = ''
              wrapProgram $out/bin/pathfinder --prefix PATH : ${
                nixpkgs.lib.makeBinPath (
                  [
                    pkgs.openssh
                    pkgs.wireguard-tools
                    pkgs.amneziawg-tools
                  ]
                  ++ nixpkgs.lib.optionals pkgs.stdenv.isLinux [
                    pkgs.iproute2
                    pkgs.iputils
                  ]
                )
              }
            '';
            meta.mainProgram = "pathfinder";
          };
        }
      );
      apps = forSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/pathfinder";
        };
      });
      checks = forSystems (system: {
        package = self.packages.${system}.default;
      });
      nixosModules.default = import ./nix/module.nix self;
    };
}
