# What `nixin outdated` compares: the installed package lists and the kernel of one host.
#
# Applied to `nixosConfigurations.<host>` twice: once as the flake is locked, once with
# `--override-input <nixpkgs> <target>`. Reads names and versions only, never
# system.build.toplevel, so nothing is built or substituted (the target side downloads the
# nixpkgs tarball, nothing else). Packages as in truth/packages.nix: { name, pname, version }.
cfg:
let
  inherit (builtins)
    isAttrs
    isList
    map
    tryEval
    mapAttrs
    ;
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
  config = cfg.config;
in
{
  system = pkgList config.environment.systemPackages;
  home = mapAttrs (_: hc: pkgList hc.home.packages) (config.home-manager.users or { });
  kernel = safe null (config.boot.kernelPackages.kernel.version or null);
  release = safe null (config.system.nixos.release or null);
}
