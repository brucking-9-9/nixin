# Installed packages and enabled programs of one host.
#
# Applied to `nixosConfigurations.<host>` (the evalModules result: { config, options,
# lib, pkgs, ... }). Shipped as text and passed through `nix eval --apply`, because pure
# evaluation forbids importing a file from outside the flake.
#
# Attribute paths (pkgs.kitty) are not recoverable from derivations, so a package is
# reported as { name, pname, version }: pname is what the discovery layer searches for,
# name is what `nix store diff-closures` prints. Scripts made with writeShellApplication
# have no pname/version.
cfg:
let
  lib = cfg.lib;
  inherit (builtins)
    isAttrs
    isList
    attrNames
    tryEval
    filter
    map
    ;
  isOption = x: isAttrs x && (x._type or null) == "option";
  safe =
    default: x:
    let
      r = tryEval x;
    in
    if r.success then r.value else default;
  pkgInfo =
    p:
    if isAttrs p then
      {
        name = safe null (p.name or null);
        pname = safe null (p.pname or null);
        version = safe null (p.version or null);
      }
    else
      {
        name = safe null (toString p);
        pname = null;
        version = null;
      };
  pkgList = ps: if isList ps then map pkgInfo ps else [ ];

  # programs.<x>.enable == true. Gated on the option tree, so renamed aliases
  # (visible = false, and reading them prints a trace) are never touched.
  programsFromOptions =
    opts: values:
    filter (
      n:
      let
        o = opts.${n};
      in
      isAttrs o
      && !(isOption o)
      && o ? enable
      && isOption o.enable
      && (o.enable.visible or true)
      && safe false (values.${n}.enable == true)
    ) (attrNames opts);
  # Home Manager's option tree is not reachable from the NixOS eval (the submodule
  # merge keeps only `config`), so for users we read values only.
  programsFromValues =
    values:
    filter (n: safe false (isAttrs values.${n} && (values.${n}.enable or false) == true)) (
      attrNames values
    );

  hmUsers = cfg.config.home-manager.users or { };
in
{
  system = {
    packages = pkgList cfg.config.environment.systemPackages;
    programs = programsFromOptions cfg.options.programs cfg.config.programs;
  };
  home = lib.mapAttrs (_: hc: {
    packages = pkgList hc.home.packages;
    programs = programsFromValues hc.programs;
  }) hmUsers;
}
