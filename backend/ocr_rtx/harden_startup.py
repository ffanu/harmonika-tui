"""Unify legacy runtime launchers and supervise the existing stateless worker."""
from pathlib import Path
import json
import os
import subprocess


def main():
    if os.name != 'nt':
        raise SystemExit('windows_only')
    control = Path(__file__).resolve().parent
    keep = Path(r'C:\ProgramData\Harmonika\RTXKeepAlive')
    script = Path(r'C:\Users\Kraken\harmonika-vision-worker\start-runtime.ps1')
    replacement = r'''$ErrorActionPreference='Stop'
function Test-OCRRuntimePort {
    $client=New-Object Net.Sockets.TcpClient
    try { $client.Connect('127.0.0.1',11439);return $true }
    catch { return $false }
    finally { $client.Close() }
}
if(Test-OCRRuntimePort){exit 0}
Start-ScheduledTask -TaskName HarmonikaOCRRuntime
for($attempt=0;$attempt -lt 15;$attempt++){
    if(Test-OCRRuntimePort){exit 0}
    Start-Sleep -Seconds 1
}
exit 1
'''
    original = script.read_bytes()
    original_text = original.decode().replace('\r\n', '\n')
    if original_text != replacement:
        previous_wrapper = "$ErrorActionPreference='Stop'\n& 'C:\\ProgramData\\Harmonika\\RTXKeepAlive\\OCRRuntime.ps1'\n"
        if 'ollama-v0.40.11' not in original_text and original_text != previous_wrapper:
            raise SystemExit('unexpected_legacy_launcher_preserved')
        backup = control/'start-runtime.before-ocr.ps1'
        if not backup.exists():
            backup.write_bytes(original)
        if script.read_bytes() != original:
            raise SystemExit('concurrent_launcher_edit')
        script.write_text(replacement)
    ps = r'''$ErrorActionPreference='Stop'
$task=Get-ScheduledTask -TaskName HarmonikaStatelessOCR -ErrorAction Stop
$backup='C:\Users\Kraken\harmonika-ocr-runtime\worker-task.before.xml'
if(-not(Test-Path -LiteralPath $backup)){Export-ScheduledTask -TaskName HarmonikaStatelessOCR|Set-Content -LiteralPath $backup -Encoding UTF8}
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Set-ScheduledTask -TaskName HarmonikaStatelessOCR -Settings $settings|Out-Null
'''
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('worker_recovery_settings_failed')
    target = keep/'components.json'; original = target.read_bytes(); cfg = json.loads(original)
    if 'HarmonikaStatelessOCR' not in cfg['managed_tasks']:
        (control/'components-before-ocr-worker.json').write_bytes(original)
        cfg['managed_tasks'].append('HarmonikaStatelessOCR')
        if target.read_bytes() != original:
            raise SystemExit('concurrent_watchdog_edit')
        temporary = target.with_suffix('.ocr-worker.tmp'); temporary.write_text(json.dumps(cfg, indent=2))
        temporary.replace(target)
    print(json.dumps({'legacy_launchers_share_mutex': True, 'worker_watchdog': True,
                      'running_processes_stopped': False}))


if __name__ == '__main__':
    main()
