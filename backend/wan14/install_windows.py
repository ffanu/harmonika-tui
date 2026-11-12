"""Install the isolated candidate listener; activation still requires GPU evidence."""
from pathlib import Path
import json
import os
import subprocess


def main():
    if os.name != 'nt':
        raise SystemExit('windows_only')
    root = Path(r'C:\ProgramData\Harmonika\RTXKeepAlive')
    control = Path(__file__).resolve().parent
    config = json.loads((root/'high-video.json').read_text())
    config.update(name='Wan14Video', port=18263, directory=str(control))
    target = root/'wan14-video.json'
    if target.exists() and json.loads(target.read_text()) != config:
        raise SystemExit('existing_candidate_config_preserved')
    target.write_text(json.dumps(config))
    script = (root/'HighVideo.ps1').read_text().replace('high-video.json', 'wan14-video.json')
    (root/'Wan14Video.ps1').write_text(script)
    ps = r'''$ErrorActionPreference='Stop'
if(Get-ScheduledTask -TaskName HarmonikaWan14Video -ErrorAction SilentlyContinue){throw 'existing_task_preserved'}
$action=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\ProgramData\Harmonika\RTXKeepAlive\Wan14Video.ps1"'
$trigger=New-ScheduledTaskTrigger -AtStartup
$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName HarmonikaWan14Video -Action $action -Trigger $trigger -Principal $principal -Settings $settings |Out-Null
Start-ScheduledTask -TaskName HarmonikaWan14Video
'''
    result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit('candidate_task_setup_failed')
    path = root/'components.json'; original = path.read_bytes(); cfg = json.loads(original)
    name = 'HarmonikaWan14Video'
    if name not in cfg['managed_tasks']:
        (control/'components-before-wan14.json').write_bytes(original)
        cfg['managed_tasks'].append(name)
        if path.read_bytes() != original:
            raise SystemExit('watchdog_concurrent_edit')
        path.write_text(json.dumps(cfg, indent=2))
    print(json.dumps({'task_installed': True, 'port': 18263, 'activation': 'qualification_required'}))


if __name__ == '__main__':
    main()
