"""Compact evidence-based status summaries and a persistent issue lifecycle journal."""
import hashlib
import html
import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from network_diagnostics import save

RETENTION = 90 * 86400
SYSTEMS = {'WattBox':'power','Sense':'power','Sonos':'av','Bluesound':'av','Crestron':'av','Control4':'av','UniFi':'network','Ruckus':'network','Araknis':'network','Internet':'network'}


def number(raw):
    try:
        if isinstance(raw,bool):return None
        n=float(raw)
        return n if math.isfinite(n) and n>=0 else None
    except (ValueError,TypeError):return None


def iso(at):return datetime.fromtimestamp(at,timezone.utc).isoformat(timespec='seconds')


def build(pilot, now=None):
    from dashboard import key_readings, tile_value, site_timezone_name
    from zoneinfo import ZoneInfo
    now=time.time() if now is None else now
    checks={c['system']:c for c in (pilot.current_service_report() or {}).get('checks',[])}
    for system,attr in (('Sonos','sonos'),('Bluesound','bluesound'),('Crestron','crestron'),('CP4-R','cp4'),('Control4','control4'),('Sense','sense'),('UniFi','unifi')):
        snap=getattr(pilot,attr,None)
        old=checks.get(system,{})
        if snap and str(snap.get('checked_at',''))>str(old.get('completed_at','')):
            checks[system]={'system':system,'snapshot':snap,'status':'unverified','completed_at':snap.get('checked_at')}
    if pilot.green_snapshot:
        old=checks.get('Internet',{})
        if str(pilot.green_snapshot.get('checked_at',''))>str(old.get('completed_at','')):
            checks['Internet']={'snapshot':pilot.green_snapshot,'completed_at':pilot.green_snapshot.get('checked_at'),'status':'unverified'}
    rows=[];issues=[];evaluated={}
    try:zone=ZoneInfo(site_timezone_name())
    except (ValueError,KeyError):zone=timezone.utc
    today=datetime.fromtimestamp(now,zone).date()
    def is_today(at):
        try:return datetime.fromisoformat(str(at).replace('Z','+00:00')).astimezone(zone).date()==today
        except (ValueError,TypeError):return False
    def issue(key,system,title,at,detail='',scope=None,active=True):
        issues.append(dict(key=key,scope=scope or system,system=system,title=title,at=at,detail=detail,active=active,view=SYSTEMS.get(system,'power'),target='system-'+system.lower()))
    for system,view in SYSTEMS.items():
        check=checks.get(system);snapshot=(check or {}).get('snapshot') or {};at=(check or {}).get('completed_at')
        # A failed/missing read does not resolve an existing equipment issue.
        usable=bool(snapshot) and check.get('status')!='unverified'
        readings=snapshot.get('players',snapshot.get('readings',[]))
        title='Not checked yet' if not check else tile_value(system,snapshot,key_readings(system,snapshot))
        state='unknown' if not usable else 'ok'
        before=len(issues)
        if readings and system in ('Sonos','Bluesound','Crestron','Control4'):
            players=[r for r in readings if system not in ('Sonos','Bluesound') or r.get('entity_id','').startswith('media_player.')]
            unavailable=[r for r in players if r.get('state')=='unavailable']
            for r in players:
                key=system+':'+r.get('entity_id',r.get('name','player'))
                if r.get('state') not in (None,'unknown','read failed'):evaluated[key]=at
                if r in unavailable:issue(key,system,str(r.get('name') or system)+' unavailable',at,'Home Assistant reports unavailable',scope=key)
            if unavailable:
                title=system+' unavailable' if system in ('Crestron','Control4') and len(unavailable)==len(players) else f'{len(unavailable)} '+(('zone' if len(unavailable)==1 else 'zones') if system in ('Sonos','Bluesound') else ('reading' if len(unavailable)==1 else 'readings'))+' unavailable'

        devices=list((snapshot.get('network_health') or {}).get('devices',[]))
        if system=='Ruckus':devices+=((snapshot.get('controller') or {}).get('access_points',[]))
        offline=[d for d in devices if d.get('state')=='offline' or d.get('online') is False]
        for d in devices:
            key=system+':'+str(d.get('id',d.get('name')))
            if d.get('state') in ('online','offline') or type(d.get('online')) is bool:evaluated[key]=at
            if d in offline:issue(key,system,str(d.get('name',system))+' offline',at,'Controller reports offline',scope=key)
        if offline:title=f'{len(offline)} devices offline'
        if check and check.get('status')=='verified' and snapshot:evaluated[system+':finding']=at
        findings=(check or {}).get('service_findings',[])
        if findings and len(issues)==before:
            title=str(findings[0]).split(';')[0]
            issue(system+':finding',system,title,at,scope=system+':finding')
        if check and not snapshot:
            title=system+' check failed' if findings else 'No readings available';state='unknown'
        if system=='Crestron':
            cp=checks.get('CP4-R') or {};cp_at=cp.get('completed_at')
            for r in (cp.get('snapshot') or {}).get('readings',[]):
                if r.get('command')=='SYSTEMREADY' and r.get('status')=='response received':
                    if 'systemready: false' in r.get('response','').lower():
                        title='Processor not ready';issue('Crestron:processor',system,title,cp_at,scope='processor')
                    evaluated['processor']=cp_at
            if cp.get('service_findings'):
                issue('Crestron:processor-finding',system,'Processor needs attention',cp_at,str(cp['service_findings'][0]),scope='processor')
                title='Processor needs attention'
        if system=='Sense':
            legs={}
            for r in readings:
                if (r.get('attributes') or {}).get('unit_of_measurement')!='V':continue
                n=number(r.get('state'));label=str(r.get('name') or r.get('entity_id',''))
                if n is None:continue
                for leg in ('l1','l2'):
                    if leg in label.lower():legs[leg]=n
                if 'l1' in label.lower() or 'l2' in label.lower():
                    if n>132:
                        title=f'High voltage · {label}: {n:g} V';issue('Sense:high:'+r.get('entity_id',label),system,title,at,'Above 132 V on a reported 120 V line')
                    elif 10<n<108:
                        title=f'Low voltage · {label}: {n:g} V';issue('Sense:low:'+r.get('entity_id',label),system,title,at,'Below 108 V on a reported 120 V line')
            if len(legs)==2:
                evaluated['Sense']=at
                missing=[leg.upper() for leg,n in legs.items() if n<10 and 108<=legs['l2' if leg=='l1' else 'l1']<=132]
                if missing:
                    title='Possible missing phase · '+', '.join(missing);issue('Sense:phase',system,title,at,'One measured line near zero; other line in range')
                elif all(n<10 for n in legs.values()):
                    title='Both lines near zero';issue('Sense:loss',system,title,at,'Both measured line voltages below 10 V')
        if len(issues)>before:state='issue'
        rows.append(dict(system=system,title=title,state=state,at=at,view=view,target='system-'+system.lower()))
    ups=pilot.usb_ups.status()
    if ups.get('enabled'):
        at=(ups.get('last_state') or {}).get('received_at') or (ups.get('last_state') or {}).get('reported_at')
        active=ups.get('status')=='reported' and ups.get('current')=='on_battery'
        if ups.get('status')=='reported':evaluated['UPS']=at
        if active:issue('UPS:battery','UPS','UPS running on battery',at,'UPS reports battery operation',scope='UPS')
        rows.append(dict(system='UPS',title='Running on battery' if active else 'Utility power reported' if ups.get('status')=='reported' and ups.get('current')=='line_power' else 'Power status unavailable',state='issue' if active else 'ok' if ups.get('status')=='reported' else 'unknown',at=at,view='power',target='ups-results'))
        for e in ups.get('events',[]):
            titles={'on_battery':'Switched to battery','line_power_restored':'Utility power restored','low_battery':'UPS battery low','replace_battery':'UPS battery replacement needed','forced_shutdown':'UPS forced shutdown','shutdown':'UPS shutdown'}
            if e.get('kind') in titles:
                when=e.get('reported_at') or e.get('received_at')
                issue('UPS:event:'+str(e.get('kind'))+':'+str(when),'UPS',titles[e['kind']],when,scope='UPS events',active=False)
    battery_events=[e for e in ups.get('events',[]) if e.get('kind')=='on_battery' and is_today(e.get('reported_at') or e.get('received_at'))]
    if battery_events:
        at=battery_events[-1].get('reported_at') or battery_events[-1].get('received_at')
        rows.append(dict(system='Power',title=f'UPS battery transfers today: {len(battery_events)}',state='issue',at=at,view='power',target='ups-results'))
    power=pilot.power_status()
    if power.get('enabled'):
        sample=power.get('current') or {};at=power.get('checked_at')
        if power.get('status')=='verified' and sample.get('connected') is True:
            evaluated['WattBox UPS']=at
            if sample.get('power_lost') is True:
                issue('WattBox:input-power','WattBox','UPS input power lost',at,'WattBox UPS reports lost input power',scope='WattBox UPS')
                rows.append(dict(system='Power',title='UPS input power lost',state='issue',at=at,view='power',target='system-wattbox'))
            if sample.get('battery_health')=='Bad':
                issue('WattBox:battery-health','WattBox','UPS battery needs attention',at,scope='WattBox UPS')
                rows.append(dict(system='Power',title='UPS battery needs attention',state='issue',at=at,view='power',target='system-wattbox'))
        for event in power.get('events',[]):
            titles={'input_power_lost':'UPS input power lost','input_power_restored':'UPS input power restored','battery_health_changed':'UPS battery health changed'}
            if event.get('kind') in titles:
                when=event.get('observed_at')
                issue('WattBox:event:'+str(event.get('kind'))+':'+str(when),'WattBox',titles[event['kind']],when,scope='WattBox power events',active=False)
    losses=[e for e in power.get('events',[]) if e.get('kind')=='input_power_lost' and is_today(e.get('observed_at'))]
    if losses:
        rows.append(dict(system='Power',title=f'UPS input-power losses today: {len(losses)}',state='issue',at=losses[-1]['observed_at'],view='power',target='system-wattbox'))
    # Count measured samples today, not fabricated outages or unmeasured time.
    trial=pilot.network_trial.snapshot();samples=trial.get('samples',[]);current=trial.get('current') or {}
    high=[];loss=[]
    for sample in samples:
        if datetime.fromtimestamp(sample['time'],timezone.utc).astimezone(zone).date()!=today:continue
        if any(number(r.get('latency_ms')) is not None and float(r['latency_ms'])>(10 if i==0 else 100) for i,r in enumerate(sample['rows'])):high.append(sample)
        if any((number(r.get('loss_percent')) or 0)>0 for r in sample['rows']):loss.append(sample)
    for label,found in (('High ping',high),('Packet loss',loss)):
        if found:
            title=f'{label} in {len(found)} samples today';at=iso(found[-1]['time'])
            rows.append(dict(system='Network',title=title,state='issue',at=at,view='network',target='network-monitoring'))
            issue('Network:'+label+':'+str(today),'Internet',title,at,'Gateway >10 ms; internet targets >100 ms' if label=='High ping' else 'Measured missed ICMP replies',scope='ping history',active=False)
    for group in list(pilot.green_history.values())+list(pilot.network_history.values()):
        if not isinstance(group,dict):continue
        for series in group.values():
            if not isinstance(series,dict) or series.get('unit') not in ('Mbps','Mbit/s'):continue
            points=sorted([p for p in series.get('points',[]) if number(p.get('value')) is not None],key=lambda p:p['time'])
            if len(points)<4:continue
            latest=points[-1];previous=points[:-1];average=sum(float(p['value']) for p in previous)/len(previous)
            scope='speed:'+str(series.get('name'));evaluated[scope]=latest['time']
            if average>0 and float(latest['value'])<average*.8:
                direction='Upload' if 'upload' in str(series.get('name','')).lower() else 'Download'
                title=f'{direction} speed {100*(1-float(latest["value"])/average):.0f}% below average'
                issue(scope,'Internet',title,latest['time'],f'{float(latest["value"]):g} Mbps · prior average {average:.0f} Mbps',scope=scope)
                rows.append(dict(system='Internet',title=title,state='issue',at=latest['time'],view='network',target='system-internet'))
    return sorted(rows,key=lambda row:({'issue':0,'unknown':1,'ok':2}[row['state']],row['system'])),issues,evaluated


class IssueLog:
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.lock=threading.RLock();self.items=[];self.error=''
        try:
            if self.path.exists():
                if self.path.stat().st_size>8388608:raise ValueError()
                raw=json.loads(self.path.read_text())
                if raw.get('format')!='adr-issues-v1' or not isinstance(raw.get('items'),list) or len(raw['items'])>10000:raise ValueError()
                self.items=raw['items']
                if any(not isinstance(r,dict) or not all(k in r for k in ('key','scope','first_seen','last_seen','title','system','state')) for r in self.items):raise ValueError()
                if any(not all(isinstance(r[k],str) for k in ('key','scope','first_seen','last_seen','title','system','state')) for r in self.items):raise ValueError()
        except (OSError,ValueError,TypeError,AttributeError):self.error='Saved issue log could not be read.'

    def update(self,issues,evaluated):
        with self.lock:
            if self.error:return
            original=json.dumps(self.items,sort_keys=True)
            cutoff=iso(self.clock()-RETENTION)
            self.items=[r for r in self.items if r['last_seen']>=cutoff]
            present={r['key'] for r in issues};active={r['key']:r for r in self.items if r['state']=='Open'}
            for issue in issues:
                at=issue.get('at')
                if not at or at<cutoff:continue
                event=not issue.get('active',True)
                existing=next((r for r in self.items if r['key']==issue['key']),None) if event else active.get(issue['key'])
                if existing:
                    if at>existing['last_seen']:existing.update(last_seen=at,title=issue['title'],detail=issue.get('detail',''))
                    continue
                self.items.append(dict(key=issue['key'],scope=issue['scope'],system=issue['system'],title=issue['title'],detail=issue.get('detail',''),first_seen=at,last_seen=at,state='Recorded' if event else 'Open',resolved_at=None))
            for item in self.items:
                at=evaluated.get(item['scope'])
                if item['state']=='Open' and item['key'] not in present and at and at>item['last_seen']:
                    item.update(state='Recovered',resolved_at=at,last_seen=at)
            self.items=sorted(self.items,key=lambda r:r['last_seen'])[-10000:]
            if json.dumps(self.items,sort_keys=True)!=original:
                try:save(self.path,{'format':'adr-issues-v1','items':self.items})
                except OSError:self.error='Issue log could not be saved.'


def overview_markup(rows):
    from dashboard import display_time
    esc=lambda value:html.escape(str(value),quote=True)
    out='<section class="status-overview" aria-label="System status"><h2>System status</h2><ul>'
    for r in rows:
        out+='<li><button type="button" class="snapshot-row snapshot-'+r['state']+'" data-overview-view="'+esc(r['view'])+'" data-overview-target="'+esc(r['target'])+'"><span class="snapshot-system">'+esc(r['system'])+'</span><span class="snapshot-message">'+esc(r['title'])+'</span><small>'+esc(display_time(r['at']) if r['at'] else 'Awaiting check')+'</small></button></li>'
    return out+'</ul></section>'


def log_markup(journal):
    from dashboard import display_time
    esc=lambda value:html.escape(str(value),quote=True)
    out='<section id="issue-log"><h2>Issue log</h2><p class="report-caption">Last 90 days</p>'
    if journal.error:return out+'<p>'+esc(journal.error)+'</p></section>'
    day=None
    for item in sorted(journal.items,key=lambda r:r['first_seen'],reverse=True):
        date=display_time(item['first_seen']).split(' · ')[0]
        if date!=day:
            if day is not None:out+='</ul></details>'
            out+='<details class="issue-day"><summary>'+esc(date)+'</summary><ul>';day=date
        out+='<li><strong>'+esc(item['system']+' · '+item['title'])+'</strong><span>'+esc(item['state'])+'</span><small>First: '+esc(display_time(item['first_seen']))+' · Last: '+esc(display_time(item['last_seen']))+'</small>'
        if item.get('resolved_at'):out+='<small>Recovered: '+esc(display_time(item['resolved_at']))+'</small>'
        if item.get('detail'):out+='<small>'+esc(item['detail'])+'</small>'
        out+='</li>'
    if day is not None:out+='</ul></details>'
    else:out+='<p>No issues recorded yet.</p>'
    return out+'</section>'
