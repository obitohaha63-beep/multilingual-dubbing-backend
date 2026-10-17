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

    btnStart.disabled = false;
    btnStop.disabled = true;
    setGpsStatus("Đã dừng GPS", "badge-neutral");
    setApiStatus("Sẵn sàng", "badge-neutral");
  }

  // Event Listeners
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

  // Export for testing purposes if in test environment
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      calculateDistanceM,
      checkAndSendDetect,
      onGpsSuccess,
      onGpsError,
    };
  }
})();
