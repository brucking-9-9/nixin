# 19BK
{
  config,
  lib,
  pkgs,
  ...
}:
{
  imports = [
    ./ssh.nix
    ./services.nix
  ];

  environment.systemPackages = with pkgs; [
    git
    ripgrep # fast grep
    jq
  ];
}
