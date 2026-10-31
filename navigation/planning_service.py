"""Local, non-ROS mission library. Reads one bounded request and exits.

Map bytes are supplied explicitly; YAML image paths are never followed.
This service cannot connect to a robot or start any process.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from mission_plan import MissionPlanStore, validate_plan, SCHEMA, DEFAULT_PROFILE

MAX_REQUEST = 8 * 1024 * 1024
MAX_PIXELS = 4_000_000


def fail(message):
    raise ValueError(message)


def object_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            fail('duplicate_json_key')
        out[key] = value
    return out


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=object_pairs,
                      parse_constant=lambda _: fail('non_finite_number'))


def bounded_read(path, maximum):
    try:
        with path.open('rb') as stream:
            value = stream.read(maximum + 1)
    except FileNotFoundError:
        fail('map_not_found')
    if len(value) > maximum:
        fail('stored_asset_size_limit')
    return value


def safe_root(root):
    root = Path(root).absolute()
    for p in [root, *root.parents]:
        if p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()):
            fail('linked_storage')
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_pgm(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_PIXELS * 4 + 65536:
        fail('pgm_size_limit')
    position = 0

    def token():
        nonlocal position
        while position < len(raw):
            if raw[position] in b' \r\n\t':
                position += 1
            elif raw[position] == 35:
                end = raw.find(b'\n', position)
                position = len(raw) if end < 0 else end + 1
            else:
                break
        start = position
        while position < len(raw) and raw[position] not in b' \r\n\t#':
            position += 1
        if start == position or position - start > 32:
            fail('pgm_header')
        return raw[start:position]

    kind = token()
    if kind not in (b'P5', b'P2'):
        fail('pgm_requires_p5_or_p2')
    def decimal():
        value = token()
        if not re.fullmatch(rb'[0-9]+', value):
            fail('pgm_integer')
        return int(value)
    try:
        width, height, maximum = (decimal() for _ in range(3))
    except ValueError:
        fail('pgm_header')
    if not (1 <= width <= 4096 and 1 <= height <= 4096 and width * height <= MAX_PIXELS and maximum == 255):
        fail('pgm_dimensions_or_depth')
    size = width * height
    if kind == b'P5':
        if position >= len(raw) or raw[position] not in b' \r\n\t':
            fail('pgm_separator')
        position += 2 if raw[position:position+2] == b'\r\n' else 1
        pixels = raw[position:]
        if len(pixels) != size:
            fail('pgm_raster_length')
    else:
        try:
            pixels = bytes(decimal() for _ in range(size))
        except ValueError:
            fail('pgm_pixel')
        # Only trailing whitespace/comments are permitted.
        if re.sub(rb'#[^\n]*(?:\n|$)|\s+', b'', raw[position:]):
            fail('pgm_extra_pixels')
    return width, height, pixels


def parse_map_yaml(raw):
    """Restricted flat nav2 map_saver YAML. No generic YAML construction."""
    if len(raw) > 65536:
        fail('yaml_size_limit')
    values = {}
    for line in raw.decode('utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([a-z_]+):\s*(.*?)\s*', line)
        if not match:
            fail('unsupported_map_yaml')
        key, value = match.groups()
        if key in values:
            fail('duplicate_yaml_key')
        if key not in {'image', 'resolution', 'origin', 'negate', 'occupied_thresh', 'free_thresh', 'mode'}:
            fail('unsupported_map_yaml_field')
        if key in ('image', 'mode'):
            # Image is metadata only; not a path to open.
            value = value.split(' #', 1)[0].strip()
            if value.startswith(('!', '&', '*', '{', '[', '|', '>')):
                fail('unsupported_map_yaml_string')
            if value.startswith(('"', "'")):
                if len(value) < 2 or value[-1] != value[0]:
                    fail('map_yaml_quote')
                value = value[1:-1]
            if not value or len(value) > 256:
                fail('map_yaml_string')
            values[key] = value
        else:
            text = value.split('#', 1)[0].strip()
            if not re.fullmatch(r'[\[\]0-9eE+.,\s-]+', text):
                fail('map_yaml_number')
            try:
                values[key] = parse_json(text) if key == 'origin' else float(text)
            except (ValueError, TypeError):
                fail('map_yaml_number')
    if set(values) - {'mode'} != {'image', 'resolution', 'origin', 'negate', 'occupied_thresh', 'free_thresh'}:
        fail('map_yaml_fields')
    resolution, origin = values['resolution'], values['origin']
    if not math.isfinite(resolution) or not .0001 <= resolution <= 10:
        fail('map_resolution')
    if not isinstance(origin, list) or len(origin) != 3 or any(type(v) not in (float, int) or abs(v) > 100000 or not math.isfinite(v) for v in origin):
        fail('map_origin')
    if abs(origin[2]) > math.pi:
        fail('map_origin_yaw')
    if values['negate'] not in (0, 1) or not 0 <= values['free_thresh'] < values['occupied_thresh'] <= 1 or values.get('mode', 'trinary') != 'trinary':
        fail('map_requires_trinary_thresholds')
    return values


def decode(value, maximum):
    if not isinstance(value, str) or len(value) > maximum * 4 // 3 + 8:
        fail('asset_size_limit')
    try:
        result = base64.b64decode(value, validate=True)
    except ValueError:
        fail('invalid_asset_encoding')
    if len(result) > maximum:
        fail('asset_size_limit')
    return result


class PlanningService:
    def __init__(self, root):
        self.root = safe_root(root)
        self.maps = safe_root(self.root / 'maps')
        self.store = MissionPlanStore(self.root / 'plans')

    def map_key(self, binding):
        if type(binding) is not dict or set(binding) != {'id','yaml_sha256','pgm_sha256','frame','resolution','origin','size'}:
            fail('map_binding_fields')
        validate_plan(dict(schema=SCHEMA, revision=0, map=binding, profile=DEFAULT_PROFILE,
                           waypoints=[], routes=[], patrols=[], keepouts=[]))
        yaml_hash, pgm_hash = binding['yaml_sha256'], binding['pgm_sha256']
        if not all(isinstance(v, str) and re.fullmatch('[0-9a-f]{64}', v) for v in (yaml_hash, pgm_hash)):
            fail('invalid_map_hash')
        if binding['id'] != 'map-' + yaml_hash[:12] + '-' + pgm_hash[:12]:
            fail('map_binding_id')
        return yaml_hash + '_' + pgm_hash

    def import_map(self, body):
        if set(body) != {'yaml_base64', 'pgm_base64'}:
            fail('map_import_fields')
        yaml_raw = decode(body['yaml_base64'], 65536)
        pgm_raw = decode(body['pgm_base64'], MAX_PIXELS * 4 + 65536)
        meta = parse_map_yaml(yaml_raw)
        width, height, _ = parse_pgm(pgm_raw)
        yh, ph = (hashlib.sha256(v).hexdigest() for v in (yaml_raw, pgm_raw))
        binding = dict(id='map-' + yh[:12] + '-' + ph[:12], yaml_sha256=yh, pgm_sha256=ph,
                       frame='map', resolution=meta['resolution'], origin=meta['origin'], size=[width, height])
        path = self.maps / self.map_key(binding)
        if path.exists():
            self.read_map(binding)
        else:
            temporary = Path(tempfile.mkdtemp(prefix='import-', dir=self.maps))
            try:
                for filename, data in (('map.yaml',yaml_raw),('map.pgm',pgm_raw)):
                    with (temporary / filename).open('wb') as stream:
                        stream.write(data);stream.flush();os.fsync(stream.fileno())
                try:
                    temporary.rename(path)
                except OSError:
                    if not path.exists():
                        raise
                    # A concurrent identical import may win the rename.
                    self.read_map(binding)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        return binding

    def read_map(self, binding):
        path = self.maps / self.map_key(binding)
        for candidate in (path, path / 'map.yaml', path / 'map.pgm'):
            if candidate.is_symlink() or (hasattr(candidate, 'is_junction') and candidate.is_junction()):
                fail('linked_map')
        yr = bounded_read(path / 'map.yaml', 65536)
        pr = bounded_read(path / 'map.pgm', MAX_PIXELS * 4 + 65536)
        if hashlib.sha256(yr).hexdigest() != binding['yaml_sha256'] or hashlib.sha256(pr).hexdigest() != binding['pgm_sha256']:
            fail('map_hash_mismatch')
        meta = parse_map_yaml(yr)
        width, height, pixels = parse_pgm(pr)
        if binding['size'] != [width, height] or binding['resolution'] != meta['resolution'] or binding['origin'] != meta['origin'] or binding['frame'] != 'map':
            fail('map_metadata_mismatch')
        return dict(map=binding, pixels_base64=base64.b64encode(pixels).decode('ascii'),
                    negate=meta['negate'], free_thresh=meta['free_thresh'], occupied_thresh=meta['occupied_thresh'])

    def request(self, request):
        if type(request) is not dict or not {'action'} <= set(request) <= {'action','body'}:
            fail('request_fields')
        action, body = request.get('action'), request.get('body', {})
        fields = {'list':set(), 'import_map':{'yaml_base64','pgm_base64'},
            'map':{'id','yaml_sha256','pgm_sha256','frame','resolution','origin','size'},
            'load':{'id'}, 'create':{'document'}, 'update':{'id','document','expected_revision'}}
        if type(action) is not str or action not in fields:
            fail('unknown_planning_action')
        if type(body) is not dict or set(body) != fields[action]:
            fail('request_body_fields')
        if action == 'list':
            return self.store.list()
        if action == 'import_map':
            return self.import_map(body)
        if action == 'map':
            return self.read_map(body)
        if action == 'load':
            return self.store.load(body['id'])
        if action in ('create', 'update'):
            document = validate_plan(body['document'])
            self.read_map(document['map'])
            if action == 'create':
                return self.store.create(document)
            return self.store.update(body['id'], document, body['expected_revision'])
        fail('unknown_planning_action')


def main():
    # Location is configured by the trusted local launcher, never by an HTTP body.
    try:
        if len(sys.argv) != 2:
            fail('storage_root_required')
        root = sys.argv[1]
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            fail('request_too_large')
        result = PlanningService(root).request(parse_json(raw))
        response = dict(ok=True, result=result)
    except Exception as error:
        # Never disclose filesystem paths, arbitrary input, or a traceback to the GUI.
        code = str(error)
        known = code if re.fullmatch('[a-z_]{3,80}', code) else type(error).__name__
        response = dict(ok=False, error=known)
    sys.stdout.write(json.dumps(response, ensure_ascii=True, allow_nan=False))


if __name__ == '__main__':
    main()
