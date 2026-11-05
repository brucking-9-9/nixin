# mbp13
{ pkgs, lib, ... }:
let
  greeting = "hello ${"world"}";
in
{
  imports = [
    ./hardware.nix
    ./desktop.nix # the desktop
  ];

  environment.systemPackages =
    with pkgs;
    [
      git
      kitty # terminal
      btop
    ]
    ++ lib.optionals false [ sl ];

  boot.kernelPackages = pkgs.linuxPackages_7_2; # not _latest

  services.openssh = {
    enable = true;
    settings.PermitRootLogin = "no";
  };

  programs.bash.shellInit = ''
    # not a binding: services.openssh.enable = false;
    echo "${greeting}" ''${HOME} }
  '';
}
