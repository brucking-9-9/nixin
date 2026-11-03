# The recorded option indexes under tests/fixtures/optindex/: the module tests' systems
# (default.nix), their index documents trimmed to a few option trees, in the form the
# modules write (builtins.toJSON: compact, keys sorted). Re-record:
#
#   scripts/record-index-fixtures.sh
{
  nixin,
  homeManager,
}:
let
  inherit (nixin.inputs.nixpkgs) lib;
  wanted =
    prefixes: names: o:
    builtins.elem o.name names || builtins.any (p: lib.hasPrefix p o.name) prefixes;
  trim =
    keep: doc:
    let
      options = builtins.filter keep doc.options;
    in
    doc
    // {
      inherit options;
      count = builtins.length options;
    };

  nixosDoc =
    (lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        nixin.nixosModules.default
        ./nixos-config.nix
        {
          programs.nixin.optionIndex.configRoot = ./.;
          networking.hostName = lib.mkForce "mbp13";
        }
      ];
    }).config.programs.nixin.optionIndex.file.passthru.doc;

  homeDoc =
    (import "${homeManager}/modules" {
      pkgs = nixin.inputs.nixpkgs.legacyPackages.x86_64-linux;
      configuration = {
        imports = [
          nixin.homeModules.default
          ./home-config.nix
        ];
        programs.nixin.optionIndex.configRoot = ./.;
      };
    }).config.programs.nixin.optionIndex.file.passthru.doc;
in
{
  nixos = trim (wanted
    [
      "services.openssh."
      "programs.nixin."
      "nixinTest."
      "users.users.<name>."
      "boot.loader.systemd-boot."
    ]
    [
      "systemd.services"
      "systemd.services.<name>.enable"
      "networking.hostName"
      "environment.systemPackages"
      "services.pihole-ftl.webserverEnabled"
      "boot.bootMount"
      "boot.cleanTmpDir"
      "home-manager.users"
    ]
  ) nixosDoc;
  home = trim (wanted
    [
      "programs.git."
      "programs.kitty."
      "programs.nixin."
      "nixinTest."
    ]
    [
      "home.packages"
      "home.username"
      "services.ssh-agent.enable"
    ]
  ) homeDoc;
}
