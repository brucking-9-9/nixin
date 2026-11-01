# The option index of one host: every option (or those under a prefix) with its type,
# declarations, definitions and, on request, its current value.
#
# Applied as `(import-text) ARGS` to `nixosConfigurations.<host>` through
# `nix eval --apply`, with ARGS a Nix attrset the Python side renders:
#   prefix   = [ "services" "openssh" ];   # [] = everything
#   values   = "none" | "defined" | "all"; # which options get `value`
#   hm       = null | "<user>";            # null: the NixOS tree; a user: that user's HM tree
#   exclude  = [ "hardware.nvidia.open" ]; # options to skip (see below)
#   maxItems = 60; maxDepth = 5; maxStr = 400;   # value size caps
#
# Why `exclude`: definitionsWithLocations and isDefined force every definition's mkIf
# condition, and a few nixpkgs modules trip a *type* error there when their service is
# off (`null.version`), which builtins.tryEval cannot catch. Each entry is wrapped in
# addErrorContext "nixin-option <name>"; the Python side reads that marker from the
# failed eval's trace, excludes the option, and retries.
#
# Home Manager: the NixOS module keeps only the users' `config`, so the HM option tree is
# rebuilt the way types.attrsOf (submoduleWith ...) merges it: the submodule's modules +
# the user's own definitions + `_module.args.name`, with the same specialArgs.
args: cfg:
let
  lib = cfg.lib;
  inherit (builtins)
    isAttrs
    isList
    isString
    isFunction
    isPath
    attrNames
    concatMap
    length
    tryEval
    deepSeq
    substring
    stringLength
    elem
    addErrorContext
    ;
  isOption = x: isAttrs x && (x._type or null) == "option";

  prefix = args.prefix or [ ];
  values = args.values or "none";
  exclude = args.exclude or [ ];
  maxItems = args.maxItems or 60;
  maxDepth = args.maxDepth or 5;
  maxStr = args.maxStr or 400;

  # --- value serialisation: JSON-able, size-capped, with placeholders -------------------
  ser =
    limit: depth: v:
    if depth > maxDepth then
      "<…>"
    else if isAttrs v then
      if (v.type or null) == "derivation" then
        "<derivation ${v.name or "?"}>"
      else if (v._type or null) == "if" then
        # a mkIf nested inside a definition's value (only the top level is discharged)
        (if v.condition then ser limit depth v.content else "<mkIf false>")
      else if
        elem (v._type or null) [
          "override"
          "order"
        ]
      then
        ser limit depth v.content
      else if (v._type or null) == "merge" then
        map (ser limit depth) v.contents
      else
        let
          ns = attrNames v;
          n = length ns;
          keep = lib.take limit ns;
        in
        (lib.listToAttrs (
          map (k: {
            name = k;
            value = ser limit (depth + 1) v.${k};
          }) keep
        ))
        // (if n > limit then { "<…>" = "${toString (n - limit)} more attributes"; } else { })
    else if isList v then
      let
        n = length v;
      in
      (map (ser limit (depth + 1)) (lib.take limit v))
      ++ (if n > limit then [ "<… ${toString (n - limit)} more>" ] else [ ])
    else if isFunction v then
      "<function>"
    else if isPath v then
      toString v
    else if isString v then
      (if stringLength v > maxStr then substring 0 maxStr v + "<…>" else v)
    else
      v;

  # force deeply inside tryEval; catches throw/assert (not type errors: see `exclude`)
  safeDeep =
    x:
    let
      r = tryEval (deepSeq x x);
    in
    if r.success then
      {
        ok = true;
        value = r.value;
      }
    else
      { ok = false; };

  shortDescription =
    d:
    let
      s = if isString d then d else (d.text or "");
      firstPara = lib.head (lib.splitString "\n\n" s);
    in
    substring 0 300 (lib.replaceStrings [ "\n" ] [ " " ] firstPara);

  describe =
    path: o:
    let
      name = lib.concatStringsSep "." path;
      defsR = safeDeep (map (d: d.file) o.definitionsWithLocations);
      isDefR = safeDeep o.isDefined;
      defined = isDefR.ok && isDefR.value;
      wantValue = values == "all" || (values == "defined" && defined);
      valueR = safeDeep (ser maxItems 0 o.value);
      # per-definition values: what each file contributes (lists kept whole, so a
      # package can be traced to the file that lists it)
      defValuesR = safeDeep (
        map (d: {
          file = d.file;
          value = ser 2000 0 d.value;
        }) o.definitionsWithLocations
      );
      base = {
        inherit name;
        type = o.type.description or o.type.name or "?";
        description = shortDescription (o.description or "");
        declarations = o.declarations;
        declarationPositions = o.declarationPositions or [ ];
        isDefined = if isDefR.ok then isDefR.value else null;
        definitions = if defsR.ok then defsR.value else null;
        internal = o.internal or false;
        visible = (o.visible or true) != false;
        readOnly = o.readOnly or false;
      }
      // (if defsR.ok then { } else { definitionsError = true; });
      withValue =
        if !wantValue then
          { }
        else if valueR.ok then
          {
            value = valueR.value;
            highestPrio = o.highestPrio or null;
          }
        else
          { valueError = true; };
      withDefValues =
        if !wantValue || !defined then
          { }
        else if defValuesR.ok then
          { definitionValues = defValuesR.value; }
        else
          { };
      e = base // withValue // withDefValues;
    in
    addErrorContext "nixin-option ${name}" (deepSeq e e);

  underPrefix = p: lib.take (length prefix) p == prefix || lib.take (length p) prefix == p;

  walk =
    path: set:
    concatMap (
      n:
      let
        v = set.${n};
        p = path ++ [ n ];
        name = lib.concatStringsSep "." p;
      in
      if !(underPrefix p) then
        [ ]
      else if isOption v then
        (if elem name exclude then [ ] else [ (describe p v) ])
      else if isAttrs v && !(v ? _type) then
        walk p v
      else
        [ ]
    ) (attrNames set);

  nixosOptions = walk [ ] (removeAttrs cfg.options [ "_module" ]);

  hmOptions =
    user:
    let
      opt = cfg.options.home-manager.users;
      elemType = opt.type.nestedTypes.elemType;
      payload = elemType.functor.payload;
      userDefs = concatMap (
        d:
        if isAttrs d.value && d.value ? ${user} then
          [
            {
              inherit (d) file;
              value = d.value.${user};
            }
          ]
        else
          [ ]
      ) opt.definitionsWithLocations;
      mods = map (
        { file, value }:
        {
          _file = file;
          imports = [ value ];
        }
      ) userDefs;
      ev = lib.evalModules {
        modules = elemType.getSubModules ++ [ { _module.args.name = user; } ] ++ mods;
        specialArgs = payload.specialArgs;
        class = payload.class or null;
        prefix = [
          "home-manager"
          "users"
          user
        ];
      };
    in
    walk [ ] (removeAttrs ev.options [ "_module" ]);
in
if (args.hm or null) != null then hmOptions args.hm else nixosOptions
