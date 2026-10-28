const { test } = require("node:test");
const assert = require("node:assert/strict");
const { translate } = require("../gateway-service/client/i18n.js");
const { TourStateManager } = require("../gateway-service/client/app.js");

test("UI labels and dynamic error codes in all three languages", () => {
  assert.equal(translate("Ngôn ngữ thuyết minh", "en"), "Narration language");
  assert.equal(translate("Ngôn ngữ thuyết minh", "ja"), "音声ガイドの言語");
  assert.equal(translate("Ngôn ngữ thuyết minh", "vi"), "Ngôn ngữ thuyết minh");
  assert.equal(translate("Lỗi tạo âm thanh (gemini_invalid_text)", "en"), "Audio generation failed (gemini_invalid_text)");
  assert.equal(translate("🔇 Tắt tiếng", "en"), "🔇 Mute");
  assert.equal(translate("GPS chưa ổn định (±90m > 50m)", "en"), "GPS unstable (±90m > 50m)");
});

test("Poll completed narration sooner without overlapping requests", () => {
  const manager = new TourStateManager();
  manager.lastFix = { latitude: 10, longitude: 106, accuracy: 5, timestamp: 10000 };
  manager.lastSentFix = { ...manager.lastFix };
  manager.lastRequestTime = 10000;
  assert.equal(manager.shouldSendDetect(13000), false);
  manager.handleDetectResponse({ detected: true, requested_lang: "vi", narration_status: "processing",
    location: { id: 1, latitude: 10, longitude: 106 } }, manager.sequenceToken, "vi");
  assert.equal(manager.shouldSendDetect(13000), true);
  manager.isRequestInFlight = true;
  assert.equal(manager.shouldSendDetect(13000), false);
  manager.setLanguage("ja");
  assert.equal(manager.generationPending, false);
});
