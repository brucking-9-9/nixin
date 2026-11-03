# Build-time option indexes: a module system's `options` as one JSON document, what
# `nixin search --kind opt|hm`, `info`, `where` and `status` read instead of asking
# search.nixos.org or evaluating the config (format: docs/CONTRACT.md, "Option indexes").
#
#   optionsIndex = import ./lib/options-index.nix { inherit lib; };
#   optionsIndex.entries  { options, roots ? [ ], prune ? [ ] }  -> [ { name, loc, … } ]
#   optionsIndex.document { options, scope, roots ? [ ], prune ? [ ], meta ? { } } -> attrset
#   optionsIndex.mkIndex  { pkgs, options, scope, … } -> derivation, $out = the JSON file
#
# `options` is a module system's option tree (the `options` module argument, or
# `nixosConfigurations.<host>.options`). Entries come from lib.optionAttrSetToDocList, so
# submodule options appear as templates (`users.users.<name>.home`) exactly as in the
# NixOS manual; each entry is
#
#   { name, loc, description, type, default, example, declarations, readOnly, internal,
#     visible, errors? }
#
# with `default`/`example` the literalExpression (or literalMD) text, and `declarations`
# a list of { source, file, line } where `file` is relative to the longest of `roots`
# that contains it: roots = [ { source = "config"; path = "/nix/store/…-source"; }
# { source = "nixpkgs"; path = …; } ]; a file under no root keeps its full path with
# source "other". File and line come from the option's declarationPositions (line null
# for submodule templates and generated modules).
#
# A field whose evaluation throws becomes null and its name goes into `errors`, so one
# broken default does not cost the whole index (builtins.tryEval: throw and assert only).
# `prune` lists option paths whose sub-options are left out (the option itself stays):
# NixOS's home-manager.users would otherwise repeat every Home Manager option per user.
# Every submodule's own `_module.*` options (args, check, freeformType, specialArgs) are
# left out too: on mbp13 they were 6 340 of 34 366 entries, all internal.
#
# The JSON is written at evaluation time without string context
# (builtins.unsafeDiscardStringContext, as nixpkgs' make-options-doc does), so a default
# like "${pkgs.foo}/bin/foo" does not make the index depend on building pkgs.foo.
{ lib }:
let
  inherit (builtins)
    tryEval
    deepSeq
    isString
    isAttrs
    filter
    head
    length
    sort
    stringLength
    substring
    unsafeDiscardStringContext
    ;

  safe =
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
      {
        ok = false;
        value = null;
      };

  text =
    v:
    if v == null then
      null
    else if isString v then
      v
    else if isAttrs v && v ? text then
      v.text
    else
      toString v;

  # longest root first, so a config living inside another root still wins
  sortRoots =
    roots: sort (a: b: stringLength (toString a.path) > stringLength (toString b.path)) roots;

  relative =
    roots: file:
    let
      f = toString file;
      hits = filter (r: lib.hasPrefix "${toString r.path}/" f) roots;
    in
    if hits == [ ] then
      {
        source = "other";
        file = f;
      }
    else
      let
        r = head hits;
      in
      {
        inherit (r) source;
        file = substring (stringLength (toString r.path) + 1) (stringLength f) f;
      };

  # sub-options of these paths are not listed: `visible = "shallow"` keeps the option
  # itself documented and stops optionAttrSetToDocList from descending into it
  pruneTree =
    prune: options:
    lib.updateManyAttrsByPath (map (p: {
      path = p;
      update = o: o // { visible = "shallow"; };
    }) (filter (p: lib.hasAttrByPath p options) prune)) options;

  # Where each declaration is, with its line: the option's declarationPositions, parallel
  # to its declarations. A position names the file the option is written in (a file
  # imported into `options.x = import ./x.nix`: that one); a position inside nixpkgs'
  # lib/ is where a helper built the option (mkRenamedOptionModule's aliases), so the
  # declaring module stands instead, without a line. Templates under a submodule
  # (`<name>`, `*`) are not reachable by path: their files only.
  declarationsOf =
    roots: options: d:
    let
      o = lib.attrByPath d.loc null options;
      files = if lib.isOption o then o.declarations or [ ] else [ ];
      pos = if lib.isOption o then o.declarationPositions or [ ] else [ ];
      generated = r: r.source == "nixpkgs" && lib.hasPrefix "lib/" r.file;
      one =
        i:
        let
          f = builtins.elemAt files i;
          p = builtins.elemAt pos i;
          at = relative roots p.file;
        in
        if toString p.file == toString f || !(generated at) then
          at // { line = p.line or null; }
        else
          relative roots f // { line = null; };
      known = filter (i: toString (builtins.elemAt files i) != "<unknown-file>") (
        lib.range 0 (length files - 1)
      );
    in
    if files != [ ] && length files == length pos then
      map one known
    else
      map (f: relative roots f // { line = null; }) d.declarations;

  entry =
    roots: options: d:
    let
      fields = {
        description = text (d.description or null);
        type = d.type or null;
        default = text (d.default or null);
        example = text (d.example or null);
        declarations = declarationsOf roots options d;
        readOnly = d.readOnly or false;
        internal = d.internal or false;
        visible = d.visible or true;
      };
      checked = lib.mapAttrs (_: safe) fields;
      errors = lib.attrNames (lib.filterAttrs (_: r: !r.ok) checked);
    in
    {
      inherit (d) name loc;
    }
    // lib.mapAttrs (_: r: r.value) checked
    // lib.optionalAttrs (errors != [ ]) { inherit errors; };

  entries =
    {
      options,
      roots ? [ ],
      prune ? [ ],
    }:
    let
      tree = pruneTree prune (removeAttrs options [ "_module" ]);
    in
    map (entry (sortRoots roots) tree) (
      filter (d: !(builtins.elem "_module" d.loc)) (lib.optionAttrSetToDocList tree)
    );

  document =
    {
      options,
      scope,
      roots ? [ ],
      prune ? [ ],
      meta ? { },
    }:
    let
      list = entries { inherit options roots prune; };
    in
    {
      format = "nixin-options/1";
      inherit scope;
      count = length list;
      roots = map (r: {
        inherit (r) source;
        path = toString r.path;
      }) roots;
      options = list;
    }
    // meta;

  mkIndex =
    {
      pkgs,
      options,
      scope,
      roots ? [ ],
      prune ? [ ],
      meta ? { },
      name ? "nixin-options-${scope}.json",
    }:
    let
      doc = document {
        inherit
          options
          scope
          roots
          prune
          meta
          ;
      };
      json = builtins.toFile "nixin-options-${scope}-content.json" (
        unsafeDiscardStringContext (builtins.toJSON doc)
      );
    in
    pkgs.runCommandLocal name
      {
        passthru = {
          inherit doc;
          inherit (doc) options count;
        };
      }
      ''
        cp ${json} $out
      '';
in
{
  inherit
    entries
    document
    mkIndex
    relative
    ;
}
