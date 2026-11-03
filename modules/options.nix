# The `programs.nixin` options both modules declare (nixos.nix, home.nix); `kind` is
# "nixos" or "home" and only changes the descriptions and the index's file name.
{
  lib,
  pkgs,
  kind,
}:
let
  inherit (lib) mkOption types literalExpression;
  where =
    if kind == "nixos" then
      "/etc/nixin/options-nixos.json"
    else
      "$XDG_CONFIG_HOME/nixin/options-home.json";
in
{
  enable = lib.mkEnableOption "nixin, the package and option installer (nixin, nixin-tui, nixin-gui)";

  package = mkOption {
    type = types.package;
    default = pkgs.python3Packages.callPackage ../package.nix { };
    defaultText = literalExpression "pkgs.python3Packages.callPackage <nixin>/package.nix { }";
    description = "The nixin package, built by default with this configuration's own pkgs.";
  };

  optionIndex = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Write an index of every option of this ${
          if kind == "nixos" then "NixOS system" else "Home Manager configuration"
        } to {file}`${where}` when it is built (lib/options-index.nix). `nixin search --kind
        ${if kind == "nixos" then "opt" else "hm"}`, `info` and `where` read it instead of asking
        upstream or evaluating the config, and it includes the config's own options. Each
        rebuild then also evaluates every option's documentation (README, "Modules", has
        the cost measured on two configs).
      '';
    };
    configRoot = mkOption {
      type = types.nullOr (types.either types.path types.str);
      default = null;
      example = literalExpression "./.";
      description = ''
        The config's top directory, so that the index names the config's own files
        relative to it (source `config`). Null: the source of the file that sets
        `programs.nixin.enable` (the flake's store path, or /etc/nixos). Given as a
        string or a path; a path is not copied to the store.
      '';
    };
    prune = mkOption {
      type = types.listOf (types.listOf types.str);
      default = lib.optional (kind == "nixos") [
        "home-manager"
        "users"
      ];
      defaultText = literalExpression (
        if kind == "nixos" then ''[ [ "home-manager" "users" ] ]'' else "[ ]"
      );
      description = ''
        Option paths whose sub-options the index leaves out (the option itself stays).
        Home Manager's NixOS module would otherwise repeat every Home Manager option under
        home-manager.users.<name>; homeModules.default indexes each user's own.
      '';
    };
    file = mkOption {
      type = types.nullOr types.package;
      default = null;
      internal = true; # set by the module (not readOnly: that counts the default as a definition)
      description = "The generated index (a derivation; passthru: doc, options, count).";
    };
  };

  outdated = {
    enable = lib.mkEnableOption ''
      a user timer that runs `nixin outdated` (the lock's next nixpkgs) and `nixin outdated
      --published` (search.nixos.org) on the deployed config and keeps the result in
      $XDG_STATE_HOME/nixin (summary.json, outdated-lock.json, outdated-published.json:
      what bars and dashboards read; `nixin-state summary` prints it)'';
    interval = mkOption {
      type = types.str;
      default = "*-*-* 09:00:00";
      example = "weekly";
      description = "When the check runs (systemd OnCalendar), and 10 minutes after boot.";
    };
    notify = mkOption {
      type = types.bool;
      default = true;
      description = "notify-send when the number of packages with a newer version changed.";
    };
    notifyTimeout = mkOption {
      type = types.nullOr types.ints.positive;
      default = null;
      example = 30000;
      description = "How long the notification stays (ms); null: the notification daemon's default.";
    };
    quietHours = mkOption {
      type = types.nullOr (types.strMatching "[0-9]{1,2}-[0-9]{1,2}");
      default = null;
      example = "23-8";
      description = "Hours (H-H, local time) in which a notification waits for the next run.";
    };
  };

  pending = {
    enable = lib.mkEnableOption ''
      a user timer that keeps `nixin status`'s pending block (unapplied commits and edits in
      the workspace) in $XDG_STATE_HOME/nixin/status.json and summary.json, refreshed on an
      interval and right after a switch or a commit in either tree'';
    interval = mkOption {
      type = types.str;
      default = "10min";
      description = "How often (systemd OnUnitActiveSec); a run with nothing changed is skipped.";
    };
  };

  authoritative = mkOption {
    type = types.str;
    default = "/etc/nixos";
    description = ''
      The deployed config (the root-owned checkout systems are switched from): what the
      outdated check reads, and one of the trees the pending refresh watches.
    '';
  };
  workspace = mkOption {
    type = types.nullOr types.str;
    default = null;
    example = "/home/alice/nixos";
    description = ''
      The user's workspace clone of the config, for the pending refresh ($NIXIN_CONFIG).
      Null: ~/nixos when it is a git checkout, else the deployed config (then nothing is
      pending).
    '';
  };
}
