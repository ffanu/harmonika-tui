"""Keep key-authenticated per-port forwards; never terminate/adopt other processes."""
import fcntl, os, socket, subprocess, time
from pathlib import Path


def listening(port):
    try:
        with socket.create_connection(('127.0.0.1',port),.5):return True
    except OSError:return False


def main():
    root=Path.home()/'.config/harmonika-terminal/media-runtime';root.mkdir(mode=0o700,exist_ok=True)
    lock=open(root/'tunnels.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:return
    children={};retry={};ports=(18200,18188)
    while True:
        for port in ports:
            child=children.get(port)
            if child and child.poll() is not None:
                children.pop(port);retry[port]=time.monotonic()+5
            if listening(port) or port in children or time.monotonic()<retry.get(port,0):continue
            children[port]=subprocess.Popen(['/usr/bin/ssh','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes',
                '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=15',
                '-o','ServerAliveCountMax=3','-NT','-L',f'127.0.0.1:{port}:127.0.0.1:{port}','win-kraken'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        time.sleep(2)

if __name__=='__main__':main()
