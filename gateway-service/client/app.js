/**
 * GPS Audio Guide - Client Logic (app.js)
 * Đáp ứng các quy tắc:
 * 1. navigator.geolocation.watchPosition với success/error callback.
 * 2. Xin quyền sau khi ấn Bắt đầu; clearWatch khi ấn Dừng.
 * 3. Lưu fix gần nhất; gửi đầu tiên ngay lập tức.
 * 4. Gửi tiếp khi di chuyển >= 20m HOẶC đã qua 10s.
 * 5. Khoảng cách hai request tối thiểu 3s (throttle).
 * 6. Tối đa một request đang chạy (in-flight guard).
 * 7. Có timer (mỗi 1s kiểm tra) để gửi định kỳ khi đứng yên, không phụ thuộc watchPosition callback.
 * 8. Không dùng fix cũ quá 30s.
 * 9. Hiển thị rõ denied/unavailable/timeout, không bịa tọa độ.
 * 10. Gọi API cùng origin (/locations/detect).
 */

(() => {
  // State
  let watchId = null;
  let timerId = null;
  let lastFix = null;             // { latitude, longitude, accuracy, timestamp }
  let lastSentFix = null;         // { latitude, longitude, timestamp }
  let lastRequestTime = 0;        // timestamp ms
  let isRequestInFlight = false;

  // DOM Elements
  const langSelect = document.getElementById("lang-select");
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

  // DOM Elements - Audio Player (Tuần 4)
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

  // DOM Elements - Debug Mode (Tuần 4)
  const debugTextInput = document.getElementById("debug-text-input");
  const btnDebugTtsPlay = document.getElementById("btn-debug-tts-play");
  const btnDebugTest404 = document.getElementById("btn-debug-test-404");
  const btnDebugTestNoVoice = document.getElementById("btn-debug-test-novoice");
  const debugApiResult = document.getElementById("debug-api-result");

  // Audio Player State & Singleton Audio Element
  const audioElement = new Audio();
  let currentNarration = null;     // { audio_url, translated_text, lang }
  let isMuted = false;
  let activeAudioSource = null;    // null | 'mp3' | 'speech'
  let availableVoices = [];

  // Haversine formula (meters)
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

  // Update Status Badges
  function setGpsStatus(text, badgeClass) {
    gpsStatus.textContent = text;
    gpsStatus.className = `status-value badge ${badgeClass}`;
  }

  function setApiStatus(text, badgeClass) {
    apiStatus.textContent = text;
    apiStatus.className = `status-value badge ${badgeClass}`;
  }

  // =========================================================================
  // Tuần 4: Audio Player & Web Speech API Helpers
  // =========================================================================

  function setPlayerStatus(text, badgeClass) {
    if (playerStatusBadge) {
      playerStatusBadge.textContent = text;
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
    if (audioErrorMsg) audioErrorMsg.textContent = msg || "Không thể phát âm thanh.";
    if (audioRetryBanner) audioRetryBanner.classList.remove("hidden");
  }

  function hideRetryBanner() {
    if (audioRetryBanner) audioRetryBanner.classList.add("hidden");
  }

  function loadVoices() {
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      availableVoices = window.speechSynthesis.getVoices() || [];
    }
  }

  if (typeof window !== "undefined" && "speechSynthesis" in window) {
    window.speechSynthesis.onvoiceschanged = loadVoices;
    loadVoices();
  }

  function mapLangToBcp47(lang) {
    const l = (lang || "").toLowerCase().trim();
    if (l === "vi") return "vi-VN";
    if (l === "en") return "en-US";
    if (l === "ja") return "ja-JP";
    return lang;
  }

  function findMatchingVoice(lang) {
    loadVoices();
    if (!availableVoices || availableVoices.length === 0) return null;
    const bcp47 = mapLangToBcp47(lang).toLowerCase();
    const shortLang = (lang || "").toLowerCase().split("-")[0];

    // 1. Khớp chính xác mã BCP-47 (ví dụ vi-VN, en-US, ja-JP)
    let matched = availableVoices.find(v => {
      const vLang = (v.lang || "").toLowerCase().replace("_", "-");
      return vLang === bcp47;
    });
    if (matched) return matched;

    // 2. Khớp theo tiền tố ngôn ngữ (vi, en, ja)
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
    if (typeof window !== "undefined" && "speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    activeAudioSource = null;
    hidePermissionBanner();
    setPlayerStatus("Đã dừng phát", "badge-neutral");
    if (btnAudioStop) btnAudioStop.disabled = true;
  }

  function fallbackToSpeech(text, lang, reason) {
    console.info(`Kích hoạt Fallback SpeechSynthesis. Lý do: ${reason}`);
    if (audioElement) {
      audioElement.pause();
      audioElement.currentTime = 0;
      audioElement.removeAttribute("src");
    }

    if (typeof window === "undefined" || !("speechSynthesis" in window)) {
      setPlayerStatus("Trình duyệt không hỗ trợ Web Speech", "badge-danger");
      if (playerSource) playerSource.textContent = "Không hỗ trợ Web Speech API";
      showRetryBanner("Trình duyệt không hỗ trợ Web Speech API.");
      return;
    }

    loadVoices();
    const voice = findMatchingVoice(lang);
    if (!voice) {
      // Khi không có voice phù hợp: hiển thị text/trạng thái, không hứa có đủ mọi ngôn ngữ, giữ nút thử lại
      if (playerSource) playerSource.textContent = "Chỉ hiển thị văn bản (Trình duyệt chưa có voice)";
      setPlayerStatus("Không có giọng đọc cho ngôn ngữ này trong trình duyệt", "badge-warning");
      showRetryBanner(`Trình duyệt không có gói giọng đọc cho ngôn ngữ '${lang}'. Bạn vẫn có thể đọc bài thuyết minh bên trên.`);
      if (btnAudioStop) btnAudioStop.disabled = true;
      return;
    }

    if (playerSource) {
      playerSource.textContent = `Giọng đọc trình duyệt (${voice.name || voice.lang})`;
    }
    setPlayerStatus("Đang phát giọng đọc trình duyệt", "badge-info");
    hideRetryBanner();

    const utter = new SpeechSynthesisUtterance(text);
    utter.lang = mapLangToBcp47(lang);
    utter.voice = voice;

    utter.onstart = () => {
      activeAudioSource = "speech";
      if (btnAudioStop) btnAudioStop.disabled = false;
    };
    utter.onend = () => {
      activeAudioSource = null;
      setPlayerStatus("Đã phát xong", "badge-neutral");
      if (btnAudioStop) btnAudioStop.disabled = true;
    };
    utter.onerror = (ev) => {
      activeAudioSource = null;
      console.error("SpeechSynthesis error:", ev);
      setPlayerStatus("Lỗi phát giọng đọc trình duyệt", "badge-danger");
      showRetryBanner("Lỗi phát giọng đọc: " + (ev.error || "Không rõ nguyên nhân"));
      if (btnAudioStop) btnAudioStop.disabled = true;
    };

    window.speechSynthesis.speak(utter);
  }

  function playNarration({ audio_url, translated_text, lang }) {
    // 1. Chỉ phát một narration tại một thời điểm: dừng/cancel âm thanh cũ
    stopCurrentAudio();

    currentNarration = { audio_url, translated_text, lang };

    if (playerText) {
      playerText.textContent = translated_text || "(Không có văn bản)";
    }
    if (btnAudioReplay) btnAudioReplay.disabled = false;
    hidePermissionBanner();
    hideRetryBanner();

    // 2. Nếu có file audio MP3 từ Gateway (same origin)
    if (audio_url) {
      if (playerSource) playerSource.textContent = `File MP3 Gateway (${audio_url})`;
      audioElement.src = audio_url;
      audioElement.muted = isMuted;

      audioElement.onended = () => {
        activeAudioSource = null;
        setPlayerStatus("Đã phát xong", "badge-neutral");
        if (btnAudioStop) btnAudioStop.disabled = true;
      };

      audioElement.onerror = () => {
        console.warn("audioElement onerror: MP3 bị 404 hoặc lỗi mạng, tự động fallback sang SpeechSynthesis.");
        fallbackToSpeech(translated_text, lang, "File MP3 404 hoặc lỗi tải từ Gateway");
      };

      // Bắt Promise của audio.play()
      const playPromise = audioElement.play();
      if (playPromise !== undefined) {
        playPromise
          .then(() => {
            activeAudioSource = "mp3";
            setPlayerStatus("Đang phát MP3", "badge-success");
            if (btnAudioStop) btnAudioStop.disabled = false;
            hidePermissionBanner();
            hideRetryBanner();
          })
          .catch((err) => {
            if (err.name === "NotAllowedError") {
              // Bị chặn bởi Autoplay Policy của trình duyệt
              // QUAN TRỌNG: Hiển thị nút 'Cho phép phát' để người dùng chạm lại, KHÔNG coi là file hỏng!
              activeAudioSource = null;
              setPlayerStatus("Trình duyệt chặn tự động phát (Chạm để nghe)", "badge-warning");
              showPermissionBanner();
              if (btnAudioStop) btnAudioStop.disabled = true;
            } else {
              // Lỗi khác (network, decode...) -> fallback sang SpeechSynthesis
              fallbackToSpeech(translated_text, lang, err.message);
            }
          });
      }
    } else if (translated_text) {
      // 3. Không có audio MP3 nhưng có văn bản -> dùng SpeechSynthesisUtterance trực tiếp
      fallbackToSpeech(translated_text, lang, "Không có audio_url, dùng SpeechSynthesis cho văn bản");
    } else {
      setPlayerStatus("Không có nội dung âm thanh hoặc văn bản", "badge-neutral");
      if (playerSource) playerSource.textContent = "--";
    }
  }

  // Geolocation Success
  function onGpsSuccess(position) {
    const coords = position.coords;
    const now = Date.now();

    lastFix = {
      latitude: coords.latitude,
      longitude: coords.longitude,
      accuracy: coords.accuracy,
      timestamp: now,
    };

    setGpsStatus("Đang nhận GPS", "badge-success");
    coordsDisplay.textContent = `${coords.latitude.toFixed(5)}, ${coords.longitude.toFixed(5)}`;
    accuracyDisplay.textContent = `±${Math.round(coords.accuracy)}m`;
    timestampDisplay.textContent = new Date(now).toLocaleTimeString();

    // Thử gửi ngay nếu thỏa điều kiện
    checkAndSendDetect();
  }

  // Geolocation Error
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
    accuracyDisplay.textContent = "--";
  }

  // Evaluate conditions to send POST /locations/detect
  function checkAndSendDetect() {
    if (!lastFix) return;

    const now = Date.now();

    // 1. Không dùng fix cũ quá 30s
    if (now - lastFix.timestamp > 30000) {
      setGpsStatus("Fix GPS quá cũ (>30s)", "badge-warning");
      return;
    }

    // 2. Tối đa 1 request đang chạy (in-flight)
    if (isRequestInFlight) {
      return;
    }

    // 3. Khoảng cách hai request tối thiểu 3s (throttle)
    if (now - lastRequestTime < 3000) {
      return;
    }

    // 4. Quyết định có cần gửi hay không:
    // - Lần đầu tiên (chưa gửi lần nào) -> gửi ngay
    // - Đã di chuyển >= 20m so với lần gửi trước -> gửi
    // - Hoặc đã qua 10s kể từ lần gửi trước -> gửi
    let shouldSend = false;

    if (!lastSentFix) {
      shouldSend = true;
    } else {
      const dist = calculateDistanceM(
        lastSentFix.latitude,
        lastSentFix.longitude,
        lastFix.latitude,
        lastFix.longitude
      );
      const timeSinceLastSent = now - lastSentFix.timestamp;

      if (dist >= 20 || timeSinceLastSent >= 10000) {
        shouldSend = true;
      }
    }

    if (shouldSend) {
      sendDetectRequest(lastFix);
    }
  }

  // API Call: POST /locations/detect (Same origin)
  async function sendDetectRequest(fix) {
    isRequestInFlight = true;
    lastRequestTime = Date.now();
    setApiStatus("Đang gửi...", "badge-info");

    const payload = {
      latitude: fix.latitude,
      longitude: fix.longitude,
      language: langSelect.value,
      accuracy_m: fix.accuracy,
    };

    try {
      const response = await fetch("/locations/detect", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP ${response.status}`);
      }

      const data = await response.json();
      lastSentFix = {
        latitude: fix.latitude,
        longitude: fix.longitude,
        timestamp: Date.now(),
      };

      setApiStatus("Đã phản hồi", "badge-success");
      renderDetectResult(data);
    } catch (err) {
      console.error("Detect API error:", err);
      setApiStatus(`Lỗi: ${err.message}`, "badge-danger");
    } finally {
      isRequestInFlight = false;
    }
  }

  // Render Result to UI
  function renderDetectResult(data) {
    // Debug candidates list
    renderCandidates(data.candidates);

    if (data.detected && data.location) {
      // Trong bán kính kích hoạt
      noDetectBox.classList.add("hidden");
      detectedContent.classList.remove("hidden");

      const loc = data.location;
      placeName.textContent = loc.name || "Địa điểm không tên";
      activationBadge.textContent = "Trong bán kính kích hoạt";
      activationBadge.className = "badge badge-success";

      placeDistance.textContent = data.distance_m !== null ? `${data.distance_m} m` : "--";
      placeSource.textContent = loc.source === "wikimedia" ? "Wikipedia (Wikimedia)" : (loc.source || "Manual");

      if (data.source_url) {
        sourceLinkRow.style.display = "flex";
        placeUrl.href = data.source_url;
        placeUrl.textContent = data.source_url;
      } else {
        sourceLinkRow.style.display = "none";
      }

      narrationStatus.textContent = data.narration_status === "not_implemented"
        ? "Chưa phát âm thanh (Tuần 3: detected=true)"
        : (data.narration_status || "--");

      placeDesc.textContent = loc.description_source || loc.description_vi || "(Không có mô tả)";

      // Tự động phát thuyết minh nếu detect có audio_url hoặc nội dung bài viết
      if (data.audio_url || data.translated_text) {
        playNarration({
          audio_url: data.audio_url,
          translated_text: data.translated_text || loc.description_source || loc.name,
          lang: data.lang || langSelect.value,
        });
      }
    } else if (data.reason === "outside_activation_radius" && data.location) {
      // Có địa điểm lân cận nhưng ngoài activation radius
      noDetectBox.classList.add("hidden");
      detectedContent.classList.remove("hidden");

      const loc = data.location;
      placeName.textContent = loc.name || "Địa điểm lân cận";
      activationBadge.textContent = "Ngoài bán kính kích hoạt";
      activationBadge.className = "badge badge-warning";

      placeDistance.textContent = data.distance_m !== null ? `${data.distance_m} m (Cần lại gần hơn)` : "--";
      placeSource.textContent = loc.source === "wikimedia" ? "Wikipedia (Wikimedia)" : (loc.source || "Manual");

      if (data.source_url) {
        sourceLinkRow.style.display = "flex";
        placeUrl.href = data.source_url;
        placeUrl.textContent = data.source_url;
      } else {
        sourceLinkRow.style.display = "none";
      }

      narrationStatus.textContent = "Không kích hoạt";
      placeDesc.textContent = loc.description_source || loc.description_vi || "(Không có mô tả)";
    } else {
      // no_place_found hoặc upstream_unavailable
      detectedContent.classList.add("hidden");
      noDetectBox.classList.remove("hidden");

      if (data.reason === "upstream_unavailable") {
        noDetectBox.textContent = "Máy chủ nội bộ (Content Service) tạm thời gián đoạn kết nối.";
      } else {
        noDetectBox.textContent = "Không tìm thấy địa điểm nào trong phạm vi 500m.";
      }
    }
  }

  function renderCandidates(candidates) {
    if (!candidates || candidates.length === 0) {
      candidatesList.innerHTML = '<p class="empty-note">Chưa có dữ liệu ứng viên trong 500m.</p>';
      return;
    }

    candidatesList.innerHTML = candidates.map(c => `
      <div class="candidate-item">
        <strong>${c.name}</strong> (${c.distance_m ? Math.round(c.distance_m) + "m" : "--"})
        <br><small>Bán kính kích hoạt: ${c.radius || 80}m | Nguồn: ${c.source || "manual"}</small>
      </div>
    `).join("");
  }

  // Start Action
  function startTracking() {
    if (!("geolocation" in navigator)) {
      setGpsStatus("Trình duyệt không hỗ trợ Geolocation", "badge-danger");
      return;
    }

    setGpsStatus("Đang xin quyền GPS...", "badge-warning");
    btnStart.disabled = true;
    btnStop.disabled = false;

    // Bắt đầu quan sát GPS
    watchId = navigator.geolocation.watchPosition(onGpsSuccess, onGpsError, {
      enableHighAccuracy: true,
      maximumAge: 5000,
      timeout: 10000,
    });

    // Timer độc lập chạy mỗi 1s:
    // Đảm bảo khi người dùng đứng yên, cứ 10s sẽ gửi định kỳ lại
    // không bị phụ thuộc vào việc watchPosition callback có bắn hay không.
    timerId = setInterval(() => {
      checkAndSendDetect();
    }, 1000);
  }

  // Stop Action
  function stopTracking() {
    if (watchId !== null) {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
    if (timerId !== null) {
      clearInterval(timerId);
      timerId = null;
    }

    lastFix = null;
    lastSentFix = null;
    isRequestInFlight = false;

    // Dừng luôn cả âm thanh thuyết minh đang phát
    stopCurrentAudio();

    btnStart.disabled = false;
    btnStop.disabled = true;
    setGpsStatus("Đã dừng GPS", "badge-neutral");
    setApiStatus("Sẵn sàng", "badge-neutral");
  }

  // Event Listeners: Core Tracking
  btnStart.addEventListener("click", startTracking);
  btnStop.addEventListener("click", stopTracking);

  // Khi đổi ngôn ngữ, nếu có fix hợp lệ thì cho phép gửi cập nhật
  langSelect.addEventListener("change", () => {
    if (lastSentFix) {
      // Reset mốc thời gian để gửi lại theo ngôn ngữ mới
      lastSentFix.timestamp = 0;
      checkAndSendDetect();
    }
  });

  // Event Listeners: Audio Player Controls (Tuần 4)
  if (btnAllowPlay) {
    btnAllowPlay.addEventListener("click", () => {
      hidePermissionBanner();
      if (audioElement && audioElement.src) {
        audioElement.play().then(() => {
          activeAudioSource = "mp3";
          setPlayerStatus("Đang phát MP3", "badge-success");
          if (btnAudioStop) btnAudioStop.disabled = false;
        }).catch((err) => {
          console.warn("Chạm nút cho phép phát vẫn lỗi:", err);
          fallbackToSpeech(currentNarration?.translated_text, currentNarration?.lang, err.message);
        });
      }
    });
  }

  if (btnRetryPlay) {
    btnRetryPlay.addEventListener("click", () => {
      hideRetryBanner();
      if (currentNarration) {
        playNarration(currentNarration);
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
      btnAudioMute.textContent = isMuted ? "🔊 Bật tiếng" : "🔇 Tắt tiếng";
      if (isMuted && activeAudioSource === "speech" && typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    });
  }

  if (btnAudioReplay) {
    btnAudioReplay.addEventListener("click", () => {
      if (currentNarration) {
        playNarration(currentNarration);
      }
    });
  }

  // Event Listeners: Debug Mode Player (Tuần 4 - Tắt mặc định)
  if (btnDebugTtsPlay) {
    btnDebugTtsPlay.addEventListener("click", async () => {
      const text = (debugTextInput.value || "").trim();
      const lang = langSelect.value;
      if (!text) {
        alert("Vui lòng nhập văn bản kiểm tra");
        return;
      }
      setPlayerStatus("Đang tạo audio qua Edge TTS...", "badge-info");
      if (debugApiResult) {
        debugApiResult.classList.remove("hidden");
        debugApiResult.textContent = "Đang gửi POST /debug/text-to-speech...";
      }

      try {
        const resp = await fetch("/debug/text-to-speech", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text, lang }),
        });
        if (!resp.ok) {
          const errData = await resp.json().catch(() => ({}));
          throw new Error(errData.detail || `HTTP ${resp.status}`);
        }
        const result = await resp.json();
        if (debugApiResult) {
          debugApiResult.textContent = JSON.stringify(result, null, 2);
        }
        playNarration({
          audio_url: result.audio_url,
          translated_text: text,
          lang: result.lang || lang,
        });
      } catch (e) {
        console.error("Debug TTS Error:", e);
        if (debugApiResult) {
          debugApiResult.textContent = `Lỗi: ${e.message}`;
        }
        setPlayerStatus(`Lỗi TTS: ${e.message}`, "badge-danger");
        fallbackToSpeech(text, lang, `Debug API lỗi: ${e.message}`);
      }
    });
  }

  if (btnDebugTest404) {
    btnDebugTest404.addEventListener("click", () => {
      const text = (debugTextInput.value || "").trim();
      const lang = langSelect.value;
      if (debugApiResult) {
        debugApiResult.classList.remove("hidden");
        debugApiResult.textContent = "Đang thử nghiệm phát file MP3 404 (/audio/nonexistent_test.mp3)...";
      }
      playNarration({
        audio_url: "/audio/nonexistent_test.mp3",
        translated_text: text || "Đây là bài kiểm tra fallback khi file MP3 bị 404.",
        lang: lang,
      });
    });
  }

  if (btnDebugTestNoVoice) {
    btnDebugTestNoVoice.addEventListener("click", () => {
      const text = (debugTextInput.value || "").trim();
      if (debugApiResult) {
        debugApiResult.classList.remove("hidden");
        debugApiResult.textContent = "Đang thử nghiệm Fallback khi ngôn ngữ không có Voice...";
      }
      playNarration({
        audio_url: null,
        translated_text: text || "Kiểm tra hiển thị text khi không có giọng đọc phù hợp.",
        lang: "xx-unknown-language",
      });
    });
  }

  // Export for testing purposes if in test environment
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      calculateDistanceM,
      checkAndSendDetect,
      onGpsSuccess,
      onGpsError,
      playNarration,
      stopCurrentAudio,
      fallbackToSpeech,
      findMatchingVoice,
      mapLangToBcp47,
    };
  }
})();

