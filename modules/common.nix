# What nixos.nix and home.nix share besides the option declarations: the index's roots
# and the nixin-state command lines of the two timers.
{ lib }:
let
  optionsIndex = import ../lib/options-index.nix { inherit lib; };

  # the tree a file of the config lives in: its store path (a flake's source), /etc/nixos,
  # else the file's own directory
  rootOf =
    file:
    let
      f = toString file;
      parts = lib.splitString "/" f;
    in
    if lib.hasPrefix "/nix/store/" f && builtins.length parts > 4 then
      lib.concatStringsSep "/" (lib.take 4 parts)
    else if lib.hasPrefix "/etc/nixos/" f then
      "/etc/nixos"
    else if lib.hasPrefix "/" f then
      dirOf f
    else
      null;

  # programs.nixin.optionIndex.configRoot, else the tree of the first file that sets
  # programs.nixin.enable. Skipped: its own default (a definition from the declaring
  # file), and trees that are already roots: an inline module in a nixosSystem call is
  # attributed to nixpkgs' flake.nix, and that must not make nixpkgs the config.
  configRoot =
    cfg: options: known:
    if cfg.optionIndex.configRoot != null then
      toString cfg.optionIndex.configRoot
    else
      let
        opt = options.programs.nixin.enable;
        files = map (d: d.file) opt.definitionsWithLocations;
        own = builtins.filter (f: !(builtins.elem f opt.declarations)) files;
        roots = builtins.filter (r: r != null && !(builtins.elem r known)) (map rootOf own);
      in
      if roots == [ ] then null else builtins.head roots;
in
{
  inherit optionsIndex rootOf configRoot;

  # the index's roots: the config (found as above), nixin itself, and the module system's
  # own sources (`extra`: nixpkgs; home-manager)
  roots =
    cfg: options: extra:
    let
      others = [
        {
          source = "nixin";
          path = toString ../.;
        }
      ]
      ++ extra;
      config = configRoot cfg options (map (r: r.path) others);
    in
    lib.optional (config != null) {
      source = "config";
      path = config;
    }
    ++ others;

  outdatedArgs =
    cfg:
    [ "outdated" ]
    ++ lib.optional (!cfg.outdated.notify) "--no-notify"
    ++ lib.optionals (cfg.outdated.notifyTimeout != null) [
      "--timeout"
      (toString cfg.outdated.notifyTimeout)
    ]
    ++ lib.optionals (cfg.outdated.quietHours != null) [
      "--quiet-hours"
      cfg.outdated.quietHours
    ];

  # a switch shows in the profiles directory (a new system-N-link); a commit in a ref.
  # Not /run/current-system: PathChanged watches the inode of the symlink's target,
  # which never changes (found on mbp13, 2026-11-02)
  pendingWatch = cfg: [
    "/nix/var/nix/profiles"
    "${cfg.authoritative}/.git/refs/heads"
    "${if cfg.workspace != null then cfg.workspace else "%h/nixos"}/.git/refs/heads"
  ];

  # the trees nixin-state reads (its defaults: /etc/nixos, and ~/nixos when it is a clone)
  environment =
    cfg:
    {
      NIXIN_AUTHORITATIVE = cfg.authoritative;
    }
    // lib.optionalAttrs (cfg.workspace != null) { NIXIN_CONFIG = cfg.workspace; };
}
