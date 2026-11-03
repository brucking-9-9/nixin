# A minimal Home Manager user for the module tests (default.nix), with an option of its
# own and the outdated timer on.
{ lib, ... }:
{
  options.nixinTest.colour = lib.mkOption {
    type = lib.types.enum [
      "cyan"
      "pink"
    ];
    default = "cyan";
    description = "A test option the user's config declares itself.";
  };

  config = {
    home.username = "alice";
    home.homeDirectory = "/home/alice";
    home.stateVersion = "25.05";
    programs.nixin.enable = true;
    programs.nixin.outdated = {
      enable = true;
      notify = false;
    };
    programs.git.enable = true;
  };
}
