const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('adr_service_agent/voice_ui.js','utf8');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function harness({secure=true,media=true,deferred=false}={}){
  const elements=new Map(),events={},calls=[],timers=[],recorders=[],tracks=[],spoken=[];
  const ids=['adr-record','adr-record-cancel','adr-transcribe-file','adr-read-answer','adr-stop-reading',
    'adr-voice-status','adr-question','diag-csrf','adr-audio-file',
    'adr-transcript','adr-use-transcript'];
  for(const id of ids)elements.set(id,{id,value:'',textContent:'',checked:false,hidden:false,files:[],listeners:{},
    setAttribute(k,v){this[k]=v;},addEventListener(k,v){this.listeners[k]=v;}});
  elements.get('adr-question').value='Original question';elements.get('diag-csrf').value='test-csrf';
  const track={stop(){tracks.push('stopped');}},stream={getTracks:()=>[track]};let allow;
  const getUserMedia=async()=>deferred?new Promise(resolve=>{allow=resolve;}):stream;
  class Recorder{
    constructor(_,options){this.mimeType=options.mimeType;this.state='inactive';recorders.push(this);}
    static isTypeSupported(t){return t==='audio/mp4';}
    start(){this.state='recording';}
    stop(){this.state='inactive';this.ondataavailable?.({data:new Blob([Buffer.from('0000ftypM4A 00000000')],{type:'audio/mp4'})});this.onstop?.();}
  }
  const speech={cancel(){spoken.push('cancel');},speak(u){spoken.push(u.text);}};
  const document={hidden:false,getElementById:id=>elements.get(id),addEventListener:(k,v)=>events[k]=v};
  const window={isSecureContext:secure,speechSynthesis:speech};
  const context={window,document,navigator:{language:'en-US',mediaDevices:media?{getUserMedia}:undefined},
    MediaRecorder:Recorder,SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},Blob,AbortController,
    setTimeout(fn,ms){const t={fn,ms};timers.push(t);return t;},clearTimeout(t){if(t)t.canceled=true;},
    addEventListener:(k,v)=>events[k]=v,
    fetch:async(url,options)=>{calls.push({url,options});return{status:202,json:async()=>({accepted:true})};}};
  vm.runInNewContext(source,context);
  const state={voice_enabled:true,cloud_enabled:true,blocked:false,busy:false,answer:'',transcript:'',transcript_id:''};
  const update=patch=>window.ADRVoice.update(Object.assign(state,patch));
  const click=async id=>{await elements.get(id).listeners.click();await flush();};
  update({});
  return{elements,events,calls,timers,recorders,tracks,spoken,window,state,update,click,allow:()=>allow?.(stream)};
}
(async()=>{
  {
    const h=harness();h.update({voice_enabled:false});await h.click('adr-record');assert.equal(h.recorders.length,0);assert.equal(h.calls.length,0);
    assert.match(h.elements.get('adr-voice-status').textContent,/Enable voice/);
  }
  {
    const h=harness({secure:false});
    await h.click('adr-record');assert.equal(h.calls.length,0);assert.match(h.elements.get('adr-voice-status').textContent,/keyboard dictation/);
  }
  {
    const h=harness();
    await h.click('adr-record');assert.equal(h.recorders.length,1);assert.equal(h.calls.length,0);
    await h.click('adr-record-cancel');assert.ok(h.tracks.length);assert.equal(h.calls.length,0);
    assert.equal(h.window.ADRVoice.busy(),false);
  }
  {
    const h=harness({deferred:true});
    const starting=h.click('adr-record');await flush();await h.click('adr-record-cancel');h.allow();await starting;
    assert.ok(h.tracks.length);assert.equal(h.calls.length,0);assert.equal(h.recorders.length,0);
  }
  {
    const h=harness();
    await h.click('adr-record');await h.click('adr-record');
    assert.equal(h.calls.length,1);const req=h.calls[0];assert.equal(req.url,'voice-transcribe');
    assert.equal(req.options.headers['X-ADR-Voice-Consent'],'yes');assert.equal(req.options.headers['X-ADR-CSRF'],'test-csrf');
    // A stale status response must not discard the accepted transcription.
    h.update({});assert.equal(h.window.ADRVoice.busy(),true);
    h.update({transcript_id:req.options.headers['X-ADR-Recording-ID'],transcript:'Check office'});
    assert.equal(h.elements.get('adr-question').value,'Check office');assert.equal(h.calls.length,1);
    assert.equal(h.window.ADRVoice.busy(),false);assert.ok(h.tracks.length);
  }
  {
    const h=harness();
    await h.click('adr-record');h.elements.get('adr-question').value='New typed question';
    await h.click('adr-record');const req=h.calls[0];
    h.update({transcript_id:req.options.headers['X-ADR-Recording-ID'],transcript:'Spoken question'});
    assert.equal(h.elements.get('adr-question').value,'New typed question');
    assert.equal(h.elements.get('adr-transcript').textContent,'Spoken question');
    await h.click('adr-use-transcript');assert.equal(h.elements.get('adr-question').value,'Spoken question');
  }
  {
    const h=harness();h.update({answer:'The office replied.'});assert.equal(h.spoken.length,0);
    await h.click('adr-read-answer');assert.ok(h.spoken.includes('The office replied.'));
    await h.click('adr-stop-reading');assert.equal(h.spoken.at(-1),'cancel');
    h.window.ADRVoice.expectAnswer();
    const priorSpeech=h.spoken.length;h.update({busy:true,answer:'Other stored answer.',answer_id:'stored'});assert.equal(h.spoken.length,priorSpeech);
    h.update({busy:false,answer:'New answer.',answer_id:'answer-2'});
    assert.ok(h.spoken.includes('New answer.'));
    const count=h.spoken.length;h.update({});assert.equal(h.spoken.length,count);
    assert.equal(h.calls.length,0);
  }
  {
    const h=harness();await h.click('adr-record');
    h.events.pagehide();await flush();assert.ok(h.tracks.length);assert.equal(h.calls.length,0);
  }
  {
    const h=harness({secure:false});
    h.elements.get('adr-audio-file').files=[Object.assign(new Blob(['recording'],{type:'audio/mp4'}),{name:'question.m4a'})];
    await h.click('adr-transcribe-file');assert.equal(h.calls.length,1);assert.equal(h.calls[0].url,'voice-transcribe');
  }
  console.log('Voice UI checks passed: consent, fallback, cancellation, late permission, transcript review, stale polls, typed edits, speech controls and page cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
