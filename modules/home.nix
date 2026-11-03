# homeModules.default: programs.nixin for a Home Manager user (standalone, or as the
# NixOS module's home-manager.users.<name>).
#
#   imports = [ inputs.nixin.homeModules.default ];
#   programs.nixin.enable = true;
#
# Installs nixin (built with Home Manager's pkgs), writes this user's option index to
# $XDG_CONFIG_HOME/nixin/options-home.json (optionIndex.enable, default on), and on
# request runs the outdated check and the pending refresh as systemd user timers.
{
  config,
  lib,
  pkgs,
  options,
  modulesPath,
  osConfig ? null,
  ...
}:
let
  cfg = config.programs.nixin;
  common = import ./common.nix { inherit lib; };
  inherit (lib) mkIf mkMerge;

  index = common.optionsIndex.mkIndex {
    inherit pkgs options;
    scope = "home";
    inherit (cfg.optionIndex) prune;
    # as a NixOS module, Home Manager evaluates a user under home-manager.users.<name>:
    # every loc starts with that; standalone, nothing
    stripPrefix =
      let
        loc = options.programs.nixin.enable.loc;
      in
      lib.take (builtins.length loc - 3) loc;
    roots = common.roots cfg options [
      {
        source = "home-manager";
        path = dirOf (toString modulesPath); # <home-manager>/modules
      }
      {
        source = "nixpkgs";
        path = toString pkgs.path;
      }
    ];
    meta = {
      host = if osConfig != null then osConfig.networking.hostName or null else null;
      user = config.home.username;
      system = pkgs.stdenv.hostPlatform.system;
      nixpkgs = {
        version = lib.version;
        release = lib.trivial.release;
        revision = lib.trivial.revisionWithDefault null;
      };
      home_manager = {
        inherit (config.home.version) release;
        revision = config.home.version.revision or null;
      };
      nixin = cfg.package.version or null;
    };
  };

  # nix from the system profile on NixOS, else the default profile's (the daemon's
  # version either way, as nixin expects); git and notify-send from pkgs
  path = lib.concatStringsSep ":" [
    "/run/current-system/sw/bin"
    "/nix/var/nix/profiles/default/bin"
    "${config.home.profileDirectory}/bin"
    (lib.makeBinPath [
      pkgs.git
      pkgs.libnotify
    ])
  ];
  environment = [
    "PATH=${path}"
  ]
  ++ lib.mapAttrsToList (k: v: "${k}=${v}") (common.environment cfg);
in
{
  options.programs.nixin = import ./options.nix {
    inherit lib pkgs;
    kind = "home";
  };

  config = mkIf cfg.enable (mkMerge [
    { home.packages = [ cfg.package ]; }

    (mkIf cfg.optionIndex.enable {
      programs.nixin.optionIndex.file = index;
      xdg.configFile."nixin/options-home.json".source = index;
    })

    (mkIf cfg.outdated.enable {
      systemd.user.services.nixin-outdated = {
        Unit.Description = "nixin: outdated check (lock + published), notify on a change";
        Service = {
          Type = "oneshot";
          ExecStart = "${cfg.package}/bin/nixin-state ${lib.escapeShellArgs (common.outdatedArgs cfg)}";
          Environment = environment;
          TimeoutStartSec = "45min";
          Nice = 19;
          IOSchedulingClass = "idle";
        };
      };
      systemd.user.timers.nixin-outdated = {
        Unit.Description = "nixin outdated check";
        Timer = {
          OnCalendar = cfg.outdated.interval;
          OnBootSec = "10min";
        };
        Install.WantedBy = [ "timers.target" ];
      };
    })

    (mkIf cfg.pending.enable {
      systemd.user.services.nixin-status = {
        Unit.Description = "nixin: pending changes (nixin status on the workspace)";
        Service = {
          Type = "oneshot";
          ExecStart = "${cfg.package}/bin/nixin-state status";
          Environment = environment;
          TimeoutStartSec = "20min";
          Nice = 19;
          IOSchedulingClass = "idle";
        };
      };
      systemd.user.timers.nixin-status = {
        Unit.Description = "nixin pending refresh";
        Timer = {
          OnBootSec = "3min";
          OnUnitActiveSec = cfg.pending.interval;
        };
        Install.WantedBy = [ "timers.target" ];
      };
      systemd.user.paths.nixin-status = {
        Unit.Description = "nixin pending refresh on a switch or a commit";
        Path.PathChanged = common.pendingWatch cfg;
        Install.WantedBy = [ "paths.target" ];
      };
    })
  ]);
}
