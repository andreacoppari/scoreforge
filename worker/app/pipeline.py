import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pretty_midi
from music21 import converter

from .engines import basic_pitch, muscriptor
from .models import Artifact, Instrument

GM_PROGRAMS = {
    Instrument.piano: 0,
    Instrument.guitar: 24,
    Instrument.trumpet: 56,
    Instrument.violin: 40,
    Instrument.flute: 73,
}

# MuScriptor's MT3_FULL_PLUS groups use their representative GM program.
MUSCRIPTOR_PROGRAMS = {
    Instrument.piano: 0,
    Instrument.guitar: 24,
    Instrument.trumpet: 56,
    Instrument.violin: 40,
    Instrument.flute: 72,
}

PLAYABLE_RANGES = {
    Instrument.piano: (21, 108),
    Instrument.guitar: (40, 88),
    Instrument.trumpet: (54, 82),
    Instrument.violin: (55, 103),
    Instrument.flute: (60, 96),
}

POLYPHONY_LIMITS = {
    Instrument.piano: 10,
    Instrument.guitar: 6,
    Instrument.trumpet: 1,
    Instrument.violin: 2,
    Instrument.flute: 1,
}

# "Balanced" notation is intentionally less literal than the MIDI inference.
# An eighth-note grid is readable for most contemporary songs while preserving
# enough rhythmic character to make the exported score useful for rehearsal.
SIMPLIFICATION_SUBDIVISIONS_PER_BEAT = 2
MIN_NOTE_DURATION_GRID_FRACTION = 0.20


def _polyphony_limit(instrument: Instrument) -> int:
    return POLYPHONY_LIMITS[instrument]


def _grid_duration(score: pretty_midi.PrettyMIDI) -> float:
    _, tempi = score.get_tempo_changes()
    tempo = float(tempi[0]) if len(tempi) and tempi[0] > 0 else 120.0
    return 60.0 / tempo / SIMPLIFICATION_SUBDIVISIONS_PER_BEAT


def _enforce_polyphony(notes: list[pretty_midi.Note], maximum: int) -> list[pretty_midi.Note]:
    """Keep the most prominent notes when a part exceeds its playable limit."""
    accepted: list[pretty_midi.Note] = []
    for note in sorted(notes, key=lambda item: (item.start, item.pitch, item.end)):
        inactive = [item for item in accepted if item.end <= note.start]
        active = [item for item in accepted if item.end > note.start]
        candidates = active + [note]
        if len(candidates) <= maximum:
            accepted.append(note)
            continue
        # Prefer clearly played / sustained notes; the higher note wins a tie,
        # which preserves the melodic top line in a simplified chord.
        keep = set(
            id(item)
            for item in sorted(
                candidates,
                key=lambda item: (item.velocity, item.end - item.start, item.pitch),
                reverse=True,
            )[:maximum]
        )
        accepted = inactive + [item for item in active if id(item) in keep]
        if id(note) in keep:
            accepted.append(note)
    return sorted(accepted, key=lambda item: (item.start, item.pitch, item.end))


def _simplify_midi(
    path: Path, monophonic: bool = False, maximum_polyphony: int | None = None
) -> None:
    """Turn expressive raw MIDI into a readable, playable balanced score.

    AMT output is deliberately high-resolution. This pass removes notes shorter
    than one fifth of a grid, snaps timing to an eighth-note grid, combines repeated
    notes, and (for lead arrangements) suppresses one-grid pitch spikes.
    """
    score = pretty_midi.PrettyMIDI(str(path))
    grid = _grid_duration(score)
    for track in score.instruments:
        if track.is_drum:
            continue
        quantized: list[pretty_midi.Note] = []
        for note in sorted(track.notes, key=lambda item: (item.start, item.pitch, item.end)):
            if note.end - note.start < grid * MIN_NOTE_DURATION_GRID_FRACTION:
                continue
            start = max(0.0, round(note.start / grid) * grid)
            end = max(start + grid, round(note.end / grid) * grid)
            quantized.append(pretty_midi.Note(note.velocity, note.pitch, start, end))

        # Merge repeats of one pitch separated only by a quantization-sized gap.
        by_pitch: dict[int, list[pretty_midi.Note]] = {}
        for note in quantized:
            by_pitch.setdefault(note.pitch, []).append(note)
        merged: list[pretty_midi.Note] = []
        for notes in by_pitch.values():
            for note in notes:
                if merged and merged[-1].pitch == note.pitch and note.start <= merged[-1].end + grid * 0.5:
                    merged[-1].end = max(merged[-1].end, note.end)
                else:
                    merged.append(note)
        track.notes = sorted(merged, key=lambda item: (item.start, item.pitch, item.end))

        if monophonic:
            notes = track.notes
            # A short excursion that immediately returns to the same pitch is
            # normally a transcription artifact, not a desirable melody note.
            spikes = {
                index
                for index in range(1, len(notes) - 1)
                if notes[index].end - notes[index].start <= grid
                and notes[index - 1].pitch == notes[index + 1].pitch
            }
            cleaned = [note for index, note in enumerate(notes) if index not in spikes]
            playable: list[pretty_midi.Note] = []
            for note in cleaned:
                if playable and note.start < playable[-1].end:
                    note.start = playable[-1].end
                if note.end <= note.start:
                    note.end = note.start + grid
                playable.append(note)
            track.notes = playable
        if maximum_polyphony is not None:
            track.notes = _enforce_polyphony(track.notes, maximum_polyphony)
    score.write(str(path))


def _label_midi(source: Path, target: Path, instrument: Instrument) -> None:
    """Give an instrument-agnostic transcription a correct user-selected label."""
    score = pretty_midi.PrettyMIDI(str(source))
    if not score.instruments:
        raise RuntimeError("The transcription contains no pitched notes.")
    for track in score.instruments:
        track.program = GM_PROGRAMS[instrument]
        track.name = instrument.value.title()
    score.write(str(target))


def _write_musicxml(midi_file: Path, xml_file: Path) -> None:
    try:
        score = converter.parse(str(midi_file))
        score.write("musicxml", fp=str(xml_file))
    except Exception as exc:
        raise RuntimeError(f"Could not turn MIDI into MusicXML: {exc}") from exc


def _lead_notes(source: Path) -> list[pretty_midi.Note]:
    """Derive one practical lead voice from a multi-track transcription.

    This is intentionally an arrangement step, not source separation. The
    upper sounding voice is a useful melodic default for songs; users should
    still review the exported notation for musical editorial decisions.
    """
    score = pretty_midi.PrettyMIDI(str(source))
    lead_source = pretty_midi.PrettyMIDI(initial_tempo=120)
    lead_source.instruments = [track for track in score.instruments if not track.is_drum]
    roll = lead_source.get_piano_roll(fs=50)
    if roll.size == 0 or not np.any(roll):
        raise RuntimeError("No pitched notes were found from which to create a melody.")
    pitches = np.full(roll.shape[1], -1, dtype=int)
    active = np.any(roll > 0, axis=0)
    pitches[active] = np.max(np.where(roll[:, active] > 0, np.arange(128)[:, None], -1), axis=0)
    notes: list[pretty_midi.Note] = []
    start = 0
    current = int(pitches[0])
    for frame in range(1, len(pitches) + 1):
        next_pitch = int(pitches[frame]) if frame < len(pitches) else -2
        if next_pitch == current:
            continue
        if current >= 0 and (frame - start) >= 2:
            notes.append(
                pretty_midi.Note(
                    velocity=96,
                    pitch=current,
                    start=start / 50,
                    end=frame / 50,
                )
            )
        start = frame
        current = next_pitch
    if not notes:
        raise RuntimeError("The detected melody was too short to export.")
    return notes


def _fit_pitch(pitch: int, low: int, high: int) -> int | None:
    while pitch < low:
        pitch += 12
    while pitch > high:
        pitch -= 12
    return pitch if low <= pitch <= high else None


def _write_arrangements(source: Path, output_dir: Path, instruments: list[Instrument]) -> list[Artifact]:
    melody = _lead_notes(source)
    artifacts: list[Artifact] = []
    for instrument in instruments or list(Instrument):
        low, high = PLAYABLE_RANGES[instrument]
        arrangement = pretty_midi.PrettyMIDI(initial_tempo=120)
        track = pretty_midi.Instrument(program=GM_PROGRAMS[instrument], name=instrument.value.title())
        for note in melody:
            pitch = _fit_pitch(note.pitch, low, high)
            if pitch is not None:
                track.notes.append(
                    pretty_midi.Note(note.velocity, pitch, note.start, note.end)
                )
        if not track.notes:
            continue
        arrangement.instruments.append(track)
        midi_name = f"{instrument.value}_melody.mid"
        xml_name = f"{instrument.value}_melody.musicxml"
        midi_path = output_dir / midi_name
        arrangement.write(str(midi_path))
        _simplify_midi(midi_path, monophonic=True, maximum_polyphony=1)
        _write_musicxml(midi_path, output_dir / xml_name)
        artifacts.extend([
            Artifact(name=midi_name, label=f"{instrument.value.title()} melody · MIDI", format="MIDI"),
            Artifact(name=xml_name, label=f"{instrument.value.title()} melody · MusicXML", format="MusicXML"),
        ])
    if not artifacts:
        raise RuntimeError("The melody could not be adapted to the requested instrument ranges.")
    return artifacts


def _write_detected_parts(source: Path, output_dir: Path, instruments: list[Instrument]) -> list[Artifact]:
    score = pretty_midi.PrettyMIDI(str(source))
    artifacts: list[Artifact] = []
    for instrument in instruments or list(Instrument):
        matching = [
            track for track in score.instruments if track.program == MUSCRIPTOR_PROGRAMS[instrument]
        ]
        if not matching:
            continue
        part = pretty_midi.PrettyMIDI(initial_tempo=120)
        merged_track = pretty_midi.Instrument(
            program=MUSCRIPTOR_PROGRAMS[instrument], name=instrument.value.title()
        )
        merged_track.notes = [
            pretty_midi.Note(note.velocity, note.pitch, note.start, note.end)
            for track in matching
            for note in track.notes
        ]
        part.instruments = [merged_track]
        midi_name = f"{instrument.value}_detected.mid"
        xml_name = f"{instrument.value}_detected.musicxml"
        midi_path = output_dir / midi_name
        part.write(str(midi_path))
        _simplify_midi(midi_path, maximum_polyphony=_polyphony_limit(instrument))
        _write_musicxml(midi_path, output_dir / xml_name)
        artifacts.extend([
            Artifact(name=midi_name, label=f"{instrument.value.title()} part · MIDI", format="MIDI"),
            Artifact(name=xml_name, label=f"{instrument.value.title()} part · MusicXML", format="MusicXML"),
        ])
    if not artifacts:
        raise RuntimeError("No requested recorded parts were detected in this audio.")
    return artifacts


def transcribe(
    audio_path: Path,
    output_dir: Path,
    instruments: list[Instrument],
    engine: str,
    muscriptor_model: str = "large",
    device: str = "cpu",
    mode: str = "melody",
) -> list[Artifact]:
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "raw"
    if engine == "basic-pitch":
        if mode == "single_part":
            raise RuntimeError(
                "Single part mode requires MuScriptor. Basic Pitch is instrument-agnostic "
                "and cannot identify an original violin, flute, guitar, piano, or trumpet part."
            )
        raw_midi = basic_pitch.transcribe(audio_path, raw_dir)
        chosen = instruments or list(Instrument)
        artifacts = _write_arrangements(raw_midi, output_dir, chosen)
        shutil.rmtree(raw_dir, ignore_errors=True)
        return artifacts

    if engine == "muscriptor":
        raw_dir = output_dir / "raw"
        raw_midi = muscriptor.transcribe(
            audio_path,
            raw_dir,
            instruments if mode == "single_part" else None,
            muscriptor_model,
            device,
        )
        artifacts = (
            _write_detected_parts(raw_midi, output_dir, instruments)
            if mode == "single_part"
            else _write_arrangements(raw_midi, output_dir, instruments)
        )
        shutil.rmtree(raw_dir, ignore_errors=True)
        return artifacts
    raise RuntimeError(f"Unsupported transcription engine: {engine}")


def separate(audio_path: Path, output_dir: Path, model: str, device: str) -> list[Artifact]:
    """Use Demucs' supported two-stem vocals mode and standardize output names."""
    demucs_dir = output_dir / "demucs"
    command = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--two-stems",
        "vocals",
        "-n",
        model,
        "-o",
        str(demucs_dir),
        "-d",
        device,
        str(audio_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=60 * 60)
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()[-800:]
        raise RuntimeError(f"Demucs failed: {detail}")

    vocal_sources = list(demucs_dir.rglob("vocals.wav"))
    music_sources = list(demucs_dir.rglob("no_vocals.wav"))
    if not vocal_sources or not music_sources:
        raise RuntimeError("Demucs completed but did not produce both vocal and instrumental files.")
    vocals = output_dir / "vocals.wav"
    instrumental = output_dir / "instrumental.wav"
    shutil.move(str(vocal_sources[0]), vocals)
    shutil.move(str(music_sources[0]), instrumental)
    shutil.rmtree(demucs_dir, ignore_errors=True)
    return [
        Artifact(name="vocals.wav", label="Vocals", format="WAV"),
        Artifact(name="instrumental.wav", label="Instrumental", format="WAV"),
    ]
