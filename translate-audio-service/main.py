"""
endpoint giả lập POST /translate (chưa kết nối engine dịch thật).
chưa tích hợp engine dịch thực tế (Google Translate / DeepL / ...).

Dữ liệu đầu vào nhận từ content-service có dạng:
  - text          : chuỗi văn bản cần dịch (ví dụ: description_vi của Location)
  - target_lang   : mã ngôn ngữ đích, ví dụ "en", "ja", "ko"
    (tương ứng với từng phần tử trong target_languages của Location sau khi .split
"""

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="translate-audio-service")


# ---------- Pydantic schemas ----------

class TranslateRequest(BaseModel):
    """Dữ liệu client (hoặc service khác) gửi lên để yêu cầu dịch."""
    text: str           # văn bản gốc cần dịch (thường là description_vi)
    target_lang: str    # mã ngôn ngữ đích, ví dụ "en", "ja", "ko"


class TranslateResponse(BaseModel):
    """Kết quả dịch trả về."""
    translated_text: str


# ---------- Routes ----------

@app.get("/health")
def health():
    """Endpoint kiểm tra tình trạng service."""
    return {"service": "translate-audio-service", "status": "ok"}


@app.post("/translate", response_model=TranslateResponse)
def translate(request: TranslateRequest):
    """
    Nhận văn bản tiếng Việt và ngôn ngữ đích, trả về bản dịch.

    Hiện tại trả về kết quả GIẢ LẬP để phục vụ test.
    chưa tích hợp engine dịch thực tế.
    """
    # TODO: thay thế bằng lời gọi engine dịch thực tế (Google Translate, DeepL, ...)
    mock_translated = f"[{request.target_lang.upper()}] {request.text}"
    return TranslateResponse(translated_text=mock_translated)

