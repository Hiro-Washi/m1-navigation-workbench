"""Actual HTTP from an isolated copy, empty venv, no package installation."""
import base64
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import urllib.request
import urllib.error
import venv

root=Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='planning-first-use-') as directory:
 work=Path(directory);copy=work/'candidate';copy.mkdir()
 manifest=json.loads((root/'source-manifest.json').read_text())
 for name in [*manifest['files'],'source-manifest.json']:
  p=copy/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/name,p)
 venv.create(work/'venv',with_pip=False)
 python=work/'venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
 env={k:v for k,v in os.environ.items() if not k.startswith('M1_') and k not in ('NODE_OPTIONS','PYTHONPATH','PYTHONHOME')}
 env.update(M1_PLANNING_PORT='0',M1_PLANNING_PYTHON=str(python),M1_NAV_PLANS_ROOT=str(work/'data'),PYTHONDONTWRITEBYTECODE='1')
 child=subprocess.Popen(['node',str(copy/'preview/planning_server.mjs')],cwd=copy,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  lines=queue.Queue();threading.Thread(target=lambda:lines.put(child.stdout.readline()),daemon=True).start()
  url=json.loads(lines.get(timeout=10))['url'];opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
  def request(path,body=None,origin=None):
   headers={'Origin':origin or url}
   if body is not None:headers['Content-Type']='application/json'
   req=urllib.request.Request(url+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
   try:response=opener.open(req,timeout=12)
   except urllib.error.HTTPError as e:response=e
   with response:return response.status,response.read()
  def call(action,body=None):
   status,raw=request('/api/planning/'+action,body);assert status==200,(status,raw);return json.loads(raw)['result']
  assert call('list')==[]
  for asset in ['planning.html','planning.css','planning.mjs','planning_model.mjs']:assert request('/'+asset)[0]==200
  assert '実機Console'.encode() in request('/live.html')[1]
  assert request('/api/operator/state')[0]==404
  assert request('/api/planning/list',origin='https://example.invalid')[0]==403
  assert request('/api/planning/execute',{})[0]==404
  assert request('/source-manifest.json')[0]==404
  yaml=b'image: ignored.pgm\nresolution: 0.1\norigin: [1, 2, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\nmode: trinary\n'
  pgm=b'P5\n20 20\n255\n'+bytes([254])*400
  binding=call('import_map',{'yaml_base64':base64.b64encode(yaml).decode(),'pgm_base64':base64.b64encode(pgm).decode()})
  assert len(base64.b64decode(call('map',binding)['pixels_base64']))==400
  doc=dict(schema='m1-mission-plan/1',revision=0,map=binding,profile=dict(translation_mps=.15,rotation_rps=.5),
   waypoints=[dict(id='a',name='入口',x=1.3,y=2.5,yaw=0),dict(id='b',name='戻り',x=2.3,y=3.2,yaw=1)],
   routes=[dict(id='r',name='経路',waypoint_ids=['a','b'])],patrols=[dict(id='p',name='巡回',route_id='r',cycles=2,dwell_seconds=1)],
   keepouts=[dict(id='k',vertices=[[1.1,2.1],[1.2,2.1],[1.2,2.2]])])
  record=call('create',{'document':doc});doc=call('load',{'id':record['id']});assert doc['revision']==1
  doc['waypoints'][0]['name']='更新'
  body={'id':record['id'],'document':doc,'expected_revision':1}
  assert call('update',body)['revision']==2
  assert request('/api/planning/update',body)[0]==409
  assert call('load',{'id':record['id']})['waypoints'][0]['name']=='更新'
  assert len(call('list'))==1
  bad=json.loads(json.dumps(doc));bad['profile']['translation_mps']=.151
  assert request('/api/planning/create',{'document':bad})[0]==400
  print(json.dumps({'first_use_http':True,'empty_venv':True,'third_party_packages':0,'robot_endpoints':False,'revision_conflict_preserved':True}))
 finally:
  child.terminate()
  try:child.communicate(timeout=12)
  except subprocess.TimeoutExpired:child.kill();child.communicate(timeout=5);raise
  assert child.returncode is not None
print('owned server reaped')
