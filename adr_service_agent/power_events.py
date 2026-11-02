"""Bounded local UPS observations. Poll times are never exact outage times."""
import copy
import hashlib
import json
import math
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from network_diagnostics import save
from wattbox import ProtocolError

FIELDS=('connected','charge_percent','load_percent','battery_health','power_lost','runtime_minutes','alarm_enabled','alarm_muted')
STATES={'input_power_lost','input_power_available','ups_not_connected'}
LIMITATIONS=['These are sampled UPS observations, not a complete source event log.',
    'Events shorter than the polling interval, busy periods or communication gaps can be missed.',
    'Input power loss does not identify a utility outage, voltage disturbance or disconnected input cable.',
    'No exact outage start, restoration time or duration is inferred from polling.']


def stamp(value):return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec='seconds')


def identity(options,keyfile):
    pin=Path(keyfile).read_text() if Path(keyfile).exists() else ''
    return hashlib.sha256(json.dumps([options.get('wattbox_host'),options.get('ssh_port'),pin]).encode()).hexdigest()


def clean(sample):
    if not isinstance(sample,dict) or type(sample.get('connected')) is not bool:
        raise ProtocolError('Invalid UPS connection response.')
    if sample['connected'] is False:return {'connected':False}
    for key in ('charge_percent','load_percent','runtime_minutes'):
        n=sample.get(key)
        if type(n) not in (int,float) or not math.isfinite(n) or n<0 or n>(100 if key!='runtime_minutes' else 100000):
            raise ProtocolError('Invalid UPS measurement.')
    if sample.get('battery_health') not in ('Good','Bad') or any(type(sample.get(k)) is not bool for k in ('power_lost','alarm_enabled','alarm_muted')):
        raise ProtocolError('Invalid UPS status response.')
    return {k:sample[k] for k in FIELDS}


def operating(sample):
    return 'ups_not_connected' if not sample['connected'] else 'input_power_lost' if sample['power_lost'] else 'input_power_available'


class PowerJournal:
    def __init__(self,path,interval=15,clock=time.time):
        self.path=Path(path);self.clock=clock;self.interval=interval;self.lock=threading.RLock()
        self.data={'format':'adr-power-events-v1','target':'','events':[],'latest':None}
        self.error='';self.last_saved=0;self.gap=True
        self.read_status='unverified';self.last_attempt=None
        try:
            if self.path.exists():
                if self.path.stat().st_size>512*1024:raise ValueError()
                data=json.loads(self.path.read_text())
                if not isinstance(data,dict) or data.get('format')!='adr-power-events-v1' or not isinstance(data.get('events'),list):raise ValueError()
                if not isinstance(data.get('target'),str) or (data['target'] and (len(data['target'])!=64 or any(c not in '0123456789abcdef' for c in data['target']))):raise ValueError()
                events=data['events']
                if len(events)>512:raise ValueError()
                for e in events:
                    if not isinstance(e,dict) or type(e.get('observed_at_epoch')) not in (int,float) or not math.isfinite(e['observed_at_epoch']):raise ValueError()
                    if e.get('kind') not in ('initial_observation','observation_after_gap','input_power_lost','input_power_restored','ups_link_changed','battery_health_changed','monitor_unverified'):raise ValueError()
                    if e.get('observed_at')!=stamp(e['observed_at_epoch']) or e.get('state') not in STATES|{'unverified'}:raise ValueError()
                    if set(e)-{'kind','observed_at','observed_at_epoch','state','sample','previous_observation_at','previous_state','continuous_sampling'}:raise ValueError()
                    if type(e.get('continuous_sampling')) is not bool:raise ValueError()
                    if 'sample' in e:e['sample']=clean(e['sample'])
                    if 'previous_state' in e and e['previous_state'] not in STATES:raise ValueError()
                    if 'previous_observation_at' in e:
                        previous=datetime.fromisoformat(e['previous_observation_at'])
                        if previous.tzinfo is None:raise ValueError()
                latest=data.get('latest')
                if latest is not None:
                    if not isinstance(latest,dict) or type(latest.get('observed_at_epoch')) not in (int,float) or not math.isfinite(latest['observed_at_epoch']) or latest.get('observed_at')!=stamp(latest['observed_at_epoch']):raise ValueError()
                    latest={'sample':clean(latest['sample']),'observed_at':latest['observed_at'],'observed_at_epoch':latest['observed_at_epoch']}
                self.data={'format':'adr-power-events-v1','target':data['target'],'events':events,'latest':latest}
                self.prune(self.clock())
        except Exception:self.error='Saved power history could not be read. No data was removed; inspect app storage.'

    def prune(self,current):
        self.data['events']=[e for e in self.data['events'] if current-30*86400<=e['observed_at_epoch']<=current+60][-512:]

    def persist(self,current,force=False):
        if not force and current-self.last_saved<300:return
        try:
            save(self.path,self.data);self.last_saved=current
        except Exception:
            self.error='Power history could not be saved. Monitoring history requires local storage attention.'
            raise ProtocolError(self.error) from None

    def observe(self,target,sample):
        with self.lock:
            if self.error:raise ProtocolError(self.error)
            if not isinstance(target,str) or len(target)!=64 or any(c not in '0123456789abcdef' for c in target):raise ProtocolError('Invalid power monitor target.')
            current=self.clock();sample=clean(sample)
            if target!=self.data['target']:
                self.data={'format':'adr-power-events-v1','target':target,'events':[],'latest':None};self.gap=True
            old=self.data['latest'];state=operating(sample);kind=None
            gap=self.gap or old is None or not 0<=current-old['observed_at_epoch']<=max(45,self.interval*2)
            if old is None:kind='initial_observation'
            elif gap:kind='observation_after_gap'
            elif sample['connected']!=old['sample']['connected']:kind='ups_link_changed'
            elif sample['connected'] and sample['power_lost']!=old['sample'].get('power_lost'):
                kind='input_power_lost' if sample['power_lost'] else 'input_power_restored'
            changed=False
            def event(kind):
                item={'kind':kind,'observed_at':stamp(current),'observed_at_epoch':current,'state':state,'sample':copy.deepcopy(sample)}
                if old is not None:
                    item['previous_observation_at']=old['observed_at'];item['previous_state']=operating(old['sample'])
                item['continuous_sampling']=not gap
                self.data['events'].append(item)
            if kind:event(kind);changed=True
            if old and sample['connected'] and old['sample'].get('connected') and sample['battery_health']!=old['sample'].get('battery_health'):
                event('battery_health_changed');changed=True
            self.data['latest']={'sample':sample,'observed_at':stamp(current),'observed_at_epoch':current}
            self.gap=False;self.read_status='verified';self.last_attempt=stamp(current)
            self.prune(current);self.persist(current,force=changed)

    def unverified(self):
        with self.lock:
            current=self.clock()
            if self.error:return
            if not self.gap or self.last_attempt is None:
                self.data['events'].append({'kind':'monitor_unverified','observed_at':stamp(current),
                    'observed_at_epoch':current,'state':'unverified','continuous_sampling':False})
                self.prune(current)
                try:self.persist(current,force=True)
                except ProtocolError:pass
            self.gap=True;self.read_status='unverified';self.last_attempt=stamp(current)

    def status(self,target):
        with self.lock:
            self.prune(self.clock())
            same=target==self.data['target'];latest=self.data['latest'] if same else None
            fresh=bool(latest and 0<=self.clock()-latest['observed_at_epoch']<=max(45,self.interval*2))
            verified=same and fresh and self.read_status=='verified' and not self.error
            return {'enabled':True,'status':'verified' if verified else 'unverified',
                'checked_at':self.last_attempt,'last_verified':copy.deepcopy(latest),
                'current':copy.deepcopy(latest['sample']) if verified else None,
                'events':copy.deepcopy(self.data['events'][-50:]) if same else [],
                'poll_interval_seconds':self.interval,'error':self.error,
                'limitations':list(LIMITATIONS)}
