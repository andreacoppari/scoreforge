"""MuScriptor CLI adapter.

The model's command-line interface supports hard instrument constraints, which
is more reliable than taking a generic MIDI result and guessing tracks later.
"""
import subprocess
import sys
from pathlib import Path

from ..models import Instrument

MUSCRIPTOR_GROUPS = {
    Instrument.piano: "acoustic_piano",
    Instrument.guitar: "acoustic_guitar",
    Instrument.trumpet: "trumpet",
    Instrument.violin: "violin",
    Instrument.flute: "flutes",
}


def transcribe(
    audio_path: Path,
    output_dir: Path,
    instruments: list[Instrument] | None,
    model: str,
    device: str,
) -> Path:
    """Run the requested gated checkpoint and return its multitrack MIDI file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "score.mid"
    command = [
        sys.executable,
        "-m",
        "muscriptor.main",
        "transcribe",
        str(audio_path),
        "--output",
        str(output),
        "--format",
        "midi",
        "--model",
        model,
        "--device",
        device,
        "--detect-tempo",
        "best-effort",
    ]
    if instruments:
        expected_groups = ",".join(MUSCRIPTOR_GROUPS[instrument] for instrument in instruments)
        command.extend(["--instruments", expected_groups])
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=60 * 60)
    except FileNotFoundError as exc:
        raise RuntimeError("MuScriptor runtime is not installed in this worker image.") from exc
    if completed.returncode or not output.is_file():
        detail = (completed.stderr or completed.stdout).strip()[-1200:]
        if "HuggingFace" in detail or "token" in detail.lower() or "gated" in detail.lower():
            detail = (
                "MuScriptor Large needs a Hugging Face token with its gated CC BY-NC license accepted. "
                "Set HF_TOKEN in .env and rebuild/restart the worker. " + detail
            )
        raise RuntimeError(f"MuScriptor failed: {detail or 'no MIDI output was created.'}")
    return output
