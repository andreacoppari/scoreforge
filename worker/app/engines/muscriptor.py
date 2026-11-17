"""Cached MuScriptor adapter with hard instrument constraints."""
from functools import lru_cache
from pathlib import Path

from muscriptor import TranscriptionModel

from ..models import Instrument

MUSCRIPTOR_GROUPS = {
    Instrument.piano: "acoustic_piano",
    Instrument.guitar: "acoustic_guitar",
    Instrument.trumpet: "trumpet",
    Instrument.violin: "violin",
    Instrument.flute: "flutes",
}


@lru_cache(maxsize=2)
def _load_model(model: str, device: str) -> TranscriptionModel:
    """Keep the CPU model in this long-lived worker after the first job."""
    return TranscriptionModel.load_model(weights_path=model, device=device)


def transcribe(
    audio_path: Path,
    output_dir: Path,
    instruments: list[Instrument] | None,
    model: str,
    device: str,
) -> Path:
    """Run the requested checkpoint and return its constrained MIDI result."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "score.mid"
    expected_groups = [MUSCRIPTOR_GROUPS[instrument] for instrument in instruments] if instruments else None
    try:
        midi_bytes = _load_model(model, device).transcribe_to_midi(
            audio_path,
            instruments=expected_groups,
            detect_tempo="best-effort",
        )
        output.write_bytes(midi_bytes)
    except Exception as exc:
        detail = str(exc).strip()[-1200:]
        if "HuggingFace" in detail or "token" in detail.lower() or "gated" in detail.lower():
            detail = (
                "MuScriptor needs a Hugging Face token with its gated CC BY-NC license accepted. "
                "Set HF_TOKEN in .env and restart the worker. " + detail
            )
        raise RuntimeError(f"MuScriptor failed: {detail or 'no MIDI output was created.'}")
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("MuScriptor did not produce MIDI output.")
    return output
