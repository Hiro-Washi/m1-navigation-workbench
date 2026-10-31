import http from 'node:http';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {PlanningBridge,planningRoute} from './planning_bridge.mjs';

// Deliberately no operator/live/guide import, proxy, ROS or device route.
const requested=process.env.M1_PLANNING_PORT??'18800';
if(!/^\d{1,5}$/.test(requested)||Number(requested)>65535)throw Error('invalid_port');
const manifest=JSON.parse(readFileSync(new URL('../source-manifest.json',import.meta.url)));
for(const [path,hash] of Object.entries(manifest.files)){
 if(!/^[a-zA-Z0-9_./-]+$/.test(path)||path.split('/').some(p=>p==='..'||p===''))throw Error('unsafe_manifest');
 const bytes=readFileSync(new URL('../'+path,import.meta.url));
 if(createHash('sha256').update(bytes).digest('hex')!==hash)throw Error('manifest_mismatch');
}
const assets=new Map();
for(const [name,type]of [['planning.html','text/html'],['planning.css','text/css'],['planning.mjs','text/javascript'],['planning_model.mjs','text/javascript']]){
 const bytes=readFileSync(new URL(name,import.meta.url));if(bytes.length>524288)throw Error('asset_size_limit');assets.set('/'+name,{bytes,type});
}
const bridge=new PlanningBridge();let port,closing=false;
const server=http.createServer(async(req,res)=>{
 const respond=(code,body,type='text/plain')=>{res.writeHead(code,{'Content-Type':type+'; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'});res.end(body);};
 if(req.headers.host!==`127.0.0.1:${port}`||(req.headers.origin&&req.headers.origin!==`http://127.0.0.1:${port}`))return respond(403,'origin_rejected');
 try{
  const path=new URL(req.url,`http://127.0.0.1:${port}`).pathname;
  if(await planningRoute(req,res,path,port,bridge))return;
  if(req.method!=='GET')return respond(405,'method_rejected');
  if(path==='/live.html')return respond(200,'<!doctype html><html lang="ja"><meta charset="utf-8"><title>計画エディター</title><p>実機Consoleはこの候補に含まれません。機体へ接続・指令送信は行いません。</p><a href="/planning.html">計画編集へ戻る</a></html>','text/html');
  const asset=assets.get(path==='/'?'/planning.html':path);
  if(!asset)return respond(404,'not_found');
  respond(200,asset.bytes,asset.type);
 }catch{if(!res.headersSent)respond(500,'request_failed');else res.destroy();}
});
server.headersTimeout=10000;server.requestTimeout=15000;server.keepAliveTimeout=5000;
server.on('error',()=>{bridge.close();process.exitCode=1;});
server.listen(Number(requested),'127.0.0.1',()=>{port=server.address().port;console.log(JSON.stringify({url:`http://127.0.0.1:${port}`,scope:'planning-only'}));});
function close(){
 if(closing)return;closing=true;bridge.close();server.close();server.closeAllConnections();
 const until=Date.now()+9000;
 const timer=setInterval(()=>{
  if(bridge.children.size===0){clearInterval(timer);process.exit(0);}
  if(Date.now()>=until){for(const child of bridge.children)child.kill('SIGKILL');clearInterval(timer);process.exit(1);}
 },25);
}
process.on('SIGINT',close);process.on('SIGTERM',close);
