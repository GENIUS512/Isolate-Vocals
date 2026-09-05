(() => {
  const API_BASE_URL = ''; // фронтенд и бэкенд теперь на одном origin (один HF Space)

  const dropzone = document.getElementById('dropzone');
  const fileInput = document.getElementById('fileInput');
  const fileRow = document.getElementById('fileRow');
  const fileNameEl = document.getElementById('fileName');
  const separateBtn = document.getElementById('separateBtn');
  const statusLine = document.getElementById('statusLine');
  const progressTrack = document.getElementById('progressTrack');
  const progressFill = document.getElementById('progressFill');
  const strips = document.getElementById('strips');
  const formatButtons = document.querySelectorAll('.format-toggle button');

  const vocalFader = document.getElementById('vocalFader');
  const instrFader = document.getElementById('instrFader');
  const vocalDb = document.getElementById('vocalDb');
  const instrDb = document.getElementById('instrDb');
  const vocalDownload = document.getElementById('vocalDownload');
  const instrDownload = document.getElementById('instrDownload');

  const playBtn = document.getElementById('playBtn');
  const timeLabel = document.getElementById('timeLabel');
  const waveCanvas = document.getElementById('waveCanvas');

  const step1 = document.getElementById('step-1');
  const step2 = document.getElementById('step-2');
  const step3 = document.getElementById('step-3');

  let selectedFile = null;
  let outputFormat = 'mp3';
  let jobId = null;

  // ---- Web Audio ----
  let audioCtx = null;
  let vocalBuffer = null, instrBuffer = null;
  let vocalSource = null, instrSource = null;
  let vocalGain = null, instrGain = null;
  let isPlaying = false;
  let startedAt = 0;
  let pausedAt = 0;
  let rafId = null;

  function setStep(active) {
    [step1, step2, step3].forEach((el, i) => {
      el.classList.remove('is-active', 'is-done');
      if (i + 1 < active) el.classList.add('is-done');
      if (i + 1 === active) el.classList.add('is-active');
    });
  }

  function setStatus(text, alert = false) {
    statusLine.textContent = text;
    statusLine.classList.toggle('is-alert', alert);
  }

  function formatTime(sec) {
    if (!isFinite(sec)) return '00:00';
    const m = Math.floor(sec / 60).toString().padStart(2, '0');
    const s = Math.floor(sec % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  function handleFile(file) {
    if (!file) return;
    selectedFile = file;
    fileRow.style.display = 'flex';
    fileNameEl.textContent = file.name;
    separateBtn.disabled = false;
    setStep(1);
    setStatus('Файл готов. Нажми «Разделить дорожки».');
  }

  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => handleFile(e.target.files[0]));

  ['dragenter', 'dragover'].forEach(evt =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add('is-drag');
    })
  );
  ['dragleave', 'drop'].forEach(evt =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove('is-drag');
    })
  );
  dropzone.addEventListener('drop', (e) => {
    const file = e.dataTransfer.files[0];
    handleFile(file);
  });

  formatButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      formatButtons.forEach(b => b.classList.remove('is-selected'));
      btn.classList.add('is-selected');
      outputFormat = btn.dataset.format;
    });
  });

  separateBtn.addEventListener('click', async () => {
    if (!selectedFile) return;
    separateBtn.disabled = true;
    progressTrack.style.display = 'block';
    progressFill.style.width = '8%';
    setStep(2);
    setStatus('Загружаем файл и запускаем разделение — на длинных треках может занять пару минут (на бесплатном CPU-хостинге медленнее, чем на видеокарте)…');

    try {
      const form = new FormData();
      form.append('file', selectedFile);

      let fakeProgress = 8;
      const progressTimer = setInterval(() => {
        fakeProgress = Math.min(fakeProgress + Math.random() * 6, 92);
        progressFill.style.width = fakeProgress + '%';
      }, 900);

      const res = await fetch(`${API_BASE_URL}/api/separate?output_format=${outputFormat}`, {
        method: 'POST',
        body: form,
      });

      clearInterval(progressTimer);

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Ошибка сервера (${res.status})`);
      }

      const data = await res.json();
      jobId = data.job_id;
      progressFill.style.width = '100%';

      const vocalUrl = `${API_BASE_URL}${data.vocal_url}`;
      const instrUrl = `${API_BASE_URL}${data.instrumental_url}`;

      vocalDownload.href = vocalUrl;
      instrDownload.href = instrUrl;
      vocalDownload.download = `vocal.${outputFormat}`;
      instrDownload.download = `instrumental.${outputFormat}`;

      setStatus('Готово. Загружаем дорожки в плеер…');
      await loadIntoPlayer(vocalUrl, instrUrl);

      setStep(3);
      strips.classList.add('is-ready');
      setStatus('Дорожки разделены и готовы к прослушиванию.');
    } catch (err) {
      console.error(err);
      setStatus(`Не получилось разделить трек: ${err.message}`, true);
      separateBtn.disabled = false;
    }
  });

  async function loadIntoPlayer(vocalUrl, instrUrl) {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();

    const [vocalBuf, instrBuf] = await Promise.all([
      fetchAndDecode(vocalUrl),
      fetchAndDecode(instrUrl),
    ]);
    vocalBuffer = vocalBuf;
    instrBuffer = instrBuf;

    vocalGain = audioCtx.createGain();
    instrGain = audioCtx.createGain();
    vocalGain.connect(audioCtx.destination);
    instrGain.connect(audioCtx.destination);
    applyFaderGain(vocalFader, vocalGain, vocalDb);
    applyFaderGain(instrFader, instrGain, instrDb);

    drawWaveform(vocalBuffer);
    timeLabel.textContent = `00:00 / ${formatTime(vocalBuffer.duration)}`;
  }

  async function fetchAndDecode(url) {
    const res = await fetch(url);
    const arr = await res.arrayBuffer();
    return audioCtx.decodeAudioData(arr);
  }

  function faderToGain(rawValue) {
    const v = rawValue / 100;
    return Math.pow(v, 1.3);
  }

  function gainToDb(gain) {
    if (gain <= 0.0001) return -Infinity;
    return 20 * Math.log10(gain);
  }

  function applyFaderGain(faderEl, gainNode, labelEl) {
    const raw = Number(faderEl.value);
    const g = faderToGain(raw);
    if (gainNode) gainNode.gain.value = g;
    const db = gainToDb(g);
    labelEl.textContent = db === -Infinity ? '−∞ dB' : `${db >= 0 ? '+' : ''}${db.toFixed(1)} dB`;
  }

  vocalFader.addEventListener('input', () => applyFaderGain(vocalFader, vocalGain, vocalDb));
  instrFader.addEventListener('input', () => applyFaderGain(instrFader, instrGain, instrDb));

  function stopSources() {
    [vocalSource, instrSource].forEach(s => { try { s && s.stop(); } catch (e) {} });
    vocalSource = null;
    instrSource = null;
  }

  function startPlayback(offset) {
    stopSources();
    vocalSource = audioCtx.createBufferSource();
    vocalSource.buffer = vocalBuffer;
    vocalSource.connect(vocalGain);

    instrSource = audioCtx.createBufferSource();
    instrSource.buffer = instrBuffer;
    instrSource.connect(instrGain);

    const when = audioCtx.currentTime + 0.05;
    vocalSource.start(when, offset);
    instrSource.start(when, offset);
    startedAt = when - offset;

    vocalSource.onended = () => {
      if (isPlaying) {
        isPlaying = false;
        playBtn.textContent = '▶';
        pausedAt = 0;
        cancelAnimationFrame(rafId);
        timeLabel.textContent = `00:00 / ${formatTime(vocalBuffer.duration)}`;
      }
    };
  }

  function tick() {
    if (!isPlaying) return;
    const elapsed = audioCtx.currentTime - startedAt;
    timeLabel.textContent = `${formatTime(elapsed)} / ${formatTime(vocalBuffer.duration)}`;
    if (elapsed >= vocalBuffer.duration) {
      isPlaying = false;
      playBtn.textContent = '▶';
      pausedAt = 0;
      return;
    }
    rafId = requestAnimationFrame(tick);
  }

  playBtn.addEventListener('click', () => {
    if (!vocalBuffer || !instrBuffer) return;
    if (audioCtx.state === 'suspended') audioCtx.resume();

    if (isPlaying) {
      pausedAt = audioCtx.currentTime - startedAt;
      stopSources();
      isPlaying = false;
      playBtn.textContent = '▶';
      cancelAnimationFrame(rafId);
    } else {
      startPlayback(pausedAt);
      isPlaying = true;
      playBtn.textContent = '❚❚';
      tick();
    }
  });

  function drawWaveform(buffer) {
    const ctx = waveCanvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = waveCanvas.getBoundingClientRect();
    waveCanvas.width = rect.width * dpr;
    waveCanvas.height = rect.height * dpr;
    ctx.scale(dpr, dpr);

    const data = buffer.getChannelData(0);
    const width = rect.width;
    const height = rect.height;
    const step = Math.ceil(data.length / width);
    const amp = height / 2;

    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = 'rgba(201,138,62,0.55)';

    for (let i = 0; i < width; i++) {
      let min = 1.0, max = -1.0;
      for (let j = 0; j < step; j++) {
        const datum = data[(i * step) + j] || 0;
        if (datum < min) min = datum;
        if (datum > max) max = datum;
      }
      ctx.fillRect(i, amp + min * amp, 1, Math.max(1, (max - min) * amp));
    }
  }
})();
