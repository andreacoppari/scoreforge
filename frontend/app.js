(() => {
  const api = (window.SCOREFORGE_API_URL || "").replace(/\/$/, "");
  const $ = (id) => document.getElementById(id);
  const fileInput = $("audio-file");
  const dropzone = $("dropzone");
  const form = $("job-form");
  let mode = "transcribe";
  let pollTimer;

  const endpoint = (path) => `${api}${path}`;
  const formatSize = (bytes) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;

  function updateFile(file) {
    if (!file) { $("file-card").hidden = true; dropzone.hidden = false; return; }
    $("file-name").textContent = file.name;
    $("file-meta").textContent = `${formatSize(file.size)} · ${mode === "pdf" ? "ready to convert" : "ready to process"}`;
    $("file-card").hidden = false;
    dropzone.hidden = true;
  }

  function setMode(nextMode) {
    mode = nextMode;
    const isPdf = mode === "pdf";
    document.querySelectorAll(".mode").forEach((button) => {
      const active = button.dataset.mode === mode;
      button.classList.toggle("active", active);
      button.setAttribute("aria-selected", String(active));
    });
    $("transcription-options").hidden = mode !== "transcribe";
    $("separation-note").hidden = mode !== "separate";
    $("pdf-note").hidden = !isPdf;
    fileInput.accept = isPdf
      ? ".musicxml,.xml,.mid,.midi,application/vnd.recordare.musicxml+xml,application/xml,audio/midi,audio/x-midi"
      : "audio/mpeg,audio/wav,audio/x-wav,audio/mp4,audio/flac,audio/ogg,audio/aac,.mp3,.wav,.m4a,.flac,.ogg,.aac";
    $("drop-title").textContent = isPdf ? "Drop a MusicXML or MIDI file here" : "Drop an audio file here";
    $("drop-detail").textContent = isPdf ? "MusicXML, XML, MIDI or MID · up to 80 MB" : "MP3, WAV, M4A, FLAC, OGG or AAC · up to 80 MB";
    $("submit-text").textContent = mode === "transcribe" ? "Create sheet music" : isPdf ? "Open printable PDF" : "Separate voice & music";
    fileInput.value = "";
    updateFile();
  }

  document.querySelectorAll(".mode").forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
  fileInput.addEventListener("change", () => updateFile(fileInput.files[0]));
  $("remove-file").addEventListener("click", () => { fileInput.value = ""; updateFile(); });
  ["dragenter", "dragover"].forEach((event) => dropzone.addEventListener(event, (e) => { e.preventDefault(); dropzone.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((event) => dropzone.addEventListener(event, (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); }));
  dropzone.addEventListener("drop", (event) => { if (event.dataTransfer.files.length) { fileInput.files = event.dataTransfer.files; updateFile(fileInput.files[0]); } });
  const transcriptionMode = () => document.querySelector('input[name="transcription-mode"]:checked')?.value;
  const instrumentInputs = () => [...document.querySelectorAll('input[name="instrument"]')];

  function syncInstrumentChoices() {
    const isSinglePart = transcriptionMode() === "single_part";
    $("all-instruments").hidden = isSinglePart;
    if (!isSinglePart) return;

    // A single-part transcription has one source-part hypothesis, not a
    // collection of unrelated instruments. Keep the first selected choice.
    let foundSelection = false;
    instrumentInputs().forEach((instrument) => {
      if (instrument.checked && !foundSelection) foundSelection = true;
      else if (instrument.checked) instrument.checked = false;
    });
  }

  $("all-instruments").addEventListener("click", () => instrumentInputs().forEach((input) => { input.checked = true; }));
  instrumentInputs().forEach((input) => input.addEventListener("change", () => {
    if (transcriptionMode() !== "single_part" || !input.checked) return;
    instrumentInputs().forEach((other) => { if (other !== input) other.checked = false; });
  }));
  document.querySelectorAll('input[name="transcription-mode"]').forEach((input) => input.addEventListener("change", () => {
    document.querySelectorAll(".transcription-mode-card").forEach((card) => card.classList.toggle("selected", card.querySelector("input").checked));
    $("instrument-helper").textContent = input.value === "melody"
      ? "Choose one or more instruments. The same detected melody can be adapted for flute, violin, or guitar, with octave changes when needed."
      : "Choose exactly one original instrument part to find. Single part needs the MuScriptor engine; it enforces that instrument's playable chord limit.";
    syncInstrumentChoices();
  }));

  function showStatus(job) {
    $("result-panel").hidden = false;
    $("job-message").textContent = job.message;
    $("job-percent").textContent = `${job.progress}%`;
    $("progress-bar").style.width = `${job.progress}%`;
    if (job.status === "failed") {
      $("result-copy").textContent = job.error || "The worker could not finish this file.";
      $("new-job").hidden = false;
    }
    if (job.status === "succeeded") {
      const transcriptionMode = document.querySelector('input[name="transcription-mode"]:checked')?.value;
      $("result-copy").textContent = mode === "transcribe"
        ? transcriptionMode === "melody"
          ? "Each download is a playable one-note melody arrangement. Review the AI draft in MuseScore or another notation editor."
          : "Each download is a simplified, playable original-instrument part. Review the AI draft in MuseScore or another notation editor."
        : "Two WAV stems are ready. Separation quality depends on the original mix.";
      const downloads = $("downloads");
      downloads.replaceChildren(...job.artifacts.map((artifact) => {
        const card = document.createElement("div");
        card.className = "download-card";
        const link = document.createElement("a");
        link.className = "download";
        link.href = endpoint(`/api/jobs/${job.id}/downloads/${encodeURIComponent(artifact.name)}`);
        link.innerHTML = `<span>${artifact.label}<small>${artifact.format}</small></span><span>↓</span>`;
        card.append(link);
        return card;
      }));
      $("new-job").hidden = false;
    }
  }

  async function poll(jobId) {
    try {
      const response = await fetch(endpoint(`/api/jobs/${jobId}`));
      const job = await response.json();
      if (!response.ok) throw new Error(job.detail || "Could not read job status.");
      showStatus(job);
      if (["queued", "running"].includes(job.status)) pollTimer = window.setTimeout(() => poll(jobId), 1500);
    } catch (error) {
      showStatus({ status: "failed", progress: 100, message: "Connection lost", error: error.message });
    }
  }

  async function convertScoreToPdf(file) {
    const preview = window.open("", "scoreforge-pdf");
    if (preview) preview.document.write("<title>ScoreForge PDF</title><p>Creating your printable PDF…</p>");
    const data = new FormData();
    data.append("file", file);
    const response = await fetch(endpoint("/api/convert/pdf"), { method: "POST", body: data });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      if (preview) preview.close();
      throw new Error(error.detail || "The worker could not convert this score.");
    }
    const pdf = await response.blob();
    const pdfUrl = URL.createObjectURL(pdf);
    if (preview) preview.location.replace(pdfUrl);
    $("result-panel").hidden = false;
    $("job-message").textContent = "Printable PDF ready";
    $("job-percent").textContent = "100%";
    $("progress-bar").style.width = "100%";
    $("result-copy").textContent = preview
      ? "Your PDF opened in a separate tab. Use that tab’s print button or Ctrl+P / Cmd+P."
      : "Your browser blocked the new tab. Use the PDF link below.";
    const link = document.createElement("a");
    link.className = "download";
    link.href = pdfUrl;
    link.target = "_blank";
    link.rel = "noopener";
    link.innerHTML = "<span>Printable score<small>PDF</small></span><span>↗</span>";
    $("downloads").replaceChildren(link);
    $("new-job").hidden = false;
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearTimeout(pollTimer);
    $("form-error").textContent = "";
    const file = fileInput.files[0];
    if (!file) { $("form-error").textContent = "Choose an audio file first."; return; }
    const instruments = [...document.querySelectorAll('input[name="instrument"]:checked')];
    if (mode === "transcribe" && !instruments.length) { $("form-error").textContent = "Choose at least one instrument."; return; }
    if (mode === "transcribe" && transcriptionMode() === "single_part" && instruments.length !== 1) {
      $("form-error").textContent = "Single part needs exactly one instrument.";
      return;
    }
    const data = new FormData();
    data.append("file", file);
    const submit = $("submit");
    submit.disabled = true;
    $("result-panel").hidden = false;
    $("downloads").replaceChildren();
    $("new-job").hidden = true;
    if (mode === "pdf") {
      $("job-message").textContent = "Engraving your score…";
      $("job-percent").textContent = "…";
      $("progress-bar").style.width = "45%";
      $("result-copy").textContent = "MuseScore is creating a local PDF.";
      try {
        await convertScoreToPdf(file);
      } catch (error) {
        showStatus({ status: "failed", progress: 100, message: "Could not create PDF", error: error.message });
      } finally { submit.disabled = false; }
      return;
    }
    if (mode === "transcribe") {
      instruments.forEach((instrument) => data.append("instruments", instrument.value));
      data.append("transcription_mode", document.querySelector('input[name="transcription-mode"]:checked').value);
    }
    showStatus({ status: "queued", progress: 2, message: "Uploading securely to your worker…" });
    try {
      const response = await fetch(endpoint(`/api/jobs/${mode === "transcribe" ? "transcribe" : "separate"}`), { method: "POST", body: data });
      const job = await response.json();
      if (!response.ok) throw new Error(job.detail || "The worker rejected this file.");
      showStatus(job);
      poll(job.id);
    } catch (error) {
      showStatus({ status: "failed", progress: 100, message: "Could not start the job", error: error.message });
    } finally { submit.disabled = false; }
  });
  $("new-job").addEventListener("click", () => { $("result-panel").hidden = true; $("new-job").hidden = true; $("downloads").replaceChildren(); fileInput.value = ""; updateFile(); window.scrollTo({ top: 0, behavior: "smooth" }); });

  fetch(endpoint("/health"))
    .then((response) => response.ok ? response.json() : Promise.reject())
    .then((health) => {
      const status = $("system-status");
      status.className = "system-status online";
      status.innerHTML = `<span></span> Worker online · ${health.device}`;
      if (health.engine === "hybrid") {
        $("engine-name").textContent = health.capabilities?.muscriptor ? "Fast Melody + MuScriptor parts" : "Basic Pitch Melody";
        $("engine-copy").textContent = health.capabilities?.muscriptor
          ? "Melody uses fast Basic Pitch. Single part uses CPU MuScriptor with instrument constraints."
          : "Melody is available. Rebuild the worker and configure HF_TOKEN for Single part.";
      }
    })
    .catch(() => { const status = $("system-status"); status.className = "system-status offline"; status.innerHTML = "<span></span> Worker unavailable"; });
})();
