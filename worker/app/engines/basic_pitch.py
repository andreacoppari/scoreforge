from pathlib import Path

from basic_pitch.inference import predict


def transcribe(audio_path: Path, output_dir: Path) -> Path:
    """Run Spotify Basic Pitch and return its generated MIDI file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    _, midi, _ = predict(str(audio_path))
    target = output_dir / "transcription.mid"
    midi.write(str(target))
    return target
