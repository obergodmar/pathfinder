self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.pathfinder;
in
{
  options.programs.pathfinder = {
    enable = lib.mkEnableOption "Pathfinder manual network diagnostics";
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
    };
    topology = lib.mkOption {
      type = lib.types.attrs;
      default = { };
      description = "Non-secret schema-versioned network inventory.";
    };
  };
  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];
    environment.etc."pathfinder/topology.json".text = builtins.toJSON cfg.topology;
  };
}
