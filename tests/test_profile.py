import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from obsidian_profile import (
    ProfileError,
    bundle_plugins,
    capture_profile,
    install_profile,
    verify_vault,
)


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def empty_profile(profile: Path) -> None:
    write(profile / "profile/app.json", "{}\n")
    write(profile / "plugin-lock.json", '{"schemaVersion": 1, "plugins": []}\n')


def plugin_lock(profile: Path, plugin_id: str = "example", version: str = "1.0.0") -> dict:
    artifact_dir = profile / "artifacts/plugins" / plugin_id
    write(artifact_dir / "manifest.json", json.dumps({"id": plugin_id, "version": version}) + "\n")
    write(artifact_dir / "main.js", "plugin code\n")
    return {
        "schemaVersion": 1,
        "plugins": [{
            "id": plugin_id,
            "version": version,
            "files": {
                filename: {
                    "path": f"artifacts/plugins/{plugin_id}/{filename}",
                    "sha256": sha256(artifact_dir / filename),
                }
                for filename in ("manifest.json", "main.js")
            },
        }],
    }


class ProfileTests(unittest.TestCase):
    def test_install_does_not_follow_preexisting_state_temp_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            outside = root / "outside.txt"
            empty_profile(profile)
            vault.mkdir()
            write(outside, "do not overwrite\n")
            state_dir = vault / ".obsidian-profile"
            state_dir.mkdir()
            (state_dir / "state.json.tmp").symlink_to(outside)

            install_profile(profile, vault)

            self.assertEqual(outside.read_text(), "do not overwrite\n")
            self.assertTrue((state_dir / "state.json.tmp").is_symlink())
            self.assertEqual(json.loads((state_dir / "state.json").read_text())["schemaVersion"], 1)

    def test_install_overlays_profile_and_backs_up_conflicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", '{"vimMode": true}\n')
            write(profile / "profile/snippets/focus.css", "body {}\n")
            write(profile / "plugin-lock.json", '{"schemaVersion": 1, "plugins": []}\n')
            write(vault / ".obsidian/app.json", '{"vimMode": false}\n')

            result = install_profile(profile, vault)

            self.assertEqual(json.loads((vault / ".obsidian/app.json").read_text()), {"vimMode": True})
            self.assertTrue((vault / ".obsidian/snippets/focus.css").exists())
            backup = vault / ".obsidian-profile/backups" / result.install_id / ".obsidian/app.json"
            self.assertEqual(json.loads(backup.read_text()), {"vimMode": False})
            state = json.loads((vault / ".obsidian-profile/state.json").read_text())
            self.assertIn(".obsidian/app.json", state["managedFiles"])

    def test_install_preserves_unmanaged_vault_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", '{}\n')
            write(profile / "plugin-lock.json", '{"schemaVersion": 1, "plugins": []}\n')
            write(vault / ".obsidian/workspace.json", '{"open": "local-note"}\n')
            write(vault / ".obsidian/plugins/templater-obsidian/data.json", '{"template": "local"}\n')

            install_profile(profile, vault)

            self.assertEqual(json.loads((vault / ".obsidian/workspace.json").read_text()), {"open": "local-note"})
            self.assertEqual(json.loads((vault / ".obsidian/plugins/templater-obsidian/data.json").read_text()), {"template": "local"})

    def test_install_rejects_symlinked_configuration_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            outside = root / "outside"
            write(profile / "profile/app.json", '{}\n')
            write(profile / "plugin-lock.json", '{"schemaVersion": 1, "plugins": []}\n')
            vault.mkdir()
            outside.mkdir()
            (vault / ".obsidian").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ProfileError, "symbolic link"):
                install_profile(profile, vault)

            self.assertFalse((outside / "app.json").exists())

    def test_install_checks_bundled_plugin_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            artifact = profile / "artifacts/plugins/example/main.js"
            manifest = profile / "artifacts/plugins/example/manifest.json"
            write(artifact, "plugin code\n")
            write(manifest, '{"id": "example", "version": "1.0.0"}\n')
            write(profile / "profile/app.json", '{}\n')
            lock = {
                "schemaVersion": 1,
                "plugins": [{
                    "id": "example",
                    "version": "1.0.0",
                    "files": {
                        "main.js": {"path": "artifacts/plugins/example/main.js", "sha256": "0" * 64},
                        "manifest.json": {"path": "artifacts/plugins/example/manifest.json", "sha256": sha256(manifest)},
                    },
                }],
            }
            write(profile / "plugin-lock.json", json.dumps(lock))
            vault.mkdir()

            with self.assertRaisesRegex(ProfileError, "checksum"):
                install_profile(profile, vault)

    def test_install_rejects_plugin_id_path_traversal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", '{}\n')
            write(profile / "artifact/main.js", "code\n")
            write(profile / "artifact/manifest.json", '{}\n')
            lock = {
                "schemaVersion": 1,
                "plugins": [{
                    "id": "../../outside",
                    "version": "1.0.0",
                    "files": {
                        name: {"path": f"artifact/{name}", "sha256": sha256(profile / "artifact" / name)}
                        for name in ("main.js", "manifest.json")
                    },
                }],
            }
            write(profile / "plugin-lock.json", json.dumps(lock))
            vault.mkdir()

            with self.assertRaisesRegex(ProfileError, "plugin id"):
                install_profile(profile, vault)

    def test_install_rejects_noncanonical_lock_path_and_malformed_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", "{}\n")
            lock = plugin_lock(profile)
            vault.mkdir()

            cases = {
                "canonical": "artifacts/plugins/other/main.js",
                "SHA-256": "A" * 64,
            }
            for expected, bad_value in cases.items():
                with self.subTest(expected=expected):
                    candidate = json.loads(json.dumps(lock))
                    metadata = candidate["plugins"][0]["files"]["main.js"]
                    if expected == "canonical":
                        metadata["path"] = bad_value
                    else:
                        metadata["sha256"] = bad_value
                    write(profile / "plugin-lock.json", json.dumps(candidate))
                    with self.assertRaisesRegex(ProfileError, expected):
                        install_profile(profile, vault)

    def test_install_rejects_lock_identity_or_version_that_disagrees_with_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", "{}\n")
            lock = plugin_lock(profile)
            vault.mkdir()

            for field, value in (("id", "other"), ("version", "9.9.9")):
                with self.subTest(field=field):
                    candidate = json.loads(json.dumps(lock))
                    if field == "id":
                        manifest = profile / "artifacts/plugins/example/manifest.json"
                        write(manifest, '{"id": "other", "version": "1.0.0"}\n')
                        candidate["plugins"][0]["files"]["manifest.json"]["sha256"] = sha256(manifest)
                    else:
                        candidate["plugins"][0]["version"] = value
                    write(profile / "plugin-lock.json", json.dumps(candidate))
                    with self.assertRaisesRegex(ProfileError, "manifest"):
                        install_profile(profile, vault)
                    write(profile / "artifacts/plugins/example/manifest.json", '{"id": "example", "version": "1.0.0"}\n')
                    lock["plugins"][0]["files"]["manifest.json"]["sha256"] = sha256(
                        profile / "artifacts/plugins/example/manifest.json"
                    )

    def test_install_rejects_malformed_lock_types_with_profile_error(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", "{}\n")
            vault.mkdir()
            malformed = (
                {"schemaVersion": True, "plugins": []},
                {"schemaVersion": 1, "plugins": ["example"]},
                {"schemaVersion": 1, "plugins": [{"id": "example", "version": 1, "files": {}}]},
            )
            for lock in malformed:
                with self.subTest(lock=lock):
                    write(profile / "plugin-lock.json", json.dumps(lock))
                    with self.assertRaises(ProfileError):
                        install_profile(profile, vault)

    def test_install_records_hash_from_verified_snapshot_if_artifact_changes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", "{}\n")
            lock = plugin_lock(profile)
            write(profile / "plugin-lock.json", json.dumps(lock))
            vault.mkdir()
            artifact = profile / "artifacts/plugins/example/main.js"
            expected = lock["plugins"][0]["files"]["main.js"]["sha256"]
            real_copy2 = __import__("shutil").copy2
            changed = False

            def mutate_after_copy(source, destination, *args, **kwargs):
                nonlocal changed
                result = real_copy2(source, destination, *args, **kwargs)
                if Path(source).resolve() == artifact.resolve() and not changed:
                    write(artifact, "changed after verified copy\n")
                    changed = True
                return result

            with mock.patch("obsidian_profile.shutil.copy2", side_effect=mutate_after_copy):
                install_profile(profile, vault)

            state = json.loads((vault / ".obsidian-profile/state.json").read_text())
            self.assertEqual(state["managedFiles"][".obsidian/plugins/example/main.js"], expected)

    def test_verify_detects_managed_file_drift(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            write(profile / "profile/app.json", '{}\n')
            write(profile / "plugin-lock.json", '{"schemaVersion": 1, "plugins": []}\n')
            vault.mkdir()
            install_profile(profile, vault)
            write(vault / ".obsidian/app.json", '{"changed": true}\n')

            report = verify_vault(profile, vault)

            self.assertFalse(report.ok)
            self.assertEqual(report.drifted, [".obsidian/app.json"])

    def test_verify_requires_real_vault_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            empty_profile(profile)
            real_vault = root / "real-vault"
            real_vault.mkdir()
            linked_vault = root / "linked-vault"
            linked_vault.symlink_to(real_vault, target_is_directory=True)

            for vault in (root / "missing", linked_vault):
                with self.subTest(vault=vault), self.assertRaisesRegex(ProfileError, "vault directory"):
                    verify_vault(profile, vault)

    def test_verify_rejects_symlinked_managed_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            profile = root / "profile-repo"
            vault = root / "vault"
            outside = root / "outside.json"
            empty_profile(profile)
            write(outside, "{}\n")
            (vault / ".obsidian").mkdir(parents=True)
            (vault / ".obsidian/app.json").symlink_to(outside)

            with self.assertRaisesRegex(ProfileError, "symbolic link"):
                verify_vault(profile, vault)

    def test_capture_sanitizes_app_and_excludes_vault_specific_plugin_data(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            write(source / ".obsidian/app.json", json.dumps({
                "vimMode": True,
                "showLineNumber": True,
                "attachmentFolderPath": "private/files",
                "newFileFolderPath": "private",
            }))
            write(source / ".obsidian/appearance.json", '{"cssTheme": ""}\n')
            write(source / ".obsidian/plugins/omnisearch/data.json", '{"vimLikeNavigationShortcut": true}\n')
            write(source / ".obsidian/plugins/templater-obsidian/data.json", '{"templates_folder": "private"}\n')
            policy = {
                "schemaVersion": 1,
                "appKeys": ["vimMode", "showLineNumber"],
                "files": ["appearance.json"],
                "pluginData": ["omnisearch"],
                "jsonOverrides": {"appearance.json": {"cssTheme": "Minimal"}},
            }
            write(repo / "profile-policy.json", json.dumps(policy))

            capture_profile(repo, source)

            app = json.loads((repo / "profile/app.json").read_text())
            self.assertEqual(app, {"vimMode": True, "showLineNumber": True})
            appearance = json.loads((repo / "profile/appearance.json").read_text())
            self.assertEqual(appearance["cssTheme"], "Minimal")
            self.assertTrue((repo / "profile/plugins/omnisearch/data.json").exists())
            self.assertFalse((repo / "profile/plugins/templater-obsidian/data.json").exists())

    def test_capture_validates_policy_before_replacing_live_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            write(repo / "profile/app.json", '{"old": true}\n')
            write(source / ".obsidian/app.json", "{}\n")
            write(repo / "profile-policy.json", json.dumps({
                "schemaVersion": True,
                "appKeys": [],
                "files": [],
                "pluginData": [],
                "jsonOverrides": {},
            }))

            with self.assertRaises(ProfileError):
                capture_profile(repo, source)

            self.assertEqual(json.loads((repo / "profile/app.json").read_text()), {"old": True})

    def test_capture_rejects_symlinked_source_before_replacing_live_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            outside = root / "outside.json"
            write(repo / "profile/app.json", '{"old": true}\n')
            write(source / ".obsidian/app.json", "{}\n")
            write(outside, "{}\n")
            (source / ".obsidian/appearance.json").symlink_to(outside)
            write(repo / "profile-policy.json", json.dumps({
                "schemaVersion": 1,
                "appKeys": [],
                "files": ["appearance.json"],
                "pluginData": [],
                "jsonOverrides": {},
            }))

            with self.assertRaisesRegex(ProfileError, "symbolic link"):
                capture_profile(repo, source)

            self.assertEqual(json.loads((repo / "profile/app.json").read_text()), {"old": True})

    def test_capture_rejects_symlinked_destination_before_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            outside = root / "outside"
            write(source / ".obsidian/app.json", "{}\n")
            write(source / ".obsidian/appearance.json", "{}\n")
            outside.mkdir()
            (repo / "profile").mkdir(parents=True)
            (repo / "profile/themes").symlink_to(outside, target_is_directory=True)
            write(repo / "profile-policy.json", json.dumps({
                "schemaVersion": 1,
                "appKeys": [],
                "files": ["appearance.json"],
                "pluginData": [],
                "jsonOverrides": {},
            }))

            with self.assertRaisesRegex(ProfileError, "symbolic link"):
                capture_profile(repo, source)

            self.assertFalse((repo / "profile/app.json").exists())

    def test_capture_staging_failure_preserves_live_profile(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            write(repo / "profile/app.json", '{"old": true}\n')
            write(source / ".obsidian/app.json", '{"new": true}\n')
            write(repo / "profile-policy.json", json.dumps({
                "schemaVersion": 1,
                "appKeys": ["new"],
                "files": [],
                "pluginData": [],
                "jsonOverrides": {"missing.json": {"value": True}},
            }))

            with self.assertRaisesRegex(ProfileError, "missing required file"):
                capture_profile(repo, source)

            self.assertEqual(json.loads((repo / "profile/app.json").read_text()), {"old": True})

    def test_bundle_plugins_copies_enabled_artifacts_and_writes_checksums(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            write(repo / "profile/community-plugins.json", '["example"]\n')
            write(source / ".obsidian/plugins/example/manifest.json", json.dumps({
                "id": "example", "version": "1.2.3", "authorUrl": "https://example.test/plugin"
            }))
            write(source / ".obsidian/plugins/example/main.js", "plugin code\n")
            write(source / ".obsidian/plugins/example/styles.css", "body {}\n")

            count = bundle_plugins(repo, source)

            self.assertEqual(count, 1)
            lock = json.loads((repo / "plugin-lock.json").read_text())
            self.assertEqual(lock["plugins"][0]["version"], "1.2.3")
            metadata = lock["plugins"][0]["files"]["main.js"]
            artifact = repo / metadata["path"]
            self.assertEqual(metadata["sha256"], sha256(artifact))

    def test_bundle_rejects_symlinked_source_and_preserves_existing_bundle(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            outside = root / "outside.js"
            write(repo / "profile/community-plugins.json", '["example"]\n')
            write(repo / "artifacts/plugins/old/main.js", "old code\n")
            write(repo / "plugin-lock.json", '{"schemaVersion": 1, "plugins": []}\n')
            write(source / ".obsidian/plugins/example/manifest.json", '{"id":"example","version":"1.0.0"}\n')
            write(outside, "outside code\n")
            (source / ".obsidian/plugins/example/main.js").symlink_to(outside)

            with self.assertRaisesRegex(ProfileError, "symbolic link"):
                bundle_plugins(repo, source)

            self.assertEqual((repo / "artifacts/plugins/old/main.js").read_text(), "old code\n")

    def test_bundle_rejects_symlinked_destination_before_any_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            outside = root / "outside"
            write(repo / "profile/community-plugins.json", "[]\n")
            outside.mkdir()
            (repo / "artifacts").mkdir()
            (repo / "artifacts/plugins").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ProfileError, "symbolic link"):
                bundle_plugins(repo, source)

            self.assertFalse((repo / "plugin-lock.json").exists())
            self.assertEqual(list(outside.iterdir()), [])

    def test_bundle_rolls_back_artifacts_when_lock_replacement_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo = root / "profile-repo"
            source = root / "source-vault"
            write(repo / "profile/community-plugins.json", '["example"]\n')
            write(repo / "artifacts/plugins/old/main.js", "old code\n")
            old_lock = '{"schemaVersion": 1, "plugins": []}\n'
            write(repo / "plugin-lock.json", old_lock)
            write(source / ".obsidian/plugins/example/manifest.json", '{"id":"example","version":"1.0.0"}\n')
            write(source / ".obsidian/plugins/example/main.js", "new code\n")
            real_replace = os.replace

            def fail_new_lock(source_path, destination_path):
                destination = Path(destination_path)
                if (
                    destination.name == "plugin-lock.json"
                    and destination.parent.resolve() == repo.resolve()
                    and ".tmp." in Path(source_path).name
                ):
                    raise OSError("injected lock replacement failure")
                return real_replace(source_path, destination_path)

            with mock.patch("obsidian_profile.os.replace", side_effect=fail_new_lock):
                with self.assertRaisesRegex(OSError, "injected"):
                    bundle_plugins(repo, source)

            self.assertEqual((repo / "artifacts/plugins/old/main.js").read_text(), "old code\n")
            self.assertEqual((repo / "plugin-lock.json").read_text(), old_lock)


class CliTests(unittest.TestCase):
    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(REPO / "bin/obsidian-profile"), "--help"],
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("install", result.stdout)
        self.assertIn("capture", result.stdout)
        self.assertIn("verify", result.stdout)


if __name__ == "__main__":
    unittest.main()
