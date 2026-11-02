{ pkgs, ... }:
{
  environment.systemPackages = [
    pkgs.btop
    pkgs.firefox
  ];
}
