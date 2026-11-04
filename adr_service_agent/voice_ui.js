(() => {
  const $=id=>document.getElementById(id),maxBytes=2*1024*1024;
  if(!$('adr-record'))return;
  let state={},active=null,pending=null,draft='',lastAnswer='',utterance=null,expectedAnswer=null;
  const message=text=>{$('adr-voice-status').textContent=text;};
  const busy=()=>Boolean(active||pending);
  const supported=()=>window.isSecureContext&&navigator.mediaDevices?.getUserMedia&&typeof MediaRecorder!=='undefined';
  const voiceAllowed=()=>state.voice_enabled&&state.cloud_enabled&&!state.blocked&&!state.busy;
  function controls(){
    $('adr-record').disabled=Boolean(pending)||(!active&&!voiceAllowed())||Boolean(active&&active.phase!=='recording');
    $('adr-record').textContent=active?.phase==='recording'?'Stop recording':'Speak';
    $('adr-record').setAttribute('aria-pressed',String(active?.phase==='recording'));
    $('adr-record-cancel').hidden=!active||!['requesting','recording'].includes(active.phase);
    $('adr-read-answer').disabled=!state.answer||!window.speechSynthesis;
    $('adr-stop-reading').disabled=!utterance;
  }
  function stopReading(){
    if(window.speechSynthesis)window.speechSynthesis.cancel();utterance=null;controls();
  }
  function speak(){
    if(!window.speechSynthesis||typeof SpeechSynthesisUtterance==='undefined'){
      message('Spoken answers are unavailable here. The answer is shown below.');return;
    }
    if(!state.answer)return;
    stopReading();
    utterance=new SpeechSynthesisUtterance(state.answer);utterance.lang=navigator.language||'en-US';
    const spoken=utterance;
    spoken.onend=()=>{if(utterance===spoken){utterance=null;controls();}};
    spoken.onerror=e=>{if(utterance!==spoken)return;utterance=null;controls();if(!['canceled','interrupted'].includes(e.error))message('Could not read aloud here. Tap Read answer to try again.');};
    try{window.speechSynthesis.speak(spoken);controls();}catch(_){utterance=null;controls();message('Spoken answers are unavailable here.');}
  }
  function tracks(recording){recording?.stream?.getTracks().forEach(t=>t.stop());}
  function discard(text='Recording canceled.'){
    const recording=active;
    if(recording){recording.canceled=true;clearTimeout(recording.timer);if(recording.recorder?.state==='recording')recording.recorder.stop();tracks(recording);recording.chunks=[];}
    active=null;message(text);controls();
  }
  function consent(){
    if(!voiceAllowed())throw new Error('Enable voice and cloud analysis in Configuration, or wait for the current check.');
  }
  function formatType(mime,name=''){
    const base=(mime||'').split(';')[0].toLowerCase();
    if(['audio/mp4','audio/webm','audio/wav','audio/mpeg'].includes(base))return base;
    if(['audio/x-m4a','audio/m4a','video/mp4'].includes(base))return 'audio/mp4';
    if(base==='audio/x-wav')return 'audio/wav';
    const ext=name.split('.').pop().toLowerCase();
    return ({m4a:'audio/mp4',mp4:'audio/mp4',webm:'audio/webm',wav:'audio/wav',mp3:'audio/mpeg'})[ext]||'';
  }
  async function upload(blob,mime,original){
    if(!blob.size||blob.size>maxBytes||!mime)throw new Error('Use an MP4, WebM, WAV or MP3 recording smaller than 2 MB.');
    consent();
    const id=Date.now().toString(36)+'_'+Math.random().toString(36).slice(2);
    const job={phase:'uploading',controller:new AbortController()};active=job;controls();
    message('Sending recording…');
    const timeout=setTimeout(()=>job.controller.abort(),20000);
    try{
      const response=await fetch('voice-transcribe',{method:'POST',credentials:'same-origin',redirect:'error',
        headers:{'Content-Type':mime,'X-ADR-CSRF':$('diag-csrf').value,'X-ADR-Voice-Consent':'yes','X-ADR-Recording-ID':id},
        body:blob,signal:job.controller.signal});
      const result=await response.json();
      if(response.status!==202)throw new Error(result.error||'Recording could not be sent.');
      pending={id,original,acceptedAt:Date.now()};message('Transcribing…');
    }finally{clearTimeout(timeout);if(active===job)active=null;controls();}
  }
  async function record(){
    if(active?.phase==='recording'){
      clearTimeout(active.timer);active.phase='stopping';active.recorder.stop();tracks(active);message('Preparing recording…');controls();return;
    }
    if(busy())return;
    let recording=null;
    try{
      consent();stopReading();
      if(!supported())throw new Error('Microphone unavailable here. Use keyboard dictation or choose a recording. Microphone access needs HTTPS and app permission.');
      recording={phase:'requesting',canceled:false,chunks:[],bytes:0,original:$('adr-question').value};
      active=recording;message('Allow microphone access…');controls();
      recording.timer=setTimeout(()=>{if(active===recording)discard('Microphone request timed out. Use keyboard dictation or choose a recording.');},20000);
      recording.stream=await navigator.mediaDevices.getUserMedia({audio:true,video:false});
      clearTimeout(recording.timer);
      if(recording.canceled||active!==recording){tracks(recording);return;}
      const mime=['audio/mp4','audio/webm;codecs=opus','audio/webm'].find(t=>MediaRecorder.isTypeSupported(t));
      if(!mime){discard('This recorder format is unsupported. Use keyboard dictation or choose a recording.');return;}
      const recorder=new MediaRecorder(recording.stream,{mimeType:mime,audioBitsPerSecond:64000});recording.recorder=recorder;
      recorder.ondataavailable=e=>{
        if(recording.canceled||!e.data.size)return;
        recording.bytes+=e.data.size;
        if(recording.bytes>maxBytes){discard('Recording exceeded 2 MB. Try a shorter question.');return;}
        recording.chunks.push(e.data);
      };
      recorder.onerror=()=>{if(active===recording)discard('Recording failed. Use keyboard dictation or choose a recording.');};
      recorder.onstop=async()=>{
        clearTimeout(recording.timer);tracks(recording);
        if(recording.canceled||active!==recording)return;
        const blob=new Blob(recording.chunks,{type:recorder.mimeType});recording.chunks=[];
        active=null;
        try{await upload(blob,formatType(recorder.mimeType),recording.original);}
        catch(error){message(error.name==='AbortError'?'Upload timed out. Check status before trying again.':error.message);controls();}
      };
      recording.phase='recording';recorder.start(1000);
      message('Listening… tap Stop recording when finished.');controls();
      recording.timer=setTimeout(()=>{if(active===recording&&recorder.state==='recording'){recording.phase='stopping';recorder.stop();tracks(recording);controls();}},20000);
    }catch(error){
      if(recording&&active!==recording){tracks(recording);return;}
      const denied=error.name==='NotAllowedError';
      discard(denied?'Microphone access was blocked. Use keyboard dictation or choose a recording.':error.message||'Microphone unavailable.');
    }
  }
  function update(next){
    const availability=String(next.voice_enabled)+String(next.cloud_enabled);
    const changed=availability!==String(state.voice_enabled)+String(state.cloud_enabled);
    state=next;
    if(changed&&!busy())message(state.voice_enabled&&state.cloud_enabled?'Tap Speak, then review your question.':'Enable voice and cloud analysis in Configuration.');
    if(active?.phase==='recording'||active?.phase==='requesting'){
      if(!state.voice_enabled||!state.cloud_enabled||state.blocked)discard('Voice is unavailable; recording canceled.');
    }
    if(pending&&state.transcript_id===pending.id&&!state.busy){
      if(state.transcript){
        if($('adr-question').value===pending.original){$('adr-question').value=state.transcript;message('Review your question, then tap Ask ADR.');}
        else{draft=state.transcript;$('adr-transcript').textContent=draft;$('adr-use-transcript').hidden=false;message('Your typed question was kept. Tap Use transcript to replace it.');}
      }else message(state.progress||'Transcription did not finish.');
      pending=null;
    }else if(pending&&Date.now()-pending.acceptedAt>65000){
      pending=null;message('Recording status changed. Type your question or try again.');
    }
    const answerKey=state.answer_id||state.answer||'';
    if(answerKey!==lastAnswer){
      const wasAnswer=lastAnswer;lastAnswer=answerKey;
      if(expectedAnswer&&answerKey!==expectedAnswer.key&&state.answer&&!state.busy&&!busy()&&!document.hidden){expectedAnswer=null;speak();}
      else if(wasAnswer&&utterance)stopReading();
    }
    if(expectedAnswer&&Date.now()-expectedAnswer.started>120000)expectedAnswer=null;
    controls();
  }
  window.ADRVoice={busy,update,expectAnswer:()=>{stopReading();expectedAnswer={key:state.answer_id||state.answer||'',started:Date.now()};}};
  $('adr-record').addEventListener('click',record);
  $('adr-record-cancel').addEventListener('click',()=>discard());
  $('adr-use-transcript').addEventListener('click',()=>{if(draft)$('adr-question').value=draft;draft='';$('adr-transcript').textContent='';$('adr-use-transcript').hidden=true;message('Review your question, then tap Ask ADR.');});
  $('adr-read-answer').addEventListener('click',speak);
  $('adr-stop-reading').addEventListener('click',stopReading);
  function leave(){
    if(active&&['recording','requesting','stopping'].includes(active.phase))discard();
    if(active?.controller)active.controller.abort();stopReading();
  }
  addEventListener('pagehide',leave);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)leave();});
  if(!window.speechSynthesis)message('Spoken answers unavailable here; text remains available.');
  controls();
})();
