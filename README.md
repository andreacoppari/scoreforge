# ScoreForge

ScoreForge is a self-hosted web app that turns audio into editable music notation. It has two separate workflows:

- **Transcribe** converts an MP3, WAV, M4A, FLAC, or OGG file into per-instrument MIDI files and a standard MusicXML score.
- **Voice / music split** creates separate vocal and instrumental WAV files.

It is designed so that the browser UI can be deployed free on Cloudflare Pages, while the model worker runs on hardware you control. Audio is never sent to a paid API.

## Important accuracy note

Audio-to-MIDI is the right first step, but it is not a guarantee of publish-ready engraving. Mixed commercial recordings are the difficult case: overlapping instruments, reverb, drums, and vocals can create missed or extra notes. The app therefore exports MIDI **and** MusicXML so the result can be refined in MuseScore, Dorico, or another notation editor.

The default `basic-pitch` engine is a fast, free, polyphonic transcription baseline, but it is deliberately **instrument agnostic**. It can create Melody arrangements, but cannot truthfully identify a piano, trumpet, violin, or other recorded source part in a dense mix. The optional CPU MuScriptor engine constrains decoding to piano, guitar, trumpet, violin, and flute groups for the Single part workflow. MuScriptor Large can run on CPU but is slow and memory-intensive; start with `small` or `medium` unless you have ample RAM and patience.

## Two transcription modes

- **Melody** is the default. The app transcribes the complete recording, derives its prominent upper melodic line, then writes a strictly one-note-at-a-time MIDI/MusicXML arrangement for every selected target instrument. This is the choice for “give me this melody for violin, flute, and guitar.” It does not claim that the original audio contained those instruments. Octaves are moved where necessary to remain in the target instrument's practical range.
- **Single part** asks MuScriptor to restrict decoding to exactly one selected instrument family and exports a simplified score for that recorded part. This is the choice for “find the violin actually present in this recording.” It produces notation, not an isolated audio stem; use Voice / music split for the separate vocal/instrumental audio workflow. Basic Pitch cannot run this mode because it is instrument agnostic.

Every exported score receives the same **balanced cleanup** before it becomes MusicXML: timing is snapped to an eighth-note grid, only notes shorter than one fifth of a grid are removed, and repeated pitches are joined. Melody arrangements are strictly monophonic. Single parts are constrained to playable chord limits: flute/trumpet 1, violin 2, guitar 6, piano 10. This makes the results more playable, at the cost of not preserving every expressive micro-detail in the recording.

Source separation is a different task. Demucs reliably offers **vocals / instrumental** separation, which is why that workflow is separate. It does not promise isolated piano, guitar, trumpet, violin, or flute stems.

## Architecture

```text
Cloudflare Pages (static frontend)  ──HTTPS──>  FastAPI worker (local Docker host)
                                                 ├─ Basic Pitch or MuScriptor → MIDI → MusicXML
                                                 └─ Demucs → vocals.wav + instrumental.wav
```

Cloudflare Workers are not a good home for the inference worker: it needs native audio tools, model downloads, temporary disk, and long-running CPU processing. Host `frontend/` on Pages and point it at the worker URL, or run the complete local stack using Docker Compose.

## Quick start — CPU baseline

Requirements: Docker Desktop/Engine with Compose. The first transcription downloads model weights (roughly hundreds of MB), then the models are cached in the named volume.

```bash
cp .env.cpu.example .env
docker compose up --build
```

The `.env` copy is optional: Compose has the same CPU defaults built in. It is the convenient place to make persistent local changes.

After changing worker dependencies, force a fresh worker image rather than relying on an existing container:

```bash
docker compose build --no-cache worker
docker compose up -d --force-recreate worker
```

If Docker reports that its `buildx` plugin is missing, use Docker's classic builder for the worker image, then start Compose without `--build`:

```bash
docker build -t worsheets-worker -f worker/Dockerfile --target cpu worker
docker compose up -d --force-recreate worker
```

Open `http://localhost:8080`. CPU mode is suitable for short, single-instrument recordings and the voice/music split may be slow. It needs no API key.

## Optional CPU MuScriptor

Accept the gated [MuScriptor model license](https://huggingface.co/MuScriptor/muscriptor-large), create a free Hugging Face read token, and add these values to `.env` before starting the worker:

```dotenv
TRANSCRIPTION_ENGINE=muscriptor
MUSCRIPTOR_MODEL=small
HF_TOKEN=hf_...
```

`small` is the sensible CPU starting point. `medium` is slower; `large` is available but can take a long time per recording and requires substantial system RAM. Model weights are CC BY-NC 4.0, so MuScriptor is non-commercial only.

## Cloudflare Pages deployment

1. Deploy the `frontend` directory as a static site (no build command required).
2. Deploy the worker on a reachable machine/VPS using Docker. Use HTTPS through a reverse proxy or Cloudflare Tunnel.
3. Set the browser’s API URL in `frontend/config.js` before deploying:

   ```js
   window.SCOREFORGE_API_URL = "https://score-worker.example.com";
   ```

4. Set `ALLOWED_ORIGINS=https://your-pages-project.pages.dev,https://your-domain.example` in the worker environment, then restart it.

Do not expose an unauthenticated public worker for arbitrary uploads. Put Cloudflare Access, a reverse-proxy login, or your own authentication in front of it. The demo intentionally has no identity system.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEVICE` | `cpu` | Fixed to CPU in the provided Docker configuration. |
| `TRANSCRIPTION_ENGINE` | `basic-pitch` | `basic-pitch` or optional CPU `muscriptor`. |
| `MAX_UPLOAD_MB` | `80` | Per-upload limit. |
| `JOB_TTL_HOURS` | `24` | Completed-file retention period. |
| `ALLOWED_ORIGINS` | `http://localhost:8080` | Comma-separated browser origins permitted to use the API. |
| `DEMUCS_MODEL` | `htdemucs` | Demucs separation model used by this image. |
| `MUSCRIPTOR_MODEL` | `large` | MuScriptor model size: `small`, `medium`, or `large`. Prefer `small` on CPU. |
| `HF_TOKEN` | — | Required for the gated MuScriptor checkpoint after accepting its model license. |

Hardware guidance:

- **4 CPU cores / 8 GB RAM:** use `basic-pitch`, short uploads, and `htdemucs` for splitting. The CPU image deliberately does not bundle the obsolete quantized `mdx_q`/DiffQ path.
- **8 CPU cores / 16 GB RAM:** usable for all baseline workflows, but a full song can take several minutes.
- **16 GB+ RAM:** recommended before trying MuScriptor `medium`; reserve `large` for high-memory systems and long waits.

## API

- `GET /health` reports the selected engine and installed capabilities.
- `POST /api/jobs/transcribe` accepts `multipart/form-data`: `file`, `instruments` (repeatable for `melody`, exactly one for `single_part`), `engine`, and `transcription_mode` (`melody` or `single_part`). The legacy values `arrange` and `detect` are accepted for compatibility.
- `POST /api/jobs/separate` accepts `file` and separates vocals from accompaniment.
- `GET /api/jobs/{id}` returns status and output links.
- `GET /api/jobs/{id}/downloads/{name}` downloads an artifact.
- `GET /api/jobs/{id}/print/{name}` engraves a completed MIDI or MusicXML artifact to a cached, browser-printable PDF. The interface exposes this for MusicXML because it is the more faithful notation source.

## Printing a score

Each MusicXML result includes an **Open printable PDF** link. It opens a separate browser tab while the local worker engraves and caches the PDF with MuseScore. Use the browser PDF viewer's print button (or `Ctrl+P` / `Cmd+P`) for paper output. This requires no cloud service and no GPU, but increases the worker image size because MuseScore is bundled for reliable notation layout.

The worker permits a single active job by default. This protects modest home hardware and avoids multiple large models exhausting memory. Set `MAX_CONCURRENT_JOBS` deliberately only after measuring your system.

## Development without Docker

```bash
cd worker
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Serve `frontend/` with any static server, then set `window.SCOREFORGE_API_URL = "http://localhost:8000"` in `frontend/config.js`.

## Model and license references

- [Spotify Basic Pitch](https://github.com/spotify/basic-pitch) is a lightweight open-source, instrument-agnostic polyphonic AMT model under Apache-2.0.
- [MuScriptor](https://github.com/muscriptor/muscriptor) is the optional multi-instrument engine. Its code is MIT; its gated model weights are [CC BY-NC 4.0](https://huggingface.co/MuScriptor/muscriptor-large), so do not use this configuration commercially.
- [Demucs](https://github.com/adefossez/demucs) is MIT-licensed music source separation software; its normal outputs are vocals, drums, bass, and other—not the five named orchestral instruments.

The user is responsible for having the rights or permission to process uploaded audio.
