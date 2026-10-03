# Agent instructions

## Change the machine only through the setup scripts

This repo is the source of truth for how this machine is set up. Every change
to the setup, including fixes, goes into the matching setup script
(`applications/<app>/install_<app>.py`, `bootstrap/`, ...), and the machine is
then brought to that state by running that script.

- Never fix the live system by hand: no direct edits to `~/.config/...`,
  no manual package or font installs, no calling single functions of a
  setup script outside the script's normal entry point.
- Ask before running a setup script against the live system: it changes the
  machine, and it may install packages or need sudo.
- If a fix cannot be expressed in a script, say so and ask how to proceed
  instead of working around it by hand.
