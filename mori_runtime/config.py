from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_NAME = "mori.config.json"

_COMMON_KEYS = {
    "workdir",
    "llama_bin_dir",
    "chat_model",
    "embed_model",
    "ctx_size",
    "n_predict",
    "temp",
    "top_p",
    "system",
}

_CLI_KEYS = {
    "audio_dir",
    "interrupt_policy",
}

_VTUBER_KEYS = {
    "live_dir",
    "subtitle_file",
    "event_log",
    "bilibili_room_id",
    "bilibili_room_url",
    "bilibili_interval",
    "bilibili_catchup",
    "bilibili_exit_when_offline",
    "bilibili_live_check_interval",
    "interrupt_policy",
}

_TTS_KEY_MAP = {
    "enabled": "tts",
    "model": "tts_model",
    "device": "tts_device",
    "threads": "tts_threads",
    "prompt_wav": "tts_prompt_wav",
    "prompt_duration": "tts_prompt_duration",
    "prompt_rms": "tts_prompt_rms",
    "num_steps": "tts_num_steps",
    "guidance_scale": "tts_guidance_scale",
    "t_shift": "tts_t_shift",
    "speed": "tts_speed",
    "return_smooth": "tts_return_smooth",
}

_LOVE2D_KEYS = {
    "tts",
    "love_bin",
    "puppet",
    "mapping",
    "mouse_look",
    "skip_prepare",
}

_INOCHI_KEYS = {
    "inochi_root",
    "inochi_bin",
    "inochi_x11",
}

_PATH_DEFAULT_KEYS = {
    "workdir",
    "llama_bin_dir",
    "chat_model",
    "embed_model",
    "tts_prompt_wav",
    "puppet",
    "mapping",
    "inochi_root",
}


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick_keys(source: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in allowed:
        if key in source:
            out[key] = source[key]
    return out


def _resolve_config_relative_paths(defaults: dict[str, Any], *, config_dir: Path) -> dict[str, Any]:
    resolved = dict(defaults)
    for key in _PATH_DEFAULT_KEYS:
        value = resolved.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        path = Path(text).expanduser()
        if path.is_absolute():
            resolved[key] = str(path.resolve())
            continue
        resolved[key] = str((config_dir / path).resolve())
    return resolved


def find_config_path(
    explicit: str | Path | None = None,
    *,
    cwd: Path | None = None,
    repo_root: Path | None = None,
) -> Path | None:
    cwd_path = (cwd or Path.cwd()).resolve()

    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = (cwd_path / candidate).resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"Config file not found: {candidate}")
        return candidate.resolve()

    candidates: list[Path] = [cwd_path / DEFAULT_CONFIG_NAME]
    if repo_root is not None:
        repo_candidate = (repo_root / DEFAULT_CONFIG_NAME).resolve()
        if repo_candidate not in candidates:
            candidates.append(repo_candidate)

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def load_config(
    explicit: str | Path | None = None,
    *,
    cwd: Path | None = None,
    repo_root: Path | None = None,
) -> tuple[Path | None, dict[str, Any]]:
    path = find_config_path(explicit, cwd=cwd, repo_root=repo_root)
    if path is None:
        return None, {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config root must be a JSON object: {path}")
    return path, raw


def build_entry_defaults(config: dict[str, Any], *, mode: str) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    defaults.update(_pick_keys(_as_dict(config.get("common")), _COMMON_KEYS))

    tts = _as_dict(config.get("tts"))
    for src_key, dest_key in _TTS_KEY_MAP.items():
        if src_key in tts:
            defaults[dest_key] = tts[src_key]

    if mode == "cli":
        defaults.update(_pick_keys(_as_dict(config.get("cli")), _CLI_KEYS))
    elif mode == "vtuber":
        defaults.update(_pick_keys(_as_dict(config.get("vtuber")), _VTUBER_KEYS))
    else:
        raise ValueError(f"Unknown entry mode: {mode}")

    return defaults


def build_launcher_defaults(config: dict[str, Any], *, launcher: str) -> dict[str, Any]:
    defaults = build_entry_defaults(config, mode="vtuber")
    if "bilibili_exit_when_offline" in defaults:
        defaults["exit_when_offline"] = defaults.pop("bilibili_exit_when_offline")
    defaults.pop("bilibili_live_check_interval", None)

    if launcher == "love2d":
        defaults.update(_pick_keys(_as_dict(config.get("love2d")), _LOVE2D_KEYS))
    elif launcher == "inochi":
        defaults.update(_pick_keys(_as_dict(config.get("inochi")), _INOCHI_KEYS))
    else:
        raise ValueError(f"Unknown launcher profile: {launcher}")

    return defaults


def apply_config_defaults(
    parser: argparse.ArgumentParser,
    *,
    argv: list[str] | None,
    repo_root: Path,
    profile: str,
) -> tuple[Path | None, dict[str, Any]]:
    args_for_probe = list(argv) if argv is not None else None
    probed, _unknown = parser.parse_known_args(args_for_probe)
    config_path, config = load_config(getattr(probed, "config", ""), cwd=Path.cwd(), repo_root=repo_root)

    defaults: dict[str, Any] = {}
    if profile in {"cli", "vtuber"}:
        defaults.update(build_entry_defaults(config, mode=profile))
    elif profile in {"love2d", "inochi"}:
        defaults.update(build_launcher_defaults(config, launcher=profile))
    else:
        raise ValueError(f"Unknown parser profile: {profile}")

    if config_path is not None:
        defaults = _resolve_config_relative_paths(defaults, config_dir=config_path.parent)
        prompt_wav_key = "tts_prompt_wav"
        prompt_wav = str(defaults.get(prompt_wav_key, "") or "").strip()
        if not prompt_wav:
            repo_prompt_wav = (config_path.parent / "mori_tts" / "prompt.wav").resolve()
            if repo_prompt_wav.is_file():
                defaults[prompt_wav_key] = str(repo_prompt_wav)
        defaults.setdefault("config", str(config_path))
    if defaults:
        parser.set_defaults(**defaults)
    return config_path, config
