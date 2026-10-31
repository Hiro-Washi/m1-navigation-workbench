import base64
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mission_plan import RevisionConflict
from planning_service import PlanningService, parse_pgm, parse_map_yaml, bounded_read, MAX_REQUEST
from test_mission_plan import example

YAML=b'image: /never/follow/this.pgm\nresolution: 0.05\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\nmode: trinary\n'
PGM=b'P5\n# saved map\n100 100\n255\n'+bytes([254])*10000
def body(yaml=YAML,pgm=PGM):
    return dict(yaml_base64=base64.b64encode(yaml).decode(),pgm_base64=base64.b64encode(pgm).decode())

class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'library';self.service=PlanningService(self.root)
    def tearDown(self):self.temp.cleanup()
    def mapping(self):return self.service.request(dict(action='import_map',body=body()))

    def test_clean_first_use_import_store_reload_export_revision(self):
        self.assertEqual(self.service.request({'action':'list'}),[])
        binding=self.mapping();self.assertEqual(self.mapping(),binding)
        doc=example();doc['map']=binding
        saved=self.service.request(dict(action='create',body={'document':doc}))
        other=PlanningService(self.root)
        loaded=other.request(dict(action='load',body={'id':saved['id']}))
        self.assertEqual(loaded,saved['document'])
        self.assertEqual(len(base64.b64decode(other.request(dict(action='map',body=binding))['pixels_base64'])),10000)
        loaded['waypoints'][0]['name']='更新'
        changed=other.request(dict(action='update',body=dict(id=saved['id'],document=loaded,expected_revision=1)))
        self.assertEqual(changed['revision'],2)
        with self.assertRaises(RevisionConflict):self.service.request(dict(action='update',body=dict(id=saved['id'],document=loaded,expected_revision=1)))
        self.assertEqual(json.loads(other.store.export(saved['id']))['revision'],2)

    def test_tamper_and_metadata_mismatch_reject_plan_creation(self):
        binding=self.mapping();d=copy.deepcopy(binding);d['resolution']=.1
        with self.assertRaisesRegex(ValueError,'map_metadata_mismatch'):self.service.read_map(d)
        d=copy.deepcopy(binding);d['size']=[True,10000]
        with self.assertRaises(ValueError):self.service.read_map(d)
        path=self.service.maps/self.service.map_key(binding)/'map.pgm';path.write_bytes(PGM[:-1]+b'\0')
        with self.assertRaisesRegex(ValueError,'map_hash_mismatch'):self.service.read_map(binding)
        doc=example();doc['map']=binding
        with self.assertRaises(ValueError):self.service.request(dict(action='create',body={'document':doc}))
        self.assertEqual(self.service.store.list(),[])

    def test_bounded_disk_read_and_missing_asset(self):
        binding=self.mapping();p=self.service.maps/self.service.map_key(binding)/'map.yaml'
        p.write_bytes(b'x'*65537)
        with self.assertRaisesRegex(ValueError,'stored_asset_size_limit'):self.service.read_map(binding)
        p.unlink()
        with self.assertRaisesRegex(ValueError,'map_not_found'):self.service.read_map(binding)
        class Stream:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):self.n=n;return b'x'*n
        stream=Stream()
        with patch.object(Path,'open',return_value=stream):
            with self.assertRaises(ValueError):bounded_read(Path('unused'),7)
        self.assertEqual(stream.n,8)

    def test_pgm_exact_raster_p2_and_p5(self):
        # P5 first byte may be whitespace or '#'; it is not skipped as a header.
        self.assertEqual(parse_pgm(b'P5\n2 1\n255\n\n#'),(2,1,b'\n#'))
        self.assertEqual(parse_pgm(b'P2\n# hi\n2 1\n255\n0 255 # end'),(2,1,b'\0\xff'))
        for raw in (b'P5\n2 1\n255\n\0',b'P2\n1 1\n255\n256',b'P2\n1 1\n255\n0 1',
                    b'P5\n4000 4000\n255\n',b'P2\n1_0 1\n255\n',b'P5\n1 1\n65535\nxx'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_pgm(raw)

    def test_yaml_restricted_and_model_yaw_contract(self):
        for raw in (YAML+b'resolution: 1\n',YAML+b'unknown: 1\n',YAML.replace(b'[0, 0, 0]',b'[0, 0, 4]'),
                    YAML.replace(b'0.05',b'.nan'),YAML.replace(b'0.05',b'1e999'),YAML.replace(b'trinary',b'scale'),
                    YAML.replace(b'[0, 0, 0]',b'!!python/object:thing'),YAML.replace(b'0.196',b'0.9')):
            with self.subTest(raw=raw),self.assertRaises(ValueError):parse_map_yaml(raw)

    def test_linked_asset_and_storage_rejected(self):
        binding=self.mapping();folder=self.service.maps/self.service.map_key(binding)
        target=Path(self.temp.name)/'other';target.write_bytes(YAML)
        (folder/'map.yaml').unlink()
        try:(folder/'map.yaml').symlink_to(target)
        except OSError as exc:self.skipTest('OS symlink privilege unavailable: '+str(type(exc).__name__))
        with self.assertRaisesRegex(ValueError,'linked_map'):self.service.read_map(binding)
        linked=Path(self.temp.name)/'link';linked.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'linked_storage'):PlanningService(linked/'child')

    def test_request_schema_and_traversal(self):
        for req in ([],{'action':[]},{'action':'list','root':'x'},{'action':'list','body':[]},
                    {'action':'load','body':{'id':'../outside'}},{'action':'map','body':{'yaml_sha256':'../outside'}},
                    {'action':'create','body':{'document':example(),'extra':1}}):
            with self.subTest(request=req),self.assertRaises(ValueError):self.service.request(req)

    def test_cli_known_errors_are_json_without_paths(self):
        script=Path(__file__).with_name('planning_service.py')
        binding=self.mapping();(self.service.maps/self.service.map_key(binding)/'map.pgm').unlink()
        requests=[(json.dumps(dict(action='map',body=binding)).encode(),'map_not_found'),
                  (b' '*(MAX_REQUEST+1),'request_too_large'),
                  (b'{"action":"list","action":"load"}','duplicate_json_key')]
        for raw,expected in requests:
            done=subprocess.run([sys.executable,'-B',str(script),str(self.root)],input=raw,capture_output=True,timeout=10)
            self.assertEqual(done.returncode,0);self.assertEqual(done.stderr,b'')
            self.assertEqual(json.loads(done.stdout),dict(ok=False,error=expected))

if __name__=='__main__':unittest.main()
