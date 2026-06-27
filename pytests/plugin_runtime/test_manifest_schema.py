from pathlib import Path
from typing import Any

import ast
import json
import jsonschema
import re

from src.plugin_runtime.runner import manifest_validator
from src.plugin_runtime.runner.manifest_validator import ManifestValidator


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = PROJECT_ROOT / "plugins" / "_manifest.schema.json"


def _load_schema() -> dict[str, Any]:
    return json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))


def _load_schema_without_duplicate_keys() -> dict[str, Any]:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        seen: set[str] = set()
        result: dict[str, Any] = {}
        for key, value in pairs:
            assert key not in seen, f"schema 中存在重复字段: {key}"
            seen.add(key)
            result[key] = value
        return result

    return json.loads(MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)


def _manifest_model_fields(class_name: str) -> set[str]:
    source = Path(manifest_validator.__file__).read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        return {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
    raise AssertionError(f"未找到 manifest 模型类: {class_name}")


def _registered_capabilities() -> set[str]:
    registry_path = PROJECT_ROOT / "src" / "plugin_runtime" / "capabilities" / "registry.py"
    return set(re.findall(r'_register\("([^"]+)"', registry_path.read_text(encoding="utf-8")))


def _frontend_plugin_types() -> set[str]:
    return set(_frontend_plugin_type_labels())


def _frontend_plugin_type_labels() -> dict[str, str]:
    plugin_type_path = PROJECT_ROOT / "dashboard" / "src" / "types" / "plugin.ts"
    plugin_types_path = PROJECT_ROOT / "dashboard" / "src" / "routes" / "plugins" / "types.ts"
    source = plugin_type_path.read_text(encoding="utf-8")
    match = re.search(r"export type PluginType =(?P<body>(?:\n\s*\| '[^']+')+)", source)
    assert match is not None, "未找到前端 PluginType 联合类型"
    plugin_types = set(re.findall(r"'([^']+)'", match.group("body")))

    labels_source = plugin_types_path.read_text(encoding="utf-8")
    labels_match = re.search(
        r"export const PLUGIN_TYPE_LABELS: Record<PluginType, string> = \{(?P<body>.*?)\n\}",
        labels_source,
        re.S,
    )
    assert labels_match is not None, "未找到前端 PLUGIN_TYPE_LABELS"
    labels = dict(re.findall(r"\s*(\w+): '([^']+)'", labels_match.group("body")))
    assert set(labels) == plugin_types
    return labels


def _schema_plugin_type_labels(schema: dict[str, Any]) -> dict[str, str]:
    return {
        entry["const"]: entry["description"]
        for entry in schema["properties"]["plugin_type"]["oneOf"]
    }


def _minimal_manifest() -> dict[str, Any]:
    return {
        "manifest_version": 2,
        "version": "1.0.0",
        "name": "测试插件",
        "description": "用于测试 manifest schema 的插件",
        "author": {
            "name": "MaiBot Team",
            "url": "https://github.com/Mai-with-u",
        },
        "license": "MIT",
        "urls": {
            "repository": "https://github.com/Mai-with-u/test-plugin",
        },
        "host_application": {
            "min_version": "1.0.0",
            "max_version": "1.99.99",
        },
        "sdk": {
            "min_version": "2.6.0",
            "max_version": "2.99.99",
        },
        "dependencies": [],
        "capabilities": [],
        "i18n": {
            "default_locale": "zh-CN",
            "supported_locales": ["zh-CN"],
        },
        "id": "maibot-team.test-plugin",
    }


def test_manifest_schema_has_no_duplicate_keys() -> None:
    assert _load_schema_without_duplicate_keys()


def test_manifest_schema_fields_match_runtime_models() -> None:
    schema = _load_schema_without_duplicate_keys()

    assert set(schema["properties"]) == _manifest_model_fields("PluginManifest") | {"$schema"}
    assert set(schema["properties"]["author"]["properties"]) == _manifest_model_fields("ManifestAuthor")
    assert set(schema["properties"]["urls"]["properties"]) == _manifest_model_fields("ManifestUrls")
    assert set(schema["definitions"]["version_range"]["properties"]) == _manifest_model_fields("ManifestVersionRange")
    assert set(schema["properties"]["i18n"]["properties"]) == _manifest_model_fields("ManifestI18n")
    assert set(schema["properties"]["llm_providers"]["items"]["properties"]) == _manifest_model_fields(
        "LLMProviderManifestDeclaration"
    )
    assert set(schema["properties"]["display"]["properties"]) == _manifest_model_fields("ManifestDisplay")
    assert set(schema["properties"]["display"]["properties"]["icon"]["properties"]) == _manifest_model_fields(
        "ManifestDisplayIcon"
    )

    dependency_variants = schema["properties"]["dependencies"]["items"]["oneOf"]
    assert set(dependency_variants[0]["properties"]) == _manifest_model_fields("PluginDependencyDefinition")
    assert set(dependency_variants[1]["properties"]) == _manifest_model_fields("PythonPackageDependencyDefinition")


def test_manifest_schema_capabilities_match_host_registry() -> None:
    schema = _load_schema_without_duplicate_keys()
    schema_capabilities = {entry["const"] for entry in schema["properties"]["capabilities"]["items"]["oneOf"]}
    gateway_capability_aliases = {"gateway.route_message", "gateway.update_state"}

    assert schema_capabilities == _registered_capabilities() | gateway_capability_aliases


def test_manifest_schema_plugin_type_matches_frontend_union() -> None:
    schema = _load_schema_without_duplicate_keys()

    assert set(_schema_plugin_type_labels(schema)) == _frontend_plugin_types()
    assert _schema_plugin_type_labels(schema) == _frontend_plugin_type_labels()


def test_manifest_validator_allows_schema_metadata_but_rejects_unknown_fields() -> None:
    validator = ManifestValidator(validate_python_package_dependencies=False)
    manifest = _minimal_manifest()
    manifest["$schema"] = "../_manifest.schema.json"

    assert validator.parse_manifest(manifest, source="maibot-team.test-plugin") is not None

    manifest["unknown"] = True

    assert validator.parse_manifest(manifest, source="maibot-team.test-plugin") is None
    assert any("unknown" in error for error in validator.errors)


def test_manifest_schema_accepts_runtime_manifest_fields() -> None:
    schema = _load_schema()
    manifest = _minimal_manifest()
    manifest.update(
        {
            "$schema": "../_manifest.schema.json",
            "plugin_type": "automation",
            "display": {
                "icon": {
                    "type": "lucide",
                    "value": "package",
                    "fallback": "box",
                    "background": "#336699",
                }
            },
            "changelog": "CHANGELOG.md",
            "llm_providers": [
                {
                    "client_type": "example.provider",
                    "name": "Example Provider",
                    "description": "示例 LLM Provider",
                    "version": "1.0.0",
                }
            ],
            "capabilities": [
                "chat.open_session",
                "component.enable",
                "component.disable",
                "component.get_plugin_config_schema",
                "component.load_plugin",
                "component.reload_plugin",
                "component.unload_plugin",
                "component.update_plugin_config",
                "api.replace_dynamic",
                "emoji.register",
                "emoji.delete",
                "statistics.local.models",
                "statistics.local.model_trend",
                "statistics.local.token_trend",
                "statistics.local.token_distribution",
                "statistics.local.message_trend",
                "statistics.local.tool_trend",
                "statistics.local.online_time_trend",
            ],
            "dependencies": [
                {
                    "type": "plugin",
                    "id": "maibot-team.base-plugin",
                    "version_spec": ">=1.0.0",
                },
                {
                    "type": "python_package",
                    "name": "httpx",
                    "version_spec": ">=0.28.0",
                },
            ],
        }
    )

    jsonschema.Draft7Validator(schema).validate(manifest)


def test_manifest_schema_matches_display_icon_runtime_constraints() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    manifest = _minimal_manifest()

    manifest["display"] = {"icon": {"type": "lucide", "value": "bad icon"}}
    assert list(validator.iter_errors(manifest))

    manifest["display"] = {"icon": {"type": "local", "value": "../icon.png"}}
    assert list(validator.iter_errors(manifest))

    manifest["display"] = {"icon": {"type": "local", "value": "assets/icon.txt"}}
    assert list(validator.iter_errors(manifest))

    manifest["display"] = {"icon": {"type": "local", "value": "assets/icon.PNG"}}
    assert list(validator.iter_errors(manifest)) == []


def test_manifest_schema_accepts_changelog_suffix_like_runtime_validator() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    manifest = _minimal_manifest()

    manifest["changelog"] = "CHANGELOG.MD"
    assert list(validator.iter_errors(manifest)) == []

    manifest["changelog"] = "https://example.com/CHANGELOG.md"
    assert list(validator.iter_errors(manifest)) == []

    manifest["changelog"] = "../CHANGELOG.md"
    assert list(validator.iter_errors(manifest))


def test_manifest_schema_rejects_runtime_blank_strings() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)

    manifest = _minimal_manifest()
    manifest["name"] = "   "
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["author"]["name"] = "   "
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["urls"]["homepage"] = "https://   "
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["i18n"]["supported_locales"] = ["   "]
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["llm_providers"] = [{"client_type": "   "}]
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["display"] = {"icon": {"type": "emoji", "value": "   "}}
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["changelog"] = "   "
    assert list(validator.iter_errors(manifest))


def test_manifest_schema_rejects_runtime_invalid_local_paths() -> None:
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    manifest = _minimal_manifest()

    manifest["display"] = {"icon": {"type": "local", "value": "assets/icon\u0000.png"}}
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["changelog"] = "docs/change\u0000log.md"
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["display"] = {"icon": {"type": "local", "value": r"..\icon.png"}}
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["display"] = {"icon": {"type": "local", "value": r"C:\icons\icon.png"}}
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["changelog"] = r"..\CHANGELOG.md"
    assert list(validator.iter_errors(manifest))

    manifest = _minimal_manifest()
    manifest["changelog"] = r"C:\plugins\CHANGELOG.md"
    assert list(validator.iter_errors(manifest))


def test_manifest_schema_rejects_undeclared_fields() -> None:
    schema = _load_schema()
    manifest = _minimal_manifest()
    manifest["unknown"] = True

    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(manifest))

    assert any("unknown" in error.message for error in errors)


def test_repository_manifests_match_schema_and_runtime_validator() -> None:
    schema = _load_schema()
    schema_validator = jsonschema.Draft7Validator(schema)
    runtime_validator = ManifestValidator(validate_python_package_dependencies=False)
    manifest_paths = sorted(
        list((PROJECT_ROOT / "plugins").glob("*/_manifest.json"))
        + list((PROJECT_ROOT / "src" / "plugins" / "built_in").glob("*/_manifest.json"))
    )

    assert manifest_paths
    for manifest_path in manifest_paths:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema_errors = sorted(schema_validator.iter_errors(manifest), key=lambda error: list(error.path))
        assert schema_errors == [], f"{manifest_path}: {[error.message for error in schema_errors]}"
        assert runtime_validator.parse_manifest(manifest, source=str(manifest_path)) is not None, (
            f"{manifest_path}: {runtime_validator.errors}"
        )
