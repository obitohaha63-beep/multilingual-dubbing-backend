/* UI strings only. Wikipedia extracts and generated narration stay untouched. */
(function (root) {
  "use strict";
  const strings = [
    ["Thuyết Minh Tự Động Theo Tọa Độ (GPS Dubbing)", "GPS Audio Guide", "GPS音声ガイド"],
    ["Thuyết Minh Du Lịch Tự Động", "Automatic Travel Audio Guide", "自動観光音声ガイド"],
    ["Tự động phát hiện di tích & địa điểm theo vị trí GPS thời gian thực", "Discover landmarks and places using live GPS", "GPSで周辺の名所や史跡をリアルタイムに検出"],
    ["Ngôn ngữ thuyết minh", "Narration language", "音声ガイドの言語"],
    ["Chọn ngôn ngữ thuyết minh", "Choose narration language", "音声ガイドの言語を選択"],
    ["Bắt đầu thuyết minh", "Start audio guide", "音声ガイドを開始"],
    ["Dừng GPS", "Stop GPS", "GPSを停止"],
    ["Trạng thái hệ thống", "System status", "システムの状態"],
    ["Định vị GPS:", "GPS:", "GPS測位:"],
    ["Kết nối Gateway:", "Connection:", "接続:"],
    ["Chưa bật", "Off", "オフ"], ["Sẵn sàng", "Ready", "準備完了"],
    ["Tọa độ:", "Coordinates:", "座標:"], ["Độ chính xác:", "Accuracy:", "精度:"],
    ["Cập nhật lúc:", "Updated at:", "更新時刻:"],
    ["Địa điểm hiện tại", "Current place", "現在の場所"],
    ["Chưa có địa điểm nào được phát hiện trong phạm vi kích hoạt.", "No places detected within the activation radius.", "案内範囲内の場所はまだ検出されていません。"],
    ["Trong bán kính kích hoạt", "Within activation radius", "案内範囲内"],
    ["Ngoài bán kính kích hoạt", "Outside activation radius", "案内範囲外"],
    ["Trong bán kính", "Within radius", "範囲内"],
    ["Khoảng cách:", "Distance:", "距離:"], ["Nguồn dữ liệu:", "Data source:", "情報源:"],
    ["Bài viết gốc:", "Source article:", "元の記事:"], ["Xem liên kết", "Open link", "リンクを開く"],
    ["Trạng thái thuyết minh:", "Narration status:", "音声ガイドの状態:"],
    ["Mô tả sơ lược:", "Overview:", "概要:"],
    ["Âm thanh thuyết minh", "Audio guide", "音声ガイド"],
    ["Chưa phát", "Not playing", "未再生"], ["Nội dung thuyết minh:", "Narration:", "案内文:"],
    ["Chưa có bài thuyết minh nào được phát.", "No narration has been played yet.", "音声ガイドはまだ再生されていません。"],
    ["Nguồn âm thanh:", "Audio source:", "音声ソース:"],
    ["Trình duyệt chặn tự động phát âm thanh. Vui lòng chạm vào nút bên dưới để phát:", "Autoplay was blocked. Tap below to play:", "自動再生がブロックされました。下のボタンを押して再生してください。"],
    ["Cho phép phát âm thanh", "Allow audio playback", "音声再生を許可"],
    ["Thử lại phát âm thanh", "Retry playback", "再生を再試行"],
    ["Dừng phát", "Stop playback", "再生を停止"],
    ["Tắt tiếng", "Mute", "ミュート"], ["Bật tiếng", "Unmute", "ミュート解除"],
    ["Phát lại", "Replay", "もう一度再生"],
    ["Đã dừng phát", "Playback stopped", "再生を停止しました"],
    ["Đang phát MP3", "Playing MP3", "MP3を再生中"],
    ["Đã phát xong", "Playback finished", "再生完了"],
    ["Bản thuyết minh MP3", "MP3 narration", "MP3音声ガイド"],
    ["Trình duyệt chặn tự động phát (Chạm để nghe)", "Autoplay blocked (tap to listen)", "自動再生がブロックされています（タップして再生）"],
    ["Đang chờ bản thuyết minh mới...", "Waiting for the new narration...", "新しい音声ガイドを待っています…"],
    ["Đang biên soạn và tạo âm thanh, vui lòng chờ...", "Writing narration and generating audio. Please wait...", "案内文と音声を作成しています。しばらくお待ちください…"],
    ["Sẵn sàng file âm thanh MP3", "MP3 audio ready", "MP3音声の準備完了"],
    ["Sẵn sàng văn bản (Dùng Web Speech)", "Text ready (browser speech)", "テキストの準備完了（ブラウザー音声）"],
    ["Lỗi tạo âm thanh", "Audio generation failed", "音声生成に失敗しました"],
    ["Xem chi tiết", "See details", "詳細を確認"],
    ["Địa điểm không tên", "Unnamed place", "名称未設定の場所"],
    ["Địa điểm lân cận", "Nearby place", "周辺の場所"],
    ["(Không có mô tả)", "(No description)", "（説明なし）"],
    ["(Không có văn bản)", "(No text)", "（テキストなし）"],
    ["Cần lại gần hơn", "Move closer", "近づいてください"],
    ["Không kích hoạt (Ngoài bán kính)", "Inactive (outside radius)", "案内範囲外"],
    ["Máy chủ nội bộ (Content Service) tạm thời gián đoạn kết nối.", "Content service is temporarily unavailable.", "コンテンツサービスに一時的に接続できません。"],
    ["Không tìm thấy địa điểm nào trong phạm vi 500m.", "No places found within 500 m.", "500m以内に場所が見つかりません。"],
    ["Chưa có dữ liệu ứng viên trong 500m.", "No nearby places within 500 m yet.", "500m以内の候補はまだありません。"],
    ["Chưa có dữ liệu ứng viên.", "No nearby places yet.", "周辺の候補はまだありません。"],
    ["Bán kính kích hoạt:", "Activation radius:", "案内範囲:"], ["Nguồn:", "Source:", "情報源:"],
    ["Đang nhận GPS", "Receiving GPS", "GPS受信中"],
    ["GPS chưa ổn định", "GPS unstable", "GPSが不安定です"], ["(Kém)", "(Poor)", "（低精度）"],
    ["Đã rời khỏi địa điểm", "You have left the place", "この場所から離れました"],
    ["Đang gửi...", "Sending...", "送信中…"], ["Đã phản hồi", "Response received", "応答を受信しました"],
    ["Đang xin quyền GPS...", "Requesting GPS permission...", "GPSの許可を要求しています…"],
    ["Đã dừng GPS", "GPS stopped", "GPSを停止しました"],
    ["Trình duyệt không hỗ trợ Geolocation", "Geolocation is unavailable in this browser", "このブラウザーは位置情報に対応していません"],
    ["Quyền GPS bị từ chối (Denied)", "GPS permission denied", "位置情報の許可が拒否されました"],
    ["Vị trí không khả dụng (Unavailable)", "Position unavailable", "位置情報を取得できません"],
    ["Hết thời gian chờ GPS (Timeout)", "GPS timed out", "GPSがタイムアウトしました"],
    ["Không thể phát âm thanh.", "Unable to play audio.", "音声を再生できません。"],
    ["Không có nội dung âm thanh hoặc văn bản", "No audio or text available", "音声やテキストがありません"],
    ["Trình duyệt không hỗ trợ Web Speech", "Browser speech is unavailable", "ブラウザー音声は利用できません"],
    ["Không hỗ trợ Web Speech API", "Web Speech API unavailable", "Web Speech APIは利用できません"],
    ["Trình duyệt không hỗ trợ Web Speech API.", "This browser does not support Web Speech API.", "このブラウザーはWeb Speech APIに対応していません。"],
    ["Chỉ hiển thị văn bản (Chưa có gói giọng đọc)", "Text only (voice not installed)", "テキストのみ（音声が未インストール）"],
    ["Không có voice phù hợp trong trình duyệt", "No matching browser voice", "対応するブラウザー音声がありません"],
    ["Trình duyệt không có gói giọng đọc cho ngôn ngữ", "No browser voice installed for language", "この言語のブラウザー音声がありません:"],
    ["Bạn vẫn có thể đọc bài bên trên.", "You can still read the text above.", "上のテキストをお読みいただけます。"],
    ["Giọng đọc trình duyệt", "Browser voice", "ブラウザー音声"],
    ["Đang phát giọng đọc trình duyệt", "Playing browser speech", "ブラウザー音声を再生中"],
    ["Lỗi phát giọng đọc trình duyệt", "Browser speech failed", "ブラウザー音声の再生に失敗しました"],
    ["Lỗi phát giọng đọc:", "Speech playback error:", "音声再生エラー:"],
    ["Không rõ nguyên nhân", "Unknown cause", "原因不明"],
    ["Lỗi không xác định", "Unknown error", "不明なエラー"], ["Lỗi:", "Error:", "エラー:"],
    ["Kiểm tra Audio Player (Debug Mode - Tắt mặc định)", "Audio player test (debug, off by default)", "音声プレーヤーテスト（デバッグ・既定では無効）"],
    ["Chế độ kiểm tra âm thanh riêng biệt gọi Edge TTS qua Gateway hoặc kiểm tra các kịch bản fallback. Không ảnh hưởng luồng GPS tự động.", "Test Edge TTS through the Gateway and browser speech fallback independently of GPS tracking.", "GPS追跡に影響せず、Gateway経由のEdge TTSと代替音声をテストします。"],
    ["Văn bản mẫu để kiểm tra:", "Sample text:", "テスト用テキスト:"],
    ["Nhập văn bản mẫu...", "Enter sample text...", "サンプルテキストを入力…"],
    ["Tạo & Phát MP3", "Generate & play MP3", "MP3を生成して再生"],
    ["Test MP3 404 (Fallback Speech)", "Test MP3 404 (browser speech)", "MP3の404テスト（代替音声）"],
    ["Test Fallback Không có Voice", "Test fallback without a voice", "音声がない場合の代替テスト"],
    ["Ứng viên lân cận (Debug & Giám sát)", "Nearby places (debug & monitoring)", "周辺の候補（デバッグ・監視）"],
    ["Hệ thống Thuyết Minh Đa Ngôn Ngữ Tuần 4 • Chạy foreground trên trình duyệt di động", "Multilingual Audio Guide • Keep this mobile browser tab open", "多言語音声ガイド • モバイルブラウザーを開いたままご利用ください"],
  ].sort((a, b) => b[0].length - a[0].length);

  function translate(text, lang = "vi") {
    if (lang === "vi" || typeof text !== "string") return text;
    const index = lang === "ja" ? 2 : 1;
    // One pass: never translate a generated translation or alter error codes/URLs.
    const exact = strings.find(row => row[0] === text.trim());
    if (exact) return text.replace(text.trim(), exact[index]);
    const escape = str => str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const pattern = new RegExp(strings.map(row => escape(row[0])).join("|"), "g");
    return text.replace(pattern, match => strings.find(row => row[0] === match)[index]);
  }

  function bind(document) {
    let language = "vi";
    const dynamic = new Map();
    const nodes = [];
    const attributes = [];
    const walker = document.createTreeWalker(document.body, 4); // SHOW_TEXT
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.textContent.trim() && !node.parentElement.closest("script, style, textarea, select")) {
        nodes.push([node, node.textContent]);
      }
    }
    document.querySelectorAll("[aria-label], [placeholder]").forEach(el => {
      ["aria-label", "placeholder"].forEach(name => {
        if (el.hasAttribute(name)) attributes.push([el, name, el.getAttribute(name)]);
      });
    });
    const originalTitle = document.title;
    function setText(el, text) {
      if (!el) return;
      dynamic.set(el, text);
      el.textContent = translate(text, language);
    }
    function apply(lang) {
      language = ["vi", "en", "ja", "fr", "es"].includes(lang) ? lang : "vi";
      document.documentElement.lang = language;
      document.title = translate(originalTitle, language);
      nodes.forEach(([node, text]) => { if (node.isConnected) node.textContent = translate(text, language); });
      attributes.forEach(([el, name, text]) => el.setAttribute(name, translate(text, language)));
      dynamic.forEach((text, el) => { el.textContent = translate(text, language); });
    }
    return { apply, setText, t: text => translate(text, language) };
  }
  root.GuideI18n = { translate, bind };
  if (typeof module !== "undefined") module.exports = root.GuideI18n;
})(typeof window !== "undefined" ? window : globalThis);
