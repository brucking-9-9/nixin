{ pkgs, ... }:
{
  environment.systemPackages = [
    pkgs.btop
    pkgs.firefox
  ];
  # a module set taken beside config.boot.kernelPackages (kernel-set-mismatch)
  boot.extraModulePackages = [ pkgs.linuxPackages_6_12.nvidia_x11 ];
}
