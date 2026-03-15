from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


class LlamaCppCliError(RuntimeError):
    pass


def _with_bin_dir_ld_library_path(bin_dir: Path) -> dict[str, str]:
    env = os.environ.copy()
    ld_library_path = env.get("LD_LIBRARY_PATH", "")
    if ld_library_path:
        env["LD_LIBRARY_PATH"] = f"{bin_dir}:{ld_library_path}"
    else:
        env["LD_LIBRARY_PATH"] = str(bin_dir)
    return env


def resolve_llama_cpp_bin_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        bin_dir = Path(explicit).expanduser()
        if bin_dir.is_dir():
            return bin_dir.resolve()
        raise FileNotFoundError(f"llama.cpp bin dir not found: {bin_dir}")

    env_bin_dir = os.getenv("LLAMA_CPP_BIN_DIR")
    if env_bin_dir:
        bin_dir = Path(env_bin_dir).expanduser()
        if bin_dir.is_dir():
            return bin_dir.resolve()

    env_repo_dir = os.getenv("LLAMA_CPP_DIR")
    if env_repo_dir:
        candidate = (Path(env_repo_dir).expanduser() / "build" / "bin")
        if candidate.is_dir():
            return candidate.resolve()

    default = Path("/home/morusa/AI/llama-cpp/build/bin")
    if default.is_dir():
        return default.resolve()

    which = shutil.which("llama-cli")
    if which:
        return Path(which).resolve().parent

    raise FileNotFoundError(
        "Unable to locate llama.cpp binaries. Set LLAMA_CPP_BIN_DIR or LLAMA_CPP_DIR, "
        "or pass --llama-bin-dir."
    )


def pick_default_embed_model(model_dir: Path) -> Path:
    preferred = model_dir / "Qwen3-Embedding-0.6B-Q8_0.gguf"
    if preferred.is_file():
        return preferred
    ggufs = sorted(model_dir.glob("*.gguf"))
    if not ggufs:
        raise FileNotFoundError(f"No .gguf models found under: {model_dir}")
    return ggufs[0]


def pick_default_chat_model(model_dir: Path) -> Path:
    preferred = model_dir / "Qwen3.5-4B-Q4_K_M.gguf"
    if preferred.is_file():
        return preferred
    ggufs = sorted(model_dir.glob("*.gguf"))
    if not ggufs:
        raise FileNotFoundError(f"No .gguf models found under: {model_dir}")
    for p in ggufs:
        if "Embedding" not in p.name:
            return p
    return ggufs[0]


def _extract_first_json_object(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object found in output")
    return text[start : end + 1]


@dataclass(frozen=True)
class LlamaCppEmbeddingRunner:
    bin_path: Path
    model_path: Path
    pooling: str | None = None
    normalize: int = 2
    extra_args: tuple[str, ...] = ()

    def embed(self, text: str, *, mode: str = "query") -> list[float]:
        return self.embed_many([text], mode=mode)[0]

    def embed_many(self, texts: Sequence[str], *, mode: str = "query") -> list[list[float]]:
        if not texts:
            return []

        bin_path = self.bin_path.resolve()
        model_path = self.model_path.resolve()
        if not bin_path.is_file():
            raise FileNotFoundError(f"llama-embedding not found: {bin_path}")
        if not model_path.is_file():
            raise FileNotFoundError(f"embedding model not found: {model_path}")

        env = _with_bin_dir_ld_library_path(bin_path.parent)
        out: list[list[float]] = []
        for text in texts:
            cmd = [
                str(bin_path),
                "--verbosity",
                "0",
                "-m",
                str(model_path),
                "--embd-output-format",
                "json",
                "--embd-normalize",
                str(int(self.normalize)),
                "-p",
                str(text),
            ]
            if self.pooling:
                cmd.extend(["--pooling", self.pooling])
            cmd.extend(self.extra_args)

            proc = subprocess.run(
                cmd,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                text=True,
            )
            if proc.returncode != 0:
                raise LlamaCppCliError(
                    f"llama-embedding failed (exit={proc.returncode}). stderr:\n{proc.stderr.strip()}"
                )

            raw = proc.stdout.strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = json.loads(_extract_first_json_object(raw))

            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or not data:
                raise LlamaCppCliError(f"Unexpected embedding output: {raw[:400]}")
            emb = data[0].get("embedding") if isinstance(data[0], dict) else None
            if not isinstance(emb, list) or not emb:
                raise LlamaCppCliError(f"Unexpected embedding output: {raw[:400]}")
            out.append([float(x) for x in emb])

        return out


@dataclass(frozen=True)
class LlamaCppChatRunner:
    bin_path: Path
    model_path: Path
    ctx_size: int = 0
    n_predict: int = 512
    temperature: float = 0.7
    top_p: float = 0.9
    extra_args: tuple[str, ...] = ()

    def generate(self, *, system_prompt: str, user_prompt: str) -> str:
        bin_path = self.bin_path.resolve()
        model_path = self.model_path.resolve()
        if not bin_path.is_file():
            raise FileNotFoundError(f"llama-cli not found: {bin_path}")
        if not model_path.is_file():
            raise FileNotFoundError(f"chat model not found: {model_path}")

        cmd: list[str] = [
            str(bin_path),
            "--verbosity",
            "0",
            "-m",
            str(model_path),
            "--no-display-prompt",
            "--simple-io",
            "--conversation",
            "--single-turn",
            "--no-perf",
            "--reasoning-budget",
            "0",
            "--system-prompt",
            system_prompt,
            "--prompt",
            user_prompt,
            "--temp",
            str(float(self.temperature)),
            "--top-p",
            str(float(self.top_p)),
            "--n-predict",
            str(int(self.n_predict)),
        ]
        if self.ctx_size and self.ctx_size > 0:
            cmd.extend(["--ctx-size", str(int(self.ctx_size))])
        cmd.extend(self.extra_args)

        env = _with_bin_dir_ld_library_path(bin_path.parent)
        proc = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
        )
        if proc.returncode != 0:
            raise LlamaCppCliError(f"llama-cli failed (exit={proc.returncode}). stderr:\n{proc.stderr.strip()}")

        return proc.stdout.strip()


def iter_lua_sequence(value: object) -> Iterable[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return value

    try:
        length = len(value)  # type: ignore[arg-type]
    except Exception:
        length = 0

    if isinstance(length, int) and length > 0:
        seq: list[object] = []
        for i in range(1, length + 1):
            try:
                seq.append(value[i])  # type: ignore[index]
            except Exception:
                break
        if seq:
            return seq

    try:
        return list(value)  # type: ignore[arg-type]
    except Exception:
        return []
