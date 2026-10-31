import {test} from 'node:test';
import assert from 'node:assert/strict';
import {newPlan,worldFromPixel,pixelFromWorld,inMap,removeWaypoint,removeRoute,sameMap,normalizeAngle} from '../planning_model.mjs';

const map=()=>({id:'map-fixture',yaml_sha256:'a'.repeat(64),pgm_sha256:'b'.repeat(64),frame:'map',resolution:.2,origin:[10,-3,Math.PI/2],size:[100,50]});
const close=(actual,expected)=>actual.forEach((v,i)=>assert.ok(Math.abs(v-expected[i])<1e-9,`${v} != ${expected[i]}`));

test('nonzero origin and quarter-turn yaw transform image-down to world correctly',()=>{
 const m=map();
 // u=25 -> local x=5m; v=20 -> local y=6m; R(pi/2)[5,6]+[10,-3]=[4,2].
 close(worldFromPixel(m,25,20),[4,2]);
 close(pixelFromWorld(m,4,2),[25,20]);
 close(worldFromPixel(m,0,50),[10,-3]);
});

test('coordinate round trips preserve subpixel locations with arbitrary signed yaw',()=>{
 for(const yaw of [-2.4,-.37,0,.9,Math.PI]){
  const m={...map(),origin:[-4.35,7.19,yaw],resolution:.05};
  for(const uv of [[.3,.7],[22.125,31.625],[99.2,49.1]]){
   const xy=worldFromPixel(m,...uv);close(pixelFromWorld(m,...xy),uv);
   assert.equal(inMap(m,xy),true);
  }
 }
});

test('inMap rejects out-of-extent and nonfinite positions in rotated map coordinates',()=>{
 const m=map();
 for(const uv of [[-1,25],[101,25],[50,-1],[50,51]])assert.equal(inMap(m,worldFromPixel(m,...uv)),false);
 for(const xy of [[NaN,0],[Infinity,0],[0,-Infinity]])assert.equal(inMap(m,xy),false);
 // Boundary convention is continuous image extent, not the center of a cell.
 close(pixelFromWorld(m,...worldFromPixel(m,0,50)),[0,50]);
 assert.equal(inMap({...m,origin:[10,-3,0]},[10,-3]),true);
});

test('new plan isolates map arrays and declares editor profile without executable job state',()=>{
 const m=map(),p=newPlan(m);m.origin[0]=999;m.size[0]=1;
 assert.equal(p.map.origin[0],10);assert.equal(p.map.size[0],100);
 assert.deepEqual(p.profile,{translation_mps:.15,rotation_rps:.5});
 assert.deepEqual([p.waypoints,p.routes,p.patrols,p.keepouts],[[],[],[],[]]);
 assert.equal(p.revision,0);assert.equal(Object.hasOwn(p,'running'),false);
});

function populated(){const p=newPlan(map());p.waypoints=[{id:'a'},{id:'b'},{id:'c'}];p.routes=[{id:'only-a',waypoint_ids:['a']},{id:'a-b-c',waypoint_ids:['a','b','c']},{id:'b-c',waypoint_ids:['b','c']}];p.patrols=[{id:'pa',route_id:'only-a'},{id:'pabc',route_id:'a-b-c'},{id:'pbc',route_id:'b-c'}];p.keepouts=[{id:'k',vertices:[[1,2],[2,2],[2,3]]}];return p;}

test('waypoint removal cascades only empty routes and their patrols; retained order and original stay intact',()=>{
 const p=populated(),before=structuredClone(p),next=removeWaypoint(p,'a');
 assert.deepEqual(next.waypoints.map(w=>w.id),['b','c']);
 assert.deepEqual(next.routes,[{id:'a-b-c',waypoint_ids:['b','c']},{id:'b-c',waypoint_ids:['b','c']}]);
 assert.deepEqual(next.patrols.map(x=>x.id),['pabc','pbc']);
 assert.deepEqual(next.keepouts,p.keepouts);assert.deepEqual(p,before);
 next.keepouts[0].vertices[0][0]=999;assert.equal(p.keepouts[0].vertices[0][0],1);
});

test('route removal deletes dependent patrols but preserves waypoints and other routes',()=>{
 const p=populated(),next=removeRoute(p,'a-b-c');
 assert.deepEqual(next.routes.map(r=>r.id),['only-a','b-c']);
 assert.deepEqual(next.patrols.map(r=>r.id),['pa','pbc']);
 assert.deepEqual(next.waypoints,p.waypoints);assert.equal(p.routes.length,3);
 assert.deepEqual(removeRoute(p,'unknown'),p);
});

test('sameMap requires each binding field and array value to match, regardless of object key order',()=>{
 const m=map(),reordered=Object.fromEntries(Object.entries(m).reverse());
 assert.equal(sameMap(m,reordered),true);
 assert.equal(sameMap(m,{...m,display_name:'optional display metadata'}),true);
 for(const [key,value] of Object.entries({id:'other',yaml_sha256:'c'.repeat(64),pgm_sha256:'d'.repeat(64),frame:'other',resolution:.1,origin:[10,-3,0],size:[100,51]})){
  assert.equal(sameMap(m,{...m,[key]:value}),false,key);
 }
 assert.equal(sameMap(m,undefined),false);
});

test('sameMap rejects absent bindings and incomplete bindings even when both inputs match',()=>{
 for(const value of [undefined,null,{},[],false,'']){
  assert.equal(sameMap(value,value),false);
  assert.equal(sameMap(map(),value),false);
  assert.equal(sameMap(value,map()),false);
 }
 for(const key of ['id','yaml_sha256','pgm_sha256','frame','resolution','origin','size']){
  const incomplete=map();delete incomplete[key];
  assert.equal(sameMap(incomplete,structuredClone(incomplete)),false,key);
  assert.equal(sameMap(incomplete,map()),false,key);
 }
 const inherited=Object.create(map());
 assert.equal(sameMap(inherited,map()),false,'binding keys must be own properties');
});

test('angles preserve orientation across signed wrap boundary',()=>{
 for(const angle of [-8*Math.PI-.2,-Math.PI,0,Math.PI,9*Math.PI+.3]){
  const n=normalizeAngle(angle);assert.ok(n>=-Math.PI&&n<=Math.PI);
  assert.ok(Math.abs(Math.cos(n)-Math.cos(angle))<1e-12);
  assert.ok(Math.abs(Math.sin(n)-Math.sin(angle))<1e-12);
 }
});
