from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import random
import subprocess
import threading
from pathlib import Path
from typing import Any, Literal

from mori_tts.lux_tts import LuxTTS

TTSBackendName = Literal["lux", "zipvoice"]

DEFAULT_TTS_BACKEND: TTSBackendName = "zipvoice"

DEFAULT_ZIPVOICE_PYTHON_BIN = "/home/morusa/dataset/train/zipvoice-env/bin/python"
DEFAULT_ZIPVOICE_REPO = "/home/morusa/dataset/train/ZipVoice"
DEFAULT_ZIPVOICE_MODEL_DIR = "/home/morusa/AI/mori/model/zipvoice_zhja_iter3000_avg2"
DEFAULT_ZIPVOICE_CHECKPOINT_NAME = "iter-3000-avg-2.pt"
DEFAULT_ZIPVOICE_ZH_TOKENIZER = "emilia"
DEFAULT_ZIPVOICE_ZH_LANG = "en-us"
DEFAULT_ZIPVOICE_JA_TOKENIZER = "openjtalk"
DEFAULT_ZIPVOICE_JA_LANG = "ja"
DEFAULT_ZIPVOICE_REMOVE_LONG_SIL = False
DEFAULT_ZIPVOICE_NUM_THREAD = 1
DEFAULT_ZIPVOICE_LANG_DETECTOR = "auto"
DEFAULT_ZIPVOICE_LANG_MIN_CONF = 0.60
DEFAULT_ZIPVOICE_PROMPT_MANIFEST = ""
DEFAULT_ZIPVOICE_ZH_PROMPT_MANIFEST = ""
DEFAULT_ZIPVOICE_JA_PROMPT_MANIFEST = ""
DEFAULT_ZIPVOICE_PROMPT_POLICY = "intent_hash"

_ZIPVOICE_LANG_DETECTOR_CHOICES = {"auto", "heuristic", "lingua"}
_ZIPVOICE_PROMPT_POLICY_CHOICES = {"intent_hash", "round_robin", "random"}


def _split_path_list(value: str) -> list[str]:
    s = str(value or "").strip()
    if not s:
        return []
    return [x.strip() for x in s.split(",") if x.strip()]


def _load_prompt_pool_from_manifests(manifest_paths: str | Path) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for raw in _split_path_list(str(manifest_paths or "")):
        manifest = Path(raw).expanduser().resolve()
        if not manifest.is_file():
            raise FileNotFoundError(f"ZipVoice prompt manifest not found: {manifest}")
        for line in manifest.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split("\t")
            if len(parts) < 3:
                continue
            prompt_text = str(parts[1] or "").strip()
            prompt_path = Path(str(parts[2] or "").strip()).expanduser()
            if not prompt_path.is_absolute():
                prompt_path = (manifest.parent / prompt_path).resolve()
            else:
                prompt_path = prompt_path.resolve()
            if not prompt_text or not prompt_path.is_file():
                continue
            k = str(prompt_path)
            if k in seen:
                continue
            seen.add(k)
            items.append((prompt_path, prompt_text))
    return items


def _normalize_lang_tag(value: str) -> str:
    s = str(value or "").strip().lower().replace("_", "-")
    if not s:
        return ""
    if s == "jp" or s.startswith("ja"):
        return "ja"
    if s.startswith("zh") or s.startswith("cmn"):
        return "zh"
    if s.startswith("en"):
        return "en"
    return ""


def _detect_with_script_rules(text: str) -> str:
    s = str(text or "")
    if not s.strip():
        return "zh"
    kana_count = 0
    han_count = 0
    latin_count = 0
    for ch in s:
        if ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff"):
            kana_count += 1
        elif "\u4e00" <= ch <= "\u9fff":
            han_count += 1
        elif ("a" <= ch <= "z") or ("A" <= ch <= "Z"):
            latin_count += 1

    if kana_count > 0:
        return "ja"
    if han_count > 0:
        return "zh"
    if latin_count > 0:
        return "en"
    return "zh"


_HIRA_ROMAJI_1 = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
}

_HIRA_ROMAJI_2 = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo",
    "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo",
    "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo",
    "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "ふゅ": "fyu",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
}


def _kata_to_hira(ch: str) -> str:
    if len(ch) != 1:
        return ch
    code = ord(ch)
    if 0x30A1 <= code <= 0x30F6:
        return chr(code - 0x60)
    return ch


def _kana_to_romaji(text: str) -> str:
    s = "".join(_kata_to_hira(ch) for ch in str(text or ""))
    out: list[str] = []
    i = 0
    geminate = False

    while i < len(s):
        ch = s[i]
        if ch == "っ":
            geminate = True
            i += 1
            continue
        if ch == "ー":
            if out:
                prev = out[-1]
                for v in ("a", "i", "u", "e", "o"):
                    if prev.endswith(v):
                        out.append(v)
                        break
            i += 1
            continue

        pair = s[i : i + 2]
        if pair in _HIRA_ROMAJI_2:
            roma = _HIRA_ROMAJI_2[pair]
            i += 2
        else:
            roma = _HIRA_ROMAJI_1.get(ch, ch)
            i += 1

        if geminate and roma and roma[0].isalpha():
            roma = roma[0] + roma
            geminate = False
        out.append(roma)

    return "".join(out)


@dataclass(frozen=True)
class _ZipVoiceRoute:
    key: Literal["zh", "ja"]
    tokenizer: str
    lang: str


class _LinguaDetector:
    def __init__(self) -> None:
        self._detector: Any | None = None
        self._lang_map: dict[Any, str] = {}
        try:
            from lingua import Language, LanguageDetectorBuilder
        except Exception:
            return

        langs = [Language.CHINESE, Language.JAPANESE, Language.ENGLISH]
        self._detector = LanguageDetectorBuilder.from_languages(*langs).build()
        self._lang_map = {
            Language.CHINESE: "zh",
            Language.JAPANESE: "ja",
            Language.ENGLISH: "en",
        }

    @property
    def available(self) -> bool:
        return self._detector is not None

    def detect(self, text: str, *, min_conf: float) -> str:
        if self._detector is None:
            return ""
        s = str(text or "").strip()
        if not s:
            return ""
        try:
            scores = self._detector.compute_language_confidence_values(s)
        except Exception:
            return ""
        if not scores:
            return ""
        top = scores[0]
        top_lang = self._lang_map.get(getattr(top, "language", None), "")
        top_score = float(getattr(top, "value", 0.0) or 0.0)
        second_score = float(getattr(scores[1], "value", 0.0) or 0.0) if len(scores) > 1 else 0.0
        if not top_lang:
            return ""
        if top_score < float(min_conf):
            return ""
        # Reject unstable top1 scores and let rule fallback handle it.
        if (top_score - second_score) < 0.12:
            return ""
        return top_lang


class ZipVoiceLangDetector:
    def __init__(self, *, mode: str = DEFAULT_ZIPVOICE_LANG_DETECTOR, min_conf: float = DEFAULT_ZIPVOICE_LANG_MIN_CONF) -> None:
        m = str(mode or DEFAULT_ZIPVOICE_LANG_DETECTOR).strip().lower()
        if m not in _ZIPVOICE_LANG_DETECTOR_CHOICES:
            raise ValueError(
                f"Unsupported zipvoice lang detector: {mode!r}. Expected one of {sorted(_ZIPVOICE_LANG_DETECTOR_CHOICES)}."
            )
        self._mode = m
        self._min_conf = min(0.99, max(0.0, float(min_conf)))
        self._lingua = _LinguaDetector() if m in {"auto", "lingua"} else None

    def detect(self, text: str) -> str:
        # Hard rule first: kana strongly implies Japanese.
        by_rule = _detect_with_script_rules(text)
        if by_rule == "ja":
            return "ja"
        if self._mode == "heuristic":
            return by_rule

        lingua = self._lingua
        if lingua is not None:
            by_lingua = lingua.detect(text, min_conf=self._min_conf)
            if by_lingua:
                return by_lingua
            if self._mode == "lingua":
                return by_rule
        return by_rule


class ZipVoiceTTS:
    def __init__(
        self,
        *,
        python_bin: str | Path = DEFAULT_ZIPVOICE_PYTHON_BIN,
        zipvoice_repo: str | Path = DEFAULT_ZIPVOICE_REPO,
        model_dir: str | Path = DEFAULT_ZIPVOICE_MODEL_DIR,
        checkpoint_name: str = DEFAULT_ZIPVOICE_CHECKPOINT_NAME,
        zh_tokenizer: str = DEFAULT_ZIPVOICE_ZH_TOKENIZER,
        zh_lang: str = DEFAULT_ZIPVOICE_ZH_LANG,
        ja_tokenizer: str = DEFAULT_ZIPVOICE_JA_TOKENIZER,
        ja_lang: str = DEFAULT_ZIPVOICE_JA_LANG,
        remove_long_sil: bool = DEFAULT_ZIPVOICE_REMOVE_LONG_SIL,
        num_thread: int = DEFAULT_ZIPVOICE_NUM_THREAD,
        lang_detector: str = DEFAULT_ZIPVOICE_LANG_DETECTOR,
        lang_min_conf: float = DEFAULT_ZIPVOICE_LANG_MIN_CONF,
        prompt_manifest: str | Path = DEFAULT_ZIPVOICE_PROMPT_MANIFEST,
        zh_prompt_manifest: str | Path = DEFAULT_ZIPVOICE_ZH_PROMPT_MANIFEST,
        ja_prompt_manifest: str | Path = DEFAULT_ZIPVOICE_JA_PROMPT_MANIFEST,
        prompt_policy: str = DEFAULT_ZIPVOICE_PROMPT_POLICY,
    ) -> None:
        self._python_bin = Path(python_bin).expanduser().resolve()
        self._zipvoice_repo = Path(zipvoice_repo).expanduser().resolve()
        self._model_dir = Path(model_dir).expanduser().resolve()
        self._checkpoint_name = str(checkpoint_name).strip() or DEFAULT_ZIPVOICE_CHECKPOINT_NAME
        self._zh_tokenizer = str(zh_tokenizer or DEFAULT_ZIPVOICE_ZH_TOKENIZER).strip()
        self._zh_lang = str(zh_lang or DEFAULT_ZIPVOICE_ZH_LANG).strip()
        self._ja_tokenizer = str(ja_tokenizer or DEFAULT_ZIPVOICE_JA_TOKENIZER).strip()
        self._ja_lang = str(ja_lang or DEFAULT_ZIPVOICE_JA_LANG).strip()
        self._remove_long_sil = bool(remove_long_sil)
        self._num_thread = max(1, int(num_thread))
        self._lang_detector = ZipVoiceLangDetector(mode=lang_detector, min_conf=lang_min_conf)
        self._prompt_manifest = str(prompt_manifest or "").strip()
        self._zh_prompt_manifest = str(zh_prompt_manifest or "").strip()
        self._ja_prompt_manifest = str(ja_prompt_manifest or "").strip()
        self._prompt_policy = str(prompt_policy or DEFAULT_ZIPVOICE_PROMPT_POLICY).strip().lower()
        if self._prompt_policy not in _ZIPVOICE_PROMPT_POLICY_CHOICES:
            raise ValueError(
                f"Unsupported zipvoice prompt policy: {prompt_policy!r}. Expected one of {sorted(_ZIPVOICE_PROMPT_POLICY_CHOICES)}."
            )
        self._prompt_pool = _load_prompt_pool_from_manifests(self._prompt_manifest)
        self._zh_prompt_pool = _load_prompt_pool_from_manifests(self._zh_prompt_manifest)
        self._ja_prompt_pool = _load_prompt_pool_from_manifests(self._ja_prompt_manifest)
        self._pool_lock = threading.Lock()
        self._pool_rr_idx = {"shared": 0, "zh": 0, "ja": 0}
        self._worker_lock = threading.Lock()
        self._worker_req_id = 0
        self._worker_proc: subprocess.Popen[str] | None = None
        self._worker_script = (Path(__file__).resolve().parent / "zipvoice_worker.py").resolve()

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
        if self._prompt_manifest and not self._prompt_pool:
            raise ValueError(f"ZipVoice prompt manifest has no usable entries: {self._prompt_manifest}")
        if self._zh_prompt_manifest and not self._zh_prompt_pool:
            raise ValueError(f"ZipVoice zh prompt manifest has no usable entries: {self._zh_prompt_manifest}")
        if self._ja_prompt_manifest and not self._ja_prompt_pool:
            raise ValueError(f"ZipVoice ja prompt manifest has no usable entries: {self._ja_prompt_manifest}")
        if not (self._zh_prompt_pool or self._prompt_pool):
            raise ValueError(
                "ZipVoice zh route requires prompt manifests. Set tts_zipvoice_zh_prompt_manifest or tts_zipvoice_prompt_manifest."
            )
        if not (self._ja_prompt_pool or self._prompt_pool):
            raise ValueError(
                "ZipVoice ja route requires prompt manifests. Set tts_zipvoice_ja_prompt_manifest or tts_zipvoice_prompt_manifest."
            )
        if not self._worker_script.is_file():
            raise FileNotFoundError(f"ZipVoice worker script not found: {self._worker_script}")

        self._start_worker()

    @property
    def backend_name(self) -> str:
        return "zipvoice"

    def _read_worker_message(self) -> dict[str, Any]:
        proc = self._worker_proc
        if proc is None or proc.stdout is None:
            raise RuntimeError("ZipVoice worker is not running")
        line = proc.stdout.readline()
        if not line:
            code = proc.poll()
            raise RuntimeError(f"ZipVoice worker exited unexpectedly (code={code})")
        msg = json.loads(line)
        if not isinstance(msg, dict):
            raise RuntimeError("ZipVoice worker returned non-object message")
        return msg

    def _start_worker(self) -> None:
        cmd = [
            str(self._python_bin),
            "-u",
            str(self._worker_script),
            "--zipvoice-repo",
            str(self._zipvoice_repo),
            "--model-dir",
            str(self._model_dir),
            "--checkpoint-name",
            self._checkpoint_name,
            "--num-thread",
            str(self._num_thread),
        ]
        proc = subprocess.Popen(
            cmd,
            cwd=str(self._zipvoice_repo),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            bufsize=1,
        )
        self._worker_proc = proc
        ready = self._read_worker_message()
        if not ready.get("ready", False):
            raise RuntimeError(f"ZipVoice worker failed to start: {ready}")

    def _stop_worker(self) -> None:
        proc = self._worker_proc
        self._worker_proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None and proc.stdin is not None:
                self._worker_req_id += 1
                rid = f"shutdown-{self._worker_req_id}"
                req = {"id": rid, "cmd": "shutdown"}
                proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def __del__(self) -> None:
        try:
            self._stop_worker()
        except Exception:
            pass

    def _infer_with_worker(
        self,
        *,
        out_wav: Path,
        tokenizer: str,
        lang: str,
        prompt_wav: Path,
        prompt_text: str,
        text: str,
        num_steps: int,
        guidance_scale: float,
        t_shift: float,
        speed: float,
    ) -> None:
        proc = self._worker_proc
        if proc is None or proc.stdin is None:
            self._start_worker()
            proc = self._worker_proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("ZipVoice worker could not be started")

        self._worker_req_id += 1
        req_id = str(self._worker_req_id)
        req = {
            "id": req_id,
            "tokenizer": tokenizer,
            "lang": lang,
            "prompt_wav": str(prompt_wav),
            "prompt_text": prompt_text,
            "text": text,
            "out_wav": str(out_wav),
            "num_step": max(1, int(num_steps)),
            "guidance_scale": float(guidance_scale),
            "t_shift": float(t_shift),
            "speed": float(speed),
            "target_rms": 0.1,
            "feat_scale": 0.1,
            "max_duration": 40.0,
            "remove_long_sil": bool(self._remove_long_sil),
        }

        try:
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except Exception as e:
            self._stop_worker()
            raise RuntimeError(f"ZipVoice worker write failed: {e}")

        while True:
            msg = self._read_worker_message()
            if str(msg.get("id", "")) != req_id:
                continue
            if bool(msg.get("ok", False)):
                return
            err = str(msg.get("error", "unknown error"))
            raise RuntimeError(
                "ZipVoice inference failed.\n"
                f"tokenizer={tokenizer} lang={lang}\n"
                f"prompt_wav={prompt_wav}\n"
                f"out={out_wav}\n"
                f"{err}"
            )

    def _route_by_key(self, key: str) -> _ZipVoiceRoute:
        if key == "ja":
            return _ZipVoiceRoute(
                key="ja",
                tokenizer=self._ja_tokenizer,
                lang=self._ja_lang,
            )
        return _ZipVoiceRoute(
            key="zh",
            tokenizer=self._zh_tokenizer,
            lang=self._zh_lang,
        )

    def _route(self, *, text: str, lang_hint: str = "") -> _ZipVoiceRoute:
        lang = _normalize_lang_tag(lang_hint)
        if lang == "ja":
            return self._route_by_key("ja")
        if lang in {"zh", "en"}:
            return self._route_by_key("zh")
        detected = self._lang_detector.detect(text)
        if detected == "ja":
            return self._route_by_key("ja")
        return self._route_by_key("zh")

    @staticmethod
    def _adapt_text_for_route(*, text: str, tokenizer: str, lang: str) -> str:
        s = str(text or "")
        zl = str(lang or "").strip().lower()
        zt = str(tokenizer or "").strip().lower()
        # zh route frequently explodes on mixed kana; transliterate kana to romaji first.
        if ("zh" in zl or zt in {"emilia", "pinyin", "jieba"}) and any(
            ("\u3040" <= ch <= "\u309f") or ("\u30a0" <= ch <= "\u30ff") for ch in s
        ):
            return _kana_to_romaji(s)
        return s

    def _choose_prompt_entry(
        self,
        *,
        pool: list[tuple[Path, str]],
        pool_name: Literal["shared", "zh", "ja"],
        prompt_key: str = "",
    ) -> tuple[Path, str] | None:
        if not pool:
            return None
        n = len(pool)
        pol = self._prompt_policy
        if pol == "random":
            return pool[random.randrange(n)]
        if pol == "round_robin":
            with self._pool_lock:
                idx = int(self._pool_rr_idx.get(pool_name, 0)) % n
                self._pool_rr_idx[pool_name] = int(self._pool_rr_idx.get(pool_name, 0)) + 1
            return pool[idx]

        # intent_hash: stable prompt per intent to avoid timbre jumping across chunks.
        key = str(prompt_key or "").strip()
        if not key:
            with self._pool_lock:
                idx = int(self._pool_rr_idx.get(pool_name, 0)) % n
                self._pool_rr_idx[pool_name] = int(self._pool_rr_idx.get(pool_name, 0)) + 1
            return pool[idx]
        h = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
        idx = int.from_bytes(h, byteorder="big", signed=False) % n
        return pool[idx]

    def _choose_prompt_for_route(
        self,
        *,
        route_key: Literal["zh", "ja"],
        prompt_key: str = "",
    ) -> tuple[Path, str] | None:
        if route_key == "ja" and self._ja_prompt_pool:
            return self._choose_prompt_entry(pool=self._ja_prompt_pool, pool_name="ja", prompt_key=prompt_key)
        if route_key == "zh" and self._zh_prompt_pool:
            return self._choose_prompt_entry(pool=self._zh_prompt_pool, pool_name="zh", prompt_key=prompt_key)
        if self._prompt_pool:
            return self._choose_prompt_entry(pool=self._prompt_pool, pool_name="shared", prompt_key=prompt_key)
        return None

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
        prompt_key: str = "",
    ) -> Path:
        del prompt_wav_path, prompt_duration, prompt_rms, return_smooth
        out_wav = Path(out_wav_path).expanduser().resolve()
        out_wav.parent.mkdir(parents=True, exist_ok=True)

        route = self._route(text=str(text or ""), lang_hint=lang_hint)
        tokenizer = route.tokenizer
        lang = route.lang
        tts_text = self._adapt_text_for_route(text=str(text or ""), tokenizer=tokenizer, lang=lang)
        chosen = self._choose_prompt_for_route(route_key=route.key, prompt_key=prompt_key)
        if chosen is None:
            raise ValueError(
                f"ZipVoice prompt manifest is missing for route={route.key}. "
                "Configure tts_zipvoice_(zh|ja)_prompt_manifest or shared tts_zipvoice_prompt_manifest."
            )
        prompt_wav, prompt_text = chosen
        if not prompt_wav.is_file():
            raise FileNotFoundError(f"ZipVoice prompt wav not found: {prompt_wav}")

        with self._worker_lock:
            self._infer_with_worker(
                out_wav=out_wav,
                tokenizer=tokenizer,
                lang=lang,
                prompt_wav=prompt_wav,
                prompt_text=prompt_text,
                text=tts_text,
                num_steps=max(1, int(num_steps)),
                guidance_scale=float(guidance_scale),
                t_shift=float(t_shift),
                speed=float(speed),
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
    tts_zipvoice_zh_tokenizer: str,
    tts_zipvoice_zh_lang: str,
    tts_zipvoice_ja_tokenizer: str,
    tts_zipvoice_ja_lang: str,
    tts_zipvoice_remove_long_sil: bool,
    tts_zipvoice_num_thread: int,
    tts_zipvoice_lang_detector: str,
    tts_zipvoice_lang_min_conf: float,
    tts_zipvoice_prompt_manifest: str | Path,
    tts_zipvoice_zh_prompt_manifest: str | Path,
    tts_zipvoice_ja_prompt_manifest: str | Path,
    tts_zipvoice_prompt_policy: str,
) -> Any:
    b = str(backend or DEFAULT_TTS_BACKEND).strip().lower()
    if b == "zipvoice":
        return ZipVoiceTTS(
            python_bin=tts_zipvoice_python_bin,
            zipvoice_repo=tts_zipvoice_repo,
            model_dir=tts_zipvoice_model_dir,
            checkpoint_name=tts_zipvoice_checkpoint_name,
            zh_tokenizer=tts_zipvoice_zh_tokenizer,
            zh_lang=tts_zipvoice_zh_lang,
            ja_tokenizer=tts_zipvoice_ja_tokenizer,
            ja_lang=tts_zipvoice_ja_lang,
            remove_long_sil=tts_zipvoice_remove_long_sil,
            num_thread=tts_zipvoice_num_thread,
            lang_detector=tts_zipvoice_lang_detector,
            lang_min_conf=tts_zipvoice_lang_min_conf,
            prompt_manifest=tts_zipvoice_prompt_manifest,
            zh_prompt_manifest=tts_zipvoice_zh_prompt_manifest,
            ja_prompt_manifest=tts_zipvoice_ja_prompt_manifest,
            prompt_policy=tts_zipvoice_prompt_policy,
        )

    if b != "lux":
        raise ValueError(f"Unsupported --tts-backend: {backend!r}. Expected lux or zipvoice.")
    return LuxTTS(model=tts_model, device=tts_device, threads=tts_threads)
