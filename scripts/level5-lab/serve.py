import sys,json,threading,argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import make_server
p=argparse.ArgumentParser();p.add_argument('--db',required=True);a=p.parse_args()
s=make_server(a.db);t=threading.Thread(target=s.serve_forever);t.start();print(json.dumps({'url':'http://127.0.0.1:'+str(s.server_address[1])}),flush=True)
try:
 for line in sys.stdin:
  if line.strip()=='shutdown':break
finally:s.shutdown();t.join();s.server_close()
