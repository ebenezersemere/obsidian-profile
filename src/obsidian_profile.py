#!/usr/bin/env python3
"""Link, synchronize, install, and verify a portable Obsidian profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ProfileError(RuntimeError):
    """Raised when a profile cannot be safely applied."""


@dataclass(frozen=True)
class InstallResult:
    install_id: str
    copied: int
    backed_up: int


@dataclass(frozen=True)
class VerifyReport:
    missing: List[str]
    drifted: List[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.drifted


@dataclass(frozen=True)
class ManagedSource:
    source: Path
    destination: Path
    expected_sha256: str | None = None
    plugin_id: str | None = None
    plugin_version: str | None = None
    filename: str | None = None


@dataclass(frozen=True)
class PreparedSource:
    source: Path
    destination: Path
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileError(f"missing required file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(f"invalid JSON in {path}: {exc}") from exc
    except (OSError, UnicodeError) as exc:
        raise ProfileError(f"cannot read JSON file {path}: {exc}") from exc


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ProfileError(f"unsafe relative path in profile: {value}")
    return path


def _safe_component(value: str, label: str) -> str:
    try:
        path = _safe_relative(value)
    except ProfileError as exc:
        raise ProfileError(f"invalid {label}: {value!r}") from exc
    if len(path.parts) != 1 or path.name in (".", ".."):
        raise ProfileError(f"invalid {label}: {value!r}")
    return value


def _reject_symlink_path(root: Path, relative: Path) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ProfileError(f"refusing to use symbolic link: {current}")


def _reject_symlink_parents(root: Path, relative: Path) -> None:
    """Reject symlinked parents while allowing the final path to be a link."""
    current = root
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ProfileError(f"refusing to use symbolic link as a parent: {current}")


def _reject_symlink_tree(path: Path) -> None:
    if path.is_symlink():
        raise ProfileError(f"refusing to use symbolic link: {path}")
    if path.is_dir():
        for child in path.rglob("*"):
            if child.is_symlink():
                raise ProfileError(f"refusing to use symbolic link: {child}")


def _real_directory(path: Path, label: str) -> Path:
    expanded = Path(path).expanduser()
    if expanded.is_symlink() or not expanded.is_dir():
        raise ProfileError(f"{label} directory does not exist or is a symbolic link: {expanded}")
    return expanded.resolve()


def _require_object(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise ProfileError(f"{label} must be a JSON object")
    return value


def _require_string_list(value, label: str, *, unique: bool = True) -> List[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ProfileError(f"{label} must be a list of non-empty strings")
    if unique and len(value) != len(set(value)):
        raise ProfileError(f"{label} must not contain duplicates")
    return value


def _validate_lock(lock) -> List[dict]:
    lock = _require_object(lock, "plugin-lock.json")
    if type(lock.get("schemaVersion")) is not int or lock["schemaVersion"] != 1:
        raise ProfileError("plugin-lock.json schemaVersion must be the integer 1")
    if set(lock) != {"schemaVersion", "plugins"}:
        raise ProfileError("plugin-lock.json contains missing or unexpected fields")
    plugins = lock.get("plugins")
    if not isinstance(plugins, list):
        raise ProfileError("plugin-lock.json plugins must be a list")

    seen = set()
    for index, value in enumerate(plugins):
        plugin = _require_object(value, f"plugin-lock.json plugin {index}")
        if not {"id", "version", "files"} <= set(plugin) <= {"id", "version", "source", "files"}:
            raise ProfileError(f"plugin-lock.json plugin {index} contains missing or unexpected fields")
        plugin_id = plugin.get("id")
        version = plugin.get("version")
        if not isinstance(plugin_id, str) or not plugin_id or plugin_id in seen:
            raise ProfileError(f"invalid or duplicate plugin id: {plugin_id!r}")
        _safe_component(plugin_id, "plugin id")
        seen.add(plugin_id)
        if not isinstance(version, str) or not version:
            raise ProfileError(f"plugin {plugin_id} version must be a non-empty string")
        if "source" in plugin and plugin["source"] is not None and not isinstance(plugin["source"], str):
            raise ProfileError(f"plugin {plugin_id} source must be a string or null")
        files = plugin.get("files")
        if not isinstance(files, dict) or not {"manifest.json", "main.js"} <= set(files):
            raise ProfileError(f"plugin {plugin_id} must lock manifest.json and main.js")
        if not set(files) <= {"manifest.json", "main.js", "styles.css"}:
            raise ProfileError(f"plugin {plugin_id} locks an unsupported artifact")
        for filename, metadata_value in files.items():
            metadata = _require_object(metadata_value, f"lock metadata for {plugin_id}/{filename}")
            if set(metadata) != {"path", "sha256"}:
                raise ProfileError(f"lock metadata for {plugin_id}/{filename} has invalid fields")
            canonical = f"artifacts/plugins/{plugin_id}/{filename}"
            if metadata.get("path") != canonical:
                raise ProfileError(f"plugin artifact path must be canonical: {canonical}")
            checksum = metadata.get("sha256")
            if not isinstance(checksum, str) or SHA256_PATTERN.fullmatch(checksum) is None:
                raise ProfileError(f"invalid SHA-256 for {plugin_id}/{filename}")
    return plugins


def _validate_policy(policy) -> dict:
    policy = _require_object(policy, "profile-policy.json")
    required = {"schemaVersion", "appKeys", "files", "pluginData", "jsonOverrides"}
    if set(policy) != required:
        raise ProfileError("profile-policy.json contains missing or unexpected fields")
    if type(policy.get("schemaVersion")) is not int or policy["schemaVersion"] != 1:
        raise ProfileError("profile-policy.json schemaVersion must be the integer 1")
    app_keys = _require_string_list(policy["appKeys"], "profile-policy.json appKeys")
    files = _require_string_list(policy["files"], "profile-policy.json files")
    plugin_data = _require_string_list(policy["pluginData"], "profile-policy.json pluginData")
    overrides = _require_object(policy["jsonOverrides"], "profile-policy.json jsonOverrides")
    for value in files:
        _safe_relative(value)
    for plugin_id in plugin_data:
        _safe_component(plugin_id, "plugin id in policy")
    for value, document_overrides in overrides.items():
        if not isinstance(value, str) or not value:
            raise ProfileError("profile-policy.json override paths must be non-empty strings")
        _safe_relative(value)
        _require_object(document_overrides, f"JSON overrides for {value}")
    return {
        "appKeys": app_keys,
        "files": files,
        "pluginData": plugin_data,
        "jsonOverrides": overrides,
    }


def _profile_files(profile_root: Path) -> Iterable[ManagedSource]:
    source_root = profile_root / "profile"
    if source_root.is_symlink():
        raise ProfileError(f"profile contains a symbolic link: {source_root}")
    if not source_root.is_dir():
        raise ProfileError(f"missing profile directory: {source_root}")
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise ProfileError(f"profile contains a symbolic link: {source}")
        if source.is_file():
            yield ManagedSource(source, Path(".obsidian") / source.relative_to(source_root))


def _vault_profile_files(profile_root: Path) -> Iterable[ManagedSource]:
    """Files stored under vault-profile are linked at the vault root."""
    source_root = profile_root / "vault-profile"
    if not source_root.exists():
        return
    if source_root.is_symlink() or not source_root.is_dir():
        raise ProfileError(f"vault-profile must be a real directory: {source_root}")
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise ProfileError(f"vault-profile contains a symbolic link: {source}")
        if source.is_file():
            yield ManagedSource(source, source.relative_to(source_root))


def _plugin_files(profile_root: Path, plugins: Sequence[dict]) -> Iterable[ManagedSource]:
    for plugin in plugins:
        plugin_id = plugin["id"]
        files = plugin["files"]
        for filename, metadata in sorted(files.items()):
            relative_source = Path(metadata["path"])
            _reject_symlink_path(profile_root, relative_source)
            source = profile_root / relative_source
            if not source.is_file():
                raise ProfileError(f"missing plugin artifact: {source}")
            yield ManagedSource(
                source,
                Path(".obsidian/plugins") / plugin_id / filename,
                metadata["sha256"],
                plugin_id,
                plugin["version"],
                filename,
            )


def _planned_sources(profile_root: Path, lock) -> List[ManagedSource]:
    _reject_symlink_path(profile_root, Path("plugin-lock.json"))
    plugins = _validate_lock(lock)
    planned = (
        list(_profile_files(profile_root))
        + list(_vault_profile_files(profile_root))
        + list(_plugin_files(profile_root, plugins))
    )
    destinations = [item.destination.as_posix() for item in planned]
    if len(destinations) != len(set(destinations)):
        raise ProfileError("the profile and plugin lock manage the same destination")
    return planned


def _prepare_sources(planned: Sequence[ManagedSource], snapshot_root: Path | None = None) -> List[PreparedSource]:
    prepared = []
    manifests = []
    for index, item in enumerate(planned):
        source = item.source
        if snapshot_root is not None:
            source = snapshot_root / str(index)
            shutil.copy2(item.source, source)
        actual = _sha256(source)
        if item.expected_sha256 is not None and actual != item.expected_sha256:
            raise ProfileError(f"checksum mismatch for {item.plugin_id}/{item.filename}")
        prepared.append(PreparedSource(source, item.destination, actual))
        if item.filename == "manifest.json":
            manifests.append((source, item.plugin_id, item.plugin_version))

    for manifest_path, plugin_id, version in manifests:
        manifest = _require_object(_load_json(manifest_path), f"manifest for plugin {plugin_id}")
        if manifest.get("id") != plugin_id or manifest.get("version") != version:
            raise ProfileError(f"plugin lock id/version disagrees with manifest for {plugin_id}")
    return prepared


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=str(destination.parent))
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_symlink(target: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{destination.name}.link.{uuid.uuid4().hex}"
    try:
        temporary.symlink_to(target)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _symlink_points_to(path: Path, target: Path) -> bool:
    if not path.is_symlink():
        return False
    try:
        return (path.parent / os.readlink(path)).resolve() == target.resolve()
    except OSError:
        return False


def _stage_json(destination: Path, value) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.tmp.", dir=str(destination.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _atomic_json(destination: Path, value) -> None:
    temporary = _stage_json(destination, value)
    try:
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_directory(staging: Path, destination: Path) -> None:
    old = destination.parent / f".{destination.name}.old.{uuid.uuid4().hex}"
    had_old = destination.exists()
    try:
        if had_old:
            os.replace(destination, old)
        os.replace(staging, destination)
    except Exception:
        if had_old and old.exists():
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(old, destination)
        raise
    if old.exists():
        shutil.rmtree(old)


def install_profile(profile_root: Path, vault: Path) -> InstallResult:
    profile_root = Path(profile_root).expanduser().resolve()
    vault = _real_directory(vault, "vault")

    lock = _load_json(profile_root / "plugin-lock.json")
    planned = _planned_sources(profile_root, lock)
    for item in planned:
        _reject_symlink_path(vault, item.destination)
    _reject_symlink_path(vault, Path(".obsidian-profile"))
    _reject_symlink_path(vault, Path(".obsidian-profile/state.json"))

    install_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup_root = vault / ".obsidian-profile/backups" / install_id
    backed_up = 0
    managed: Dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="obsidian-profile-install.") as temporary_dir:
        prepared = _prepare_sources(planned, Path(temporary_dir))
        for item in prepared:
            destination = vault / item.destination
            if destination.exists() and _sha256(destination) != item.sha256:
                backup = backup_root / item.destination
                _atomic_copy(destination, backup)
                backed_up += 1
            _atomic_copy(item.source, destination)
            managed[item.destination.as_posix()] = item.sha256

    state = {
        "schemaVersion": 1,
        "profileRoot": str(profile_root),
        "installId": install_id,
        "installedAt": datetime.now(timezone.utc).isoformat(),
        "managedFiles": managed,
    }
    state_path = vault / ".obsidian-profile/state.json"
    _atomic_json(state_path, state)
    return InstallResult(install_id=install_id, copied=len(planned), backed_up=backed_up)


def link_profile(profile_root: Path, vault: Path) -> InstallResult:
    """Link portable settings into a vault and copy pinned plugin artifacts."""
    profile_root = Path(profile_root).expanduser().resolve()
    vault = _real_directory(vault, "vault")
    policy = _validate_policy(_load_json(profile_root / "profile-policy.json"))
    lock = _load_json(profile_root / "plugin-lock.json")
    planned = _planned_sources(profile_root, lock)
    prepared = _prepare_sources(planned)

    for item in planned:
        _reject_symlink_parents(vault, item.destination)
    _reject_symlink_path(vault, Path(".obsidian-profile"))

    install_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup_root = vault / ".obsidian-profile/backups" / install_id
    backed_up = 0
    managed: Dict[str, str] = {}
    linked: Dict[str, str] = {}

    for item, ready in zip(planned, prepared):
        destination = vault / item.destination
        name = item.destination.as_posix()
        is_profile_file = item.source.is_relative_to(profile_root / "profile")
        is_vault_profile_file = item.source.is_relative_to(profile_root / "vault-profile")
        is_linked_file = is_profile_file or is_vault_profile_file
        is_app = item.source == profile_root / "profile/app.json"

        if is_app:
            source_app = _require_object(_load_json(item.source), "profile app.json")
            if destination.is_symlink():
                raise ProfileError(f"app.json cannot be linked because it contains vault-local settings: {destination}")
            if destination.exists():
                vault_app = _require_object(_load_json(destination), "vault app.json")
            else:
                vault_app = {}
            merged = dict(vault_app)
            for key in policy["appKeys"]:
                if key in source_app:
                    merged[key] = source_app[key]
                else:
                    merged.pop(key, None)
            serialized = json.dumps(merged, indent=2, sort_keys=True) + "\n"
            if not destination.is_file() or destination.read_text(encoding="utf-8") != serialized:
                if destination.exists():
                    _atomic_copy(destination, backup_root / item.destination)
                    backed_up += 1
                _atomic_json(destination, merged)
            managed[name] = _sha256(destination)
        elif is_linked_file:
            if _symlink_points_to(destination, item.source):
                linked[name] = str(item.source)
                managed[name] = ready.sha256
                continue
            if destination.exists() or destination.is_symlink():
                if not destination.is_file() or destination.is_symlink() or _sha256(destination) != ready.sha256:
                    backup = backup_root / item.destination
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(destination, backup)
                    backed_up += 1
                else:
                    destination.unlink()
            _atomic_symlink(item.source, destination)
            linked[name] = str(item.source)
            managed[name] = ready.sha256
        else:
            _reject_symlink_path(vault, item.destination)
            if destination.exists() and _sha256(destination) != ready.sha256:
                _atomic_copy(destination, backup_root / item.destination)
                backed_up += 1
            _atomic_copy(item.source, destination)
            if _sha256(destination) != ready.sha256:
                raise ProfileError(f"source changed while copying {name}; rerun link")
            managed[name] = ready.sha256

    state = {
        "schemaVersion": 2,
        "mode": "linked",
        "profileRoot": str(profile_root),
        "installId": install_id,
        "installedAt": datetime.now(timezone.utc).isoformat(),
        "managedFiles": managed,
        "linkedFiles": linked,
    }
    _atomic_json(vault / ".obsidian-profile/state.json", state)
    return InstallResult(install_id=install_id, copied=len(planned), backed_up=backed_up)


def verify_vault(profile_root: Path, vault: Path) -> VerifyReport:
    profile_root = Path(profile_root).expanduser().resolve()
    vault = _real_directory(vault, "vault")
    lock = _load_json(profile_root / "plugin-lock.json")
    planned_sources = _planned_sources(profile_root, lock)
    planned = _prepare_sources(planned_sources)
    state_path = vault / ".obsidian-profile/state.json"
    state = _load_json(state_path) if state_path.is_file() else {}
    linked_mode = isinstance(state, dict) and state.get("mode") == "linked"
    policy = _validate_policy(_load_json(profile_root / "profile-policy.json")) if linked_mode else None
    missing: List[str] = []
    drifted: List[str] = []
    for source, item in zip(planned_sources, planned):
        destination = vault / item.destination
        name = item.destination.as_posix()
        is_profile_file = source.source.is_relative_to(profile_root / "profile")
        is_vault_profile_file = source.source.is_relative_to(profile_root / "vault-profile")
        is_linked_file = is_profile_file or is_vault_profile_file
        is_app = source.source == profile_root / "profile/app.json"
        if linked_mode and is_app:
            if not destination.is_file() or destination.is_symlink():
                missing.append(name)
                continue
            profile_app = _require_object(_load_json(source.source), "profile app.json")
            vault_app = _require_object(_load_json(destination), "vault app.json")
            if any(profile_app.get(key) != vault_app.get(key) for key in policy["appKeys"]):
                drifted.append(name)
        elif linked_mode and is_linked_file:
            if not destination.exists() and not destination.is_symlink():
                missing.append(name)
            elif not _symlink_points_to(destination, source.source):
                drifted.append(name)
        else:
            _reject_symlink_path(vault, item.destination)
            if not destination.is_file():
                missing.append(name)
            elif _sha256(destination) != item.sha256:
                drifted.append(name)
    return VerifyReport(missing=missing, drifted=drifted)


def sync_profile(profile_root: Path, source_vault: Path, target_vaults: Sequence[Path]) -> int:
    """Capture mixed app settings from one vault and apply the linked profile."""
    profile_root = Path(profile_root).expanduser().resolve()
    source_vault = _real_directory(source_vault, "source vault")
    policy = _validate_policy(_load_json(profile_root / "profile-policy.json"))
    source_app_path = source_vault / ".obsidian/app.json"
    _reject_symlink_path(source_vault, Path(".obsidian/app.json"))
    source_app = _require_object(_load_json(source_app_path), "source app.json")
    portable_app = {key: source_app[key] for key in policy["appKeys"] if key in source_app}
    _atomic_json(profile_root / "profile/app.json", portable_app)

    vaults = []
    seen = set()
    for value in (source_vault, *target_vaults):
        vault = _real_directory(value, "vault")
        canonical = str(vault)
        if canonical not in seen:
            seen.add(canonical)
            vaults.append(vault)
    for vault in vaults:
        link_profile(profile_root, vault)
    return len(vaults)


def capture_profile(profile_root: Path, vault: Path) -> int:
    profile_root = Path(profile_root).expanduser().resolve()
    vault = _real_directory(vault, "source vault")
    obsidian = vault / ".obsidian"
    if obsidian.is_symlink() or not obsidian.is_dir():
        raise ProfileError(f"source vault has no .obsidian directory: {vault}")
    _reject_symlink_path(profile_root, Path("profile-policy.json"))
    policy = _validate_policy(_load_json(profile_root / "profile-policy.json"))
    output = profile_root / "profile"
    _reject_symlink_path(profile_root, Path("profile"))
    _reject_symlink_tree(output)

    _reject_symlink_path(obsidian, Path("app.json"))
    app = _require_object(_load_json(obsidian / "app.json"), "source app.json")
    source_files = []
    for value in policy["files"]:
        relative = _safe_relative(value)
        _reject_symlink_path(obsidian, relative)
        source = obsidian / relative
        if not source.exists():
            continue
        if source.is_dir():
            _reject_symlink_tree(source)
        elif source.is_file():
            pass
        else:
            raise ProfileError(f"unsupported source file type: {source}")
        source_files.append((source, relative))

    plugin_files = []
    for plugin_id in policy["pluginData"]:
        source = obsidian / "plugins" / plugin_id / "data.json"
        _reject_symlink_path(obsidian, Path("plugins") / plugin_id / "data.json")
        if source.is_file():
            plugin_files.append((source, Path("plugins") / plugin_id / "data.json"))

    staging = Path(tempfile.mkdtemp(prefix=".profile.tmp.", dir=str(profile_root)))
    copied = 1
    try:
        portable_app = {key: app[key] for key in policy["appKeys"] if key in app}
        _atomic_json(staging / "app.json", portable_app)
        for source, relative in source_files:
            destination = staging / relative
            if source.is_dir():
                shutil.copytree(source, destination)
                copied += sum(1 for path in destination.rglob("*") if path.is_file())
            else:
                _atomic_copy(source, destination)
                copied += 1
        for source, relative in plugin_files:
            _atomic_copy(source, staging / relative)
            copied += 1
        for value, overrides in policy["jsonOverrides"].items():
            destination = staging / _safe_relative(value)
            document = _require_object(_load_json(destination), f"captured JSON file {value}")
            document.update(overrides)
            _atomic_json(destination, document)
        _replace_directory(staging, output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return copied


def bundle_plugins(profile_root: Path, vault: Path) -> int:
    """Bundle the enabled plugins from a vault and regenerate the checksum lock."""
    profile_root = Path(profile_root).expanduser().resolve()
    vault = _real_directory(vault, "source vault")
    source_plugins = vault / ".obsidian/plugins"
    _reject_symlink_path(vault, Path(".obsidian/plugins"))
    _reject_symlink_path(profile_root, Path("profile/community-plugins.json"))
    enabled = _require_string_list(
        _load_json(profile_root / "profile/community-plugins.json"),
        "community-plugins.json",
    )
    for plugin_id in enabled:
        _safe_component(plugin_id, "plugin id")

    previous_sources = {}
    lock_path = profile_root / "plugin-lock.json"
    _reject_symlink_path(profile_root, Path("plugin-lock.json"))
    if lock_path.is_file():
        previous = _load_json(lock_path)
        _validate_lock(previous)
        previous_sources = {
            item["id"]: item.get("source")
            for item in previous["plugins"]
        }

    artifacts_root = profile_root / "artifacts"
    destination = artifacts_root / "plugins"
    _reject_symlink_path(profile_root, Path("artifacts/plugins"))
    _reject_symlink_tree(destination)

    sources = []
    for plugin_id in enabled:
        source_dir = source_plugins / plugin_id
        _reject_symlink_path(source_plugins, Path(plugin_id))
        if source_dir.is_symlink() or not source_dir.is_dir():
            raise ProfileError(f"enabled plugin is missing required artifacts: {plugin_id}")
        manifest_path = source_dir / "manifest.json"
        main_path = source_dir / "main.js"
        for filename in ("manifest.json", "main.js", "styles.css"):
            _reject_symlink_path(source_plugins, Path(plugin_id) / filename)
        if not manifest_path.is_file() or not main_path.is_file():
            raise ProfileError(f"enabled plugin is missing required artifacts: {plugin_id}")
        manifest = _require_object(_load_json(manifest_path), f"manifest for plugin {plugin_id}")
        if manifest.get("id") != plugin_id or not isinstance(manifest.get("version"), str) or not manifest["version"]:
            raise ProfileError(f"invalid manifest for plugin: {plugin_id}")
        sources.append((plugin_id, source_dir, manifest))

    artifacts_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="plugins.", dir=str(artifacts_root)))
    plugins = []
    lock_temporary = None
    try:
        for plugin_id, source_dir, manifest in sources:
            files = {}
            target_dir = staging / plugin_id
            target_dir.mkdir(parents=True)
            for filename in ("manifest.json", "main.js", "styles.css"):
                source = source_dir / filename
                if source.is_file():
                    target = target_dir / filename
                    shutil.copy2(source, target)
                    files[filename] = {
                        "path": f"artifacts/plugins/{plugin_id}/{filename}",
                        "sha256": _sha256(target),
                    }
            plugins.append({
                "id": plugin_id,
                "version": manifest["version"],
                "source": previous_sources.get(plugin_id) or manifest.get("authorUrl"),
                "files": files,
            })

        new_lock = {"schemaVersion": 1, "plugins": plugins}
        _validate_lock(new_lock)
        lock_temporary = _stage_json(lock_path, new_lock)

        old_plugins = artifacts_root / f".plugins.old.{uuid.uuid4().hex}"
        old_lock = profile_root / f".plugin-lock.json.old.{uuid.uuid4().hex}"
        had_plugins = destination.exists()
        had_lock = lock_path.exists()
        try:
            if had_plugins:
                os.replace(destination, old_plugins)
            os.replace(staging, destination)
            if had_lock:
                os.replace(lock_path, old_lock)
            os.replace(lock_temporary, lock_path)
            lock_temporary = None
        except Exception:
            if had_lock and old_lock.exists():
                if lock_path.exists() or lock_path.is_symlink():
                    lock_path.unlink()
                os.replace(old_lock, lock_path)
            if destination.exists():
                shutil.rmtree(destination)
            if had_plugins and old_plugins.exists():
                os.replace(old_plugins, destination)
            raise
        if old_plugins.exists():
            shutil.rmtree(old_plugins)
        if old_lock.exists():
            old_lock.unlink()
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        if lock_temporary is not None:
            lock_temporary.unlink(missing_ok=True)
        raise
    return len(plugins)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-root", type=Path, default=Path(__file__).resolve().parents[1])
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("install", "copy the profile into a vault"),
        ("link", "link portable settings and install pinned plugins"),
        ("verify", "check a vault for profile drift"),
        ("capture", "capture allowlisted settings from a source vault"),
        ("bundle-plugins", "bundle enabled plugins from a source vault"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("vault", type=Path)
    sync = subparsers.add_parser("sync", help="capture mixed settings from a source and link one or more vaults")
    sync.add_argument("source", type=Path)
    sync.add_argument("vaults", type=Path, nargs="*")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "install":
            result = install_profile(args.profile_root, args.vault)
            print(f"installed {result.copied} files; backed up {result.backed_up}; install id {result.install_id}")
        elif args.command == "link":
            result = link_profile(args.profile_root, args.vault)
            print(f"linked profile across {result.copied} managed files; backed up {result.backed_up}; install id {result.install_id}")
        elif args.command == "sync":
            synced = sync_profile(args.profile_root, args.source, args.vaults)
            print(f"synced portable app settings and linked {synced} vaults")
        elif args.command == "capture":
            copied = capture_profile(args.profile_root, args.vault)
            print(f"captured {copied} portable settings files")
        elif args.command == "bundle-plugins":
            bundled = bundle_plugins(args.profile_root, args.vault)
            print(f"bundled {bundled} plugins and regenerated plugin-lock.json")
        else:
            report = verify_vault(args.profile_root, args.vault)
            if report.ok:
                print("profile verified: no drift")
                return 0
            for name in report.missing:
                print(f"missing: {name}")
            for name in report.drifted:
                print(f"drifted: {name}")
            return 1
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
