import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parent.parent
for n,h in json.loads((root/'source-manifest.json').read_text())['files'].items():
 assert hashlib.sha256((root/n).read_bytes()).hexdigest()==h,n
env={k:v for k,v in os.environ.items() if not k.startswith('M1_') and k not in ('NODE_OPTIONS','PYTHONPATH','PYTHONHOME')}
env['PYTHONDONTWRITEBYTECODE']='1'
for cwd,cmd in [
 ('navigation',[sys.executable,'-B','-m','unittest','-v','test_mission_plan','test_planning_service']),
 ('preview',['node','--test','tests/planning_model.test.mjs']),
 ('.',[sys.executable,'-B','ci/first_use.py'])]:
 subprocess.run(cmd,cwd=root/cwd,env=env,check=True,timeout=90)
print('Standalone planner checks passed; no robot or browser acceptance')
