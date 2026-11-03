# A minimal NixOS system for the module tests (default.nix): enough to evaluate, never
# built. It declares an option of its own, as a config's modules do.
{ lib, ... }:
{
  options.nixinTest.greeting = lib.mkOption {
    type = lib.types.str;
    default = "hello";
    example = "hej";
    description = "A test option the config declares itself.";
  };

  config = {
    boot.loader.grub.enable = false;
    fileSystems."/" = {
      device = "none";
      fsType = "tmpfs";
    };
    networking.hostName = "nixin-test";
    system.stateVersion = "26.05";
    programs.nixin.enable = true;
  };
}
