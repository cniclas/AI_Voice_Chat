# Vendored openai-whisper (minimal)

This directory is a trimmed, vendored copy of [openai/whisper](https://github.com/openai/whisper).
It is committed to this repository on purpose: `requirements.txt` installs it with `-e ./whisper`
and `pyproject.toml` lists it as a `[tool.uv.workspace]` member, so without it checked in a fresh
clone cannot install at all.

## Provenance

| | |
|---|---|
| Upstream | https://github.com/openai/whisper |
| Commit | `04f449b8a437f1bbd3dba5c9f826aca972e7709a` (2026-04-15) |
| Version | `20250625` (see `whisper/version.py`) |
| License | MIT — see `LICENSE`, copyright (c) 2022 OpenAI |

**Every retained file is byte-identical to upstream, except `README.md` (this file) and
`.gitattributes` (added to protect the asset files from CRLF translation on Windows).**
`pyproject.toml` is also verbatim, which keeps the `openai-whisper` entry in the root `uv.lock`
valid — its recorded dependency set still matches, so vendoring did not require relocking.

Nothing was patched. Verify with:

```bash
git clone --depth 1 https://github.com/openai/whisper.git /tmp/whisper-upstream
cd /tmp/whisper-upstream && git checkout 04f449b8a437f1bbd3dba5c9f826aca972e7709a
diff -r /tmp/whisper-upstream/whisper <repo>/whisper/whisper --exclude=__main__.py --exclude=normalizers
```

## What was removed, and why

Only files this project never loads. The removals are deletions, not edits:

| Removed | Reason |
|---|---|
| `whisper/__main__.py` | `python -m whisper` CLI shim. The console-script entry point (`whisper.transcribe:cli`) still exists and still works. |
| `whisper/normalizers/` | `EnglishTextNormalizer` / `BasicTextNormalizer`, used only by upstream's WER evaluation notebooks and tests. No runtime module imports it — confirmed by grep across the retained sources. |
| `tests/`, `notebooks/`, `data/` | Upstream's test suite, Jupyter notebooks, and the `meanwhile.json` evaluation fixture. |
| `README.md` (upstream), `CHANGELOG.md`, `model-card.md`, `approach.png`, `language-breakdown.svg` | Documentation and images. |
| `.github/`, `.flake8`, `.pre-commit-config.yaml`, `.gitattributes` (upstream), `.gitignore`, `MANIFEST.in`, `requirements.txt` | Upstream's CI, linting, and packaging metadata, redundant here — `pyproject.toml` already declares the same dependency set. |

The full inference path is intact: `whisper.load_model()` and `model.transcribe()` behave exactly as
upstream, including `word_timestamps=True` (`timing.py` and `triton_ops.py` are retained), and both
the multilingual and `.en` tokenizer assets are present so any model size can be selected.

## How this project uses it

`session_core.transcribe_audio()` decodes WAVs in-process via `load_audio_16k()` and passes a numpy
array straight to `model.transcribe()`, deliberately bypassing `whisper.audio.load_audio()`. That is
the only reason **ffmpeg is not required on PATH** — `load_audio()` shells out to the `ffmpeg` binary
and is still present here, so anything that calls it directly will need ffmpeg installed.

## Updating

Re-copy the retained files from a newer upstream tag, then re-check that `pyproject.toml`'s
dependency list still matches the `openai-whisper` entry in the root `uv.lock` (relock if it does
not), and update the commit and version in the provenance table above.
