import argparse, json, os, subprocess, sys, tempfile, shutil
ap=argparse.ArgumentParser(); ap.add_argument("--workspace",required=True); ap.add_argument("--json-out",required=True)
a=ap.parse_args()
ok=os.path.exists(os.path.join(a.workspace,"hello.py"))
extra=""
if ok:
    code="import sys;sys.path.insert(0,%r);import hello;print(hello.greet('X'))" % a.workspace
    p=subprocess.run([sys.executable,"-c",code],capture_output=True,text=True)
    ok = p.returncode==0 and p.stdout.strip()=="Hello, X!"
    extra=p.stderr[-300:]
out={"task_id":"smoke","status":"PASS" if ok else "FAIL","total":100 if ok else 0,
     "gates":[{"id":"G_FUNC","severity":"P0","passed":ok,"detail":extra,"evidence":"hidden"}],
     "cases":[{"id":"c1","requirement":"greet","severity":"P0","passed":ok,"detail":extra,"duration_ms":0}],
     "determinism":{"repeats":1,"agreement":True}}
json.dump(out,open(a.json_out,"w"),indent=2)
print(json.dumps({"status":out["status"]}))
sys.exit(0 if ok else 1)
