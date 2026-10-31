import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mission_plan import MissionPlanStore,PlanError,RevisionConflict,StoreBusy,validate_plan,decode,MAX_BYTES

def example():
    return dict(schema='m1-mission-plan/1',revision=0,
        map=dict(id='room101',yaml_sha256='a'*64,pgm_sha256='b'*64,frame='map',resolution=.05,origin=[0,0,0],size=[200,200]),
        profile=dict(translation_mps=.15,rotation_rps=.5),
        waypoints=[dict(id='a',name='入口',x=1,y=2,yaw=0),dict(id='b',name='机',x=2,y=3,yaw=1)],
        routes=[dict(id='r',name='往路',waypoint_ids=['a','b'])],
        patrols=[dict(id='p',name='昼巡回',route_id='r',cycles=2,dwell_seconds=1)],
        keepouts=[dict(id='k',vertices=[[3,3],[4,3],[4,4],[3,4]])])

class ModelTests(unittest.TestCase):
    def test_roundtrip_revision_and_binding(self):
        with tempfile.TemporaryDirectory() as root:
            s=MissionPlanStore(root);r=s.create(example());self.assertEqual(r['document']['revision'],1)
            doc=s.load(r['id']);doc['waypoints'][0]['name']='変更';doc=s.update(r['id'],doc,1)
            self.assertEqual(doc['revision'],2)
            with self.assertRaises(RevisionConflict):s.update(r['id'],doc,1)
            imported=s.import_plan(s.export(r['id']))
            self.assertNotEqual(imported['id'],r['id']);self.assertEqual(imported['document']['revision'],1)
            doc['map']['id']='other'
            with self.assertRaises(PlanError):s.update(r['id'],doc,2)
            self.assertEqual(s.load(r['id'])['map']['id'],'room101')

    def test_reject_bad_numeric_schema_references_and_duplicates(self):
        mutations=[lambda d:d.update(extra=1),lambda d:d.update(schema='unknown'),
            lambda d:d['profile'].update(translation_mps=.150001),lambda d:d['profile'].update(rotation_rps=.500001),
            lambda d:d['waypoints'][0].update(x=float('nan')),lambda d:d['waypoints'][0].update(x=True),
            lambda d:d['waypoints'][0].update(y=10**400),lambda d:d['waypoints'][0].update(x=-1),
            lambda d:d['map'].update(yaml_sha256='../x'),lambda d:d['map'].update(frame='odom'),
            lambda d:d['map'].update(size=[0,1]),lambda d:d['patrols'][0].update(cycles=0),
            lambda d:d['patrols'][0].update(cycles=1.5),lambda d:d['patrols'][0].update(dwell_seconds=float('inf')),
            lambda d:d['routes'][0].update(waypoint_ids=['missing']),lambda d:d['routes'][0].update(waypoint_ids=['a','a']),
            lambda d:d['patrols'][0].update(route_id='missing'),lambda d:d['waypoints'].append(copy.deepcopy(d['waypoints'][0])),
            lambda d:d['waypoints'][0].update(id='../a'),lambda d:d['waypoints'][0].update(name='bad\nname')]
        for mutate in mutations:
            d=example();mutate(d)
            with self.subTest(document=d),self.assertRaises(PlanError):validate_plan(d)

    def test_polygon_and_rotated_map(self):
        d=example();d['keepouts'][0]['vertices']=[[3,3],[5,5],[3,5],[5,3]]
        with self.assertRaises(PlanError):validate_plan(d)
        d=example();d['keepouts'][0]['vertices']=[[3,3],[4,3],[5,3]]
        with self.assertRaises(PlanError):validate_plan(d)
        d=example();d['map']['origin']=[10,0,1.5707963267948966]
        d['waypoints']=[];d['routes']=[];d['patrols']=[];d['keepouts']=[dict(id='k',vertices=[[9,1],[8,1],[8,2]])]
        validate_plan(d)

    def test_strict_json_bytes_and_duplicate_keys(self):
        for raw in (b'{"schema":1,"schema":2}',b'{"v":NaN}',b'\xff',b' '* (MAX_BYTES+1)):
            with self.assertRaises(PlanError):decode(raw)
        d=example();d['waypoints'][0]['name']='x'*129
        with self.assertRaises(PlanError):validate_plan(d)

    def test_atomic_failure_keeps_previous_document_and_unlocks(self):
        with tempfile.TemporaryDirectory() as root:
            s=MissionPlanStore(root);r=s.create(example());old=s.export(r['id']);doc=s.load(r['id']);doc['waypoints'][0]['x']=1.5
            with patch('mission_plan.os.replace',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):s.update(r['id'],doc,1)
            self.assertEqual(s.export(r['id']),old)
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()),[r['id']+'.json'])
            self.assertEqual(s.update(r['id'],doc,1)['revision'],2)

    def test_concurrent_store_revision_and_exclusive_lock(self):
        with tempfile.TemporaryDirectory() as root:
            a,b=MissionPlanStore(root),MissionPlanStore(root);r=a.create(example());stale=b.load(r['id'])
            a.update(r['id'],r['document'],1)
            with self.assertRaises(RevisionConflict):b.update(r['id'],stale,1)
            with a._locked():
                with self.assertRaises(StoreBusy):b.create(example())

    def test_paths_and_source_root_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            s=MissionPlanStore(root)
            for rid in ('../outside','/absolute','a'*32+'.json','A'*32,'..'):
                with self.assertRaises(PlanError):s.load(rid)
            with self.assertRaises(PlanError):MissionPlanStore(Path(__file__).parent/'store-forbidden')

    def test_bounded_inventory_and_corruption_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            s=MissionPlanStore(root);r=s.create(example())
            self.assertEqual(s.list()[0]['counts']['waypoints'],2)
            (Path(root)/(r['id']+'.json')).write_bytes(b'bad')
            self.assertEqual(s.list(),[dict(id=r['id'],error='invalid_record')])
            for _ in range(49):s.create(example())
            self.assertEqual(len(s.list()),50)
            with self.assertRaises(PlanError):s.create(example())
        d=example();d['map']['size']=[2001,2000]
        with self.assertRaises(PlanError):validate_plan(d)

if __name__=='__main__':unittest.main()
