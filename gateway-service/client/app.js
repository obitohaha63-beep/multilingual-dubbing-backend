/**
 * GPS Audio Guide - Client Core Flow & State Machine (app.js) - Tuần 5
 * 
 * Đáp ứng đầy đủ các quy tắc:
 * 1. watchPosition với success/error callback.
 * 2. Xin quyền khi bấm Bắt đầu, clearWatch khi Dừng.
 * 3. Gửi detect đầu tiên ngay, gửi tiếp khi di chuyển >= 20m HOẶC đã qua 10s.
 * 4. Throttle tối thiểu 3s giữa hai request. In-flight guard: tối đa 1 request đang chạy.
 * 5. Timer 1s chạy định kỳ khi đứng yên. Không dùng fix cũ quá 30s.
 * 6. Timeout ngân sách: AbortController 40s.
 * 7. State Machine âm thanh:
 *    - activeLocationId & activeLocationCoords ({ latitude, longitude, radius })
 *    - playedKeys: Set lưu `${location_id}:${requested_lang}` cho một lần ghé.
 *    - Trạng thái narration: 'idle' | 'pending' | 'playing' | 'played' | 'error'.
 *    - Chỉ ghi played khi âm thanh thực sự phát thành công hoặc speech onstart.
 *    - Khi lỗi: giữ nút thử lại thủ công, không retry tự động ở mỗi GPS tick.
 *    - Đứng yên lâu không tự phát lại nếu đã played key đó.
 *    - Đổi ngôn ngữ: phát ngôn ngữ mới 1 lần; đổi lại ngôn ngữ cũ đã nghe không tự lặp.
 *    - Nút Phát lại: thao tác chủ động, phát lại ngay cả khi đã played.
 * 8. Rời địa điểm (Hysteresis Buffer):
 *    - Chỉ coi đã rời sau 2 fix hợp lệ cách nhau >= 10s và khoảng cách > radius + 30m.
 *    - Sau khi rời rõ ràng mới reset activeLocation và playedKeys.
 *    - Không reset từ một response detected=false hoặc lỗi mạng.
 *    - accuracy > 50m: hiển thị GPS chưa ổn định, không tạo enter/exit mới.
 *    - Chuyển A -> B: dừng A rồi phát B.
 * 9. Sequence Token:
 *    - Tăng sequenceToken mỗi request, bỏ qua response cũ nếu sequence token hoặc ngôn ngữ đã thay đổi.
 * 10. Fallback:
 *    - Ưu tiên file MP3 cùng origin (/audio/<filename>).
 *    - Fallback sang SpeechSynthesis khi B trả text_ready hoặc file MP3 404/lỗi.
 *    - Autoplay blocked (NotAllowedError): hiện nút "Cho phép phát", không đánh dấu đã played.
 */

// ===========================================================================
// Core Logic Class: TourStateManager (Dùng được cho cả Browser và Node:test)
// ===========================================================================

function calculateDistanceM(lat1, lon1, lat2, lon2) {
  const R = 6371000; // Radius of the Earth in meters
  const toRad = deg => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
}

class TourStateManager {
  constructor() {
    this.activeLocationId = null;
    this.activeLocationCoords = null; // { latitude, longitude, radius }
    this.playedKeys = new Set();      // "${location_id}:${lang}"
    this.narrationState = "idle";     // 'idle' | 'pending' | 'playing' | 'played' | 'error'
    this.exitCandidateFix = null;     // { latitude, longitude, timestamp }
    this.sequenceToken = 0;
    this.currentLang = "vi";
    this.lastFix = null;              // { latitude, longitude, accuracy, timestamp }
    this.lastSentFix = null;          // { latitude, longitude, timestamp }
    this.lastRequestTime = 0;
    this.isRequestInFlight = false;
    this.generationPending = false;
  }

  resetAll() {
    this.generationPending = false;
    this.activeLocationId = null;
    this.activeLocationCoords = null;
    this.playedKeys.clear();
    this.narrationState = "idle";
    this.exitCandidateFix = null;
    this.lastFix = null;
    this.lastSentFix = null;
    this.lastRequestTime = 0;
    this.isRequestInFlight = false;
  }

  setLanguage(newLang) {
    if (this.currentLang !== newLang) {
      this.currentLang = newLang;
      this.generationPending = false;
      // Tăng sequence token để bỏ qua các response đang bay về bằng ngôn ngữ cũ
      this.sequenceToken++;
      return true;
    }
    return false;
  }

  handleGpsFix(coords, now = Date.now()) {
    // 1. Kiểm tra độ chính xác GPS
    if (coords.accuracy > 50) {
      return {
        accepted: false,
        reason: "accuracy_unstable",
        accuracy: coords.accuracy,
      };
    }

    const fix = {
      latitude: coords.latitude,
      longitude: coords.longitude,
      accuracy: coords.accuracy,
      timestamp: now,
    };

    // 2. Kiểm tra điều kiện rời địa điểm hiện tại (nếu đang có activeLocationCoords)
    let exitTriggered = false;
    if (this.activeLocationCoords) {
      const distToActive = calculateDistanceM(
        this.activeLocationCoords.latitude,
        this.activeLocationCoords.longitude,
        fix.latitude,
        fix.longitude
      );
      const exitThreshold = (this.activeLocationCoords.radius || 80) + 30; // radius + 30m

      if (distToActive > exitThreshold) {
        if (!this.exitCandidateFix) {
          // Fix đầu tiên vượt ngưỡng rời -> ghi nhận candidate
          this.exitCandidateFix = {
            latitude: fix.latitude,
            longitude: fix.longitude,
            timestamp: now,
          };
        } else {
          // Fix thứ hai vượt ngưỡng rời -> kiểm tra khoảng thời gian >= 10s
          const timeDiff = now - this.exitCandidateFix.timestamp;
          if (timeDiff >= 10000) {
            // Xác nhận ĐÃ RỜI ĐỊA ĐIỂM
            this.activeLocationId = null;
            this.activeLocationCoords = null;
            this.playedKeys.clear();
            this.exitCandidateFix = null;
            this.narrationState = "idle";
            exitTriggered = true;
          }
        }
      } else {
        // Đang ở trong bán kính hoặc quay trở lại trong ngưỡng -> xóa candidate để chống jitter rìa
        this.exitCandidateFix = null;
      }
    }

    this.lastFix = fix;
    return {
      accepted: true,
      fix: this.lastFix,
      exitTriggered,
    };
  }

  shouldSendDetect(now = Date.now()) {
    if (!this.lastFix) return false;

    // Không dùng fix cũ quá 30s
    if (now - this.lastFix.timestamp > 30000) return false;

    // Tối đa 1 request đang chạy (in-flight)
    if (this.isRequestInFlight) return false;

    // Throttle 3s giữa các request
    if (now - this.lastRequestTime < 3000) return false;

    // Gửi lần đầu ngay lập tức
    if (!this.lastSentFix) return true;

    // Gửi tiếp khi di chuyển >= 20m HOẶC đã qua 10s
    const dist = calculateDistanceM(
      this.lastSentFix.latitude,
      this.lastSentFix.longitude,
      this.lastFix.latitude,
      this.lastFix.longitude
    );
    const timeSinceLastSent = now - this.lastSentFix.timestamp;

    return dist >= 20 || timeSinceLastSent >= (this.generationPending ? 3000 : 10000);
  }

  prepareRequest(now = Date.now()) {
    this.isRequestInFlight = true;
    this.lastRequestTime = now;
    this.sequenceToken++;
    return {
      token: this.sequenceToken,
      lang: this.currentLang,
      fix: this.lastFix,
    };
  }

  finalizeRequest(fix, now = Date.now()) {
    this.isRequestInFlight = false;
    this.lastSentFix = {
      latitude: fix.latitude,
      longitude: fix.longitude,
      timestamp: now,
    };
  }

  handleDetectResponse(response, token, lang) {
    // 1. Kiểm tra stale response: sequence token hoặc ngôn ngữ đã đổi
    if (token !== this.sequenceToken || lang !== this.currentLang) {
      return { action: "ignore_stale", reason: "token_or_lang_mismatch" };
    }

    // 2. Không được reset playedKeys từ một response detected=false hoặc lỗi mạng!
    if (!response || !response.detected || !response.location) {
      return {
        action: "no_activation",
        reason: response ? response.reason : "network_error",
        data: response,
      };
    }

    const loc = response.location;
    const locId = loc.id;
    let switchedLocation = false;

    // 3. Xử lý chuyển đổi địa điểm (A -> B)
    if (this.activeLocationId !== null && this.activeLocationId !== locId) {
      // Đã chuyển sang địa điểm B khác địa điểm A
      this.activeLocationId = locId;
      this.activeLocationCoords = {
        latitude: loc.latitude,
        longitude: loc.longitude,
        radius: loc.radius || 80,
      };
      this.playedKeys.clear();
      this.exitCandidateFix = null;
      this.narrationState = "idle";
      switchedLocation = true;
    } else if (this.activeLocationId === null) {
      // Lần đầu bước vào địa điểm
      this.activeLocationId = locId;
      this.activeLocationCoords = {
        latitude: loc.latitude,
        longitude: loc.longitude,
        radius: loc.radius || 80,
      };
      this.exitCandidateFix = null;
    }

    // 4. Kiểm tra playedKeys cho cặp (location_id, requested_lang)
    this.generationPending = ["pending", "processing"].includes(response.narration_status);
    if (this.generationPending) {
      return { action: "wait_generation", switchedLocation, data: response };
    }
    if (response.narration_status === "error") {
      return { action: "generation_error", switchedLocation, data: response };
    }
    const key = `${locId}:${response.requested_lang || lang}`;
    if (this.playedKeys.has(key)) {
      return {
        action: "skip_already_played",
        key,
        locationId: locId,
        switchedLocation,
        data: response,
      };
    }

    // 5. Kiểm tra trạng thái phát: không chồng lấp nếu đang pending/playing
    if (this.narrationState === "pending" || this.narrationState === "playing") {
      return {
        action: "skip_busy",
        state: this.narrationState,
        key,
        data: response,
      };
    }

    // Đạt điều kiện tự động phát thuyết minh
    this.narrationState = "pending";
    return {
      action: "play",
      key,
      locationId: locId,
      switchedLocation,
      data: response,
    };
  }

  markPlaybackStarted(key) {
    this.narrationState = "playing";
    this.playedKeys.add(key);
  }

  markPlaybackEnded(key) {
    this.narrationState = "played";
  }

  markPlaybackError(reason) {
    this.narrationState = "error";
    // Không ghi vào playedKeys để người dùng có thể thử lại thủ công
  }

  markAutoplayBlocked() {
    this.narrationState = "idle";
    // Không ghi vào playedKeys khi bị chặn bởi chính sách Autoplay của trình duyệt
  }
}

// ===========================================================================
// Browser Runtime Initialization
// ===========================================================================

(() => {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return; // Môi trường Node.js (test runner)
  }

  // --- Analytics Client Identifier & Heartbeat (Ẩn danh, không lưu IP/GPS) ---
  function getOrCreateClientId() {
    let cid = "";
    try {
      cid = localStorage.getItem("gps_anon_cid") || "";
      if (!cid || cid.length < 16) {
        cid = "cid_" + Math.random().toString(36).substring(2, 15) + "_" + Date.now().toString(36);
        localStorage.setItem("gps_anon_cid", cid);
      }
    } catch (e) {
      cid = "cid_" + Math.random().toString(36).substring(2, 15) + "_" + Date.now().toString(36);
    }
    return cid;
  }

  const anonClientId = getOrCreateClientId();

  // Gửi lượt xem trang duy nhất (Page View) khi load giao diện /app/
  try {
    fetch("/analytics/pageview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: anonClientId, path: window.location.pathname }),
    }).catch(() => {});
  } catch (e) {}

  // Gửi heartbeat định kỳ mỗi 30s để ước tính khách đang online (90s window)
  function sendHeartbeat() {
    try {
      fetch("/analytics/heartbeat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: anonClientId }),
      }).catch(() => {});
    } catch (e) {}
  }

  sendHeartbeat();
  setInterval(sendHeartbeat, 30000);

  const manager = new TourStateManager();

  // Watch & Timer IDs
  let watchId = null;
  let timerId = null;

  // Singleton Audio Element
  const audioElement = new Audio();
  let currentNarrationPayload = null; // { audio_url, translated_text, lang, key }
  let isMuted = false;
  let activeAudioSource = null;       // null | 'mp3' | 'speech'
  let availableVoices = [];

  // DOM Elements
  const langSelect = document.getElementById("lang-select");
  const ui = window.GuideI18n.bind(document);
  const t = ui.t;
  ui.apply(langSelect.value);
  let lastCandidates = [];
  const btnStart = document.getElementById("btn-start");
  const btnStop = document.getElementById("btn-stop");
  const gpsStatus = document.getElementById("gps-status");
  const apiStatus = document.getElementById("api-status");
  const coordsDisplay = document.getElementById("coords-display");
  const accuracyDisplay = document.getElementById("accuracy-display");
  const timestampDisplay = document.getElementById("timestamp-display");

  const noDetectBox = document.getElementById("no-detect-box");
  const detectedContent = document.getElementById("detected-content");
  const placeName = document.getElementById("place-name");
  const activationBadge = document.getElementById("activation-badge");
  const placeDistance = document.getElementById("place-distance");
  const placeSource = document.getElementById("place-source");
  const placeUrl = document.getElementById("place-url");
  const sourceLinkRow = document.getElementById("source-link-row");
  const narrationStatus = document.getElementById("narration-status");
  const placeDesc = document.getElementById("place-desc");
  const candidatesList = document.getElementById("candidates-list");

  // Audio Player UI
  const playerStatusBadge = document.getElementById("player-status-badge");
  const playerText = document.getElementById("player-text");
  const playerSource = document.getElementById("player-source");
  const autoplayPermissionBanner = document.getElementById("autoplay-permission-banner");
  const btnAllowPlay = document.getElementById("btn-allow-play");
  const audioRetryBanner = document.getElementById("audio-retry-banner");
  const audioErrorMsg = document.getElementById("audio-error-msg");
  const btnRetryPlay = document.getElementById("btn-retry-play");
  const btnAudioStop = document.getElementById("btn-audio-stop");
  const btnAudioMute = document.getElementById("btn-audio-mute");
  const btnAudioReplay = document.getElementById("btn-audio-replay");

  // Timeline & Seek Controls (Tuần 5: Chỉnh thời lượng khi thuyết minh)
  const audioSeekBar = document.getElementById("audio-seek-bar");
  const audioCurrentTime = document.getElementById("audio-current-time");
  const audioDuration = document.getElementById("audio-duration");
  const btnAudioRewind = document.getElementById("btn-audio-rewind");
  const btnAudioPlayPause = document.getElementById("btn-audio-play-pause");
  const btnAudioForward = document.getElementById("btn-audio-forward");
  let isSeeking = false;

  function formatAudioTime(seconds) {
    if (!seconds || isNaN(seconds) || !isFinite(seconds) || seconds < 0) {
      return "00:00";
    }
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m < 10 ? "0" : ""}${m}:${s < 10 ? "0" : ""}${s}`;
  }

  // UI Helpers
  function setGpsStatus(text, badgeClass) {
    if (gpsStatus) {
      ui.setText(gpsStatus, text);
      gpsStatus.className = `status-value badge ${badgeClass}`;
    }
  }

  function setApiStatus(text, badgeClass) {
    if (apiStatus) {
      ui.setText(apiStatus, text);
      apiStatus.className = `status-value badge ${badgeClass}`;
    }
  }

  function setPlayerStatus(text, badgeClass) {
    if (playerStatusBadge) {
      ui.setText(playerStatusBadge, text);
      playerStatusBadge.className = `status-value badge ${badgeClass}`;
    }
  }

  function showPermissionBanner() {
    if (autoplayPermissionBanner) autoplayPermissionBanner.classList.remove("hidden");
  }

  function hidePermissionBanner() {
    if (autoplayPermissionBanner) autoplayPermissionBanner.classList.add("hidden");
  }

  function showRetryBanner(msg) {
    if (audioErrorMsg) ui.setText(audioErrorMsg, msg || "Không thể phát âm thanh.");
    if (audioRetryBanner) audioRetryBanner.classList.remove("hidden");
  }

  function hideRetryBanner() {
    if (audioRetryBanner) audioRetryBanner.classList.add("hidden");
  }

  // Web Speech Helpers
  function loadVoices() {
    if ("speechSynthesis" in window) {
      availableVoices = window.speechSynthesis.getVoices() || [];
    }
  }

  if ("speechSynthesis" in window) {
    window.speechSynthesis.onvoiceschanged = loadVoices;
    loadVoices();
  }

  function mapLangToBcp47(lang) {
    const l = (lang || "").toLowerCase().trim();
    if (l === "vi") return "vi-VN";
    if (l === "en") return "en-US";
    if (l === "ja") return "ja-JP";
    if (l === "fr") return "fr-FR";
    if (l === "es") return "es-ES";
    return lang;
  }

  function findMatchingVoice(lang) {
    loadVoices();
    if (!availableVoices || availableVoices.length === 0) return null;
    const bcp47 = mapLangToBcp47(lang).toLowerCase();
    const shortLang = (lang || "").toLowerCase().split("-")[0];

    let matched = availableVoices.find(v => {
      const vLang = (v.lang || "").toLowerCase().replace("_", "-");
      return vLang === bcp47;
    });
    if (matched) return matched;

    matched = availableVoices.find(v => {
      const vLang = (v.lang || "").toLowerCase().replace("_", "-");
      return vLang.startsWith(shortLang);
    });
    return matched || null;
  }

  function stopCurrentAudio() {
    if (audioElement) {
      audioElement.pause();
      audioElement.currentTime = 0;
      audioElement.removeAttribute("src");
    }
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    activeAudioSource = null;
    hidePermissionBanner();
    setPlayerStatus("Đã dừng phát", "badge-neutral");
    if (btnAudioStop) btnAudioStop.disabled = true;

    // Reset Timeline & Seek Bar
    if (audioSeekBar) {
      audioSeekBar.value = 0;
      audioSeekBar.disabled = true;
    }
    if (audioCurrentTime) audioCurrentTime.textContent = "00:00";
    if (audioDuration) audioDuration.textContent = "00:00";
    if (btnAudioRewind) btnAudioRewind.disabled = true;
    if (btnAudioForward) btnAudioForward.disabled = true;
    if (btnAudioPlayPause) {
      btnAudioPlayPause.disabled = true;
      btnAudioPlayPause.textContent = "⏸ Tạm dừng";
    }
  }

  function fallbackToSpeech(text, lang, key, reason) {
    console.info(`[SpeechSynthesis Fallback] Lý do: ${reason}`);
    if (audioElement) {
      audioElement.pause();
      audioElement.currentTime = 0;
      audioElement.removeAttribute("src");
    }

    if (!("speechSynthesis" in window)) {
      manager.markPlaybackError("browser_speech_not_supported");
      setPlayerStatus("Trình duyệt không hỗ trợ Web Speech", "badge-danger");
      if (playerSource) ui.setText(playerSource, "Không hỗ trợ Web Speech API");
      showRetryBanner("Trình duyệt không hỗ trợ Web Speech API.");
      return;
    }

    loadVoices();
    const voice = findMatchingVoice(lang);
    if (!voice) {
      manager.markPlaybackError("no_browser_voice_found");
      if (playerSource) ui.setText(playerSource, "Chỉ hiển thị văn bản (Chưa có gói giọng đọc)");
      setPlayerStatus("Không có voice phù hợp trong trình duyệt", "badge-warning");
      showRetryBanner(`Trình duyệt không có gói giọng đọc cho ngôn ngữ '${lang}'. Bạn vẫn có thể đọc bài bên trên.`);
      if (btnAudioStop) btnAudioStop.disabled = true;
      return;
    }

    if (playerSource) {
      ui.setText(playerSource, `Giọng đọc trình duyệt (${voice.name || voice.lang})`);
    }
    setPlayerStatus("Đang phát giọng đọc trình duyệt", "badge-info");
    hideRetryBanner();

    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = mapLangToBcp47(lang);
    utter.voice = voice;

    utter.onstart = () => {
      activeAudioSource = "speech";
      manager.markPlaybackStarted(key);
      if (btnAudioStop) btnAudioStop.disabled = false;
    };
    utter.onend = () => {
      activeAudioSource = null;
      manager.markPlaybackEnded(key);
      setPlayerStatus("Đã phát xong", "badge-neutral");
      if (btnAudioStop) btnAudioStop.disabled = true;
    };
    utter.onerror = (ev) => {
      activeAudioSource = null;
      manager.markPlaybackError(ev.error || "speech_error");
      console.error("SpeechSynthesis error:", ev);
      setPlayerStatus("Lỗi phát giọng đọc trình duyệt", "badge-danger");
      showRetryBanner("Lỗi phát giọng đọc: " + (ev.error || "Không rõ nguyên nhân"));
      if (btnAudioStop) btnAudioStop.disabled = true;
    };

    window.speechSynthesis.speak(utter);
  }

  function playNarration({ audio_url, translated_text, lang, key }, isManualReplay = false) {
    stopCurrentAudio();

    currentNarrationPayload = { audio_url, translated_text, lang, key };

    if (playerText) {
      playerText.textContent = translated_text || t("(Không có văn bản)");
    }
    if (btnAudioReplay) btnAudioReplay.disabled = false;
    hidePermissionBanner();
    hideRetryBanner();

    if (audio_url) {
      // 1. Phát qua MP3 từ Gateway (same origin)
      if (playerSource) ui.setText(playerSource, "Bản thuyết minh MP3");
      audioElement.src = audio_url;
      audioElement.muted = isMuted;

      audioElement.onended = () => {
        activeAudioSource = null;
        manager.markPlaybackEnded(key);
        setPlayerStatus("Đã phát xong", "badge-neutral");
        if (btnAudioStop) btnAudioStop.disabled = true;
        if (btnAudioPlayPause) btnAudioPlayPause.disabled = true;
        if (audioSeekBar) audioSeekBar.value = 100;
      };

      audioElement.onerror = () => {
        console.warn("[audioElement onerror] File MP3 bị lỗi hoặc 404, fallback sang SpeechSynthesis.");
        fallbackToSpeech(translated_text, lang, key, "File MP3 lỗi hoặc 404 từ Gateway");
      };

      audioElement.ontimeupdate = () => {
        if (!isSeeking && audioElement.duration && isFinite(audioElement.duration)) {
          const cur = audioElement.currentTime;
          const dur = audioElement.duration;
          if (audioCurrentTime) audioCurrentTime.textContent = formatAudioTime(cur);
          if (audioSeekBar) audioSeekBar.value = ((cur / dur) * 100).toFixed(1);
        }
      };

      audioElement.onloadedmetadata = () => {
        if (audioElement.duration && isFinite(audioElement.duration)) {
          if (audioDuration) audioDuration.textContent = formatAudioTime(audioElement.duration);
          if (audioSeekBar) audioSeekBar.disabled = false;
          if (btnAudioRewind) btnAudioRewind.disabled = false;
          if (btnAudioForward) btnAudioForward.disabled = false;
          if (btnAudioPlayPause) {
            btnAudioPlayPause.disabled = false;
            btnAudioPlayPause.textContent = "⏸ Tạm dừng";
          }
        }
      };

      const playPromise = audioElement.play();
      if (playPromise !== undefined) {
        playPromise
          .then(() => {
            activeAudioSource = "mp3";
            manager.markPlaybackStarted(key);
            setPlayerStatus("Đang phát MP3", "badge-success");
            if (btnAudioStop) btnAudioStop.disabled = false;
            hidePermissionBanner();
            hideRetryBanner();
          })
          .catch((err) => {
            if (err.name === "NotAllowedError") {
              // Bị chặn bởi Autoplay Policy của trình duyệt
              manager.markAutoplayBlocked();
              activeAudioSource = null;
              setPlayerStatus("Trình duyệt chặn tự động phát (Chạm để nghe)", "badge-warning");
              showPermissionBanner();
              if (btnAudioStop) btnAudioStop.disabled = true;
            } else {
              // Lỗi network hoặc decode -> fallback SpeechSynthesis
              fallbackToSpeech(translated_text, lang, key, err.message);
            }
          });
      }
    } else if (translated_text) {
      // 2. Không có file MP3 nhưng có text_ready -> Web SpeechSynthesis
      fallbackToSpeech(translated_text, lang, key, "Không có audio_url, dùng SpeechSynthesis");
    } else {
      manager.markPlaybackError("no_audio_or_text");
      setPlayerStatus("Không có nội dung âm thanh hoặc văn bản", "badge-neutral");
      if (playerSource) ui.setText(playerSource, "--");
    }
  }

  // Geolocation Handlers
  function onGpsSuccess(position) {
    const coords = position.coords;
    const now = Date.now();

    const res = manager.handleGpsFix(coords, now);

    if (!res.accepted) {
      setGpsStatus(`GPS chưa ổn định (±${Math.round(coords.accuracy)}m > 50m)`, "badge-warning");
      ui.setText(accuracyDisplay, `±${Math.round(coords.accuracy)}m (Kém)`);
      return;
    }

    if (res.exitTriggered) {
      setPlayerStatus("Đã rời khỏi địa điểm", "badge-neutral");
      stopCurrentAudio();
    }

    setGpsStatus("Đang nhận GPS", "badge-success");
    coordsDisplay.textContent = `${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}`;
    ui.setText(accuracyDisplay, `±${Math.round(coords.accuracy)}m`);
    timestampDisplay.textContent = new Date(now).toLocaleTimeString({
      vi: "vi-VN",
      en: "en-US",
      ja: "ja-JP",
      fr: "fr-FR",
      es: "es-ES",
    }[langSelect.value] || "vi-VN");

    checkAndSendDetect();
  }

  function onGpsError(err) {
    let msg = "Lỗi không xác định";
    switch (err.code) {
      case err.PERMISSION_DENIED:
        msg = "Quyền GPS bị từ chối (Denied)";
        break;
      case err.POSITION_UNAVAILABLE:
        msg = "Vị trí không khả dụng (Unavailable)";
        break;
      case err.TIMEOUT:
        msg = "Hết thời gian chờ GPS (Timeout)";
        break;
    }
    setGpsStatus(msg, "badge-danger");
    coordsDisplay.textContent = "--";
    ui.setText(accuracyDisplay, "--");
  }

  async function checkAndSendDetect() {
    const now = Date.now();
    if (!manager.shouldSendDetect(now)) return;

    const reqContext = manager.prepareRequest();
    setApiStatus("Đang gửi...", "badge-info");

    const payload = {
      latitude: reqContext.fix.latitude,
      longitude: reqContext.fix.longitude,
      language: reqContext.lang,
      accuracy_m: reqContext.fix.accuracy,
    };

    // Timeout ngân sách: Client abort 40s
    const abortController = new AbortController();
    const abortTimer = setTimeout(() => abortController.abort(), 40000);

    try {
      const response = await fetch("/locations/detect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: abortController.signal,
      });

      clearTimeout(abortTimer);

      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${response.status}`);
      }

      const data = await response.json();
      manager.finalizeRequest(reqContext.fix, Date.now());
      setApiStatus("Đã phản hồi", "badge-success");

      // Xử lý response qua State Manager
      const evalResult = manager.handleDetectResponse(data, reqContext.token, reqContext.lang);

      if (evalResult.action === "ignore_stale") {
        console.warn("[Stale Response Ignored] Bỏ qua kết quả cũ do đổi ngôn ngữ hoặc đã có request mới.");
        return;
      }

      renderDetectResult(data);
      if (evalResult.switchedLocation && evalResult.action !== "play") {
        stopCurrentAudio();
        currentNarrationPayload = null;
        if (btnAudioReplay) btnAudioReplay.disabled = true;
        if (playerText) playerText.textContent = t("Đang chờ bản thuyết minh mới...");
      }

      if (evalResult.action === "play") {
        if (evalResult.switchedLocation) {
          stopCurrentAudio();
        }
        playNarration({
          audio_url: data.audio_url,
          translated_text: data.translated_text || data.location.description_source || data.location.name,
          lang: data.lang || reqContext.lang,
          key: evalResult.key,
        });
      } else if (evalResult.action === "skip_already_played") {
        console.info(`[Audio Guard] Địa điểm ${evalResult.locationId} với ngôn ngữ '${data.requested_lang}' đã được phát trong lần ghé này.`);
      }
    } catch (err) {
      clearTimeout(abortTimer);
      manager.isRequestInFlight = false;
      const isAbort = err.name === "AbortError";
      console.error("Detect API error:", err);
      setApiStatus(isAbort ? "Lỗi: Timeout 40s" : `Lỗi: ${err.message}`, "badge-danger");
    }
  }

  function renderDetectResult(data) {
    renderCandidates(data.candidates);

    if (data.detected && data.location) {
      noDetectBox.classList.add("hidden");
      detectedContent.classList.remove("hidden");

      const loc = data.location;
      placeName.textContent = loc.name || t("Địa điểm không tên");
      ui.setText(activationBadge, "Trong bán kính kích hoạt");
      activationBadge.className = "badge badge-success";

      ui.setText(placeDistance, data.distance_m !== null ? `${data.distance_m} m` : "--");
      placeSource.textContent = loc.source === "wikimedia" ? "Wikipedia (Wikimedia)" : (loc.source || "Manual");

      if (data.source_url) {
        sourceLinkRow.style.display = "flex";
        placeUrl.href = data.source_url;
        placeUrl.textContent = data.source_url;
      } else {
        sourceLinkRow.style.display = "none";
      }

      let statusLabel = data.narration_status || "--";
      if (data.narration_status === "audio_ready") statusLabel = "Sẵn sàng file âm thanh MP3";
      if (["pending", "processing"].includes(data.narration_status)) statusLabel = "Đang biên soạn và tạo âm thanh, vui lòng chờ...";
      if (data.narration_status === "text_ready") statusLabel = "Sẵn sàng văn bản (Dùng Web Speech)";
      if (data.narration_status === "error") statusLabel = `Lỗi tạo âm thanh (${data.reason || "Xem chi tiết"})`;
      ui.setText(narrationStatus, statusLabel);

      placeDesc.textContent = data.translated_text || loc.description_source || loc.description_vi || t("(Không có mô tả)");
    } else if (data.reason === "outside_activation_radius" && data.location) {
      noDetectBox.classList.add("hidden");
      detectedContent.classList.remove("hidden");

      const loc = data.location;
      placeName.textContent = loc.name || t("Địa điểm lân cận");
      ui.setText(activationBadge, "Ngoài bán kính kích hoạt");
      activationBadge.className = "badge badge-warning";

      ui.setText(placeDistance, data.distance_m !== null ? `${data.distance_m} m (Cần lại gần hơn)` : "--");
      placeSource.textContent = loc.source === "wikimedia" ? "Wikipedia (Wikimedia)" : (loc.source || "Manual");

      if (data.source_url) {
        sourceLinkRow.style.display = "flex";
        placeUrl.href = data.source_url;
        placeUrl.textContent = data.source_url;
      } else {
        sourceLinkRow.style.display = "none";
      }

      ui.setText(narrationStatus, "Không kích hoạt (Ngoài bán kính)");
      placeDesc.textContent = loc.description_source || loc.description_vi || t("(Không có mô tả)");
    } else {
      detectedContent.classList.add("hidden");
      noDetectBox.classList.remove("hidden");

      if (data.reason === "upstream_unavailable") {
        ui.setText(noDetectBox, "Máy chủ nội bộ (Content Service) tạm thời gián đoạn kết nối.");
      } else {
        ui.setText(noDetectBox, "Không tìm thấy địa điểm nào trong phạm vi 500m.");
      }
    }
  }

  function renderCandidates(candidates) {
    lastCandidates = candidates || [];
    if (!candidates || candidates.length === 0) {
      candidatesList.innerHTML = `<p class="empty-note">${t("Chưa có dữ liệu ứng viên trong 500m.")}</p>`;
      return;
    }

    candidatesList.innerHTML = candidates.map(c => `
      <div class="candidate-item">
        <strong>${c.name}</strong> (${c.distance_m ? Math.round(c.distance_m) + "m" : "--"})
        <br><small>${t("Bán kính kích hoạt:")} ${c.radius || 80}m | ${t("Nguồn:")} ${c.source || "manual"}</small>
      </div>
    `).join("");
  }

  function startTracking() {
    if (!("geolocation" in navigator)) {
      setGpsStatus("Trình duyệt không hỗ trợ Geolocation", "badge-danger");
      return;
    }

    setGpsStatus("Đang xin quyền GPS...", "badge-warning");
    btnStart.disabled = true;
    btnStop.disabled = false;

    manager.setLanguage(langSelect.value);

    watchId = navigator.geolocation.watchPosition(onGpsSuccess, onGpsError, {
      enableHighAccuracy: true,
      maximumAge: 5000,
      timeout: 10000,
    });

    timerId = setInterval(() => {
      checkAndSendDetect();
    }, 1000);
  }

  function stopTracking() {
    if (watchId !== null) {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
    if (timerId !== null) {
      clearInterval(timerId);
      timerId = null;
    }

    manager.resetAll();
    stopCurrentAudio();

    btnStart.disabled = false;
    btnStop.disabled = true;
    setGpsStatus("Đã dừng GPS", "badge-neutral");
    setApiStatus("Sẵn sàng", "badge-neutral");
  }

  btnStart.addEventListener("click", startTracking);
  btnStop.addEventListener("click", stopTracking);

  langSelect.addEventListener("change", () => {
    manager.setLanguage(langSelect.value);
    ui.apply(langSelect.value);
    renderCandidates(lastCandidates);
    stopCurrentAudio();
    manager.narrationState = "idle";
    currentNarrationPayload = null;
    if (btnAudioReplay) btnAudioReplay.disabled = true;
    // Khi người dùng đổi ngôn ngữ, cho phép gửi lại ngay lập tức
    if (manager.lastSentFix) {
      manager.lastSentFix.timestamp = 0;
      checkAndSendDetect();
    }
  });

  if (btnAllowPlay) {
    btnAllowPlay.addEventListener("click", () => {
      hidePermissionBanner();
      if (audioElement && audioElement.src) {
        audioElement.play().then(() => {
          activeAudioSource = "mp3";
          if (currentNarrationPayload && currentNarrationPayload.key) {
            manager.markPlaybackStarted(currentNarrationPayload.key);
          }
          setPlayerStatus("Đang phát MP3", "badge-success");
          if (btnAudioStop) btnAudioStop.disabled = false;
        }).catch((err) => {
          console.warn("Chạm Cho phép phát vẫn lỗi:", err);
          fallbackToSpeech(
            currentNarrationPayload?.translated_text,
            currentNarrationPayload?.lang,
            currentNarrationPayload?.key,
            err.message
          );
        });
      }
    });
  }

  if (btnRetryPlay) {
    btnRetryPlay.addEventListener("click", () => {
      hideRetryBanner();
      if (currentNarrationPayload) {
        playNarration(currentNarrationPayload, true);
      }
    });
  }

  if (btnAudioStop) {
    btnAudioStop.addEventListener("click", () => {
      stopCurrentAudio();
    });
  }

  if (btnAudioMute) {
    btnAudioMute.addEventListener("click", () => {
      isMuted = !isMuted;
      audioElement.muted = isMuted;
      ui.setText(btnAudioMute, isMuted ? "🔊 Bật tiếng" : "🔇 Tắt tiếng");
      if (isMuted && activeAudioSource === "speech" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    });
  }

  if (btnAudioReplay) {
    btnAudioReplay.addEventListener("click", () => {
      if (currentNarrationPayload) {
        // Nút Phát lại: Thao tác chủ động của người dùng -> bypass playedKeys
        playNarration(currentNarrationPayload, true);
      }
    });
  }

  // Sự kiện chỉnh thời lượng bằng Slider
  if (audioSeekBar) {
    audioSeekBar.addEventListener("input", () => {
      isSeeking = true;
      if (audioElement.duration && isFinite(audioElement.duration)) {
        const seekRatio = parseFloat(audioSeekBar.value) / 100;
        const targetTime = seekRatio * audioElement.duration;
        if (audioCurrentTime) audioCurrentTime.textContent = formatAudioTime(targetTime);
      }
    });

    audioSeekBar.addEventListener("change", () => {
      if (audioElement.duration && isFinite(audioElement.duration)) {
        const seekRatio = parseFloat(audioSeekBar.value) / 100;
        audioElement.currentTime = seekRatio * audioElement.duration;
      }
      isSeeking = false;
    });
  }

  // Nút Tua lùi 10s
  if (btnAudioRewind) {
    btnAudioRewind.addEventListener("click", () => {
      if (audioElement && audioElement.duration) {
        audioElement.currentTime = Math.max(0, audioElement.currentTime - 10);
        if (audioCurrentTime) audioCurrentTime.textContent = formatAudioTime(audioElement.currentTime);
        if (audioSeekBar) audioSeekBar.value = ((audioElement.currentTime / audioElement.duration) * 100).toFixed(1);
      }
    });
  }

  // Nút Tua tới 10s
  if (btnAudioForward) {
    btnAudioForward.addEventListener("click", () => {
      if (audioElement && audioElement.duration) {
        audioElement.currentTime = Math.min(audioElement.duration, audioElement.currentTime + 10);
        if (audioCurrentTime) audioCurrentTime.textContent = formatAudioTime(audioElement.currentTime);
        if (audioSeekBar) audioSeekBar.value = ((audioElement.currentTime / audioElement.duration) * 100).toFixed(1);
      }
    });
  }

  // Nút Tạm dừng / Tiếp tục
  if (btnAudioPlayPause) {
    btnAudioPlayPause.addEventListener("click", () => {
      if (!audioElement || !audioElement.src) return;
      if (audioElement.paused) {
        audioElement.play().then(() => {
          btnAudioPlayPause.textContent = "⏸ Tạm dừng";
          setPlayerStatus("Đang phát MP3", "badge-success");
        }).catch((err) => {
          console.warn("Không thể tiếp tục phát:", err);
        });
      } else {
        audioElement.pause();
        btnAudioPlayPause.textContent = "▶ Tiếp tục";
        setPlayerStatus("Tạm dừng phát", "badge-warning");
      }
    });
  }
})();

// ===========================================================================
// CommonJS Export (Cho node --test)
// ===========================================================================
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    calculateDistanceM,
    TourStateManager,
  };
}
