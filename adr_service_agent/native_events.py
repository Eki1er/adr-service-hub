"""Bounded native syslog ingestion and explicit text-log imports.

Messages are untrusted device reports, never commands or proof of complete health.
"""
import copy
import hashlib
import ipaddress
import json
import math
import re
import socket
import threading
import time
from datetime import datetime,timezone
from pathlib import Path
from network_diagnostics import save
from wattbox import ProtocolError

VENDORS=('unifi','ruckus','wattbox','crestron','control4','sonos','bluos')
PRIVATE=tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16'))
MAX_PACKET=8192
MAX_IMPORT=128*1024


def iso(epoch):return datetime.fromtimestamp(epoch,timezone.utc).isoformat(timespec='seconds')


def sources(options):
    rows=options.get('native_event_sources',[])
    if not isinstance(rows,list) or len(rows)>16:raise ProtocolError('Configure at most 16 native event sources.')
    result={}
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'host','vendor','name'}:raise ProtocolError('Each event source needs host, vendor and name.')
        try:
            ip=ipaddress.ip_address(row['host'])
            if ip.version!=4 or not any(ip in n for n in PRIVATE):raise ValueError()
        except (ValueError,TypeError):raise ProtocolError('Event sources must use explicit private IPv4 addresses.') from None
        if row['vendor'] not in VENDORS or not isinstance(row['name'],str) or not 1<=len(row['name'])<=60 or any(ord(c)<32 for c in row['name']):
            raise ProtocolError('Set a supported event vendor and a source name of 1–60 characters.')
        host=str(ip)
        if host in result:raise ProtocolError('Each event source address must be unique.')
        result[host]={'host':host,'vendor':row['vendor'],'name':row['name'],
            'id':hashlib.sha256(json.dumps([host,row['vendor'],row['name']]).encode()).hexdigest()[:24]}
    return result


def cef_parts(text):
    # CEF has seven unescaped pipes; escaped pipes/backslashes remain in fields.
    fields=[];word=[];escaped=False
    for index,char in enumerate(text):
        if escaped:
            word.extend(('\\',char));escaped=False
        elif char=='\\':escaped=True
        elif char=='|' and len(fields)<7:
            fields.append(''.join(word));word=[]
            if len(fields)==7:
                fields.append(text[index+1:]);return fields
        else:word.append(char)
    return []


def source_time(raw,received):
    if not raw:return None
    try:
        if re.fullmatch(r'\d{10,13}',raw):
            value=float(raw)/(1000 if len(raw)>10 else 1)
        else:
            dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if dt.tzinfo is None:return None
            value=dt.timestamp()
        if not math.isfinite(value) or not 1577836800<=value<=received+300:return None
        return iso(value)
    except (ValueError,OverflowError,OSError):return None


def normalize(text,source,received,scrub,transport):
    if not isinstance(text,str) or not text.strip() or '\x00' in text or len(text.encode())>MAX_PACKET:
        raise ProtocolError('Invalid or oversized log message.')
    text=text.strip();priority=None;native_time=None;event_name=None;event_code=None;severity=None;message=text
    pri=re.match(r'^<(\d{1,3})>',text)
    if pri:
        priority=int(pri[1])
        if priority>191:raise ProtocolError('Invalid syslog priority.')
        text=text[pri.end():];message=text;severity='syslog:'+str(priority%8)
    # RFC5424 timestamps include year/timezone. RFC3164 timestamps are kept as
    # reported text because year/timezone cannot be inferred reliably.
    stamp=re.match(r'^1\s+(\S+)\s+',text)
    if stamp and stamp[1]!='-':native_time=stamp[1]
    else:
        stamp=re.match(r'^(\d{4}-\d\d-\d\d[T ][0-9:.+-]+Z?)\s+',text)
        if stamp:native_time=stamp[1]
        else:
            stamp=re.match(r'^([A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d)\s+',text)
            if stamp:native_time=stamp[1]
    position=text.find('CEF:')
    parser='syslog' if pri else 'text'
    if position>=0:
        parts=cef_parts(text[position:])
        if len(parts)==8 and parts[0]=='CEF:0':
            parser='cef';event_code=parts[4];event_name=parts[5];severity='cef:'+parts[6] if re.fullmatch(r'(?:[0-9]|10)',parts[6]) else None
            extension=parts[7]
            matches=list(re.finditer(r'(?:^|\s)([A-Za-z][A-Za-z0-9_]*)=',extension))
            values={m[1]:extension[m.end():matches[i+1].start() if i+1<len(matches) else len(extension)].strip() for i,m in enumerate(matches)}
            message=values.get('msg') or event_name
            native_time=values.get('rt') or native_time
    device_at=source_time(native_time,received)
    return {'source_id':source['id'],'source':scrub(source['name'])[:60],'vendor':source['vendor'],
        'transport':transport,'parser':parser,'received_at':iso(received),'received_epoch':received,
        'device_time':device_at,'device_time_text':scrub(native_time)[:80] if native_time else None,
        'time_basis':'device_reported' if device_at else 'receipt_only',
        'event_code':scrub(event_code)[:100] if event_code else None,
        'event_name':scrub(event_name)[:160] if event_name else None,
        'severity':severity[:40] if severity else None,'message':scrub(message)[:800],
        'message_truncated':len(scrub(message))>800,
        'authenticity':'source_ip_allowlisted' if transport=='udp' else 'user_supplied_export'}


class NativeEvents:
    def __init__(self,path,options,clock=time.time):
        self.path=Path(path);self.clock=clock;self.lock=threading.RLock();self.stop=threading.Event()
        self.enabled=options.get('native_events_enabled') is True;self.sources={};self.error=''
        self.events=[];self.dirty=False;self.last_flush=0;self.running=False;self.thread=None;self.sock=None
        self.rejected=0;self.rate_dropped=0;self.duplicates=0;self.last_received={};self.rate={};self.started_at=None
        self.private=[v for k,v in options.items() if any(w in k for w in ('password','api_key','token')) and isinstance(v,str) and v]
        try:
            self.sources=sources(options)
            if self.path.exists():
                if self.path.stat().st_size>3*1024*1024:raise ValueError()
                data=json.loads(self.path.read_text())
                if not isinstance(data,dict) or data.get('format')!='adr-native-events-v1' or not isinstance(data.get('events'),list) or len(data['events'])>1000:raise ValueError()
                allowed={s['id']:s for s in self.sources.values()}
                keys={'source_id','source','vendor','transport','parser','received_at','received_epoch','device_time','device_time_text','time_basis','event_code','event_name','severity','message','message_truncated','authenticity'}
                for event in data['events']:
                    if not isinstance(event,dict) or set(event)!=keys or type(event['received_epoch']) not in (int,float) or not math.isfinite(event['received_epoch']):raise ValueError()
                    if event['source_id'] not in allowed:continue
                    if event['received_at']!=iso(event['received_epoch']) or event['transport'] not in ('udp','file') or event['vendor'] not in VENDORS:raise ValueError()
                    if any(v is not None and not isinstance(v,(str,int,float)) for v in event.values()):raise ValueError()
                    safe={k:self.scrub(v)[:800] if isinstance(v,str) else v for k,v in event.items()}
                    self.events.append(safe)
                self.prune()
        except ProtocolError as exc:self.error=str(exc)
        except Exception:self.error='Native event history could not be read. Inspect app storage; no data was removed.'

    def scrub(self,text):
        text=str(text)
        for secret in self.private:text=text.replace(secret,'[redacted]')
        text=re.sub(r"(?i)\b(password|passwd|(?:access[_-]?|auth[_-]?)?token|api[_-]?key|authorization)[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",r'\1=[redacted]',text)
        text=re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;\"']+",r'\1 [redacted]',text)
        return ''.join(c for c in text if c in '\n\t' or ord(c)>=32)

    def prune(self):
        now=self.clock();ids={s['id'] for s in self.sources.values()}
        self.events=[e for e in self.events if e['source_id'] in ids and now-30*86400<=e['received_epoch']<=now+60][-1000:]

    def flush(self,force=False):
        with self.lock:
            if not self.dirty or self.error or (not force and self.clock()-self.last_flush<5):return
            try:
                self.prune()
                # Bound serialized size too: escaped Unicode can be much larger
                # than its in-memory character count.
                size=0;kept=[]
                for event in reversed(self.events):
                    weight=len(json.dumps(event).encode())+2
                    if size+weight>2*1024*1024-1024:break
                    kept.append(event);size+=weight
                self.events=list(reversed(kept))
                save(self.path,{'format':'adr-native-events-v1','events':self.events})
                self.last_flush=self.clock();self.dirty=False
            except Exception:self.error='Native events could not be saved. Check app storage.'

    def append_event(self,event):
        if event['device_time'] and any(old['source_id']==event['source_id'] and old['device_time']==event['device_time'] and old['event_code']==event['event_code'] and old['message']==event['message'] for old in self.events):
            self.duplicates+=1;return False
        self.events.append(event);self.dirty=True;return True

    def accept(self,packet,peer,transport='udp'):
        with self.lock:
            if not self.enabled or self.error:return False
            source=self.sources.get(peer)
            if not source:self.rejected+=1;return False
            if not isinstance(packet,bytes) or not 1<=len(packet)<=MAX_PACKET:self.rejected+=1;return False
            now=self.clock()
            if transport=='udp':
                window,count=self.rate.get(peer,(now,0))
                if now-window>=1:window,count=now,0
                if count>=20:self.rate_dropped+=1;return False
                self.rate[peer]=(window,count+1)
            try:event=normalize(packet.decode('utf-8',errors='replace'),source,now,self.scrub,transport)
            except ProtocolError:self.rejected+=1;return False
            self.append_event(event);self.last_received[source['id']]=iso(now);self.prune()
            return True

    def import_text(self,source_id,text):
        with self.lock:
            if not self.enabled or self.error:raise ProtocolError(self.error or 'Enable native events in Configuration first.')
            source=next((s for s in self.sources.values() if s['id']==source_id),None)
            if not source:raise ProtocolError('Choose a configured event source.')
            if not isinstance(text,str) or not 1<=len(text.encode())<=MAX_IMPORT or '\x00' in text:raise ProtocolError('Choose a plain-text log smaller than 128 KB.')
            lines=[line for line in text.splitlines() if line.strip()]
            if not lines or len(lines)>256:raise ProtocolError('Import at most 256 nonempty log lines at a time.')
            # Validate the whole batch before modifying history. Imported source
            # identity is a user assertion, not a live authenticated device read.
            now=self.clock();events=[normalize(line,source,now,self.scrub,'file') for line in lines]
            count=sum(self.append_event(event) for event in events);self.prune();self.flush(force=True)
            if self.error:raise ProtocolError(self.error)
            return count

    def status(self):
        with self.lock:
            self.prune()
            coverage=[{'id':s['id'],'name':self.scrub(s['name']),'vendor':s['vendor'],
                'last_received_at':self.last_received.get(s['id']),
                'coverage':'events_received_this_session' if s['id'] in self.last_received else 'delivery_unverified'} for s in self.sources.values()]
            return {'enabled':self.enabled,'listener_running':self.running,'started_at':self.started_at,'error':self.error,
                'sources':coverage,'events':copy.deepcopy(self.events[-30:]),'rejected':self.rejected,'rate_dropped':self.rate_dropped,'duplicates':self.duplicates,
                'network_events':copy.deepcopy([e for e in self.events if re.search(r'\b(?:STP|RSTP|MSTP|BPDU|IGMP|multicast|querier|loop|topology|MAC flapp\w*)\b',
                    str(e.get('event_name',''))+' '+str(e.get('message','')),re.I)][-16:]),
                'limitations':['No events received does not establish that equipment is healthy.',
                    'UDP forwarding is lossy and source IPs can be spoofed; messages are untrusted device reports.',
                    'Device time may be missing, unsynchronized or ambiguous; receipt time is kept separately.',
                    'Historical files require manual import; forwarding does not backfill earlier events.',
                    'A quiet source does not prove the feed has failed or that no events occurred.']}

    def start(self,socket_factory=socket.socket,thread_factory=threading.Thread):
        with self.lock:
            if self.running or not self.enabled or self.error:return
            if not self.sources:self.error='Configure native event sources before starting log forwarding.';return
            try:
                sock=socket_factory(socket.AF_INET,socket.SOCK_DGRAM);sock.settimeout(1);sock.bind(('0.0.0.0',5514))
            except Exception:
                try:sock.close()
                except Exception:pass
                self.error='Native event listener could not bind UDP 5514. Check app networking.';return
            self.sock=sock;self.running=True;self.started_at=iso(self.clock())
        def run():
            try:
                while not self.stop.is_set():
                    try:
                        packet,peer=sock.recvfrom(MAX_PACKET+1)
                        self.accept(packet,peer[0])
                        self.stop.wait(.005)
                    except socket.timeout:pass
                    except OSError:break
                    self.flush()
            finally:
                sock.close()
                with self.lock:self.running=False
                self.flush(force=True)
        try:
            self.thread=thread_factory(target=run,daemon=True,name='adr-native-events');self.thread.start()
        except Exception:
            sock.close();self.running=False;self.error='Native event listener could not start.'

    def close(self):
        self.stop.set()
        if self.sock:self.sock.close()
        if self.thread:self.thread.join(timeout=2)
        self.flush(force=True)
