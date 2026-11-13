"""Install additive, boot supervised vision worker; preserve existing listeners."""
from pathlib import Path
import json, os, socket, subprocess


def main():
    if os.name != 'nt':
        raise SystemExit('windows_only')
    control = Path(__file__).resolve().parent
    root = Path(r'C:\ProgramData\Harmonika\RTXKeepAlive')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 18188)) == 0:
            raise SystemExit('existing_listener_preserved')
    target = root/'vision-quality.json'
    if target.exists():
        raise SystemExit('existing_config_preserved')
    target.write_text(json.dumps({'name':'VisionQuality','port':18188,
        'executable':r'C:\Users\Kraken\harmonika-vision-worker\.venv\Scripts\python.exe',
        'directory':str(control),'arguments':'-u vision_worker.py',
        'environment':{'PYTHONUNBUFFERED':'1','DO_NOT_TRACK':'1'}}))
    wrapper = (root/'HighVideo.ps1').read_text().replace('high-video.json','vision-quality.json')
    (root/'VisionQuality.ps1').write_text(wrapper)
    script = r'''$ErrorActionPreference='Stop'
if(Get-ScheduledTask -TaskName HarmonikaVisionQuality -ErrorAction SilentlyContinue){throw 'existing_task_preserved'}
$a=New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\ProgramData\Harmonika\RTXKeepAlive\VisionQuality.ps1"'
$t=New-ScheduledTaskTrigger -AtStartup
$p=New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName HarmonikaVisionQuality -Action $a -Trigger $t -Principal $p -Settings $s|Out-Null
Start-ScheduledTask -TaskName HarmonikaVisionQuality
'''
    r=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],capture_output=True)
    if r.returncode:raise SystemExit('vision_task_setup_failed')
    target=root/'components.json'; original=target.read_bytes(); cfg=json.loads(original)
    if 'HarmonikaVisionQuality' not in cfg['managed_tasks']:
        (control/'components-before-vision.json').write_bytes(original)
        cfg['managed_tasks'].append('HarmonikaVisionQuality')
        if target.read_bytes()!=original:raise SystemExit('concurrent_watchdog_edit')
        tmp=target.with_suffix('.vision.tmp');tmp.write_text(json.dumps(cfg,indent=2));tmp.replace(target)
    legacy=Path(r'C:\Users\Kraken\harmonika-vision-worker\start-worker.ps1')
    original=legacy.read_bytes();backup=control/'legacy-start-worker-before.ps1'
    if not backup.exists():backup.write_bytes(original)
    launcher=r'''$ErrorActionPreference='Stop'
$c=New-Object Net.Sockets.TcpClient
try {$c.Connect('127.0.0.1',18188);exit 0} catch {} finally {$c.Dispose()}
Start-ScheduledTask -TaskName HarmonikaVisionQuality
'''
    if legacy.read_bytes()!=original:raise SystemExit('concurrent_launcher_edit')
    legacy.write_text(launcher)
    print(json.dumps({'installed':True,'port':18188,'inference':'test_required'}))

if __name__=='__main__':main()
