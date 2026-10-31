import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {homedir} from 'node:os';
import {join} from 'node:path';

// Local disk operations only; never shares the control queue or starts ROS.
export class PlanningBridge {
 constructor({python=process.env.M1_PLANNING_PYTHON||process.env.M1_GUIDE_PYTHON||'python',root=process.env.M1_NAV_PLANS_ROOT||join(process.env.LOCALAPPDATA||homedir(),'M1Console','navigation-plans')}={}){
  this.python=python;this.root=root;this.children=new Set();this.closed=false;
 }
 async request(action,body){
  if(this.closed||this.children.size>=2)return {ok:false,error:'planning_busy'};
  return new Promise(resolve=>{
   const child=spawn(this.python,['-X','utf8',fileURLToPath(new URL('../navigation/planning_service.py',import.meta.url)),this.root],{stdio:['pipe','pipe','pipe'],windowsHide:true});
   this.children.add(child);let output='',finished=false,failure=null;
   const finish=value=>{if(finished)return;finished=true;clearTimeout(timer);resolve(value);};
   const timer=setTimeout(()=>{failure='planning_timeout';child.kill();},8000);
   child.stdout.on('data',part=>{output+=part.toString();if(output.length>8*1024*1024){failure='planning_output_limit';child.kill();}});
   child.stderr.on('data',()=>{});
   child.stdin.on('error',()=>{});
   child.on('error',()=>{this.children.delete(child);finish({ok:false,error:'planning_python_unavailable'});});
   child.on('close',code=>{this.children.delete(child);if(failure||code!==0)return finish({ok:false,error:failure||'planning_worker_failed'});try{const value=JSON.parse(output);if(typeof value.ok!=='boolean')throw Error();finish(value);}catch{finish({ok:false,error:'planning_invalid_response'});}});
   child.stdin.end(JSON.stringify({action,body}));
  });
 }
 close(){this.closed=true;for(const child of this.children)child.kill();}
}

export async function planningRoute(req,res,path,port,bridge){
 if(!path.startsWith('/api/planning/'))return false;
 const reply=(status,body)=>{if(!req.readableEnded)req.resume();res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(JSON.stringify(body));};
 const host=`127.0.0.1:${port}`,origin=`http://${host}`;
 if(req.headers.host!==host||(req.headers.origin&&req.headers.origin!==origin)||(req.method!=='GET'&&req.headers.origin!==origin)){reply(403,{error:'origin_rejected'});return true;}
 const action=path.slice('/api/planning/'.length);
 let body={};
 if(req.method==='POST'){
  if(req.headers['content-type']?.split(';')[0]!=='application/json'){reply(415,{error:'json_required'});return true;}
  try{const chunks=[];let bytes=0;for await(const part of req){bytes+=part.length;if(bytes>8*1024*1024){reply(413,{error:'request_too_large'});return true;}chunks.push(part);}body=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(!body||Array.isArray(body)||typeof body!=='object')throw Error();}
  catch{reply(400,{error:'invalid_json'});return true;}
 }
 if(!['list','load','create','update','map','import_map'].includes(action)){reply(404,{error:'unknown_planning_action'});return true;}
 if(req.method!==(action==='list'?'GET':'POST')){reply(405,{error:'method_rejected'});return true;}
 const result=await bridge.request(action,body);
 const status=result.ok?200:/conflict|RevisionConflict/i.test(result.error)?409:/unavailable|busy|timeout|worker/.test(result.error)?503:400;
 reply(status,result);return true;
}
