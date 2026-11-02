"""What the interactive front ends share: one data path and one view-model.

    backend   the verb handlers, called in-process through `cli.document` (the
              documents `nixin --json` prints, without a subprocess)
    view      documents → display rows (pure: strings, Fields, Spans, Hits, Notices)

The TUI (nixin.tui, Textual) and the GUI (nixin.gui, Qt) both render through these, so
a screen in either shows what the CLI would print. Neither module imports a toolkit.
"""
