#!/usr/bin/env python3
"""Static trust-boundary control for the Voicebox provider-only candidate."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ACTIVE_ROOTS = (
    "backend",
    "app/src",
    "web/src",
    "tauri/src-tauri/src",
    "tauri/src",
    "scripts",
    "docs",
)
SUFFIXES = {".md", ".mdx", ".py", ".ts", ".tsx", ".rs", ".sh", ".toml", ".txt"}
ABSENT = (
    "backend/backends/mlx_backend.py",
    "backend/backends/pytorch_backend.py",
    "backend/platform_detect.py",
    "backend/requirements-mlx.txt",
    "backend/utils/hf_progress.py",
    "app/src/components/ServerSettings/ModelProgress.tsx",
    "app/src/lib/hooks/useModelDownloadToast.tsx",
    "mlx-test/demo.py",
    "mlx-test/test_tts.py",
    "app/src/components/ServerSettings/UpdateStatus.tsx",
    "app/src/hooks/useAutoUpdater.ts",
    "app/src/hooks/useAutoUpdater.tsx",
    "tauri/src/platform/updater.ts",
    "web/src/platform/updater.ts",
    "docs/AUTOUPDATER.md",
    "docs/AUTOUPDATER_QUICKSTART.md",
    "docs/developer/autoupdater.mdx",
    ".github/workflows/release.yml",
    "backend/example_usage.py",
    "backend/studio.py",
    "backend/utils/progress.py",
    "backend/utils/tasks.py",
    "docs/plans/DOCKER_DEPLOYMENT.md",
    "docs/plans/EXTERNAL_PROVIDERS.md",
    "docs/plans/MLX_AUDIO.md",
    "docs/plans/OPENAI_SUPPORT.md",
    "docs/plans/TTS_PROVIDER_ARCHITECTURE.md",
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise SystemExit(f"KILIX_BOUNDARY_FAIL {code}")


def main() -> int:
    for relative in ABSENT:
        require(not (ROOT / relative).exists(), f"FORBIDDEN_PATH:{relative}")

    files = sorted(
        path
        for root in ACTIVE_ROOTS
        for path in (ROOT / root).rglob("*")
        if path.is_file() and path.suffix in SUFFIXES and "node_modules" not in path.parts
    )
    files.extend(
        (
            ROOT / "Makefile",
            ROOT / "pyproject.toml",
            ROOT / "tauri/package.json",
            ROOT / "docs/package.json",
        )
    )
    files = sorted(set(files))
    import_pattern = re.compile(
        r"^\s*(?:from|import)\s+(?:torch|transformers|mlx|mlx_audio|qwen_tts|huggingface_hub)\b",
        re.MULTILINE,
    )
    mutable_install = re.compile(r"(?:pip\s+install|git\+https://)")
    remote_bind = re.compile(r"0\.0\.0\.0|--host(?![ \t]+127\.0\.0\.1)")
    bad_imports = []
    bad_installs = []
    bad_binds = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        if import_pattern.search(text):
            bad_imports.append(path)
        if mutable_install.search(text):
            bad_installs.append(path)
        if remote_bind.search(text):
            bad_binds.append(path)
    require(not bad_imports, "EMBEDDED_RUNTIME_IMPORT")
    require(not bad_installs, "MUTABLE_RUNTIME_INSTALL")
    require(not bad_binds, "REMOTE_BIND_SURFACE")

    generated_api_roots = {
        ROOT / "app/src/lib/api/core",
        ROOT / "app/src/lib/api/models",
        ROOT / "app/src/lib/api/schemas",
        ROOT / "app/src/lib/api/services",
    }
    active_typescript = [
        path
        for path in files
        if path.suffix in {".ts", ".tsx"}
        and path != ROOT / "app/src/lib/api/index.ts"
        and not any(root in path.parents for root in generated_api_roots)
    ]
    generated_client_uses = [
        path
        for path in active_typescript
        if re.search(r"\b(?:DefaultService|OpenAPI)\b", path.read_text(encoding="utf-8"))
    ]
    require(not generated_client_uses, "UNAUTHENTICATED_GENERATED_CLIENT")

    static_console = re.compile(
        r"\s*console\.(?:log|error|warn|debug)\(['\"][A-Z][A-Z0-9_]*['\"]\);\s*"
    )
    unsafe_console_lines = []
    for path in active_typescript:
        if path == ROOT / "app/src/lib/utils/debug.ts":
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "console." in line and not static_console.fullmatch(line):
                unsafe_console_lines.append((path, line_number))
    require(not unsafe_console_lines, "UNSANITIZED_FRONTEND_LOG")

    main_source = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    model_source = (ROOT / "backend/models.py").read_text(encoding="utf-8")
    native_source = (ROOT / "tauri/src-tauri/src/main.rs").read_text(encoding="utf-8")
    client_source = (ROOT / "app/src/lib/api/client.ts").read_text(encoding="utf-8")
    debug_source = (ROOT / "app/src/lib/utils/debug.ts").read_text(encoding="utf-8")
    capability = json.loads(
        (ROOT / "tauri/src-tauri/capabilities/default.json").read_text(encoding="utf-8")
    )
    tauri_config = json.loads(
        (ROOT / "tauri/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    )
    capability_permissions = set(capability["permissions"])
    forbidden_permissions = {
        "core:webview:allow-internal-toggle-devtools",
        "dialog:allow-open",
        "fs:read-all",
        "fs:write-all",
        "process:default",
        "shell:allow-execute",
        "shell:allow-open",
        "shell:allow-spawn",
        "updater:default",
    }
    forbidden_process_discovery = (
        "lsof", "netstat", "taskkill", "tasklist", "pkill", "killall",
        "Command::new(", "keep_running_on_close", "keepServerRunning",
    )
    require("detail=str(" not in main_source, "RAW_EXCEPTION_RESPONSE")
    require("detail=f\"" not in main_source or "str(e)" not in main_source,
            "RAW_EXCEPTION_RESPONSE")
    require(model_source.count('@field_serializer("audio_path")') == 4,
            "PRIVATE_AUDIO_PATH_RESPONSE")
    require(model_source.count('@field_serializer("avatar_path")') == 1,
            "PRIVATE_AVATAR_PATH_RESPONSE")
    active_text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    require(
        not any(token in active_text for token in forbidden_process_discovery),
        "UNOWNED_PROCESS_CONTROL",
    )
    require(not (capability_permissions & forbidden_permissions),
            "BROAD_WEBVIEW_PERMISSION")
    require("remote" not in capability, "REMOTE_WEBVIEW_CAPABILITY")
    require(capability_permissions == {
        "core:default", "core:window:default", "core:window:allow-start-dragging",
        "core:webview:default", "dialog:allow-save", "fs:allow-write-file",
    }, "CAPABILITY_LEAST_PRIVILEGE")
    require(tauri_config["app"]["security"]["csp"] is not None, "MISSING_CSP")
    require(tauri_config["app"]["withGlobalTauri"] is False, "GLOBAL_TAURI_API")
    require(tauri_config["bundle"]["createUpdaterArtifacts"] is False,
            "UPDATER_ARTIFACT")
    require("plugins" not in tauri_config, "TAURI_REMOTE_PLUGIN_CONFIG")

    required = (
        (ROOT / "uv.lock").is_file(),
        (ROOT / "pyproject.toml").is_file(),
        "127.0.0.1" in (ROOT / "backend/server.py").read_text(encoding="utf-8"),
        "SO_PEERCRED" in (ROOT / "backend/kilix_provider.py").read_text(encoding="utf-8"),
        "SOCK_SEQPACKET" in (ROOT / "backend/kilix_provider.py").read_text(encoding="utf-8"),
        "consent_asserted" in (ROOT / "backend/models.py").read_text(encoding="utf-8"),
        "consentAsserted" in (ROOT / "app/src/lib/hooks/useGenerationForm.ts").read_text(
            encoding="utf-8"
        ),
        "include_samples: bool = False" in (ROOT / "backend/main.py").read_text(
            encoding="utf-8"
        ),
        "delete-preview" in (ROOT / "backend/main.py").read_text(encoding="utf-8"),
        "sidecar-provenance" in (ROOT / "backend/build_binary.py").read_text(encoding="utf-8"),
        "detail=str(" not in main_source,
        model_source.count("Expose a loopback API resource") == 5,
        "Mutex<Option<OwnedServer>>" in native_source,
        "stop_owned_server" in native_source,
        "sidecar(\"voicebox-server\")" in native_source,
        capability_permissions == {
            "core:default", "core:window:default", "core:window:allow-start-dragging",
            "core:webview:default", "dialog:allow-save", "fs:allow-write-file",
        },
        "tauri-plugin-updater" not in (ROOT / "tauri/src-tauri/Cargo.toml").read_text(
            encoding="utf-8"
        ),
        "ALLOWED_PRODUCT_ORIGINS" in main_source,
        "ORIGIN_FORBIDDEN" in main_source,
        '@app.post("/shutdown")' not in main_source,
        '@app.post("/history/import")' not in main_source,
        '@app.post("/models/download")' not in main_source,
        '@app.post("/models/load")' not in main_source,
        'generation_provenance' in (ROOT / "backend/private_store.py").read_text(
            encoding="utf-8"
        ),
        'preview_owned_delete' in (ROOT / "backend/private_store.py").read_text(
            encoding="utf-8"
        ),
        'archive_records' in (ROOT / "backend/private_store.py").read_text(
            encoding="utf-8"
        ),
        '@app.get("/stories/{story_id}/delete-preview")' in main_source,
        'O_NOFOLLOW' in main_source,
        'X-Kilix-Voicebox-Session' in client_source,
        '?session=${token}' in client_source,
        'hmac.compare_digest' in main_source,
        'SESSION_UNAUTHORIZED' in main_source,
        'OsRng' in native_source,
        '.env("KILIX_VOICEBOX_SESSION_TOKEN"' in native_source,
        'access_log=False' in (ROOT / "backend/server.py").read_text(encoding="utf-8"),
        "[redacted]" in debug_source,
        "safeArguments(args)" in debug_source,
        not generated_client_uses,
        not unsafe_console_lines,
    )
    require(all(required), "REQUIRED_BOUNDARY")
    print(
        "KILIX_BOUNDARY_PASS "
        f"(absent paths {len(ABSENT)}/{len(ABSENT)}; "
        f"active files scanned {len(files)}/{len(files)}; "
        f"embedded runtime imports 0/{len(files)}; mutable installs 0/{len(files)}; "
        f"remote bind surfaces 0/{len(files)}; forbidden webview permissions "
        f"0/{len(forbidden_permissions)}; forbidden process controls "
        f"0/{len(forbidden_process_discovery)}; required controls "
        f"{sum(required)}/{len(required)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
