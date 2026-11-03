# What `nixin try` needs to know about one attribute of a nixpkgs before it builds it.
#
# Applied to `<nixpkgs>#legacyPackages.<system>` as `(<this>) { path = [ "cowsay" ]; }`.
# Reads meta and the out paths of the outputs `nix build` installs (meta.outputsToInstall),
# never builds. An unfree, broken or insecure package that nixpkgs refuses to evaluate gives
# `out_paths = null` (tryEval) and says why in `unfree`/`broken`/`insecure`. Not there:
# `found = false` with the names next to it, so the caller can suggest one. An alias that
# throws ("'x' has been renamed to 'y'") fails the whole eval with nixpkgs' message.
{ path }:
pkgs:
let
  inherit (builtins)
    attrNames
    deepSeq
    isAttrs
    isList
    isString
    length
    listToAttrs
    map
    tryEval
    ;
  lib = pkgs.lib;
  safe =
    x:
    let
      r = tryEval (deepSeq x x);
    in
    if r.success then r.value else null;
  p = lib.attrByPath path null pkgs;
  parent = if length path > 1 then lib.attrByPath (lib.init path) null pkgs else pkgs;
  namesOf = s: if isAttrs s then attrNames s else [ ];
  meta = p.meta or { };
  outputs = meta.outputsToInstall or p.outputs or [ "out" ];
  lic = meta.license or [ ];
  lics = if isList lic then lic else [ lic ];
  spdx = l: if isString l then l else (l.spdxId or l.shortName or "unknown");
in
if p == null then
  {
    found = false;
    names = namesOf parent;
  }
else if !(lib.isDerivation p) then
  {
    found = true;
    derivation = false;
    names = namesOf p;
  }
else
  {
    found = true;
    derivation = true;
    name = safe (p.name or null);
    pname = safe (p.pname or null);
    version = safe (p.version or null);
    description = safe (meta.description or null);
    license = safe (map spdx lics);
    main_program = safe (meta.mainProgram or null);
    unfree = safe (meta.unfree or false);
    broken = safe (meta.broken or false);
    insecure = safe (meta.insecure or false);
    available = safe (meta.available or true);
    outputs = safe outputs;
    out_paths = safe (
      listToAttrs (
        map (o: {
          name = o;
          value = p.${o}.outPath;
        }) outputs
      )
    );
  }
