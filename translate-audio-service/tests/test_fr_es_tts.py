"""
Kiểm tra tạo file MP3 cho 2 ngôn ngữ mới: Tiếng Pháp (fr) và Tiếng Tây Ban Nha (es)
Sử dụng Edge TTS neural voices:
- fr: fr-FR-DeniseNeural
- es: es-ES-ElviraNeural
"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tts


def test_generate_french_audio_real(tmp_path):
    text = "Bonjour, bienvenue à Paris. Ceci est un test de synthèse vocale."
    res = asyncio.run(tts.synthesize_speech(text=text, lang="fr", audio_dir=tmp_path, timeout_seconds=15.0))
    assert res["voice"] == "fr-FR-DeniseNeural"
    mp3_file = tmp_path / res["audio_filename"]
    assert mp3_file.exists()
    assert mp3_file.stat().st_size > 1000
    with open(mp3_file, "rb") as f:
        header = f.read(3)
        assert header.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))


def test_generate_spanish_audio_real(tmp_path):
    text = "Hola, bienvenido a Madrid. Esta es una prueba de audio en español."
    res = asyncio.run(tts.synthesize_speech(text=text, lang="es", audio_dir=tmp_path, timeout_seconds=15.0))
    assert res["voice"] == "es-ES-ElviraNeural"
    mp3_file = tmp_path / res["audio_filename"]
    assert mp3_file.exists()
    assert mp3_file.stat().st_size > 1000
    with open(mp3_file, "rb") as f:
        header = f.read(3)
        assert header.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"))
