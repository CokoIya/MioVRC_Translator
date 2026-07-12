# Installer language catalogs

These catalogs are vendored from `jrsoftware/issrc` commit
`eafc69c06f3b23bdccbf22d3fde83b499ddc4901`. They use the Inno Setup
6.5.0-compatible message schema. `English.isl` is the upstream `Default.isl`
renamed to serve as the pinned key and placeholder baseline.

Project-local changes are intentionally small:

- `AdditionalTasks` is added to every `[CustomMessages]` section for Mio's
  installer task group.
- `Japanese.isl` includes the intentionally empty `HelpTextNote` key that is
  absent upstream, keeping exact key parity with `Default.isl`.
- The remaining upstream English copy in Japanese `ProgramOnTheWeb` and the
  Russian translator attribution are localized.
- Each catalog keeps an explicit Unicode-capable Windows UI font for its
  language, preserving the installer's previous CJK rendering behavior.

Update all five `.isl` files together. The installer localization tests enforce
section uniqueness, exact message-key coverage, placeholder parity, and the
absence of compiler-provided English fallback catalogs.
