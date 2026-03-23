from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any


LUX_VOCODER_DEFAULT_MODEL = "YatharthS/LuxTTS"
LUX_VOCODER_OUTPUT_SAMPLE_RATE = 48_000
_MODEL_CONFIG_FILENAMES = ("model.json", "config.json")
_PROMPT_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}


def _bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _emit(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _resolve_model_config_path(model_dir: Path) -> Path | None:
    for filename in _MODEL_CONFIG_FILENAMES:
        candidate = (model_dir / filename).resolve()
        if candidate.is_file():
            return candidate
    return None


def _prompt_cache_key(
    *,
    prompt_text: str,
    prompt_wav: str,
    tokenizer: Any,
    sampling_rate: int,
    target_rms: float,
    feat_scale: float,
    device: Any,
) -> tuple[Any, ...]:
    return (
        str(Path(str(prompt_wav)).expanduser().resolve()),
        str(prompt_text),
        id(tokenizer),
        int(sampling_rate),
        float(target_rms),
        float(feat_scale),
        str(device),
    )


def _build_prompt_context(
    *,
    prompt_text: str,
    prompt_wav: str,
    tokenizer: Any,
    feature_extractor: Any,
    sampling_rate: int,
    target_rms: float,
    feat_scale: float,
    device: Any,
    load_prompt_wav_fn: Any,
    remove_silence_fn: Any,
    rms_norm_fn: Any,
    add_punctuation_fn: Any,
) -> dict[str, Any]:
    prompt_wav_arr = load_prompt_wav_fn(prompt_wav, sampling_rate=sampling_rate)
    # Match upstream ZipVoice behavior so prewarm and runtime synthesis share the same prompt features.
    prompt_wav_arr = remove_silence_fn(
        prompt_wav_arr,
        sampling_rate,
        only_edge=False,
        trail_sil=200,
    )
    prompt_wav_arr, prompt_rms = rms_norm_fn(prompt_wav_arr, target_rms)
    prompt_duration = prompt_wav_arr.shape[-1] / sampling_rate

    prompt_features = feature_extractor.extract(
        prompt_wav_arr,
        sampling_rate=sampling_rate,
    ).to(device)
    prompt_features = prompt_features.unsqueeze(0) * feat_scale

    prompt_text_norm = add_punctuation_fn(prompt_text)
    prompt_tokens_str = tokenizer.texts_to_tokens([prompt_text_norm])[0]
    if len(prompt_tokens_str) == 0:
        raise ValueError("Prompt text tokenization returned empty tokens.")
    prompt_tokens = tokenizer.tokens_to_token_ids([prompt_tokens_str])
    if len(prompt_tokens[0]) == 0:
        raise ValueError("Prompt text token-id mapping returned empty ids.")

    return {
        "prompt_rms": float(prompt_rms),
        "prompt_duration": float(prompt_duration),
        "prompt_features": prompt_features,
        "prompt_tokens_str": prompt_tokens_str,
        "prompt_tokens": prompt_tokens,
    }


def _get_cached_prompt_context(
    *,
    prompt_text: str,
    prompt_wav: str,
    tokenizer: Any,
    feature_extractor: Any,
    sampling_rate: int,
    target_rms: float,
    feat_scale: float,
    device: Any,
    load_prompt_wav_fn: Any,
    remove_silence_fn: Any,
    rms_norm_fn: Any,
    add_punctuation_fn: Any,
) -> dict[str, Any]:
    key = _prompt_cache_key(
        prompt_text=prompt_text,
        prompt_wav=prompt_wav,
        tokenizer=tokenizer,
        sampling_rate=sampling_rate,
        target_rms=target_rms,
        feat_scale=feat_scale,
        device=device,
    )
    cached = _PROMPT_CACHE.get(key)
    if cached is not None:
        return cached

    ctx = _build_prompt_context(
        prompt_text=prompt_text,
        prompt_wav=prompt_wav,
        tokenizer=tokenizer,
        feature_extractor=feature_extractor,
        sampling_rate=sampling_rate,
        target_rms=target_rms,
        feat_scale=feat_scale,
        device=device,
        load_prompt_wav_fn=load_prompt_wav_fn,
        remove_silence_fn=remove_silence_fn,
        rms_norm_fn=rms_norm_fn,
        add_punctuation_fn=add_punctuation_fn,
    )
    _PROMPT_CACHE[key] = ctx
    return ctx


def _build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Persistent ZipVoice inference worker.")
    p.add_argument("--zipvoice-repo", required=True)
    p.add_argument("--model-type", default="zipvoice", choices=["zipvoice", "zipvoice_distill"])
    p.add_argument("--model-dir", required=True)
    p.add_argument("--checkpoint-name", required=True)
    p.add_argument("--num-thread", type=int, default=1)
    p.add_argument("--vocoder-profile", default="base_24k", choices=["base_24k", "lux_48k"])
    p.add_argument("--vocoder-model", default=LUX_VOCODER_DEFAULT_MODEL)
    return p.parse_args()


def _resolve_lux_vocoder_dir(model_ref: str) -> Path:
    text = str(model_ref or "").strip() or LUX_VOCODER_DEFAULT_MODEL
    path = Path(text).expanduser()
    if path.exists():
        root = path.resolve()
    else:
        from huggingface_hub import snapshot_download

        root = Path(snapshot_download(text)).resolve()

    candidates = [root / "vocoder", root]
    for cand in candidates:
        if (cand / "config.yaml").is_file() and (cand / "vocos.bin").is_file():
            return cand
    raise FileNotFoundError(
        f"Lux vocoder files not found under {root}. Expected config.yaml and vocos.bin."
    )


def _load_vocoder(*, profile: str, model_ref: str, device: Any):
    import torch

    from zipvoice.bin.infer_zipvoice import get_vocoder

    if str(profile or "").strip().lower() != "lux_48k":
        vocoder = get_vocoder(None).to(device)
        vocoder.eval()
        return vocoder, 24_000

    try:
        from linacodec.vocoder.vocos import Vocos as LinaVocos  # type: ignore[import-not-found]
        from torch.nn.utils import parametrize
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "ZipVoice Lux 48k vocoder requires `linacodec` in the zipvoice env. "
            "Install it with: "
            "`/home/morusa/dataset/train/zipvoice-env/bin/pip install "
            "git+https://github.com/ysharma3501/LinaCodec.git`."
        ) from e

    vocoder_dir = _resolve_lux_vocoder_dir(model_ref)
    vocoder = LinaVocos.from_hparams(str(vocoder_dir / "config.yaml")).to(device)
    try:
        for layer in getattr(vocoder.upsampler, "upsample_layers", []):
            parametrize.remove_parametrizations(layer, "weight")
    except Exception:
        pass

    state_dict = torch.load(str(vocoder_dir / "vocos.bin"), map_location=device)
    vocoder.load_state_dict(state_dict)
    vocoder.freq_range = 12000
    vocoder.return_48k = True
    vocoder.eval()
    return vocoder, LUX_VOCODER_OUTPUT_SAMPLE_RATE


def _generate_sentence_with_vocoder(
    *,
    save_path: str,
    prompt_text: str,
    prompt_wav: str,
    text: str,
    model: Any,
    vocoder: Any,
    tokenizer: Any,
    feature_extractor: Any,
    device: Any,
    num_step: int,
    guidance_scale: float,
    speed: float,
    t_shift: float,
    target_rms: float,
    feat_scale: float,
    feature_sampling_rate: int,
    output_sampling_rate: int,
    max_duration: float,
    remove_long_sil: bool,
    return_smooth: bool,
    add_punctuation: Any,
    batchify_tokens: Any,
    chunk_tokens_punctuation: Any,
    cross_fade_concat: Any,
    remove_silence: Any,
    _get_cached_prompt_context: Any,
):
    import torch
    import torchaudio

    ctx = _get_cached_prompt_context(
        prompt_text=prompt_text,
        prompt_wav=prompt_wav,
        tokenizer=tokenizer,
        feature_extractor=feature_extractor,
        sampling_rate=feature_sampling_rate,
        target_rms=target_rms,
        feat_scale=feat_scale,
        device=device,
    )
    prompt_rms = float(ctx["prompt_rms"])
    prompt_duration = float(ctx["prompt_duration"])
    prompt_features = ctx["prompt_features"]
    prompt_tokens_str = ctx["prompt_tokens_str"]
    prompt_tokens = ctx["prompt_tokens"]

    text = add_punctuation(text)
    tokens_str = tokenizer.texts_to_tokens([text])[0]
    if len(tokens_str) == 0:
        raise ValueError("Text tokenization returned empty tokens.")

    token_duration = prompt_duration / (len(prompt_tokens_str) * speed)
    if token_duration <= 0:
        raise ValueError("Invalid token_duration <= 0. Check prompt/text tokenization.")
    max_tokens = int((25 - prompt_duration) / token_duration)
    if max_tokens <= 0:
        max_tokens = 1
    chunked_tokens_str = chunk_tokens_punctuation(tokens_str, max_tokens=max_tokens)
    chunked_tokens = tokenizer.tokens_to_token_ids(chunked_tokens_str)
    tokens_batches, chunked_index = batchify_tokens(
        chunked_tokens, max_duration, prompt_duration, token_duration
    )

    chunked_features = []
    start_t = dt.datetime.now()
    with torch.inference_mode():
        for batch_tokens in tokens_batches:
            batch_prompt_tokens = prompt_tokens * len(batch_tokens)
            batch_prompt_features = prompt_features.repeat(len(batch_tokens), 1, 1)
            batch_prompt_features_lens = torch.full(
                (len(batch_tokens),), prompt_features.size(1), device=device
            )
            pred_features, pred_features_lens, _pred_prompt_features, _pred_prompt_features_lens = model.sample(
                tokens=batch_tokens,
                prompt_tokens=batch_prompt_tokens,
                prompt_features=batch_prompt_features,
                prompt_features_lens=batch_prompt_features_lens,
                speed=speed,
                t_shift=t_shift,
                duration="predict",
                num_step=num_step,
                guidance_scale=guidance_scale,
            )
            pred_features = pred_features.permute(0, 2, 1) / feat_scale
            chunked_features.append((pred_features, pred_features_lens))

        chunked_wavs = []
        start_vocoder_t = dt.datetime.now()

        if hasattr(vocoder, "return_48k"):
            vocoder.return_48k = not bool(return_smooth)

        for pred_features, pred_features_lens in chunked_features:
            batch_wav = []
            for idx in range(pred_features.size(0)):
                wav = (
                    vocoder.decode(pred_features[idx][None, :, : pred_features_lens[idx]])
                    .squeeze(1)
                    .clamp(-1, 1)
                )
                if prompt_rms < target_rms:
                    wav = wav * prompt_rms / target_rms
                batch_wav.append(wav)
            chunked_wavs.extend(batch_wav)

    t = (dt.datetime.now() - start_t).total_seconds()
    indexed_chunked_wavs = [(index, wav) for index, wav in zip(chunked_index, chunked_wavs)]
    sequential_indexed_chunked_wavs = sorted(indexed_chunked_wavs, key=lambda x: x[0])
    sequential_chunked_wavs = [
        sequential_indexed_chunked_wavs[idx][1]
        for idx in range(len(sequential_indexed_chunked_wavs))
    ]
    final_wav = cross_fade_concat(
        sequential_chunked_wavs,
        fade_duration=0.1,
        sample_rate=output_sampling_rate,
    )
    final_wav = remove_silence(
        final_wav,
        output_sampling_rate,
        only_edge=(not remove_long_sil),
        trail_sil=0,
    )

    t_no_vocoder = (start_vocoder_t - start_t).total_seconds()
    t_vocoder = (dt.datetime.now() - start_vocoder_t).total_seconds()
    wav_seconds = final_wav.shape[-1] / output_sampling_rate
    torchaudio.save(save_path, final_wav.cpu(), sample_rate=output_sampling_rate)
    return {
        "t": t,
        "t_no_vocoder": t_no_vocoder,
        "t_vocoder": t_vocoder,
        "wav_seconds": wav_seconds,
        "rtf": (t / wav_seconds) if wav_seconds > 0 else None,
        "rtf_no_vocoder": (t_no_vocoder / wav_seconds) if wav_seconds > 0 else None,
        "rtf_vocoder": (t_vocoder / wav_seconds) if wav_seconds > 0 else None,
    }


def main() -> None:
    args = _build_args()
    repo = Path(args.zipvoice_repo).expanduser().resolve()
    model_type = str(args.model_type or "zipvoice").strip().lower()
    model_dir = Path(args.model_dir).expanduser().resolve()
    ckpt = str(args.checkpoint_name).strip()
    vocoder_profile = str(args.vocoder_profile or "base_24k").strip().lower()
    vocoder_model = str(args.vocoder_model or LUX_VOCODER_DEFAULT_MODEL).strip()

    if not repo.is_dir():
        raise NotADirectoryError(f"ZipVoice repo not found: {repo}")
    if not model_dir.is_dir():
        raise NotADirectoryError(f"ZipVoice model_dir not found: {model_dir}")
    for fn in [ckpt, "tokens.txt"]:
        f = model_dir / fn
        if not f.is_file():
            raise FileNotFoundError(f"ZipVoice model file missing: {f}")
    model_config_path = _resolve_model_config_path(model_dir)
    if model_config_path is None:
        expected = ", ".join(_MODEL_CONFIG_FILENAMES)
        raise FileNotFoundError(
            f"ZipVoice model config missing under {model_dir}. Expected one of: {expected}"
        )

    os.environ["PYTHONPATH"] = f"{repo}:{os.environ.get('PYTHONPATH', '')}".rstrip(":")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    import safetensors
    import torch

    from zipvoice.bin.infer_zipvoice import get_vocoder
    from zipvoice.models.zipvoice import ZipVoice
    from zipvoice.models.zipvoice_distill import ZipVoiceDistill
    from zipvoice.tokenizer import tokenizer as tokenizer_mod
    from zipvoice.utils.checkpoint import load_checkpoint
    from zipvoice.utils.feature import VocosFbank
    from zipvoice.utils.infer import (
        add_punctuation,
        batchify_tokens,
        chunk_tokens_punctuation,
        cross_fade_concat,
        load_prompt_wav,
        remove_silence,
        rms_norm,
    )

    build_tokenizer = getattr(tokenizer_mod, "build_tokenizer", None)
    if build_tokenizer is None:
        def build_tokenizer(tokenizer_name: str, token_file: str, lang: str) -> Any:
            name = str(tokenizer_name or "").strip().lower()
            if name == "emilia":
                return tokenizer_mod.EmiliaTokenizer(token_file=token_file)
            if name == "espeak":
                return tokenizer_mod.EspeakTokenizer(token_file=token_file, lang=lang)
            if name == "libritts":
                return tokenizer_mod.LibriTTSTokenizer(token_file=token_file)
            if name == "simple":
                return tokenizer_mod.SimpleTokenizer(token_file=token_file)
            if name == "dialog":
                return tokenizer_mod.DialogTokenizer(token_file=token_file)
            if name == "openjtalk":
                openjtalk_cls = getattr(tokenizer_mod, "OpenJTalkTokenizer", None)
                if openjtalk_cls is None:
                    raise ValueError(
                        "Tokenizer 'openjtalk' is not available in the current zipvoice repo. "
                        "This matches the Phase3 todo: distill/openjtalk support is not ready yet."
                    )
                return openjtalk_cls(token_file=token_file)
            raise ValueError(f"Unsupported tokenizer: {tokenizer_name!r}")

    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s [zipvoice_worker] %(message)s",
        force=True,
    )

    torch.set_num_threads(max(1, int(args.num_thread)))
    torch.set_num_interop_threads(1)

    with model_config_path.open("r", encoding="utf-8") as f:
        model_conf = json.load(f)
    token_file = model_dir / "tokens.txt"

    tokenizer_boot = build_tokenizer(
        tokenizer_name="emilia",
        token_file=str(token_file),
        lang="en-us",
    )
    model_cls = ZipVoice if model_type == "zipvoice" else ZipVoiceDistill
    model = model_cls(
        **model_conf["model"],
        vocab_size=tokenizer_boot.vocab_size,
        pad_id=tokenizer_boot.pad_id,
    )

    model_ckpt = model_dir / ckpt
    if str(model_ckpt).endswith(".safetensors"):
        safetensors.torch.load_model(model, model_ckpt)
    elif str(model_ckpt).endswith(".pt"):
        load_checkpoint(filename=model_ckpt, model=model, strict=True)
    else:
        raise NotImplementedError(f"Unsupported model checkpoint format: {model_ckpt}")

    if torch.cuda.is_available():
        device = torch.device("cuda", 0)
    elif torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")

    model = model.to(device)
    model.eval()

    vocoder, output_sample_rate = _load_vocoder(
        profile=vocoder_profile,
        model_ref=vocoder_model,
        device=device,
    )

    if model_conf["feature"]["type"] != "vocos":
        raise NotImplementedError(f"Unsupported feature type: {model_conf['feature']['type']}")
    feature_extractor = VocosFbank()
    feature_sampling_rate = int(model_conf["feature"]["sampling_rate"])

    tokenizer_cache: dict[tuple[str, str], Any] = {}

    def get_cached_tokenizer(tokenizer: str, lang: str) -> Any:
        key = (str(tokenizer).strip(), str(lang).strip())
        tk = tokenizer_cache.get(key)
        if tk is not None:
            return tk
        tk = build_tokenizer(
            tokenizer_name=key[0],
            token_file=str(token_file),
            lang=key[1],
        )
        tokenizer_cache[key] = tk
        return tk

    def get_cached_prompt_context(
        *,
        prompt_text: str,
        prompt_wav: str,
        tokenizer: Any,
        feature_extractor: Any,
        sampling_rate: int,
        target_rms: float,
        feat_scale: float,
        device: Any,
    ) -> dict[str, Any]:
        return _get_cached_prompt_context(
            prompt_text=prompt_text,
            prompt_wav=prompt_wav,
            tokenizer=tokenizer,
            feature_extractor=feature_extractor,
            sampling_rate=sampling_rate,
            target_rms=target_rms,
            feat_scale=feat_scale,
            device=device,
            load_prompt_wav_fn=load_prompt_wav,
            remove_silence_fn=remove_silence,
            rms_norm_fn=rms_norm,
            add_punctuation_fn=add_punctuation,
        )

    _emit(
        {
            "ready": True,
            "device": str(device),
            "model_type": model_type,
            "sampling_rate": feature_sampling_rate,
            "output_sample_rate": int(output_sample_rate),
            "vocoder_profile": vocoder_profile,
        }
    )

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req_id = ""
        try:
            req = json.loads(line)
            req_id = str(req.get("id", ""))
        except Exception as e:
            _emit({"id": req_id, "ok": False, "error": f"invalid json: {e}"})
            continue

        cmd = str(req.get("cmd", "")).strip().lower()
        if cmd == "shutdown":
            _emit({"id": req_id, "ok": True, "shutdown": True})
            return

        try:
            tokenizer = str(req["tokenizer"])
            lang = str(req["lang"])
            prompt_wav = str(Path(str(req["prompt_wav"])).expanduser().resolve())
            prompt_text = str(req["prompt_text"])
            tk = get_cached_tokenizer(tokenizer, lang)
            if cmd == "prewarm":
                ctx = _get_cached_prompt_context(
                    prompt_text=prompt_text,
                    prompt_wav=prompt_wav,
                    tokenizer=tk,
                    feature_extractor=feature_extractor,
                    sampling_rate=feature_sampling_rate,
                    target_rms=0.1,
                    feat_scale=0.1,
                    device=device,
                    load_prompt_wav_fn=load_prompt_wav,
                    remove_silence_fn=remove_silence,
                    rms_norm_fn=rms_norm,
                    add_punctuation_fn=add_punctuation,
                )
                prompt_features = ctx["prompt_features"]
                prompt_tokens = ctx["prompt_tokens"]
                _emit(
                    {
                        "id": req_id,
                        "ok": True,
                        "prewarmed": True,
                        "prompt_duration": float(ctx["prompt_duration"]),
                        "prompt_frames": int(prompt_features.shape[1]),
                        "prompt_feature_dim": int(prompt_features.shape[2]),
                        "prompt_feature_bytes": int(prompt_features.numel() * prompt_features.element_size()),
                        "prompt_tokens": len(prompt_tokens[0]) if prompt_tokens else 0,
                    }
                )
                continue

            out_wav = Path(str(req["out_wav"])).expanduser().resolve()
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            text = str(req["text"])
            metrics = _generate_sentence_with_vocoder(
                save_path=str(out_wav),
                prompt_text=prompt_text,
                prompt_wav=prompt_wav,
                text=text,
                model=model,
                vocoder=vocoder,
                tokenizer=tk,
                feature_extractor=feature_extractor,
                device=device,
                num_step=max(1, int(req.get("num_step", 8))),
                guidance_scale=float(req.get("guidance_scale", 1.0)),
                speed=float(req.get("speed", 1.0)),
                t_shift=float(req.get("t_shift", 0.5)),
                target_rms=float(req.get("target_rms", 0.1)),
                feat_scale=float(req.get("feat_scale", 0.1)),
                feature_sampling_rate=feature_sampling_rate,
                output_sampling_rate=int(output_sample_rate),
                max_duration=float(req.get("max_duration", 40.0)),
                remove_long_sil=_bool(req.get("remove_long_sil", False), default=False),
                return_smooth=_bool(req.get("return_smooth", False), default=False),
                add_punctuation=add_punctuation,
                batchify_tokens=batchify_tokens,
                chunk_tokens_punctuation=chunk_tokens_punctuation,
                cross_fade_concat=cross_fade_concat,
                remove_silence=remove_silence,
                _get_cached_prompt_context=get_cached_prompt_context,
            )
            _emit(
                {
                    "id": req_id,
                    "ok": True,
                    "out_wav": str(out_wav),
                    "sample_rate": int(output_sample_rate),
                    "vocoder_profile": vocoder_profile,
                    **metrics,
                }
            )
        except Exception as e:
            _emit({"id": req_id, "ok": False, "error": repr(e)})


if __name__ == "__main__":
    main()
