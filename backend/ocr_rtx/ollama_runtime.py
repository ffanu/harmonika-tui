"""Supervised local OCR/VLM runtime. No raw inference output reaches logs."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

VISION = Path(r'C:\Users\Kraken\harmonika-vision-worker')
EXECUTABLE = VISION/'ollama-v0.40.11/ollama.exe'
MODELS = VISION/'models'
CONTAINMENT = Path(r'C:\Users\Kraken\harmonika-video-toolkit-source\owned_job.py')


def environment(source):
    values = {k: v for k, v in source.items() if not any(
        s in k.upper() for s in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY'))}
    values.update(OLLAMA_HOST='127.0.0.1:11439', OLLAMA_MODELS=str(MODELS),
                  OLLAMA_NO_CLOUD='1', OLLAMA_NOHISTORY='1', OLLAMA_DEBUG='0',
                  OLLAMA_NUM_PARALLEL='1', OLLAMA_MAX_LOADED_MODELS='1',
                  OLLAMA_CONTEXT_LENGTH='16384', OLLAMA_KEEP_ALIVE='0',
                  OLLAMA_VULKAN='0', OLLAMA_FLASH_ATTENTION='1',
                  OLLAMA_KV_CACHE_TYPE='q8_0', HTTP_PROXY='', HTTPS_PROXY='',
                  ALL_PROXY='', NO_PROXY='127.0.0.1,localhost')
    return values


def main():
    if os.name != 'nt' or not EXECUTABLE.is_file() or not MODELS.is_dir():
        raise SystemExit('ocr_runtime_dependencies_missing')
    spec = importlib.util.spec_from_file_location('ocr_owned_child', CONTAINMENT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    child = subprocess.Popen([str(EXECUTABLE), 'serve'], cwd=VISION,
                             env=environment(os.environ), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=0x08000004)
    guard = None
    try:
        guard = module.WindowsJob(child)
        return child.wait()
    finally:
        if guard:
            guard.close()
        if child.poll() is None:
            child.kill(); child.wait(timeout=10)  # Only the child created above.


if __name__ == '__main__':
    sys.exit(main())
