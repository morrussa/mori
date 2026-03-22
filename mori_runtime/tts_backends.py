from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Literal

from mori_tts.lux_tts import LuxTTS

TTSBackendName = Literal["lux", "zipvoice"]

DEFAULT_TTS_BACKEND: TTSBackendName = "zipvoice"

DEFAULT_ZIPVOICE_PYTHON_BIN = "/home/morusa/dataset/train/zipvoice-env/bin/python"
DEFAULT_ZIPVOICE_REPO = "/home/morusa/dataset/train/ZipVoice"
DEFAULT_ZIPVOICE_MODEL_DIR = "/home/morusa/AI/mori/model/zipvoice_zhja_iter3000_avg2"
DEFAULT_ZIPVOICE_CHECKPOINT_NAME = "iter-3000-avg-2.pt"
DEFAULT_ZIPVOICE_ZH_PROMPT_TEXT = "你觉得你可以跟我玩儿一场"
DEFAULT_ZIPVOICE_JA_PROMPT_TEXT = "基本的に全く辛くなくてあの"
DEFAULT_ZIPVOICE_ZH_TOKENIZER = "emilia"
DEFAULT_ZIPVOICE_ZH_LANG = "en-us"
DEFAULT_ZIPVOICE_JA_TOKENIZER = "openjtalk"
DEFAULT_ZIPVOICE_JA_LANG = "ja"
DEFAULT_ZIPVOICE_REMOVE_LONG_SIL = False
DEFAULT_ZIPVOICE_NUM_THREAD = 1


class ZipVoiceTTS:
    def __init__(
        self,
        *,
        python_bin: str | Path = DEFAULT_ZIPVOICE_PYTHON_BIN,
        zipvoice_repo: str | Path = DEFAULT_ZIPVOICE_REPO,
        model_dir: str | Path = DEFAULT_ZIPVOICE_MODEL_DIR,
        checkpoint_name: str = DEFAULT_ZIPVOICE_CHECKPOINT_NAME,
        zh_prompt_text: str = DEFAULT_ZIPVOICE_ZH_PROMPT_TEXT,
        ja_prompt_text: str = DEFAULT_ZIPVOICE_JA_PROMPT_TEXT,
        zh_tokenizer: str = DEFAULT_ZIPVOICE_ZH_TOKENIZER,
        zh_lang: str = DEFAULT_ZIPVOICE_ZH_LANG,
        ja_tokenizer: str = DEFAULT_ZIPVOICE_JA_TOKENIZER,
        ja_lang: str = DEFAULT_ZIPVOICE_JA_LANG,
        remove_long_sil: bool = DEFAULT_ZIPVOICE_REMOVE_LONG_SIL,
        num_thread: int = DEFAULT_ZIPVOICE_NUM_THREAD,
    ) -> None:
        self._python_bin = Path(python_bin).expanduser().resolve()
        self._zipvoice_repo = Path(zipvoice_repo).expanduser().resolve()
        self._model_dir = Path(model_dir).expanduser().resolve()
        self._checkpoint_name = str(checkpoint_name).strip() or DEFAULT_ZIPVOICE_CHECKPOINT_NAME
        self._zh_prompt_text = str(zh_prompt_text or "").strip()
        self._ja_prompt_text = str(ja_prompt_text or "").strip()
        self._zh_tokenizer = str(zh_tokenizer or DEFAULT_ZIPVOICE_ZH_TOKENIZER).strip()
        self._zh_lang = str(zh_lang or DEFAULT_ZIPVOICE_ZH_LANG).strip()
        self._ja_tokenizer = str(ja_tokenizer or DEFAULT_ZIPVOICE_JA_TOKENIZER).strip()
        self._ja_lang = str(ja_lang or DEFAULT_ZIPVOICE_JA_LANG).strip()
        self._remove_long_sil = bool(remove_long_sil)
        self._num_thread = max(1, int(num_thread))

        if not self._python_bin.is_file():
            raise FileNotFoundError(f"ZipVoice python not found: {self._python_bin}")
        if not self._zipvoice_repo.is_dir():
            raise NotADirectoryError(f"ZipVoice repo not found: {self._zipvoice_repo}")
        if not self._model_dir.is_dir():
            raise NotADirectoryError(f"ZipVoice model_dir not found: {self._model_dir}")
        for fn in [self._checkpoint_name, "model.json", "tokens.txt"]:
            f = self._model_dir / fn
            if not f.is_file():
                raise FileNotFoundError(f"ZipVoice model file missing: {f}")
        if not self._zh_prompt_text:
            raise ValueError("ZipVoice zh_prompt_text is empty")
        if not self._ja_prompt_text:
            raise ValueError("ZipVoice ja_prompt_text is empty")

    @property
    def backend_name(self) -> str:
        return "zipvoice"

    @staticmethod
    def _detect_lang(text: str) -> str:
        s = str(text or "")
        has_ja = any(
            ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff")
            for ch in s
        )
        if has_ja:
            return "ja"
        has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in s)
        if has_cjk:
            return "zh"
        return "zh"

    def _route(self, *, text: str, lang_hint: str = "") -> tuple[str, str, str]:
        lang = str(lang_hint or "").strip().lower()
        if lang.startswith("ja") or lang == "jp":
            return self._ja_tokenizer, self._ja_lang, self._ja_prompt_text
        if lang.startswith("zh") or lang.startswith("cmn"):
            return self._zh_tokenizer, self._zh_lang, self._zh_prompt_text
        detected = self._detect_lang(text)
        if detected == "ja":
            return self._ja_tokenizer, self._ja_lang, self._ja_prompt_text
        return self._zh_tokenizer, self._zh_lang, self._zh_prompt_text

    def synthesize_to_wav(
        self,
        *,
        text: str,
        out_wav_path: str | Path,
        prompt_wav_path: str | Path,
        prompt_duration: float = 0.0,
        prompt_rms: float = 0.0,
        num_steps: int = 8,
        guidance_scale: float = 1.0,
        t_shift: float = 0.5,
        speed: float = 1.0,
        return_smooth: bool = False,
        lang_hint: str = "",
    ) -> Path:
        del prompt_duration, prompt_rms, return_smooth
        out_wav = Path(out_wav_path).expanduser().resolve()
        out_wav.parent.mkdir(parents=True, exist_ok=True)

        prompt_wav = Path(prompt_wav_path).expanduser().resolve()
        if not prompt_wav.is_file():
            raise FileNotFoundError(f"ZipVoice prompt wav not found: {prompt_wav}")

        tokenizer, lang, prompt_text = self._route(text=str(text or ""), lang_hint=lang_hint)

        cmd = [
            str(self._python_bin),
            "-m",
            "zipvoice.bin.infer_zipvoice",
            "--model-name",
            "zipvoice",
            "--model-dir",
            str(self._model_dir),
            "--checkpoint-name",
            self._checkpoint_name,
            "--tokenizer",
            tokenizer,
            "--lang",
            lang,
            "--prompt-wav",
            str(prompt_wav),
            "--prompt-text",
            prompt_text,
            "--text",
            str(text or ""),
            "--res-wav-path",
            str(out_wav),
            "--num-step",
            str(max(1, int(num_steps))),
            "--guidance-scale",
            str(float(guidance_scale)),
            "--t-shift",
            str(float(t_shift)),
            "--speed",
            str(float(speed)),
            "--target-rms",
            "0.1",
            "--max-duration",
            "40.0",
            "--remove-long-sil",
            "True" if self._remove_long_sil else "False",
            "--num-thread",
            str(self._num_thread),
        ]
        env = {**os.environ, "PYTHONPATH": str(self._zipvoice_repo)}
        proc = subprocess.run(
            cmd,
            cwd=str(self._zipvoice_repo),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if proc.returncode != 0:
            tail = "\n".join(proc.stdout.splitlines()[-80:])
            raise RuntimeError(
                "ZipVoice inference failed.\n"
                f"tokenizer={tokenizer} lang={lang}\n"
                f"prompt_wav={prompt_wav}\n"
                f"out={out_wav}\n"
                f"{tail}"
            )
        if not out_wav.is_file():
            raise FileNotFoundError(f"ZipVoice did not generate wav file: {out_wav}")
        return out_wav


def build_tts_engine(
    *,
    backend: str,
    tts_model: str | Path,
    tts_device: str,
    tts_threads: int,
    tts_zipvoice_python_bin: str | Path,
    tts_zipvoice_repo: str | Path,
    tts_zipvoice_model_dir: str | Path,
    tts_zipvoice_checkpoint_name: str,
    tts_zipvoice_zh_prompt_text: str,
    tts_zipvoice_ja_prompt_text: str,
    tts_zipvoice_zh_tokenizer: str,
    tts_zipvoice_zh_lang: str,
    tts_zipvoice_ja_tokenizer: str,
    tts_zipvoice_ja_lang: str,
    tts_zipvoice_remove_long_sil: bool,
    tts_zipvoice_num_thread: int,
) -> Any:
    b = str(backend or DEFAULT_TTS_BACKEND).strip().lower()
    if b == "zipvoice":
        return ZipVoiceTTS(
            python_bin=tts_zipvoice_python_bin,
            zipvoice_repo=tts_zipvoice_repo,
            model_dir=tts_zipvoice_model_dir,
            checkpoint_name=tts_zipvoice_checkpoint_name,
            zh_prompt_text=tts_zipvoice_zh_prompt_text,
            ja_prompt_text=tts_zipvoice_ja_prompt_text,
            zh_tokenizer=tts_zipvoice_zh_tokenizer,
            zh_lang=tts_zipvoice_zh_lang,
            ja_tokenizer=tts_zipvoice_ja_tokenizer,
            ja_lang=tts_zipvoice_ja_lang,
            remove_long_sil=tts_zipvoice_remove_long_sil,
            num_thread=tts_zipvoice_num_thread,
        )

    if b != "lux":
        raise ValueError(f"Unsupported --tts-backend: {backend!r}. Expected lux or zipvoice.")
    return LuxTTS(model=tts_model, device=tts_device, threads=tts_threads)

