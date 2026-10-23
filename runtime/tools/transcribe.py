"""Local CPU speech-to-text, no API key or private-audio upload."""
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

def main():
    ffprobe=os.environ.get('HARMONIKA_FFPROBE') or shutil.which('ffprobe') or str(Path.home()/'.local/bin/ffprobe')
    ffmpeg=os.environ.get('HARMONIKA_FFMPEG') or shutil.which('ffmpeg') or str(Path.home()/'.local/bin/ffmpeg')
    source = Path(sys.argv[1]).resolve(strict=True)
    language = sys.argv[2] if len(sys.argv)>2 and sys.argv[2] not in ("auto", "") else None
    if language is not None and (not language.isalpha() or len(language)!=2):
        raise ValueError("language")
    if not source.is_file() or source.stat().st_size > 50*1024*1024:
        raise ValueError("file")
    probe = subprocess.run([ffprobe, "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,mp3,wav,ogg,matroska,webm,aac,flac", "-show_entries", "format=duration", "-of", "json", str(source)], capture_output=True, timeout=15, check=True)
    duration = float(json.loads(probe.stdout)["format"]["duration"])
    if not math.isfinite(duration) or duration<=0 or duration>600:
        raise ValueError("duration")
    with tempfile.TemporaryDirectory(prefix="harmonika-stt-") as directory:
        output = str(Path(directory)/"input.wav")
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "mov,mp3,wav,ogg,matroska,webm,aac,flac", "-i", str(source), "-t", "120", "-vn", "-ac", "1", "-ar", "16000", output], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
        from faster_whisper import WhisperModel
        cache = str(Path.home()/".cache"/"harmonika-whisper")
        model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=2, num_workers=1, download_root=cache, local_files_only=True)
        segments, info = model.transcribe(output, language=language, beam_size=3, vad_filter=True)
        transcript = " ".join(part.text.strip() for part in segments).strip()
        print(json.dumps({"ok": bool(transcript), "status": "ok" if transcript else "degraded", "transcript": transcript[:30000], "language": info.language,
            "confidence": None, "language_probability": info.language_probability, "seconds": min(duration,120), "truncated": duration>120,
            "warnings": (["Hanya 120 detik pertama yang diproses."] if duration>120 else [])}))

if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except Exception:
        print(json.dumps({"ok": False, "status": "degraded", "transcript": "", "error": {"code": "transcription_unavailable"}}))
