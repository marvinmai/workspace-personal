# Single source of truth for the optional personal PowerShell helpers offered by
# setup-shell-extras.ps1.
#
# Each entry has:
#   Name        - short label / command users will type
#   Description - one-line summary shown in the preview
#   Script      - installer script relative to this scripts directory

@(
    [pscustomobject]@{
        Name        = 'np'
        Description = "Opens files in Notepad++ (if installed). Usage: 'np file.txt'."
        Script      = 'install-np-alias.ps1'
    }
    [pscustomobject]@{
        Name        = 'ai'
        Description = "Shortcut for Claude Code (if 'claude' is on PATH). Usage: 'ai <prompt>'."
        Script      = 'install-ai-alias.ps1'
    }
    [pscustomobject]@{
        Name        = 'll'
        Description = 'Colourised, sorted directory listing for folders, executables, files, and links.'
        Script      = 'install-ll-function.ps1'
    }
    [pscustomobject]@{
        Name        = 'ai-personal'
        Description = "Changes to the personal AI configuration repo (ai_personal_dir in the ai-workspace config). Usage: 'ai-personal'."
        Script      = 'install-ai-personal-function.ps1'
    }
    [pscustomobject]@{
        Name        = 'notes'
        Description = "Changes to the notes vault directory (notes_dir in the ai-workspace config). Usage: 'notes'."
        Script      = 'install-notes-function.ps1'
    }
    [pscustomobject]@{
        Name        = 'clone'
        Description = "Searches the configured repository sources and clones the selection. Usage: 'clone'."
        Script      = 'install-clone-function.ps1'
    }
    [pscustomobject]@{
        Name        = 'slice'
        Description = "Runs the current repository's scripts/slice.<ext> launcher, passing arguments on. Usage: 'slice [args]'."
        Script      = 'install-slice-function.ps1'
    }
)
