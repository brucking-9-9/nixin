# a module in the full form: options + config, mkMerge, mkIf, mkForce
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.local.extra;
in
{
  options.local.extra.enable = lib.mkEnableOption "extras";

  config = lib.mkMerge [
    { services.tailscale.enable = lib.mkForce true; }
    (lib.mkIf cfg.enable { environment.systemPackages = [ pkgs.htop ]; })
  ];
}
