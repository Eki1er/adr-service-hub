"""Bounded WattBox history; electrical measurements are separate from outlet states."""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from network_diagnostics import save


class OutletHistory:
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.error=''
        self.data={'target':'','samples':[]}
        try:
            if self.path.exists():
                if self.path.stat().st_size>1048576:raise ValueError()
                data=json.loads(self.path.read_text())
                if not isinstance(data,dict) or set(data)!={'target','samples'} or not isinstance(data['target'],str) or not isinstance(data['samples'],list) or len(data['samples'])>512:raise ValueError()
                for row in data['samples']:
                    if set(row)!={'time','epoch','outlets'} or type(row['epoch']) not in (int,float) or not math.isfinite(row['epoch']):raise ValueError()
                    if row['time']!=datetime.fromtimestamp(row['epoch'],timezone.utc).isoformat():raise ValueError()
                    if not isinstance(row['outlets'],dict) or len(row['outlets'])>18:raise ValueError()
                    if any(not str(k).isdigit() or not 1<=int(k)<=18 or type(v) is not bool for k,v in row['outlets'].items()):raise ValueError()
                self.data=data
        except (OSError,ValueError,TypeError,KeyError,OverflowError):
            self.error='Saved outlet history could not be read. Existing storage was preserved.'

    def observe(self,target,inventory):
        if self.error:return {'series':[],'error':self.error}
        now=self.clock()
        if target!=self.data['target']:self.data={'target':target,'samples':[]}
        outlets={str(o['number']):o['on'] for o in inventory.get('outlets',[]) if type(o.get('number')) is int and 1<=o['number']<=18 and type(o.get('on')) is bool}
        if not outlets:return {'series':[],'error':'No valid outlet samples were available.'}
        rows=[r for r in self.data['samples'] if 0<=now-r['epoch']<=7*86400]
        rows.append({'time':datetime.fromtimestamp(now,timezone.utc).isoformat(),'epoch':now,'outlets':outlets})
        self.data['samples']=rows[-512:]
        try:save(self.path,self.data)
        except OSError:self.error='Outlet history could not be saved.'
        series=[{'name':'WattBox outlets reported on','unit':'outlets','max_gap_seconds':1800,'points_only':True,
                 'points':[{'time':r['time'],'value':sum(r['outlets'].values())} for r in self.data['samples']]}]
        changed=[]
        for outlet in inventory.get('outlets',[]):
            key=str(outlet.get('number'))
            values=[r['outlets'].get(key) for r in self.data['samples']]
            if any(a is not None and b is not None and a!=b for a,b in zip(values,values[1:])):
                changed.append(outlet)
        for outlet in changed[:4]:
            key=str(outlet['number'])
            series.append({'name':str(outlet.get('label','Outlet '+key))[:120]+' · Outlet '+key,
                'unit':'Outlet power','state_labels':{'0':'Off','1':'On'},'points_only':True,
                'points':[{'time':r['time'],'value':int(r['outlets'][key]) if key in r['outlets'] else None} for r in self.data['samples']]})
        return {'series':series,'error':self.error,'note':'Sampled outlet power states, not watts or proof of equipment operation. Monitoring gaps and short changes can be missed.'}


class ElectricalHistory:
    """Persist actual system voltage/current; missing samples remain explicit gaps."""
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.error=''
        self.data={'target':'','samples':[]}
        try:
            if self.path.exists():
                if self.path.stat().st_size>1048576:raise ValueError()
                data=json.loads(self.path.read_text())
                if not isinstance(data,dict) or set(data)!={'target','samples'} or not isinstance(data['target'],str) or not isinstance(data['samples'],list) or len(data['samples'])>512:raise ValueError()
                for row in data['samples']:
                    if not isinstance(row,dict) or set(row)!={'time','epoch','voltage_v','current_a'}:raise ValueError()
                    if type(row['epoch']) not in (int,float) or not math.isfinite(row['epoch']):raise ValueError()
                    if row['time']!=datetime.fromtimestamp(row['epoch'],timezone.utc).isoformat():raise ValueError()
                    for key,limit in (('voltage_v',600),('current_a',1000)):
                        value=row[key]
                        if value is not None and (type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=limit):raise ValueError()
                self.data=data
        except (OSError,ValueError,TypeError,KeyError,OverflowError):
            self.error='Saved electrical history could not be read. Existing storage was preserved.'

    def observe(self,target,measurement):
        if self.error:return {'series':[],'error':self.error}
        now=self.clock()
        if target!=self.data['target']:self.data={'target':target,'samples':[]}
        row={'time':datetime.fromtimestamp(now,timezone.utc).isoformat(),'epoch':now}
        for key,limit in (('voltage_v',600),('current_a',1000)):
            value=(measurement or {}).get(key)
            row[key]=value if type(value) in (int,float) and math.isfinite(value) and 0<=value<=limit else None
        self.data['samples']=[r for r in self.data['samples'] if 0<=now-r['epoch']<=7*86400][-511:]+[row]
        try:save(self.path,self.data)
        except OSError:self.error='Electrical history could not be saved.'
        return {'series':[{'name':label,'unit':unit,'max_gap_seconds':3600,
            'points':[{'time':r['time'],'value':r[key]} for r in self.data['samples']]}
            for key,label,unit in (('voltage_v','WattBox voltage','V'),('current_a','WattBox current','A'))],
            'error':self.error,'note':'Whole-WattBox measurements. History starts when readings are collected; unavailable readings remain gaps.'}
