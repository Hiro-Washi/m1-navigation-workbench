"""Bounded mission documents only. No navigation execution or ROS dependencies."""
import copy
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid
from contextlib import contextmanager

SCHEMA='m1-mission-plan/1'
MAX_BYTES=262144
DEFAULT_PROFILE={'translation_mps':.15,'rotation_rps':.5}
HARD_PROFILE=dict(DEFAULT_PROFILE)

class PlanError(ValueError):pass
class RevisionConflict(PlanError):pass
class StoreBusy(PlanError):pass

def require(ok, reason):
    if not ok:raise PlanError(reason)

def keys(obj, expected):
    require(type(obj) is dict and set(obj)==set(expected.split()),'unexpected_fields')

def ident(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}',value) is not None,'invalid_id')
    return value

def number(value, lo, hi):
    require(type(value) in (int,float) and lo<=value<=hi and math.isfinite(value),'invalid_number')
    return value

def integer(value,lo,hi):
    require(type(value) is int and lo<=value<=hi,'invalid_integer')

def name(value):
    require(type(value) is str and 0<len(value)<=128 and not any(ord(c)<32 or ord(c)==127 for c in value),'invalid_name')

def seq(value,lo,hi):
    require(type(value) is list and lo<=len(value)<=hi,'invalid_array')

def encode(doc):
    try:raw=json.dumps(doc,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
    except (ValueError,TypeError,UnicodeError,RecursionError) as exc:raise PlanError('invalid_json') from exc
    require(len(raw)<=MAX_BYTES,'document_too_large')
    return raw

def decode(raw):
    require(type(raw) is bytes and len(raw)<=MAX_BYTES,'invalid_bytes')
    def pairs(items):
        d={}
        for k,v in items:
            require(k not in d,'duplicate_json_key');d[k]=v
        return d
    try:return json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,
        parse_constant=lambda _:(_ for _ in ()).throw(PlanError('nonfinite_json')))
    except (ValueError,UnicodeError,RecursionError) as exc:raise PlanError('invalid_json') from exc

def validate_plan(doc):
    encode(doc)
    keys(doc,'schema revision map profile waypoints routes patrols keepouts')
    require(doc['schema']==SCHEMA,'schema_unsupported');integer(doc['revision'],0,2147483647)
    m=doc['map'];keys(m,'id yaml_sha256 pgm_sha256 frame resolution origin size');ident(m['id'])
    require(m['frame']=='map','frame_must_be_map')
    for k in ('yaml_sha256','pgm_sha256'):
        require(type(m[k]) is str and re.fullmatch('[0-9a-f]{64}',m[k]) is not None,'invalid_sha256')
    number(m['resolution'],.0001,10);seq(m['origin'],3,3);seq(m['size'],2,2)
    for x in m['origin'][:2]:number(x,-100000,100000)
    number(m['origin'][2],-math.pi,math.pi)
    for n in m['size']:integer(n,1,100000)
    require(m['size'][0]*m['size'][1]<=4000000,'map_too_large')
    p=doc['profile'];keys(p,'translation_mps rotation_rps')
    for k,limit in HARD_PROFILE.items():number(p[k],.000001,limit)
    def point(x,y):
        number(x,-100000,100000);number(y,-100000,100000)
        dx,dy=x-m['origin'][0],y-m['origin'][1];c=math.cos(m['origin'][2]);s=math.sin(m['origin'][2])
        require(0<=c*dx+s*dy<=m['size'][0]*m['resolution'] and
                0<=-s*dx+c*dy<=m['size'][1]*m['resolution'],'outside_map_extent')
    registries={}
    for collection,limit in (('waypoints',512),('routes',128),('patrols',128),('keepouts',128)):
        seq(doc[collection],0,limit);ids=set()
        for item in doc[collection]:
            require(type(item) is dict,'invalid_record');i=ident(item.get('id'))
            require(i not in ids,'duplicate_id');ids.add(i)
        registries[collection]=ids
    for w in doc['waypoints']:
        keys(w,'id name x y yaw');name(w['name']);point(w['x'],w['y']);number(w['yaw'],-math.pi,math.pi)
    for r in doc['routes']:
        keys(r,'id name waypoint_ids');name(r['name']);seq(r['waypoint_ids'],1,512)
        for i in r['waypoint_ids']:require(ident(i) in registries['waypoints'],'unknown_waypoint')
        require(len(set(r['waypoint_ids']))==len(r['waypoint_ids']),'duplicate_route_waypoint')
    for p in doc['patrols']:
        keys(p,'id name route_id cycles dwell_seconds');name(p['name'])
        require(ident(p['route_id']) in registries['routes'],'unknown_route')
        integer(p['cycles'],1,100);number(p['dwell_seconds'],0,3600)
    for k in doc['keepouts']:
        keys(k,'id vertices');v=k['vertices'];seq(v,3,64)
        for xy in v:seq(xy,2,2);point(*xy)
        require(len({tuple(xy) for xy in v})==len(v),'duplicate_polygon_vertex')
        area=sum(v[i][0]*v[(i+1)%len(v)][1]-v[(i+1)%len(v)][0]*v[i][1] for i in range(len(v)))
        require(abs(area)>1e-10,'degenerate_polygon')
        def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        for i,b in enumerate(v):
            a,c=v[i-1],v[(i+1)%len(v)]
            require(not (cross(a,b,c)==0 and (a[0]-b[0])*(c[0]-b[0])+(a[1]-b[1])*(c[1]-b[1])>0),'overlapping_polygon_edges')
        def intersects(a,b,c,d):
            if max(a[0],b[0])<min(c[0],d[0]) or max(c[0],d[0])<min(a[0],b[0]) or max(a[1],b[1])<min(c[1],d[1]) or max(c[1],d[1])<min(a[1],b[1]):return False
            return cross(a,b,c)*cross(a,b,d)<=0 and cross(c,d,a)*cross(c,d,b)<=0
        for i in range(len(v)):
            for j in range(i+1,len(v)):
                if j==i+1 or (i==0 and j==len(v)-1):continue
                require(not intersects(v[i],v[(i+1)%len(v)],v[j],v[(j+1)%len(v)]),'self_intersecting_polygon')
    return copy.deepcopy(doc)

class MissionPlanStore:
    """Trusted fixed root, exclusive fail-closed lock, atomic file replacement.

    Root must be application-owned: hostile filesystem writers are not supported.
    A crash may leave .store.lock; never auto-break an unknown owner's lock.
    """
    def __init__(self,root):
        raw=Path(root);require(not raw.is_symlink(),'symlink_root')
        self.root=raw.resolve();source=Path(__file__).resolve().parents[1]
        require(not self.root.is_relative_to(source),'store_inside_source')
        self.root.mkdir(parents=True,exist_ok=True)
    def _path(self,record_id):
        require(type(record_id) is str and re.fullmatch('[0-9a-f]{32}',record_id) is not None,'invalid_record_id')
        p=self.root/(record_id+'.json');require(not p.is_symlink(),'symlink_record');return p
    @contextmanager
    def _locked(self):
        lock=self.root/'.store.lock'
        try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError as exc:raise StoreBusy('store_busy') from exc
        try:yield
        finally:os.close(fd);lock.unlink()
    def _write(self,path,doc):
        fd,tmp=tempfile.mkstemp(prefix='.plan-',dir=self.root)
        try:
            with os.fdopen(fd,'wb') as f:f.write(encode(doc));f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
            if os.name=='posix':
                d=os.open(self.root,os.O_RDONLY)
                try:os.fsync(d)
                finally:os.close(d)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    def load(self,record_id):
        with self._path(record_id).open('rb') as f:raw=f.read(MAX_BYTES+1)
        return validate_plan(decode(raw))
    def create(self,document):
        doc=validate_plan(document);doc['revision']=1
        with self._locked():
            require(len(self._records())<50,'store_capacity')
            rid=uuid.uuid4().hex;path=self._path(rid);require(not path.exists(),'record_exists');self._write(path,doc)
        return {'id':rid,'document':doc}
    def update(self,record_id,document,expected_revision):
        integer(expected_revision,1,2147483646);doc=validate_plan(document)
        with self._locked():
            current=self.load(record_id)
            if current['revision']!=expected_revision or doc['revision']!=expected_revision:raise RevisionConflict('revision_conflict')
            require(current['map']==doc['map'],'map_binding_immutable')
            doc['revision']=expected_revision+1;self._write(self._path(record_id),doc)
        return doc
    def export(self,record_id):return encode(self.load(record_id))
    def import_plan(self,raw):return self.create(decode(raw))
    def _records(self):
        records=[]
        for p in self.root.iterdir():
            if re.fullmatch('[0-9a-f]{32}\\.json',p.name):
                records.append(p.stem)
                require(len(records)<=50,'store_capacity')
        return sorted(records)
    def list(self):
        result=[]
        for rid in self._records():
            try:
                d=self.load(rid)
                result.append(dict(id=rid,revision=d['revision'],map_id=d['map']['id'],
                    counts={k:len(d[k]) for k in ('waypoints','routes','patrols','keepouts')}))
            except (OSError,PlanError):result.append(dict(id=rid,error='invalid_record'))
        return result
