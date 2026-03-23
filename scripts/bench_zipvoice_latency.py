from __future__ import annotations

import argparse
import json
import sys
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mori_runtime.tts_backends import DEFAULT_TTS_BACKEND, build_tts_engine


HARD_CUTS = {
    "。",
    ".",
    "?",
    "？",
    "!",
    "！",
    "…",
    "\n",
    "\r",
    "\t",
}

SOFT_CUTS = {
    ",",
    "，",
    "、",
    ":",
    "：",
    ";",
    "；",
}


@dataclass(frozen=True)
class Scenario:
    name: str
    text: str
    lang_hint: str = ""
    segmented: bool = False


def _utf8_chars(text: str) -> list[str]:
    return [ch for ch in str(text or "")]


def chunk_text(text: str, *, boost: int = 2, min_chars: int = 12, max_chars: int = 48) -> list[str]:
    chars = _utf8_chars(text)
    buf: list[str] = []
    emitted = 0
    out: list[str] = []

    def cut_once() -> str | None:
        nonlocal emitted, buf
        if not buf:
            return None

        threshold = min_chars
        if emitted < boost:
            threshold = max(4, min_chars // 3)

        last_hard_end: int | None = None
        last_hard_chars = 0
        last_soft_end: int | None = None
        last_soft_chars = 0
        max_end: int | None = None

        for idx, ch in enumerate(buf, start=1):
            if idx == max_chars:
                max_end = idx
            if ch in HARD_CUTS:
                last_hard_end = idx
                last_hard_chars = idx
            elif ch in SOFT_CUTS:
                last_soft_end = idx
                last_soft_chars = idx
            if idx >= max_chars:
                break

        cut_end: int | None = None
        if last_hard_end is not None and last_hard_chars >= threshold:
            cut_end = last_hard_end
        elif emitted < boost and last_soft_end is not None and last_soft_chars >= threshold:
            cut_end = last_soft_end
        elif max_end is not None:
            cut_end = max_end

        if cut_end is None:
            return None

        seg = "".join(buf[:cut_end]).strip()
        buf = buf[cut_end:]
        if not seg:
            return None
        emitted += 1
        return seg

    for ch in chars:
        buf.append(ch)
        while True:
            seg = cut_once()
            if seg is None:
                break
            out.append(seg)

    tail = "".join(buf).strip()
    if tail:
        emitted += 1
        out.append(tail)
    return out


def resolve_path(base_dir: Path, raw: str) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    return str((base_dir / path).resolve())


def load_tts_config(config_path: Path) -> dict[str, Any]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Config root must be an object: {config_path}")
    tts = raw.get("tts")
    if not isinstance(tts, dict):
        raise ValueError(f"Missing tts section in config: {config_path}")

    base_dir = config_path.parent
    out = dict(tts)
    for key in [
        "model",
        "prompt_wav",
        "zipvoice_python_bin",
        "zipvoice_repo",
        "zipvoice_model_dir",
        "zipvoice_zh_prompt_wav",
        "zipvoice_ja_prompt_wav",
        "zipvoice_prompt_manifest",
    ]:
        value = out.get(key)
        if isinstance(value, str) and value.strip():
            if key == "zipvoice_prompt_manifest":
                items = [item.strip() for item in value.split(",") if item.strip()]
                out[key] = ",".join(resolve_path(base_dir, item) for item in items)
            else:
                out[key] = resolve_path(base_dir, value)
    return out


def wav_duration_seconds(path: Path) -> float:
    try:
        import soundfile as sf  # type: ignore[import-not-found]

        info = sf.info(str(path))
        return float(getattr(info, "duration", 0.0) or 0.0)
    except Exception:
        try:
            import torchaudio

            info = torchaudio.info(str(path))
            num_frames = int(getattr(info, "num_frames", 0) or 0)
            sample_rate = int(getattr(info, "sample_rate", 0) or 0)
            if sample_rate > 0:
                return float(num_frames) / float(sample_rate)
        except Exception:
            pass
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
        if rate <= 0:
            return 0.0
        return float(frames) / float(rate)


def build_engine_from_config(tts: dict[str, Any], *, num_steps: int | None = None, num_thread: int | None = None):
    backend = str(tts.get("backend") or DEFAULT_TTS_BACKEND)
    engine = build_tts_engine(
        backend=backend,
        tts_model=str(tts.get("model") or ""),
        tts_device=str(tts.get("device") or "auto"),
        tts_threads=int(tts.get("threads") or 4),
        tts_zipvoice_python_bin=str(tts.get("zipvoice_python_bin") or ""),
        tts_zipvoice_repo=str(tts.get("zipvoice_repo") or ""),
        tts_zipvoice_model_type=str(tts.get("zipvoice_model_type") or "zipvoice"),
        tts_zipvoice_model_dir=str(tts.get("zipvoice_model_dir") or ""),
        tts_zipvoice_checkpoint_name=str(tts.get("zipvoice_checkpoint_name") or ""),
        tts_zipvoice_zh_tokenizer=str(tts.get("zipvoice_zh_tokenizer") or ""),
        tts_zipvoice_zh_lang=str(tts.get("zipvoice_zh_lang") or ""),
        tts_zipvoice_ja_tokenizer=str(tts.get("zipvoice_ja_tokenizer") or ""),
        tts_zipvoice_ja_lang=str(tts.get("zipvoice_ja_lang") or ""),
        tts_zipvoice_zh_prompt_text=str(tts.get("zipvoice_zh_prompt_text") or ""),
        tts_zipvoice_zh_prompt_wav=str(tts.get("zipvoice_zh_prompt_wav") or ""),
        tts_zipvoice_ja_prompt_text=str(tts.get("zipvoice_ja_prompt_text") or ""),
        tts_zipvoice_ja_prompt_wav=str(tts.get("zipvoice_ja_prompt_wav") or ""),
        tts_zipvoice_remove_long_sil=bool(tts.get("zipvoice_remove_long_sil", False)),
        tts_zipvoice_num_thread=int(num_thread if num_thread is not None else tts.get("zipvoice_num_thread") or 1),
        tts_zipvoice_lang_detector=str(tts.get("zipvoice_lang_detector") or "auto"),
        tts_zipvoice_lang_min_conf=float(tts.get("zipvoice_lang_min_conf") or 0.6),
        tts_zipvoice_prompt_manifest=str(tts.get("zipvoice_prompt_manifest") or ""),
        tts_zipvoice_prompt_policy=str(tts.get("zipvoice_prompt_policy") or "intent_hash"),
        tts_zipvoice_quality_profile=str(tts.get("zipvoice_quality_profile") or "balanced"),
        tts_zipvoice_num_steps=int(num_steps if num_steps is not None else tts.get("zipvoice_num_steps") or 0),
        tts_zipvoice_guidance_scale=float(tts.get("zipvoice_guidance_scale") or 0.0),
        tts_zipvoice_t_shift=float(tts.get("zipvoice_t_shift") or 0.0),
        tts_zipvoice_speed=float(tts.get("zipvoice_speed") or 0.0),
        tts_zipvoice_return_smooth=bool(tts.get("zipvoice_return_smooth", False)),
        tts_zipvoice_vocoder_profile=str(tts.get("zipvoice_vocoder_profile") or "base_24k"),
        tts_zipvoice_vocoder_model=str(tts.get("zipvoice_vocoder_model") or ""),
    )
    if backend.strip().lower() == "zipvoice" and hasattr(engine, "default_synthesis_options"):
        defaults = engine.default_synthesis_options()
        return engine, {
            "num_steps": int(defaults.get("num_steps") or 4),
            "guidance_scale": float(defaults.get("guidance_scale") or 1.0),
            "t_shift": float(defaults.get("t_shift") or 0.5),
            "speed": float(defaults.get("speed") or 1.0),
            "return_smooth": bool(defaults.get("return_smooth", False)),
        }
    return engine, {
        "num_steps": int(num_steps if num_steps is not None else tts.get("num_steps") or 4),
        "guidance_scale": float(tts.get("guidance_scale") or 3.0),
        "t_shift": float(tts.get("t_shift") or 0.5),
        "speed": float(tts.get("speed") or 1.0),
        "return_smooth": bool(tts.get("return_smooth", False)),
    }


def run_single_case(
    *,
    engine: Any,
    params: dict[str, float | int],
    out_dir: Path,
    scenario_name: str,
    part_idx: int,
    text: str,
    lang_hint: str,
    prompt_key: str,
) -> dict[str, Any]:
    out_wav = (out_dir / f"{scenario_name}_part{part_idx:02d}.wav").resolve()
    start = time.perf_counter()
    result = engine.synthesize_to_wav(
        text=text,
        out_wav_path=out_wav,
        prompt_wav_path="",
        prompt_duration=0.0,
        prompt_rms=0.0,
        num_steps=int(params["num_steps"]),
        guidance_scale=float(params["guidance_scale"]),
        t_shift=float(params["t_shift"]),
        speed=float(params["speed"]),
        return_smooth=bool(params.get("return_smooth", False)),
        lang_hint=lang_hint,
        prompt_key=prompt_key,
    )
    elapsed = time.perf_counter() - start
    wav_path = out_wav
    meta = result if isinstance(result, dict) else {"wav_path": str(result)}
    maybe_wav = Path(str(meta.get("wav_path") or out_wav)).expanduser().resolve()
    if maybe_wav.is_file():
        wav_path = maybe_wav
    audio_seconds = wav_duration_seconds(wav_path)
    return {
        "segment_idx": part_idx,
        "text": text,
        "chars": len(text),
        "latency_s": elapsed,
        "audio_s": audio_seconds,
        "rtf": (elapsed / audio_seconds) if audio_seconds > 0 else None,
        "wav_path": str(wav_path),
        "tts_route": str(meta.get("tts_route") or ""),
        "tts_lang": str(meta.get("tts_lang") or ""),
        "tts_tokenizer": str(meta.get("tts_tokenizer") or ""),
        "prompt_id": str(meta.get("prompt_id") or ""),
    }


def summarize_segment_series(parts: list[dict[str, Any]]) -> dict[str, Any]:
    if not parts:
        return {
            "segments": 0,
            "first_audio_ready_s": 0.0,
            "total_synth_s": 0.0,
            "total_audio_s": 0.0,
            "steady_state_backlog_s": 0.0,
        }

    total_synth = sum(float(part["latency_s"]) for part in parts)
    total_audio = sum(float(part["audio_s"]) for part in parts)
    first_audio_ready = float(parts[0]["latency_s"])

    synth_after_first = 0.0
    audio_before_current = float(parts[0]["audio_s"])
    max_backlog = 0.0
    for idx, part in enumerate(parts):
        if idx == 0:
            continue
        synth_after_first += float(part["latency_s"])
        max_backlog = max(max_backlog, synth_after_first - audio_before_current)
        audio_before_current += float(part["audio_s"])

    return {
        "segments": len(parts),
        "first_audio_ready_s": first_audio_ready,
        "total_synth_s": total_synth,
        "total_audio_s": total_audio,
        "steady_state_backlog_s": max(0.0, max_backlog),
        "overall_rtf": (total_synth / total_audio) if total_audio > 0 else None,
    }


def default_scenarios() -> list[Scenario]:
    return [
        Scenario(
            name="zh_ack",
            text="嗯，大家晚上好，今天状态不错，我们先轻松聊聊天。",
            lang_hint="zh",
        ),
        Scenario(
            name="zh_answer",
            text="这个问题我先给一个直接结论，再补充原因，这样你听起来不会太累。",
            lang_hint="zh",
        ),
        Scenario(
            name="ja_answer",
            text="こんばんは、今日は少しだけ雑談してから、配信の流れを整えていくよ。",
            lang_hint="ja",
        ),
        Scenario(
            name="mixed_switch",
            text="这段先用中文说明一下，最后补一句 arigatou，看看混合文本时路由会不会卡住。",
            lang_hint="",
        ),
        Scenario(
            name="vtuber_cn_long",
            text=(
                "今天这个模型的响应其实比我预想的稳定。"
                "如果我们把回答切成比较自然的短句，它就能更早开始说话，观感会好很多。"
                "真正需要担心的反而不是单句速度，而是连续三四段的时候会不会越积越慢。"
            ),
            lang_hint="zh",
            segmented=True,
        ),
        Scenario(
            name="vtuber_ja_long",
            text=(
                "今日は配信向けの遅延をちゃんと見たい。"
                "短い文ならすぐ話し始められるのか、長めの説明でもテンポが崩れないのか。"
                "そこが大丈夫なら、実戦でもかなり使いやすくなる。"
            ),
            lang_hint="ja",
            segmented=True,
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark ZipVoice latency for AI VTuber-like scenarios.")
    parser.add_argument("--config", default="mori.config.json")
    parser.add_argument("--out-dir", default="tts_out/bench_zipvoice")
    parser.add_argument("--num-steps", type=int, default=None, help="Override tts.num_steps from config.")
    parser.add_argument("--zipvoice-num-thread", type=int, default=None, help="Override tts.zipvoice_num_thread from config.")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tts = load_tts_config(config_path)
    if str(tts.get("backend") or DEFAULT_TTS_BACKEND).strip().lower() != "zipvoice":
        raise ValueError("This benchmark currently expects tts.backend=zipvoice.")

    engine_start = time.perf_counter()
    engine, params = build_engine_from_config(
        tts,
        num_steps=args.num_steps,
        num_thread=args.zipvoice_num_thread,
    )
    engine_init_s = time.perf_counter() - engine_start

    try:
        results: list[dict[str, Any]] = []
        for scenario in default_scenarios():
            segments = chunk_text(scenario.text) if scenario.segmented else [scenario.text]
            parts = [
                run_single_case(
                    engine=engine,
                    params=params,
                    out_dir=out_dir,
                    scenario_name=scenario.name,
                    part_idx=idx,
                    text=segment,
                    lang_hint=scenario.lang_hint,
                    prompt_key=f"{scenario.name}:bench",
                )
                for idx, segment in enumerate(segments, start=1)
            ]
            results.append(
                {
                    "scenario": scenario.name,
                    "lang_hint": scenario.lang_hint,
                    "segmented": scenario.segmented,
                    "segments_text": segments,
                    "parts": parts,
                    "summary": summarize_segment_series(parts),
                }
            )

        report = {
            "config_path": str(config_path),
            "out_dir": str(out_dir),
            "engine_init_s": engine_init_s,
            "params": params,
            "prompt_manifest": str(tts.get("zipvoice_prompt_manifest") or ""),
            "zipvoice_num_thread": int(args.zipvoice_num_thread if args.zipvoice_num_thread is not None else tts.get("zipvoice_num_thread") or 1),
            "prompt_cache_stats": getattr(engine, "_prompt_cache_stats", {}),
            "results": results,
        }
    finally:
        stop_worker = getattr(engine, "_stop_worker", None)
        if callable(stop_worker):
            stop_worker()

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
