# Obsidian Profile

A portable, versioned baseline for initializing new Obsidian vaults without copying their content model.

The profile owns interaction and appearance: Vim mode, hotkeys, theme, CSS snippets, core plugins, community plugin binaries, and selected portable plugin preferences. Vault-specific paths, workspaces, templates, scripts, homepages, and recent-file state remain local to each vault.

## Requirements

- macOS, Linux, or Windows with Python 3.9+
- An existing directory to use as the vault
- Obsidian closed while installing or updating the profile

The installer uses only Python's standard library. It does not download code or require a package manager.

## Install into a new vault

```bash
mkdir -p ~/vaults/example
/Users/ebenezersemere/repos/obsidian-profile/bin/obsidian-profile install ~/vaults/example
```

The command copies the managed baseline to `VAULT/.obsidian`, verifies every bundled plugin checksum before changing the vault, and writes installation state to `VAULT/.obsidian-profile/state.json`.

If an existing managed file differs, its previous version is saved under:

```text
VAULT/.obsidian-profile/backups/<install-id>/.obsidian/...
```

Unmanaged files are left untouched. In particular, the installer does not delete or replace workspace state or plugin data omitted from the portable profile.

Installation verifies a private source snapshot before changing the vault and replaces each managed file atomically, but the whole install is not a single transaction. If an interruption or filesystem error stops an install, close Obsidian and rerun the same `install` command to converge on the profile. `state.json` is replaced only after all managed files are copied, so a missing or older state file indicates that the preceding install may be incomplete. To undo an incomplete install instead, restore conflicting files from the newest backup directory; files that did not exist before that install have no backup and must be removed manually if exact pre-install state is required. Unmanaged vault data is not part of this recovery procedure.

## Verify a vault

```bash
bin/obsidian-profile verify ~/vaults/example
```

Exit status is zero when every managed file matches the profile. Missing and drifted files are reported individually.

## Update the profile from the source vault

Close Obsidian first, then run:

```bash
bin/obsidian-profile capture /Users/ebenezersemere/repos/ezer/memory
bin/obsidian-profile bundle-plugins /Users/ebenezersemere/repos/ezer/memory
python3 -m unittest discover -s tests -v
```

`capture` copies only files and plugin preferences allowlisted by `profile-policy.json`. `app.json` is reduced to explicitly portable keys. The captured appearance keeps Obsidian's default theme selected; installed themes remain available but inactive.

`bundle-plugins` copies the exact enabled plugin artifacts and regenerates `plugin-lock.json` with versions and SHA-256 checksums. Installation is therefore pinned and works offline.

Review the Git diff before committing profile updates.

## Portability boundary

Included:

- Vim mode, line numbers, editing behavior, and mobile toolbar commands
- Hotkeys and `obsidian.vimrc`
- Obsidian's default theme, with the installed Minimal theme available but inactive, and the `callouts.css` snippet
- Enabled core and community plugin lists
- Exact bundled plugin releases
- Allowlisted portable plugin preferences

Deliberately excluded:

- `workspace*.json`, caches, graph state, bookmarks, Publish state, and recent files
- New-note and attachment folder paths
- Homepage configuration
- Periodic Notes folder and template paths
- Templater folder rules
- Shell Commands entries that invoke vault-local scripts
- Excalidraw folder paths
- Kindle account/sync state and highlights path
- Obsidian-to-Anki file hashes and vault-specific export state

To change the boundary, edit `profile-policy.json` rather than copying all of `.obsidian`.

## Commands

```text
obsidian-profile install VAULT
obsidian-profile verify VAULT
obsidian-profile capture SOURCE_VAULT
obsidian-profile bundle-plugins SOURCE_VAULT
```

Use `--profile-root PATH` before the command when running the script from outside this repository layout.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests cover conflict backups, unmanaged-state preservation, plugin integrity checks, drift detection, configuration sanitization, plugin bundling, and the CLI entry point.
