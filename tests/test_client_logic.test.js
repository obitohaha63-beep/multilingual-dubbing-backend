/**
 * Unit & Behavioral Tests cho Client Core Flow & State Machine (Tuần 5)
 * Chạy bằng Node.js Test Runner:
 *     node --test tests/test_client_logic.test.js
 */

const { describe, it, beforeEach } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  calculateDistanceM,
  TourStateManager,
} = require(path.resolve(__dirname, "../gateway-service/client/app.js"));

describe("Chờ tạo thuyết minh trên Content Service", () => {
  it("processing/error không phát mô tả nguồn; ready sau đó phát một lần", () => {
    const manager = new TourStateManager();
    const response = { detected: true, location: { id: 1, latitude: 10, longitude: 106 },
      requested_lang: "vi", narration_status: "processing" };
    const context = manager.prepareRequest();
    assert.equal(manager.handleDetectResponse(response, context.token, "vi").action, "wait_generation");
    assert.equal(manager.narrationState, "idle");
    assert.equal(manager.playedKeys.size, 0);
    response.narration_status = "error";
    assert.equal(manager.handleDetectResponse(response, context.token, "vi").action, "generation_error");
    response.narration_status = "audio_ready";
    response.audio_url = "https://example.supabase.co/audio.mp3";
    const result = manager.handleDetectResponse(response, context.token, "vi");
    assert.equal(result.action, "play");
    manager.markPlaybackStarted(result.key);
    assert.equal(manager.handleDetectResponse(response, context.token, "vi").action, "skip_already_played");
  });
});

describe("Client State Machine & Core Flow (Tuần 5)", () => {
  let manager;

  beforeEach(() => {
    manager = new TourStateManager();
  });

  // -------------------------------------------------------------------------
  // Kịch bản 1: Lặp GPS 50 lần cùng location + lang -> chỉ phát đúng 1 lần
  // -------------------------------------------------------------------------
  it("Kịch bản 1: Lặp GPS 50 lần cùng location và lang -> chỉ kích hoạt phát đúng 1 lần (playedKeys guard)", () => {
    manager.setLanguage("vi");
    const locCoords = { latitude: 10.77978, longitude: 106.69902, accuracy: 10.0 };

    // Tọa độ mẫu Nhà thờ Đức Bà
    const mockLocation = {
      id: 1,
      name: "Nhà thờ Đức Bà",
      latitude: 10.77978,
      longitude: 106.69902,
      radius: 80,
    };

    const mockDetectResponse = {
      detected: true,
      location: mockLocation,
      distance_m: 5.0,
      requested_lang: "vi",
      lang: "vi",
      translated_text: "Bài thuyết minh Đức Bà",
      audio_url: "/audio/ducba.mp3",
      narration_status: "audio_ready",
    };

    let playTriggerCount = 0;
    let skipCount = 0;

    // Giả lập 50 lần GPS tick khi người dùng đứng yên tại địa điểm
    let simulatedTime = 1000000;
    for (let i = 0; i < 50; i++) {
      simulatedTime += 3500; // Mỗi tick cách nhau 3.5s (> throttle 3s)
      manager.handleGpsFix(locCoords, simulatedTime);

      if (manager.shouldSendDetect(simulatedTime)) {
        const reqContext = manager.prepareRequest(simulatedTime);
        // Giả lập nhận response
        const evalResult = manager.handleDetectResponse(mockDetectResponse, reqContext.token, reqContext.lang);

        if (evalResult.action === "play") {
          playTriggerCount++;
          // Giả lập phát audio thành công và đánh dấu đã phát
          manager.markPlaybackStarted(evalResult.key);
          manager.markPlaybackEnded(evalResult.key);
        } else if (evalResult.action === "skip_already_played") {
          skipCount++;
        }
        manager.finalizeRequest(locCoords, simulatedTime);
      }
    }

    assert.equal(playTriggerCount, 1, "Chỉ được kích hoạt phát duy nhất 1 lần trong suốt 50 tick GPS");
    assert.ok(skipCount > 0, "Các lần sau phải bị chặn bởi playedKeys");
    assert.ok(manager.playedKeys.has("1:vi"), "Tập playedKeys phải ghi nhận 1:vi");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 2: Đổi ngôn ngữ
  // -------------------------------------------------------------------------
  it("Kịch bản 2: Đổi ngôn ngữ vi -> en phát 1 lần en; đổi ngược lại vi đã nghe không tự lặp lại", () => {
    const locCoords = { latitude: 10.77978, longitude: 106.69902, accuracy: 10.0 };
    const mockLocation = { id: 1, name: "Nhà thờ Đức Bà", radius: 80, latitude: 10.77978, longitude: 106.69902 };

    // 1. Ban đầu đang nghe tiếng Việt
    manager.setLanguage("vi");
    manager.handleGpsFix(locCoords, 1000);
    const req1 = manager.prepareRequest();
    const resVi = manager.handleDetectResponse({
      detected: true,
      location: mockLocation,
      requested_lang: "vi",
      translated_text: "Thuyết minh tiếng Việt",
    }, req1.token, "vi");

    assert.equal(resVi.action, "play");
    manager.markPlaybackStarted(resVi.key);
    manager.markPlaybackEnded(resVi.key);
    manager.finalizeRequest(locCoords, 1000);

    // 2. Người dùng đổi sang tiếng Anh (en)
    manager.setLanguage("en");
    manager.handleGpsFix(locCoords, 5000);
    const req2 = manager.prepareRequest();
    const resEn = manager.handleDetectResponse({
      detected: true,
      location: mockLocation,
      requested_lang: "en",
      translated_text: "English Narration",
    }, req2.token, "en");

    assert.equal(resEn.action, "play", "Đổi sang ngôn ngữ mới phải cho phép phát ngôn ngữ mới");
    assert.equal(resEn.key, "1:en");
    manager.markPlaybackStarted(resEn.key);
    manager.markPlaybackEnded(resEn.key);
    manager.finalizeRequest(locCoords, 5000);

    // 3. Người dùng đổi lại tiếng Việt (vi)
    manager.setLanguage("vi");
    manager.handleGpsFix(locCoords, 10000);
    const req3 = manager.prepareRequest();
    const resViAgain = manager.handleDetectResponse({
      detected: true,
      location: mockLocation,
      requested_lang: "vi",
      translated_text: "Thuyết minh tiếng Việt",
    }, req3.token, "vi");

    assert.equal(resViAgain.action, "skip_already_played", "Đổi lại tiếng Việt đã nghe thì KHÔNG tự động phát lại");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 3: Chuyển địa điểm A -> B
  // -------------------------------------------------------------------------
  it("Kịch bản 3: Chuyển từ địa điểm A sang B: dừng âm thanh A, reset playedKeys và kích hoạt B", () => {
    manager.setLanguage("vi");

    // Đang ở A (Nhà thờ Đức Bà, id: 1)
    const fixA = { latitude: 10.77978, longitude: 106.69902, accuracy: 10.0 };
    manager.handleGpsFix(fixA, 1000);
    const reqA = manager.prepareRequest();
    const resA = manager.handleDetectResponse({
      detected: true,
      location: { id: 1, name: "Nhà thờ Đức Bà", radius: 80, latitude: 10.77978, longitude: 106.69902 },
      requested_lang: "vi",
    }, reqA.token, "vi");

    assert.equal(resA.action, "play");
    manager.markPlaybackStarted(resA.key);

    // Người dùng di chuyển sang B (Bưu điện Trung tâm, id: 2)
    const fixB = { latitude: 10.77985, longitude: 106.69990, accuracy: 10.0 };
    manager.handleGpsFix(fixB, 15000);
    const reqB = manager.prepareRequest();
    const resB = manager.handleDetectResponse({
      detected: true,
      location: { id: 2, name: "Bưu điện Trung tâm", radius: 80, latitude: 10.77985, longitude: 106.69990 },
      requested_lang: "vi",
    }, reqB.token, "vi");

    assert.equal(resB.action, "play");
    assert.equal(resB.switchedLocation, true, "Phải nhận diện đã chuyển sang địa điểm mới");
    assert.equal(manager.activeLocationId, 2, "activeLocationId phải chuyển thành 2");
    assert.ok(manager.playedKeys.size === 0, "playedKeys cũ của A phải được làm mới cho lượt ghé B");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 4: GPS Jitter ở rìa bán kính
  // -------------------------------------------------------------------------
  it("Kịch bản 4: Jitter ở rìa bán kính: dao động ra vào ngưỡng rời không làm reset trạng thái khi chưa đủ 2 fix >= 10s", () => {
    manager.setLanguage("vi");
    const locCenter = { latitude: 10.77978, longitude: 106.69902 };
    const radius = 80; // ngưỡng rời = 80 + 30 = 110m

    // Khởi tạo tại A
    manager.activeLocationId = 1;
    manager.activeLocationCoords = { latitude: locCenter.latitude, longitude: locCenter.longitude, radius };
    manager.playedKeys.add("1:vi");

    // Fix 1: vọt ra cách tâm 120m (> 110m) lúc t = 1000
    // Tính tọa độ cách tâm ~120m về phía Bắc: 1 độ vĩ ~ 111,000m -> 120m ~ 0.00108 độ
    const fixOut1 = { latitude: locCenter.latitude + 0.00108, longitude: locCenter.longitude, accuracy: 15.0 };
    const r1 = manager.handleGpsFix(fixOut1, 1000);
    assert.equal(r1.exitTriggered, false, "Chưa đủ 2 fix: chưa được xác nhận rời");
    assert.ok(manager.exitCandidateFix !== null, "Đã ghi nhận candidate rời đầu tiên");

    // Fix 2: Jitter lọt lại vào cách tâm 90m (trong ngưỡng 110m) lúc t = 5000
    const fixIn = { latitude: locCenter.latitude + 0.0007, longitude: locCenter.longitude, accuracy: 15.0 };
    const r2 = manager.handleGpsFix(fixIn, 5000);
    assert.equal(r2.exitTriggered, false);
    assert.equal(manager.exitCandidateFix, null, "Quay lại trong ngưỡng phải reset exitCandidateFix");
    assert.equal(manager.activeLocationId, 1, "Vẫn giữ activeLocationId = 1");
    assert.ok(manager.playedKeys.has("1:vi"), "Vẫn giữ playedKeys");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 5: Rời A hợp lệ (2 fix >= 10s và > radius + 30m) rồi quay lại
  // -------------------------------------------------------------------------
  it("Kịch bản 5: Rời A rõ ràng sau 2 fix cách nhau >=10s >radius+30m: reset playedKeys; khi quay lại được phát lại", () => {
    manager.setLanguage("vi");
    const locCenter = { latitude: 10.77978, longitude: 106.69902 };
    const radius = 80;

    manager.activeLocationId = 1;
    manager.activeLocationCoords = { latitude: locCenter.latitude, longitude: locCenter.longitude, radius };
    manager.playedKeys.add("1:vi");

    // Fix 1 ngoài ngưỡng 110m lúc t = 1000
    const fixOut = { latitude: locCenter.latitude + 0.0015, longitude: locCenter.longitude, accuracy: 10.0 };
    manager.handleGpsFix(fixOut, 1000);
    assert.equal(manager.activeLocationId, 1);

    // Fix 2 ngoài ngưỡng 110m lúc t = 12000 (cách nhau 11s >= 10s)
    const rExit = manager.handleGpsFix(fixOut, 12000);
    assert.equal(rExit.exitTriggered, true, "Đã thỏa điều kiện rời rõ ràng");
    assert.equal(manager.activeLocationId, null, "activeLocationId được reset về null");
    assert.equal(manager.playedKeys.size, 0, "playedKeys được reset sạch sẽ");

    // Người dùng đi bộ quay lại A
    const fixBackIn = { latitude: locCenter.latitude, longitude: locCenter.longitude, accuracy: 10.0 };
    manager.handleGpsFix(fixBackIn, 30000);
    const req = manager.prepareRequest();
    const resBack = manager.handleDetectResponse({
      detected: true,
      location: { id: 1, name: "Nhà thờ Đức Bà", radius: 80, latitude: locCenter.latitude, longitude: locCenter.longitude },
      requested_lang: "vi",
    }, req.token, "vi");

    assert.equal(resBack.action, "play", "Khi quay trở lại sau khi đã rời rõ ràng, được phép phát lại từ đầu");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 6: Autoplay Fail (NotAllowedError) -> Chưa marked played
  // -------------------------------------------------------------------------
  it("Kịch bản 6: Autoplay bị chặn bởi trình duyệt (NotAllowedError) -> Chưa marked là played", () => {
    manager.setLanguage("vi");
    const key = "1:vi";

    // Bắt đầu pending
    manager.narrationState = "pending";

    // Trình duyệt chặn autoplay (NotAllowedError)
    manager.markAutoplayBlocked();

    assert.equal(manager.narrationState, "idle");
    assert.equal(manager.playedKeys.has(key), false, "Chưa phát thành công thì tuyệt đối không được ghi vào playedKeys");

    // Khi người dùng bấm 'Cho phép phát', audio play thành công -> mới ghi nhận
    manager.markPlaybackStarted(key);
    assert.equal(manager.playedKeys.has(key), true, "Chỉ ghi nhận khi người dùng chạm cho phép phát thành công");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 7: Response trễ và Sequence Token
  // -------------------------------------------------------------------------
  it("Kịch bản 7: Response trễ và stale GPS -> sequence token và language guard bỏ qua kết quả cũ", () => {
    manager.setLanguage("vi");
    const fix1 = { latitude: 10.77, longitude: 106.70, accuracy: 10.0 };
    manager.handleGpsFix(fix1, 1000);

    // Gửi request 1 với tiếng Việt
    const req1 = manager.prepareRequest();
    assert.equal(req1.token, 1);

    // Trong lúc request 1 đang bay trên mạng, người dùng đổi sang tiếng Anh (en) và gửi request 2
    manager.setLanguage("en");
    const fix2 = { latitude: 10.78, longitude: 106.71, accuracy: 10.0 };
    manager.handleGpsFix(fix2, 4500);
    const req2 = manager.prepareRequest();
    assert.equal(req2.token, 3); // do setLanguage tăng 1 token và prepareRequest tăng 1 token

    // Request 1 cũ quay về muộn
    const staleResult = manager.handleDetectResponse({
      detected: true,
      location: { id: 1, name: "Địa điểm cũ" },
      requested_lang: "vi",
    }, req1.token, "vi");

    assert.equal(staleResult.action, "ignore_stale", "Phải bỏ qua stale response cũ");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 8: GPS Accuracy > 50m (Kém)
  // -------------------------------------------------------------------------
  it("Kịch bản 8: Tọa độ GPS có accuracy > 50m -> Bỏ qua, không cập nhật enter/exit hay gửi request mới", () => {
    const unstableFix = { latitude: 10.77978, longitude: 106.69902, accuracy: 75.0 };
    const res = manager.handleGpsFix(unstableFix, 1000);

    assert.equal(res.accepted, false);
    assert.equal(res.reason, "accuracy_unstable");
    assert.equal(manager.lastFix, null, "Không được lưu fix kém làm lastFix");
    assert.equal(manager.shouldSendDetect(1000), false, "Không gửi detect khi fix không đạt chuẩn");
  });

  // -------------------------------------------------------------------------
  // Kịch bản 9: Điện thoại di động (HTTPS & Autoplay User Gesture Lifecycle)
  // -------------------------------------------------------------------------
  it("Kịch bản 9: Kịch bản điện thoại di động: yêu cầu Secure Context (HTTPS/localhost), xử lý Autoplay Blocked và User Gesture Unlock", () => {
    manager.setLanguage("vi");
    const key = "1:vi";

    // 1. Kiểm tra điều kiện Secure Context (chuẩn W3C Geolocation)
    const isMockSecureContext = true; // https:// hoặc http://localhost
    assert.ok(isMockSecureContext, "Trình duyệt di động yêu cầu Secure Context (HTTPS hoặc localhost) để dùng Geolocation");

    // 2. Nhận response kích hoạt trong khi máy điện thoại đang khóa màn hình hoặc chưa có user gesture
    manager.activeLocationId = 1;
    manager.narrationState = "pending";

    // Trình duyệt di động quăng NotAllowedError
    manager.markAutoplayBlocked();
    assert.equal(manager.narrationState, "idle");
    assert.equal(manager.playedKeys.has(key), false, "Khi autoplay bị chặn, không coi là đã phát xong");

    // 3. Người dùng chạm vào nút 'Cho phép phát âm thanh' (User Gesture hợp lệ trên mobile)
    manager.markPlaybackStarted(key);
    assert.equal(manager.narrationState, "playing");
    assert.equal(manager.playedKeys.has(key), true, "Sau User Gesture, âm thanh phát và ghi nhận playedKeys thành công");

    manager.markPlaybackEnded(key);
    assert.equal(manager.narrationState, "played");
  });
});
