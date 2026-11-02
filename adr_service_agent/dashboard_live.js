(() => {
  const storageKey = 'adr-view:' + location.pathname;
  const main = document.querySelector('main');
  const views = ['all', 'power', 'network', 'av'];
  try {
    const saved = sessionStorage.getItem(storageKey);
    if (views.includes(saved)) {
      const selected = document.getElementById('view-' + saved);
      if (selected) selected.checked = true;
    }
  } catch (_) {}
  document.addEventListener('change', event => {
    const input = event.target;
    if (input.matches('input[name="dashboard-view"]') && input.checked) {
      try { sessionStorage.setItem(storageKey, input.id.slice(5)); } catch (_) {}
    }
  });
  const pin = () => {
    const nav = document.querySelector('main + .group-tabs');
    if (!nav || !matchMedia('(max-width:700px)').matches) {
      if (nav) nav.style.bottom = '';
      return;
    }
    let bottom = window.visualViewport ? visualViewport.offsetTop + visualViewport.height : innerHeight;
    try {
      if (window.frameElement && window.parent !== window) {
        const frame = window.frameElement.getBoundingClientRect();
        const outer = window.parent.visualViewport;
        bottom = Math.min(bottom, (outer ? outer.offsetTop + outer.height : window.parent.innerHeight) - frame.top);
      }
    } catch (_) {}
    nav.style.bottom = Math.max(0, innerHeight - bottom) + 'px';
  };
  addEventListener('resize', pin, {passive:true});
  addEventListener('scroll', pin, {passive:true});
  if (window.visualViewport) {
    visualViewport.addEventListener('resize', pin, {passive:true});
    visualViewport.addEventListener('scroll', pin, {passive:true});
  }
  try {
    if (window.parent !== window) {
      window.parent.addEventListener('scroll', pin, {passive:true,capture:true});
      window.parent.addEventListener('resize', pin, {passive:true});
    }
  } catch (_) {}
  pin();

  // Same-origin GETs only. No form is submitted or device action initiated here.
  const touched = new WeakSet();
  document.addEventListener('input', e => { if (e.target.closest('form')) touched.add(e.target.closest('form')); });
  document.addEventListener('toggle', e => { if (!applying && e.target.tagName === 'DETAILS') touched.add(e.target); }, true);
  let applying = false;
  function key(node) {
    if (node.nodeType !== 1) return '';
    if (node.id) return '#' + node.id;
    if (node.matches('article.system-card')) return 'system:' + node.querySelector('h3')?.textContent;
    if (node.tagName === 'FORM') return 'form:' + node.getAttribute('action');
    if (node.tagName === 'DETAILS') return 'details:' + (node.className || node.querySelector('summary')?.textContent);
    return node.tagName + ':' + (node.getAttribute('class') || '');
  }
  function patch(old, next) {
    if (old.nodeType !== next.nodeType || old.nodeName !== next.nodeName) {
      old.replaceWith(next.cloneNode(true)); return;
    }
    if (old.nodeType !== 1) {
      if (old.nodeValue !== next.nodeValue) old.nodeValue = next.nodeValue;
      return;
    }
    if (old.id === 'diagnostics' || old.id === 'ask-adr') return;
    if (old.isEqualNode(next)) return;
    if (old.tagName === 'FORM' && (touched.has(old) || old.contains(document.activeElement))) return;
    const protectedAttr = name => (name === 'open' && old.tagName === 'DETAILS') || (name === 'checked' && old.matches('input[name="dashboard-view"]'));
    for (const a of [...old.attributes]) if (!protectedAttr(a.name) && !next.hasAttribute(a.name)) old.removeAttribute(a.name);
    for (const a of next.attributes) if (!protectedAttr(a.name) && old.getAttribute(a.name) !== a.value) old.setAttribute(a.name, a.value);
    const remaining = [...old.childNodes];
    let cursor = old.firstChild;
    for (const fresh of next.childNodes) {
      let match = remaining.find(n => key(n) && key(n) === key(fresh));
      if (!match && cursor && !key(cursor) && !key(fresh) && cursor.nodeType === fresh.nodeType) match = cursor;
      if (match) {
        remaining.splice(remaining.indexOf(match), 1);
        if (match !== cursor) old.insertBefore(match, cursor);
        patch(match, fresh);
        cursor = match.isConnected ? match.nextSibling : old.childNodes[[...next.childNodes].indexOf(fresh)+1] || null;
      } else {
        old.insertBefore(fresh.cloneNode(true), cursor);
      }
    }
    for (const stale of remaining) stale.remove();
  }
  const refreshMeta = () => document.querySelector('meta[name="adr-refresh"]');
  let seconds = Number(refreshMeta()?.content || 0);
  let stopped = false;
  const schedule = delay => { if (!stopped && seconds) setTimeout(update, delay || seconds * 1000); };
  async function update() {
    if (document.hidden || document.querySelector('form:not(#adr-question-form):focus-within') || [...document.forms].some(f => f.id !== 'adr-question-form' && touched.has(f))) { schedule(3000); return; }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('./', {method:'GET',credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) throw new Error('Refresh unavailable');
      const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
      const incoming = doc.querySelector('main');
      if (!incoming || !incoming.querySelector('.toolbar') || doc.querySelector('script[type="module"]')?.textContent !== document.querySelector('script[type="module"]')?.textContent) throw new Error('Page changed; reopen to update');
      if (document.querySelector('form:not(#adr-question-form):focus-within') || [...document.forms].some(f => f.id !== 'adr-question-form' && touched.has(f))) { schedule(3000); return; }
      // Keep the viewed element at the same screen position when content above changes.
      const anchor = [...main.querySelectorAll('summary,h2,h3,.metric,.primary-metric')].find(e => e.getBoundingClientRect().top >= 0 && e.getBoundingClientRect().height);
      const top = anchor?.getBoundingClientRect().top;
      const x = scrollX, y = scrollY;
      applying = true;
      patch(main, incoming);
      if (!document.querySelector('main + .group-tabs') && doc.querySelector('main + .group-tabs')) {
        main.after(doc.querySelector('main + .group-tabs').cloneNode(true));
      }
      applying = false;
      const delta = anchor?.isConnected ? anchor.getBoundingClientRect().top - top : 0;
      window.scrollTo({left:x,top:y + (Number.isFinite(delta) ? delta : 0),behavior:'instant'});
      seconds = Number(doc.querySelector('meta[name="adr-refresh"]')?.content || 0);
      const meta = refreshMeta();
      if (meta) meta.content = String(seconds);
      pin();
    } catch (_) {
      // Retain the last page and retry later without a navigation or command retry.
    } finally { applying = false; clearTimeout(timeout); }
    schedule();
  }
  addEventListener('pagehide', () => { stopped = true; });
  // Diagnostic status polling updates only result nodes, preserving the question,
  // consent, selected category and open tiles. GETs never trigger checks.
  let diagnosticSubmitting=false;
  const diag = id => document.getElementById(id);
  function diagnosticRows(id,rows){
    const body=diag(id);if(!body)return;
    const serialized=JSON.stringify(rows);if(body.dataset.rows===serialized)return;
    const fragment=document.createDocumentFragment();
    for(const row of rows){const tr=document.createElement('tr');for(const value of row){const td=document.createElement('td');td.textContent=String(value??'Unverified');tr.append(td);}fragment.append(tr);}
    body.replaceChildren(fragment);body.dataset.rows=serialized;
  }
  const localDate=value=>{const d=new Date(value);return value&&!Number.isNaN(d.valueOf())?new Intl.DateTimeFormat(undefined,{dateStyle:'short',timeStyle:'short'}).format(d):'Unverified';};
  function renderNative(native){
    const status=diag('adr-native-status'),panel=diag('adr-native-history'),sources=diag('adr-native-sources'),list=diag('adr-native-list'),select=diag('adr-native-source');
    if(!status||!panel||!sources||!list||!select)return;
    status.hidden=panel.hidden=!native?.enabled;if(!native?.enabled)return;
    status.textContent=native.error||((native.listener_running?'Native log listener running':'Native log listener stopped')+' · Delivery is verified only when messages arrive.');
    const coverage=native.sources||[],signature=JSON.stringify(coverage);
    if(sources.dataset.coverage!==signature){
      const fragment=document.createDocumentFragment();
      for(const source of coverage){const p=document.createElement('p');p.textContent=source.name+' · '+(source.last_received_at?'Last received '+localDate(source.last_received_at):'Forwarding unverified');fragment.append(p);}
      sources.replaceChildren(fragment);sources.dataset.coverage=signature;
    }
    const options=JSON.stringify(coverage.map(s=>[s.id,s.name]));
    if(select.dataset.options!==options){
      const selected=select.value,fragment=document.createDocumentFragment();
      for(const source of coverage){const option=document.createElement('option');option.value=source.id;option.textContent=source.name;fragment.append(option);}
      select.replaceChildren(fragment);if(coverage.some(s=>s.id===selected))select.value=selected;select.dataset.options=options;
    }
    const events=native.events||[],serialized=JSON.stringify(events);
    if(list.dataset.events!==serialized){
      const fragment=document.createDocumentFragment();
      for(const event of [...events].reverse().slice(0,20)){
        const p=document.createElement('p');p.textContent=event.source+' · '+(event.device_time?'Device time '+localDate(event.device_time):'Received '+localDate(event.received_at)+'; device time unverified')+' · '+(event.event_name?event.event_name+': ':'')+event.message;fragment.append(p);
      }
      if(!events.length){const p=document.createElement('p');p.textContent='No native events received or imported yet.';fragment.append(p);}
      list.replaceChildren(fragment);list.dataset.events=serialized;
    }
  }
  document.addEventListener('click',async e=>{
    if(!e.target.closest('#adr-native-import'))return;
    const button=diag('adr-native-import');if(button.disabled)return;button.disabled=true;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),20000);
    try{
      const file=diag('adr-native-file').files[0],source=diag('adr-native-source').value;
      if(!file||file.size>128*1024||!source)throw new Error('Choose a source and plain-text log smaller than 128 KB.');
      const response=await fetch('native-import',{method:'POST',credentials:'same-origin',redirect:'error',signal:controller.signal,
        headers:{'Content-Type':'text/plain;charset=utf-8','X-ADR-CSRF':diag('diag-csrf').value,'X-ADR-Event-Source':source},body:file});
      const result=await response.json();if(!response.ok)throw new Error(result.error||'Import could not finish.');
      diag('adr-native-import-status').textContent=result.imported+' log lines imported. Device times remain separate from receipt times.';
      diag('adr-native-file').value='';await diagnosticStatus();
    }catch(error){diag('adr-native-import-status').textContent=error.name==='AbortError'?'Import timed out. Check recent events before trying again.':error.message;}
    finally{clearTimeout(timeout);button.disabled=false;}
  });

  let diagnosticPolling=false;
  async function diagnosticStatus(){
    if(!diag('diagnostics')||diagnosticPolling||document.hidden)return;
    diagnosticPolling=true;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch('diagnostic-state',{credentials:'same-origin',cache:'no-store',redirect:'error',signal:controller.signal});
      if(!response.ok)throw new Error();
      const state=await response.json(),result=state.result||{};
      window.ADRVoice?.update(state);
      renderNative(state.native_events);
      diag('diag-progress').textContent=state.progress;
      diag('adr-analysis-progress').textContent=!state.cloud_enabled?'Enable Ask ADR in Configuration.':state.progress;
      diag('diag-summary').textContent=state.summary;
      diag('adr-answer').textContent=state.answer;
      diagnosticRows('diag-targets',(result.targets||[]).map(t=>[t.ip,t.ping.status==='responded'?'Reply':t.ping.status==='no_reply'?'No reply':'Unverified',t.ping.average_ms==null?'Unverified':t.ping.average_ms+' ms']));
      diag('diag-discovery').textContent=result.kind==='discovery'?result.responding.length+' replies / '+result.tested+' tested. Nonresponders remain unverified.':'';
      diagnosticRows('diag-observed',(state.observed||[]).map(d=>[d.ip,localDate(d.first_seen),localDate(d.last_seen)]));
      document.querySelectorAll('button[data-diagnostic]').forEach(b=>{b.disabled=state.blocked||state.busy||diagnosticSubmitting||Boolean(window.ADRVoice?.busy())||(b.dataset.diagnostic==='ask'?!state.cloud_enabled:!state.enabled);});
    }catch(_){if(diag('diag-progress'))diag('diag-progress').textContent='Connection unavailable; previous readings retained.';}
    finally{clearTimeout(timeout);diagnosticPolling=false;}
  }
  document.addEventListener('submit',e=>{if(e.target.id==='adr-question-form')e.preventDefault();});
  document.addEventListener('click',async e=>{
    const button=e.target.closest('button[data-diagnostic]');if(!button||diagnosticSubmitting||window.ADRVoice?.busy())return;
    diagnosticSubmitting=true;
    document.querySelectorAll('button[data-diagnostic]').forEach(b=>b.disabled=true);
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
    try{
      const body=new URLSearchParams({csrf:diag('diag-csrf').value});
      if(button.dataset.diagnostic==='discover')body.set('subnet',diag('diag-subnet').value);
      if(button.dataset.diagnostic==='ask'){
        body.set('question',diag('adr-question').value);
        body.set('confirm','yes');
      }
      const response=await fetch('diagnostic-'+button.dataset.diagnostic,{method:'POST',body,credentials:'same-origin',redirect:'error',signal:controller.signal});
      if(response.status!==202){
        const doc=new DOMParser().parseFromString(await response.text(),'text/html');
        throw new Error(doc.querySelector('.notice p')?.textContent||'Request could not start.');
      }
      if(button.dataset.diagnostic==='ask')window.ADRVoice?.expectAnswer();
      diagnosticSubmitting=false;await diagnosticStatus();
    }catch(error){
      diagnosticSubmitting=false;await diagnosticStatus();
      const message=error.name==='AbortError'?'Request timed out. Check status before trying again.':error.message;
      diag('diag-progress').textContent=message;diag('adr-analysis-progress').textContent=message;
    }finally{clearTimeout(timeout);}
  });
  diagnosticStatus();
  setInterval(()=>{if(!stopped&&!diagnosticSubmitting)diagnosticStatus();},2500);

  schedule();
})();

