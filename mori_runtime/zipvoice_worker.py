from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any


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


def _build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Persistent ZipVoice inference worker.")
    p.add_argument("--zipvoice-repo", required=True)
    p.add_argument("--model-dir", required=True)
    p.add_argument("--checkpoint-name", required=True)
    p.add_argument("--num-thread", type=int, default=1)
    return p.parse_args()


def main() -> None:
    args = _build_args()
    repo = Path(args.zipvoice_repo).expanduser().resolve()
    model_dir = Path(args.model_dir).expanduser().resolve()
    ckpt = str(args.checkpoint_name).strip()

    if not repo.is_dir():
        raise NotADirectoryError(f"ZipVoice repo not found: {repo}")
    if not model_dir.is_dir():
        raise NotADirectoryError(f"ZipVoice model_dir not found: {model_dir}")
    for fn in [ckpt, "model.json", "tokens.txt"]:
        f = model_dir / fn
        if not f.is_file():
            raise FileNotFoundError(f"ZipVoice model file missing: {f}")

    os.environ["PYTHONPATH"] = f"{repo}:{os.environ.get('PYTHONPATH', '')}".rstrip(":")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    import torch
    import safetensors

    from zipvoice.bin.infer_zipvoice import VocosFbank, generate_sentence, get_vocoder
    from zipvoice.models.zipvoice import ZipVoice
    from zipvoice.tokenizer.tokenizer import build_tokenizer
    from zipvoice.utils.checkpoint import load_checkpoint

    logging.basicConfig(
        level=logging.ERROR,
        format="%(asctime)s %(levelname)s [zipvoice_worker] %(message)s",
        force=True,
    )

    torch.set_num_threads(max(1, int(args.num_thread)))
    torch.set_num_interop_threads(1)

    with (model_dir / "model.json").open("r", encoding="utf-8") as f:
        model_conf = json.load(f)
    token_file = model_dir / "tokens.txt"

    tokenizer_boot = build_tokenizer(
        tokenizer_name="emilia",
        token_file=str(token_file),
        lang="en-us",
    )
    model = ZipVoice(
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

    vocoder = get_vocoder(None).to(device)
    vocoder.eval()

    if model_conf["feature"]["type"] != "vocos":
        raise NotImplementedError(f"Unsupported feature type: {model_conf['feature']['type']}")
    feature_extractor = VocosFbank()
    sampling_rate = int(model_conf["feature"]["sampling_rate"])

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

    _emit({"ready": True, "device": str(device), "sampling_rate": sampling_rate})

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

        if str(req.get("cmd", "")).strip().lower() == "shutdown":
            _emit({"id": req_id, "ok": True, "shutdown": True})
            return

        try:
            out_wav = Path(str(req["out_wav"])).expanduser().resolve()
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            tokenizer = str(req["tokenizer"])
            lang = str(req["lang"])
            prompt_wav = str(Path(str(req["prompt_wav"])).expanduser().resolve())
            prompt_text = str(req["prompt_text"])
            text = str(req["text"])

            tk = get_cached_tokenizer(tokenizer, lang)
            generate_sentence(
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
                sampling_rate=sampling_rate,
                max_duration=float(req.get("max_duration", 40.0)),
                remove_long_sil=_bool(req.get("remove_long_sil", False), default=False),
            )
            _emit({"id": req_id, "ok": True, "out_wav": str(out_wav)})
        except Exception as e:
            _emit({"id": req_id, "ok": False, "error": repr(e)})


if __name__ == "__main__":
    main()
