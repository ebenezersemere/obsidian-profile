# Obsidian Profile

A portable, versioned Obsidian configuration shared across vaults.

The profile owns interaction and appearance: Vim mode, hotkeys, themes, CSS snippets, core and community plugin lists, pinned community plugin binaries, selected portable plugin preferences, and optionally shared vault files such as templates. Vault-specific paths, workspaces, scripts, homepages, and recent-file state remain local.

## How linked profiles work

Fully portable files are symlinked rather than copied:

```text
personal/.obsidian/hotkeys.json -> obsidian-profile/profile/hotkeys.json
work/.obsidian/hotkeys.json     -> obsidian-profile/profile/hotkeys.json
```

Editing a linked setting from either local vault edits this repository directly. The other local vault sees the same file, and `git status` shows the change without a capture step.

`app.json` is the exception: it mixes portable behavior with vault-local paths. The linker merges only the keys allowlisted in `profile-policy.json` and preserves all other keys in each vault. Pinned plugin binaries are copied rather than linked so plugin updates cannot mutate the locked artifacts accidentally.

## Requirements

- macOS, Linux, or Windows with Python 3.9+
- An existing directory to use as the vault
- Obsidian closed while linking, syncing, installing, or repairing the profile

The tool uses only Python's standard library.

## Link a vault

```bash
bin/obsidian-profile link ~/vaults/personal
bin/obsidian-profile link ~/vaults/work
```

`link`:

- symlinks every portable file under `profile/` except `app.json`;
- symlinks files under `vault-profile/` at the corresponding vault-root path;
- merges portable `app.json` keys without replacing local keys;
- copies checksum-verified plugin binaries;
- backs up conflicting files under `.obsidian-profile/backups/<install-id>/`; and
- records linked-mode state in `.obsidian-profile/state.json`.

It is idempotent. Re-run it to repair a link that Obsidian or a plugin replaced.

## Sync mixed settings across vaults

Most settings need no command after linking. For portable settings stored inside mixed `app.json`, select the vault whose values should win and list any other local vaults to update:

```bash
bin/obsidian-profile sync ~/vaults/personal ~/vaults/work
```

This captures only allowlisted `app.json` keys from `personal`, updates the profile, then links/repairs both vaults. Vault-local fields such as attachment paths remain unchanged.

## Shared templates and other vault files

Files under `vault-profile/` map to the vault root. For example:

```text
obsidian-profile/vault-profile/templates/daily.md
    -> VAULT/templates/daily.md
```

Files are linked individually, so unmanaged files can coexist in the same destination directory. Configure each vault to use the same shared folder name, such as `templates`. Do not place private vault content under `vault-profile/`.

## Move changes between machines

On the machine where a linked configuration changed, review and publish the profile repository:

```bash
git -C ~/repos/obsidian-profile status
git -C ~/repos/obsidian-profile add .
git -C ~/repos/obsidian-profile commit -m "Update Obsidian settings"
git -C ~/repos/obsidian-profile push
```

If the change is stored in mixed `app.json`, capture its portable keys before committing:

```bash
bin/obsidian-profile sync /path/to/source-vault
```

If plugins were installed or updated, regenerate the pinned bundle before committing:

```bash
bin/obsidian-profile bundle-plugins /path/to/source-vault
```

On another machine, close Obsidian, pull the profile, and reapply it to each local vault:

```bash
git -C ~/repos/obsidian-profile pull
cd ~/repos/obsidian-profile
bin/obsidian-profile link /path/to/personal-vault
bin/obsidian-profile link /path/to/work-vault
```

Existing symlinks reflect pulled changes immediately. Rerunning `link` also applies merged `app.json` settings, copies updated plugin binaries, creates newly managed links, and repairs links that were replaced.

Each vault needs its initial `link` setup only once, but rerunning it after a pull is the safest complete update workflow.

## Verify a vault

```bash
bin/obsidian-profile verify ~/vaults/personal
```

For linked vaults, verification checks link targets, portable `app.json` values, and copied plugin checksums. A replaced link is reported as drift even when its current bytes happen to match.

## Copy-only initialization

The original initializer remains available:

```bash
bin/obsidian-profile install ~/vaults/example
```

It copies the managed baseline instead of creating links. This is useful where the profile repository is unavailable, but later changes require another install or capture cycle.

## Update the profile from an unlinked source vault

Close Obsidian first, then run:

```bash
bin/obsidian-profile capture /path/to/source-vault
bin/obsidian-profile bundle-plugins /path/to/source-vault
python3 -m unittest discover -s tests -v
```

`capture` copies only files and plugin preferences allowlisted by `profile-policy.json`. `app.json` is reduced to explicitly portable keys. Use `sync`, not `capture`, after a vault has been linked: its portable files already point into the profile.

`bundle-plugins` copies exact enabled plugin artifacts and regenerates `plugin-lock.json` with versions and SHA-256 checksums. Review the Git diff before committing profile updates.

## Portability boundary

Included:

- Vim mode, line numbers, editing behavior, and mobile toolbar commands
- Hotkeys and `obsidian.vimrc`
- Appearance, themes, and CSS snippets
- Enabled core and community plugin lists
- Exact bundled plugin releases
- Allowlisted portable plugin preferences
- Explicit files under `vault-profile/`, such as shared templates

Deliberately excluded:

- `workspace*.json`, caches, graph state, bookmarks, Publish state, and recent files
- New-note and attachment folder paths
- Homepage configuration
- Vault-specific template paths and Templater folder rules
- Shell Commands entries invoking vault-local scripts
- Excalidraw folder paths
- Kindle account/sync state and highlights path
- Obsidian-to-Anki file hashes and vault-specific export state

Change the configuration boundary through `profile-policy.json`; add shared vault-root files deliberately under `vault-profile/`.

## Commands

```text
obsidian-profile link VAULT
obsidian-profile sync SOURCE [VAULT ...]
obsidian-profile verify VAULT
obsidian-profile install VAULT
obsidian-profile capture SOURCE_VAULT
obsidian-profile bundle-plugins SOURCE_VAULT
```

Use `--profile-root PATH` before the command when invoking the script outside this repository layout.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests cover selective links, mixed-setting preservation, link repair, conflict backups, unmanaged-state preservation, plugin integrity, drift detection, capture sanitization, plugin bundling, and the CLI entry point.
