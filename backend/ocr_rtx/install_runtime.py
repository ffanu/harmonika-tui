"""Add an OCR runtime boot task without adopting/stopping existing processes."""
from pathlib import Path
import json
import os
import socket
import subprocess


def main():
    if os.name != 'nt':
        raise SystemExit('windows_only')
    control = Path(__file__).resolve().parent
    root = Path(r'C:\ProgramData\Harmonika\RTXKeepAlive')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 11439)) == 0:
            raise SystemExit('existing_runtime_listener_preserved')
    config = {'name': 'OCRRuntime', 'port': 11439,
              'executable': r'C:\Users\Kraken\AppData\Local\Programs\Python\Python312\python.exe',
              'directory': str(control), 'arguments': '-u ollama_runtime.py',
              'environment': {'PYTHONUNBUFFERED': '1', 'DO_NOT_TRACK': '1'}}
    target = root/'ocr-runtime.json'
    if target.exists():
        raise SystemExit('existing_runtime_config_preserved')
    target.write_text(json.dumps(config))
    wrapper = (root/'HighVideo.ps1').read_text().replace('high-video.json', 'ocr-runtime.json')
    (root/'OCRRuntime.ps1').write_text(wrapper)
    script = r'''$ErrorActionPreference='Stop'
if(Get-ScheduledTask -TaskName HarmonikaOCRRuntime -ErrorAction SilentlyContinue){throw 'existing_task_preserved'}
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\ProgramData\Harmonika\RTXKeepAlive\OCRRuntime.ps1"'
$trigger=New-ScheduledTaskTrigger -AtStartup
$principal=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName HarmonikaOCRRuntime -Action $action -Trigger $trigger -Principal $principal -Settings $settings|Out-Null
Start-ScheduledTask -TaskName HarmonikaOCRRuntime
'''
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('ocr_task_setup_failed')
    target = root/'components.json'; original = target.read_bytes(); cfg = json.loads(original)
    if 'HarmonikaOCRRuntime' not in cfg['managed_tasks']:
        (control/'components-before-ocr.json').write_bytes(original)
        cfg['managed_tasks'].append('HarmonikaOCRRuntime')
        if target.read_bytes() != original:
            raise SystemExit('concurrent_watchdog_edit')
        temporary = target.with_suffix('.ocr.tmp'); temporary.write_text(json.dumps(cfg, indent=2))
        temporary.replace(target)
    print(json.dumps({'installed': True, 'port': 11439, 'readiness': 'inference_test_required'}))


if __name__ == '__main__':
    main()
