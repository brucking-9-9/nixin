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

  # the T2 kernel: nixpkgs' linux_7_2 with patches, under our own mkForce
  boot.kernelPackages = lib.mkForce (
    pkgs.linuxPackagesFor (
      pkgs.linux_7_2.override {
        pname = "linux-t2";
        kernelPatches = [ ];
      }
    )
  );

  environment.systemPackages = with pkgs; [
    git
    ripgrep # fast grep
    jq
  ];
}
