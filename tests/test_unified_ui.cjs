// Run with node: verifies polling and explicit Ask ADR submission without a browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map(),handlers={},intervals=[],calls=[];
function element(id=''){
  return {id,dataset:{},children:[],textContent:'',value:'',checked:false,
    append(x){this.children.push(x);},replaceChildren(x){this.children=x?[x]:[];}};
}
for(const id of ['diagnostics','ask-adr','diag-progress','adr-analysis-progress','diag-summary',
 'adr-native-status','adr-native-history','adr-native-sources','adr-native-list','adr-native-source','adr-native-file','adr-native-import','adr-native-import-status','adr-answer','diag-targets','diag-observed','diag-discovery','adr-question','diag-csrf','diag-subnet'])elements.set(id,element(id));
elements.get('adr-question').value='Check my office';
elements.get('diag-csrf').value='test-csrf';
const buttons=['check','discover','ask'].map(action=>({...element(),dataset:{diagnostic:action}}));
const state={progress:'Complete',answer:'<script>literal answer</script>',summary:'1 reply',enabled:true,cloud_enabled:true,
 native_events:{enabled:true,listener_running:true,sources:[],events:[]},busy:false,result:{targets:[]},observed:[],power_monitor:{enabled:true,status:'verified',current:{connected:true,power_lost:false,charge_percent:90,runtime_minutes:20},events:[]}};
const document={hidden:false,forms:[],activeElement:null,
 querySelector:()=>null,querySelectorAll:s=>s==='button[data-diagnostic]'?buttons:[],
 getElementById:id=>elements.get(id),createElement:()=>element(),createDocumentFragment:()=>element(),
 addEventListener:(name,fn)=>{(handlers[name]??=[]).push(fn);}};
const window={parent:null,visualViewport:null};window.parent=window;
const context={document,window,location:{pathname:'/app/'},sessionStorage:{getItem:()=>null},
 addEventListener(){},matchMedia:()=>({matches:false}),setTimeout,clearTimeout,
 setInterval:fn=>intervals.push(fn),AbortController,URLSearchParams,Intl,Date,
 fetch:async(url,options)=>{calls.push({url,options});return{ok:true,status:options.method==='POST'?202:200,json:async()=>state};}};
vm.runInNewContext(fs.readFileSync('adr_service_agent/dashboard_live.js','utf8'),context);
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();assert.equal(calls.length,1);assert.equal(calls[0].url,'diagnostic-state');
 assert.equal(elements.get('adr-question').value,'Check my office');
 assert.equal(elements.get('adr-answer').textContent,state.answer);
 assert.match(elements.get('adr-native-status').textContent,/Delivery is verified only/);
 for(const tick of intervals)tick();await flush();
 assert.equal(calls.some(c=>c.options.method==='POST'),false);
 const button=buttons[2];for(const fn of handlers.click)await fn({target:{closest:selector=>selector==='button[data-diagnostic]'?button:null}});
 await flush();
 const posts=calls.filter(c=>c.options.method==='POST');assert.equal(posts.length,1);
 assert.equal(posts[0].url,'diagnostic-ask');assert.equal(posts[0].options.body.get('confirm'),'yes');
 assert.equal(posts[0].options.body.get('question'),'Check my office');
 assert.equal(elements.get('adr-question').value,'Check my office');
 console.log('Unified UI smoke check passed: GET-only polling, retained question/consent, one explicit POST, literal answer text.');
})().catch(error=>{console.error(error);process.exitCode=1;});
