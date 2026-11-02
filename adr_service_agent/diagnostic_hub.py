"""Diagnostics and decision support inside the Service Agent process.

No second server, external command interface or model-driven device actions.
"""
import html
import json
import re
import secrets
import threading
import time
from pathlib import Path
from network_diagnostics import DiagnosticError, config, check, discover, retain, save, summary, import_report
from diagnostic_analysis import ask
from voice_input import transcribe, validate as validate_audio
from presentation import display_text
from dashboard import key_readings
from service_report import redact


def load(path):
    if not path.exists(): return {}
    try:
        if path.stat().st_size > 2*1024*1024: raise ValueError()
        value=json.loads(path.read_text())
        if not isinstance(value,dict): raise ValueError()
        return value
    except (OSError,ValueError):
        raise DiagnosticError('Saved diagnostics could not be read. Inspect app storage; no data was removed.') from None


def evidence(report, options, power=None):
    """Allowlist small health fields, never options, identifiers or raw consoles."""
    if not report and not (power and power.get('enabled')): return {}
    out=import_report(report) if report else {}
    if power and power.get('enabled'):
        out['power_monitor']={k:power.get(k) for k in ('status','checked_at','current','last_verified','poll_interval_seconds','error','limitations')}
        out['power_monitor']['events']=power.get('events',[])[-12:]
    out['summaries']=[]
    for item in (report or {}).get('checks',[])[:20]:
        snapshot=item.get('snapshot') or {}
        out['summaries'].append({'system':item.get('system'), 'status':item.get('status'),
            'checked_at':item.get('post_action_verified_at') or item.get('completed_at'),
            'readings':[display_text(str(v)[:250]) for v in key_readings(item.get('system'),snapshot)[:12]],
            'findings':[display_text(str(v)[:250]) for v in item.get('service_findings',[])[:6]],
            'limitations':[display_text(str(v)[:250]) for v in item.get('data_limitations',[])[:4]]})
    private=[v for k,v in options.items() if any(w in k for w in ('password','api_key','token')) and isinstance(v,str) and v]
    return redact(out,private)


def daily_limit(options,key):
    value=options.get(key,100)
    return value if type(value) is int and 0<=value<=10000 else 100


class Hub:
    def __init__(self, data, options, report_provider):
        self.data=Path(data)/'diagnostics'; self.options=options;self.report_provider=report_provider
        self.lock=threading.Lock();self.busy=False;self.progress='Ready';self.answer='';self.last_started=0;self.answer_id=''
        self.transcript='';self.transcript_id='';self.voice_quota={};self.last_voice_started=0
        self.error='';self.settings={'networks':[],'targets':[]};self.history={};self.quota={};self.result={}
        try:
            self.data.mkdir(parents=True,exist_ok=True)
            self.settings=config(options)
            self.history=retain(load(self.data/'history.json'))
            self.quota=load(self.data/'analysis_attempts.json')
            self.voice_quota=load(self.data/'voice_attempts.json')
            self.result=(self.history.get('samples') or [{}])[-1].get('result',{})
        except DiagnosticError as exc:self.error=str(exc)
        except Exception:self.error='Diagnostic storage or configuration needs attention. No data was removed.'

    def status(self):
        with self.lock:
            return {'busy':self.busy,'blocked':bool(self.error),'progress':self.error or self.progress,
                'transcript':self.transcript,'transcript_id':self.transcript_id,
                'voice_enabled':self.options.get('voice_enabled') is True,
                'answer':self.answer,'answer_id':self.answer_id,'result':self.result,'observed':self.history.get('observed',[]),
                'summary':summary(self.result) if self.result else 'No local checks yet.',
                'enabled':self.options.get('diagnostics_enabled') is True,
                'cloud_enabled':self.options.get('cloud_analysis_enabled') is True}

    def start_voice(self,audio,mime,confirmed,request_id,thread_factory=threading.Thread):
        with self.lock:
            if self.error:raise DiagnosticError(self.error)
            if self.busy:raise DiagnosticError('Diagnostics are already running.')
            if self.options.get('voice_enabled') is not True or self.options.get('cloud_analysis_enabled') is not True or not confirmed:
                raise DiagnosticError('Enable voice and confirm sending this recording.')
            if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',request_id):
                raise DiagnosticError('Invalid recording request.')
            mime=validate_audio(audio,mime)
            if time.time()-self.last_voice_started<10:raise DiagnosticError('Wait ten seconds between diagnostic requests.')
            day=time.strftime('%Y-%m-%d',time.gmtime())
            count=self.voice_quota.get('count',0) if self.voice_quota.get('day')==day else 0
            limit=daily_limit(self.options,'daily_voice_limit')
            if type(count) is not int or count<0:raise DiagnosticError('Saved voice counter is invalid. Inspect app storage.')
            if limit and count>=limit:
                raise DiagnosticError('Daily voice limit reached ('+str(limit)+' attempts, UTC day). Increase daily_voice_limit in Configuration.')
            quota={'day':day,'count':count+1}
            save(self.data/'voice_attempts.json',quota);self.voice_quota=quota
            self.busy=True;self.last_voice_started=time.time();self.progress='Transcribing question'
            self.transcript='';self.transcript_id=request_id
        def run():
            try:
                text=transcribe(self.options,audio,mime,True)
                with self.lock:self.transcript=text;self.progress='Review your question, then tap Ask ADR.'
            except DiagnosticError as exc:
                with self.lock:self.progress=str(exc)
            except Exception:
                with self.lock:self.progress='Transcription could not finish. Type your question or try again.'
            finally:
                with self.lock:self.busy=False
        try:thread_factory(target=run,daemon=True).start()
        except Exception:
            with self.lock:self.busy=False;self.progress='Recording job could not start.'
            raise DiagnosticError('Recording job could not start.') from None

    def record(self,result):
        with self.lock:
            history=retain(dict(self.history),result)
            save(self.data/'history.json',history)
            self.history=history;self.result=result

    def scheduled_check(self):
        if self.error:raise DiagnosticError(self.error)
        if self.options.get('diagnostics_enabled') is not True or not self.settings['targets']:
            raise DiagnosticError('Enable diagnostics and configure target addresses first.')
        result=check(self.settings)
        self.record(result)
        return result

    def start(self,action,fields,thread_factory=threading.Thread):
        with self.lock:
            if self.error:raise DiagnosticError(self.error)
            if self.busy:raise DiagnosticError('Diagnostics are already running.')
            if time.time()-self.last_started<10:raise DiagnosticError('Wait ten seconds between diagnostic requests.')
            if action not in ('check','discover','ask'):raise DiagnosticError('Unsupported diagnostic request.')
            if action!='ask' and self.options.get('diagnostics_enabled') is not True:
                raise DiagnosticError('Enable diagnostics in Configuration first.')
            if action=='check' and not self.settings['targets']:raise DiagnosticError('Configure target addresses first.')
            if action=='discover' and fields.get('subnet') not in [str(n) for n in self.settings['networks']]:
                raise DiagnosticError('Choose a configured subnet.')
            if action=='ask':
                if self.options.get('cloud_analysis_enabled') is not True or fields.get('confirm')!='yes':
                    raise DiagnosticError('Enable cloud analysis and confirm sending readings.')
                if not 1<=len(fields.get('question','').strip())<=1000:raise DiagnosticError('Enter a question of 1–1000 characters.')
                # Caller holds the app lock: capture a consistent, sanitized report.
                service=self.report_provider()
                if not service and not self.result:raise DiagnosticError('Run checks first.')
                previous=[s['result'] for s in self.history.get('samples',[])[:-1] if s.get('result',{}).get('kind')=='target_checks']
                collected={'latest_checks':self.result,'previous_checks':previous[-1:],'service_report':service}
                # Fail before charging the local attempt quota if evidence is too large.
                if len(json.dumps(collected,allow_nan=False).encode())>40000:
                    raise DiagnosticError('Readings exceed the analysis limit. Run a focused local check first.')
                day=time.strftime('%Y-%m-%d',time.gmtime())
                count=self.quota.get('count',0) if self.quota.get('day')==day else 0
                limit=daily_limit(self.options,'daily_analysis_limit')
                if type(count) is not int or count<0:raise DiagnosticError('Saved analysis counter is invalid. Inspect app storage.')
                if limit and count>=limit:raise DiagnosticError('Daily analysis limit reached ('+str(limit)+' attempts, UTC day). Increase daily_analysis_limit in Configuration.')
                quota={'day':day,'count':count+1};save(self.data/'analysis_attempts.json',quota);self.quota=quota
            self.busy=True;self.last_started=time.time();self.progress='Starting'
        def progress(text):
            with self.lock:self.progress=text
        def run():
            try:
                if action=='ask':
                    progress('Reviewing readings')
                    answer=ask(self.options,fields['question'],collected,True)
                    with self.lock:self.answer=display_text(answer);self.answer_id=secrets.token_urlsafe(12)
                else:
                    result=check(self.settings,progress=progress) if action=='check' else discover(self.settings,fields['subnet'],progress=progress)
                    self.record(result)
                progress('Complete')
            except DiagnosticError as exc:progress(str(exc))
            except Exception:progress('Request could not finish. Previous readings retained; no retry was made.')
            finally:
                with self.lock:self.busy=False
        try:thread_factory(target=run,daemon=True).start()
        except Exception:
            with self.lock:self.busy=False;self.progress='Job could not start.'
            raise DiagnosticError('Job could not start.') from None


def panels(hub, csrf):
    esc=lambda s:html.escape(str(s),quote=True)
    subnets=''.join('<option value="'+esc(n)+'">'+esc(n)+'</option>' for n in hub.settings['networks'])
    network='''<section id="diagnostics"><h2>Diagnostics</h2><p id="diag-progress" role="status" aria-live="polite">Ready</p>
<div class="toolbar"><button type="button" data-diagnostic="check">Check connectivity</button><select id="diag-subnet" aria-label="Discovery subnet">'''+subnets+'''</select><button type="button" data-diagnostic="discover">Discover devices</button></div>
<p id="diag-summary"></p><div class="diagnostic-table"><table><thead><tr><th>Target</th><th>Ping</th><th>Latency</th></tr></thead><tbody id="diag-targets"></tbody></table></div><p id="diag-discovery"></p>
<details id="diagnostic-observed"><summary>Observed devices</summary><div class="diagnostic-table"><table><thead><tr><th>Address</th><th>First seen</th><th>Last seen</th></tr></thead><tbody id="diag-observed"></tbody></table></div></details></section>'''
    assistant='''<section id="ask-adr"><h2>Ask ADR</h2><p id="adr-power-status" role="status"></p><details id="adr-power-events"><summary>Recent power events</summary><div id="adr-power-event-list"></div></details><form id="adr-question-form"><input type="hidden" id="diag-csrf" value="'''+esc(csrf)+'''">
<label for="adr-question">Question</label><textarea id="adr-question" maxlength="1000" placeholder="What needs attention?" rows="3"></textarea>
<p class="report-caption">Ask ADR sends your question and collected readings to OpenAI.</p>
<div class="toolbar"><button type="button" id="adr-record">Speak</button><button type="button" id="adr-record-cancel" hidden>Cancel</button><button type="button" data-diagnostic="ask">Ask ADR</button></div>
<p class="report-caption">Speak or Transcribe sends your recording to OpenAI. API usage applies.</p>
<div class="voice-file"><label for="adr-audio-file">Or choose a recording</label><input id="adr-audio-file" type="file" accept="audio/*,.m4a,.mp4,.webm,.wav,.mp3"><button type="button" id="adr-transcribe-file">Transcribe</button></div><p id="adr-transcript"></p><button type="button" id="adr-use-transcript" hidden>Use transcript</button><p id="adr-voice-status" role="status" aria-live="polite"></p></form>
<div class="toolbar voice-output"><button type="button" id="adr-read-answer">Read answer</button><button type="button" id="adr-stop-reading">Stop reading</button></div><p id="adr-analysis-progress" role="status"></p><div id="adr-answer" aria-live="polite"></div></section>'''
    return network,assistant
