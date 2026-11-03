# Module evaluation tests for nixosModules.default and homeModules.default, on minimal
# configs (nixos-config.nix, home-config.nix). tests/test_modules_live.py evaluates this
# with NIXIN_LIVE=1, outside the sandbox (it needs nix and the store):
#
#   nix eval --json --impure --expr 'import ./tests/nix {
#     nixin = builtins.getFlake "path:<worktree>";
#     homeManager = builtins.fetchTree { type = "github"; owner = …; rev = …; narHash = …; };
#   }'
#
# Every attribute is a small fact the test compares; nothing is built. The index lists
# come from the index derivation's passthru (lib/options-index.nix), so reading one entry
# evaluates the option tree but not every option's documentation.
{
  nixin,
  homeManager ? null,
}:
let
  nixpkgs = nixin.inputs.nixpkgs;
  inherit (nixpkgs) lib;
  system = "x86_64-linux";
  pkgs = nixpkgs.legacyPackages.${system};

  nixos =
    extra:
    lib.nixosSystem {
      inherit system;
      modules = [
        nixin.nixosModules.default
        ./nixos-config.nix
      ]
      ++ extra;
    };

  find =
    index: name:
    let
      hit = lib.findFirst (o: o.name == name) null index.passthru.options;
    in
    if hit == null then null else removeAttrs hit [ "description" ];
  header = index: removeAttrs index.passthru.doc [ "options" ];
  pnames = map (p: p.pname or p.name);

  sys = nixos [ ];
  sysIndex = sys.config.programs.nixin.optionIndex.file;

  # the same system with its config root given, the timers on and the index off
  rooted = nixos [ { programs.nixin.optionIndex.configRoot = ./.; } ];
  timers = nixos [
    {
      programs.nixin = {
        user = "alice";
        outdated = {
          enable = true;
          quietHours = "23-8";
          notifyTimeout = 30000;
        };
        pending.enable = true;
        workspace = "/home/alice/nixos";
      };
    }
  ];
  noIndex = nixos [ { programs.nixin.optionIndex.enable = false; } ];
  off = nixos [ { programs.nixin.enable = lib.mkForce false; } ];

  home = import "${homeManager}/modules" {
    inherit pkgs;
    configuration = {
      imports = [
        nixin.homeModules.default
        ./home-config.nix
      ];
    };
  };
  homeIndex = home.config.programs.nixin.optionIndex.file;

  # Home Manager as a NixOS module (as on 19BK): the user's index comes from the HM
  # module; the system's (nixosModules too) leaves home-manager.users' sub-options out
  withHm = nixos [
    "${homeManager}/nixos"
    {
      users.users.bob = {
        isNormalUser = true;
        home = "/home/bob";
      };
      home-manager.users.bob = {
        imports = [ nixin.homeModules.default ];
        home.stateVersion = "25.05";
        programs.nixin.enable = true;
      };
    }
  ];
  bob = withHm.config.home-manager.users.bob;

  # programs.nixin.optionIndex.configRoot's detection on made-up definitions
  common = import ../../modules/common.nix { inherit lib; };
  detect =
    given: files: known:
    common.configRoot { optionIndex.configRoot = given; } {
      programs.nixin.enable = {
        declarations = [ "/nix/store/nixin-source/modules/nixos.nix" ];
        definitionsWithLocations = map (file: { inherit file; }) files;
      };
    } known;
in
{
  configRoot = {
    flake =
      detect null
        [
          "/nix/store/nixin-source/modules/nixos.nix"
          "/nix/store/cfg-source/hosts/x/default.nix"
        ]
        [ ];
    inline =
      detect null
        [
          "/nix/store/np-source/flake.nix"
          "/nix/store/cfg-source/home/home.nix"
        ]
        [ "/nix/store/np-source" ];
    etc = detect null [ "/etc/nixos/hosts/x.nix" ] [ ];
    plain = detect null [ "/home/a/cfg/hosts/x.nix" ] [ ];
    none = detect null [ "/nix/store/np-source/flake.nix" ] [ "/nix/store/np-source" ];
    given = detect "/srv/cfg" [ "/nix/store/cfg-source/a.nix" ] [ ];
  };

  nixos = {
    packages = builtins.filter (n: n == "nixin") (pnames sys.config.environment.systemPackages);
    etc = sys.config.environment.etc."nixin/options-nixos.json".source.name;
    header = header sysIndex;
    greeting = find sysIndex "nixinTest.greeting";
    openssh = find sysIndex "services.openssh.enable";
    enable = find sysIndex "programs.nixin.enable";
    template = find sysIndex "users.users.<name>.home";
    moduleArgs = builtins.length (
      builtins.filter (o: builtins.elem "_module" o.loc) sysIndex.passthru.options
    );
    rootedGreeting =
      (find rooted.config.programs.nixin.optionIndex.file "nixinTest.greeting").declarations;
    timers = {
      outdated = timers.config.systemd.user.timers.nixin-outdated.timerConfig;
      outdatedExec = timers.config.systemd.user.services.nixin-outdated.serviceConfig.ExecStart;
      outdatedEnv = timers.config.systemd.user.services.nixin-outdated.environment;
      condition = timers.config.systemd.user.services.nixin-outdated.unitConfig.ConditionUser;
      statusExec = timers.config.systemd.user.services.nixin-status.serviceConfig.ExecStart;
      statusPaths = timers.config.systemd.user.paths.nixin-status.pathConfig.PathChanged;
      none = builtins.filter (n: lib.hasPrefix "nixin" n) (
        builtins.attrNames sys.config.systemd.user.timers
      );
    };
    noIndex = {
      etc = noIndex.config.environment.etc ? "nixin/options-nixos.json";
      file = noIndex.config.programs.nixin.optionIndex.file;
      packages = builtins.filter (n: n == "nixin") (pnames noIndex.config.environment.systemPackages);
    };
    off = {
      etc = off.config.environment.etc ? "nixin/options-nixos.json";
      packages = builtins.filter (n: n == "nixin") (pnames off.config.environment.systemPackages);
    };
  };

  home = lib.optionalAttrs (homeManager != null) {
    packages = builtins.filter (n: n == "nixin") (pnames home.config.home.packages);
    file = home.config.xdg.configFile."nixin/options-home.json".source.name;
    header = header homeIndex;
    colour = find homeIndex "nixinTest.colour";
    git = find homeIndex "programs.git.enable";
    enable = find homeIndex "programs.nixin.enable";
    timer = home.config.systemd.user.timers.nixin-outdated.Timer;
    exec = home.config.systemd.user.services.nixin-outdated.Service.ExecStart;
    status = home.config.systemd.user.services ? nixin-status;
  };

  nixosWithHm = lib.optionalAttrs (homeManager != null) {
    bobFile = bob.xdg.configFile."nixin/options-home.json".source.name;
    bobHeader = header bob.programs.nixin.optionIndex.file;
    bobEnable = find bob.programs.nixin.optionIndex.file "programs.nixin.enable";
    bobGit = find bob.programs.nixin.optionIndex.file "programs.git.enable";
    bobPrefixed = builtins.length (
      builtins.filter (
        o: builtins.head o.loc == "home-manager"
      ) bob.programs.nixin.optionIndex.file.passthru.options
    );
    systemHasHmTree = builtins.length (
      builtins.filter (
        o:
        lib.take 3 o.loc == [
          "home-manager"
          "users"
          "<name>"
        ]
      ) withHm.config.programs.nixin.optionIndex.file.passthru.options
    );
    systemHasUsers = (find withHm.config.programs.nixin.optionIndex.file "home-manager.users") != null;
  };
}
