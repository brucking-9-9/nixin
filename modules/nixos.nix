# nixosModules.default: programs.nixin for a NixOS system.
#
#   imports = [ inputs.nixin.nixosModules.default ];
#   programs.nixin.enable = true;
#
# Installs nixin (built with this system's pkgs, no overlay needed), writes the system's
# option index to /etc/nixin/options-nixos.json (optionIndex.enable, default on), and on
# request runs the outdated check and the pending refresh as systemd user timers
# (outdated.enable, pending.enable; `user` limits them to one account).
{
  config,
  lib,
  pkgs,
  options,
  modulesPath,
  ...
}:
let
  cfg = config.programs.nixin;
  common = import ./common.nix { inherit lib; };
  inherit (lib)
    mkIf
    mkMerge
    mkOption
    types
    ;

  index = common.optionsIndex.mkIndex {
    inherit pkgs options;
    scope = "nixos";
    inherit (cfg.optionIndex) prune;
    roots = common.roots cfg options [
      {
        source = "nixpkgs";
        path = dirOf (dirOf (toString modulesPath)); # <nixpkgs>/nixos/modules
      }
    ];
    meta = {
      host = config.networking.hostName;
      user = null;
      system = pkgs.stdenv.hostPlatform.system;
      nixpkgs = {
        inherit (config.system.nixos) version release revision;
      };
      home_manager = null;
      nixin = cfg.package.version or null;
    };
  };

  stateUnit = {
    unitConfig.ConditionUser = mkIf (cfg.user != null) cfg.user;
    # nix from the system profile (the daemon's own version), as nixin expects
    path = [
      "/run/current-system/sw"
      pkgs.git
      pkgs.libnotify
    ];
    environment = common.environment cfg;
  };
in
{
  options.programs.nixin =
    import ./options.nix {
      inherit lib pkgs;
      kind = "nixos";
    }
    // {
      user = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "alice";
        description = ''
          The account whose systemd user manager runs the outdated and pending timers
          (ConditionUser). Null: every user that logs in.
        '';
      };
    };

  config = mkIf cfg.enable (mkMerge [
    { environment.systemPackages = [ cfg.package ]; }

    (mkIf cfg.optionIndex.enable {
      programs.nixin.optionIndex.file = index;
      environment.etc."nixin/options-nixos.json".source = index;
    })

    (mkIf cfg.outdated.enable {
      systemd.user.services.nixin-outdated = stateUnit // {
        description = "nixin: outdated check (lock + published), notify on a change";
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${cfg.package}/bin/nixin-state ${lib.escapeShellArgs (common.outdatedArgs cfg)}";
          TimeoutStartSec = "45min";
          Nice = 19;
          IOSchedulingClass = "idle";
        };
      };
      systemd.user.timers.nixin-outdated = {
        description = "nixin outdated check";
        wantedBy = [ "timers.target" ];
        unitConfig.ConditionUser = mkIf (cfg.user != null) cfg.user;
        timerConfig = {
          OnCalendar = cfg.outdated.interval;
          OnStartupSec = "10min"; # after the user manager starts: OnBootSec would fire at a late login
        };
      };
    })

    (mkIf cfg.pending.enable {
      systemd.user.services.nixin-status = stateUnit // {
        description = "nixin: pending changes (nixin status on the workspace)";
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${cfg.package}/bin/nixin-state status";
          TimeoutStartSec = "20min";
          Nice = 19;
          IOSchedulingClass = "idle";
        };
      };
      systemd.user.timers.nixin-status = {
        description = "nixin pending refresh";
        wantedBy = [ "timers.target" ];
        unitConfig.ConditionUser = mkIf (cfg.user != null) cfg.user;
        timerConfig = {
          OnStartupSec = "3min";
          OnUnitActiveSec = cfg.pending.interval;
        };
      };
      systemd.user.paths.nixin-status = {
        description = "nixin pending refresh on a switch or a commit";
        wantedBy = [ "paths.target" ];
        unitConfig.ConditionUser = mkIf (cfg.user != null) cfg.user;
        pathConfig.PathChanged = common.pendingWatch cfg;
      };
    })
  ]);
}
