-- Shared WezTerm look and behavior, deployed by install_wezterm.py as the managed
-- "base" block (between `local config = wezterm.config_builder()` and
-- `return config`). Edit it here, then re-run the installer / `link`.
-- Shell choice is NOT set here: it is the separate "shell-approach" block.

-- Appearance
-- Fonts: run `wezterm ls-fonts --list-system` to list available fonts
-- examples: Catppuccin Mocha, Catppuccin Macchiato, Tokyo Night, Nord, Dracula, Gruvbox Dark, Rose Pine
config.color_scheme = "Catppuccin Mocha"
config.font = wezterm.font("JetBrains Mono", { weight = "Medium" })
config.font_size = 12.0
config.window_background_opacity = 0.95
config.window_decorations = "RESIZE"  -- no title bar, keep resize
config.enable_tab_bar = true
config.hide_tab_bar_if_only_one_tab = true

-- Behavior
config.window_close_confirmation = "NeverPrompt"
config.automatically_reload_config = true
config.scrollback_lines = 10000

-- Splits (like tmux, built in)
config.keys = config.keys or {}
table.insert(config.keys, { key = "d", mods = "CTRL|SHIFT", action = wezterm.action.SplitHorizontal })
table.insert(config.keys, { key = "e", mods = "CTRL|SHIFT", action = wezterm.action.SplitVertical })
