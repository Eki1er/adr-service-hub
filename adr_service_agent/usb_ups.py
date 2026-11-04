"""Read-only NUT state/event subscriptions with bounded, persistent history."""
import copy
import hashlib
import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import websocket
from network_diagnostics import save
from ruckus import token_value
from sonos import receive

LIMITATIONS = [
    'USB UPS reports are received through the local monitoring service; they are not historical device logs.',
    'Reported and received times are not exact physical outage times. Short events or monitoring gaps can be missed.',
    'Battery operation can result from lost input, voltage conditions or an unplugged input; it does not establish a utility outage.',
    'A connected event stream does not independently verify fresh device communication. Battery charge may be estimated by the driver.'
]
KINDS = {'baseline', 'on_battery', 'line_power_restored', 'status_unverified', 'monitoring_gap',
         'low_battery', 'replace_battery', 'forced_shutdown', 'shutdown', 'communications_restored'}
NOTICES = {'ONBATT': ('on_battery', 'on_battery'), 'ONLINE': ('line_power_restored', 'line_power'),
           'LOWBATT': ('low_battery', None), 'REPLBATT': ('replace_battery', None),
           'FSD': ('forced_shutdown', None), 'SHUTDOWN': ('shutdown', None),
           'COMMOK': ('communications_restored', None),
           'COMMBAD': ('status_unverified', 'unverified'), 'NOCOMM': ('status_unverified', 'unverified')}


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec='seconds')


def epoch(value):
    try:
        date = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        result = date.timestamp()
        return result if date.tzinfo and 1577836800 <= result <= time.time()+300 else None
    except (ValueError, TypeError, OverflowError):
        return None


def operating(raw):
    if not isinstance(raw, str) or len(raw) > 120:
        return 'unverified'
    flags = set(raw.split())
    if 'OB' in flags and 'OL' not in flags:
        return 'on_battery'
    if 'OL' in flags and 'OB' not in flags:
        return 'line_power'
    return 'unverified'


class USBUPS:
    def __init__(self, path, options, clock=time.time, connect=websocket.create_connection):
        self.path = Path(path); self.clock = clock; self.connect = connect
        self.entity = options.get('nut_status_entity', '')
        self.ups = options.get('nut_ups_name', '')
        self.enabled = bool(self.entity or self.ups)
        self.error = ''; self.running = False; self.connected = False
        self.lock = threading.RLock(); self.stop = threading.Event(); self.ws = None; self.thread = None
        self.current = 'unverified'; self.events = []; self.latest = None; self.last_state = None
        self.gap = True; self.watermark = 0; self.target = ''
        if not self.enabled:
            return
        if not isinstance(self.entity, str) or not re.fullmatch(r'sensor\.[a-z0-9_]{1,180}', self.entity) or not isinstance(self.ups, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', self.ups):
            self.error = 'Set both the USB UPS status sensor and NUT UPS name in Configuration.'
            return
        self.target = hashlib.sha256(json.dumps([self.entity, self.ups]).encode()).hexdigest()
        try:
            if self.path.exists():
                if self.path.stat().st_size > 512*1024:
                    raise ValueError()
                data = json.loads(self.path.read_text())
                if data.get('format') != 'adr-usb-ups-v1' or not isinstance(data.get('events'), list) or len(data['events']) > 512:
                    raise ValueError()
                if data.get('target') == self.target:
                    for e in data['events']:
                        if not isinstance(e, dict) or set(e) != {'kind','state','reported_at','received_at','source'} or e['kind'] not in KINDS or e['state'] not in ('line_power','on_battery','unverified') or e['source'] not in ('nut_notification','status_sensor','listener') or epoch(e['received_at']) is None or (e['reported_at'] is not None and epoch(e['reported_at']) is None):
                            raise ValueError()
                    self.events = data['events']
                    self.prune()
        except Exception:
            self.error = 'USB UPS history could not be read. Inspect app storage; no data was removed.'

    def prune(self):
        cutoff = self.clock()-30*86400
        self.events = [e for e in self.events if (epoch(e['received_at']) or 0) >= cutoff][-512:]

    def record(self, kind, state, source, reported=None):
        self.prune()
        item = {'kind':kind, 'state':state, 'source':source, 'reported_at':reported, 'received_at':iso(self.clock())}
        self.events.append(item); self.events = self.events[-512:]
        try:
            save(self.path, {'format':'adr-usb-ups-v1','target':self.target,'events':self.events})
        except Exception:
            self.error = 'USB UPS history could not be saved. Inspect app storage.'
            raise ValueError(self.error) from None
        self.latest = item

    def sensor(self, raw, reported=None, baseline=False):
        with self.lock:
            reported_epoch = epoch(reported)
            if reported_epoch and reported_epoch < self.watermark:
                return
            state = operating(raw)
            self.last_state = {'state':state, 'reported_at':reported if reported_epoch else None,
                               'received_at':iso(self.clock())}
            if baseline or self.gap:
                self.record('baseline', state, 'status_sensor', self.last_state['reported_at'])
            elif state != self.current:
                kind = 'on_battery' if state == 'on_battery' else 'line_power_restored' if state == 'line_power' and self.current == 'on_battery' else 'status_unverified' if state == 'unverified' else 'baseline'
                self.record(kind, state, 'status_sensor', self.last_state['reported_at'])
            self.current = state; self.gap = state == 'unverified'
            if reported_epoch:
                self.watermark = max(self.watermark, reported_epoch)

    def notification(self, data, reported=None):
        if not isinstance(data, dict) or data.get('ups_name') != self.ups or data.get('notify_type') not in NOTICES:
            return
        with self.lock:
            when = epoch(reported)
            if when is not None and when < self.watermark:
                return
            kind, state = NOTICES[data['notify_type']]
            state = state or self.current
            # The sensor and the native notification may describe the same transition.
            if kind in ('on_battery','line_power_restored') and not self.gap and state == self.current:
                return
            self.record(kind, state, 'nut_notification', reported if when else None)
            if data['notify_type'] in ('ONBATT','ONLINE','COMMBAD','NOCOMM'):
                self.current = state; self.gap = state == 'unverified'
            if when:
                self.watermark = max(self.watermark, when)

    def disconnected(self):
        with self.lock:
            self.connected = False
            if not self.gap:
                self.record('monitoring_gap', 'unverified', 'listener')
            self.current = 'unverified'; self.gap = True

    def session(self):
        ws = self.connect('ws://supervisor/core/websocket', timeout=8,
                          http_no_proxy=['supervisor'], suppress_origin=True)
        self.ws = ws
        try:
            if receive(ws).get('type') != 'auth_required':
                raise ValueError()
            ws.send(json.dumps({'type':'auth','access_token':token_value()}))
            if receive(ws).get('type') != 'auth_ok':
                raise ValueError()
            ws.send(json.dumps({'id':1,'type':'config/entity_registry/list'}))
            registry = receive(ws)
            if registry.get('success') is not True or not isinstance(registry.get('result'), list) or not any(e.get('entity_id') == self.entity and e.get('platform') == 'nut' and not e.get('disabled_by') for e in registry['result'] if isinstance(e,dict)):
                raise ValueError()
            ws.send(json.dumps({'id':2,'type':'subscribe_events','event_type':'nut.ups_event'}))
            ws.send(json.dumps({'id':3,'type':'subscribe_entities','entity_ids':[self.entity]}))
            ready = set(); ping_at = time.monotonic(); waiting = None; initial = True; next_ping = 10
            while not self.stop.is_set():
                try:
                    message = receive(ws)
                except websocket.WebSocketTimeoutException:
                    message = {}
                now = time.monotonic()
                if message.get('type') == 'result' and message.get('id') in (2,3):
                    if message.get('success') is not True:
                        raise ValueError()
                    ready.add(message['id'])
                    if ready == {2,3}:
                        with self.lock:
                            self.connected = True
                            if not self.error.startswith('USB UPS history'):
                                self.error = ''
                elif message.get('type') == 'event' and message.get('id') == 2:
                    event = message.get('event', {})
                    self.notification(event.get('data'), event.get('time_fired'))
                elif message.get('type') == 'event' and message.get('id') == 3:
                    event = message.get('event', {})
                    if self.entity in event.get('r', []):
                        self.sensor('unavailable')
                    added = event.get('a', {}).get(self.entity)
                    changed = event.get('c', {}).get(self.entity, {}).get('+', {})
                    row = added if added is not None else changed
                    if isinstance(row, dict) and 's' in row:
                        stamp = row.get('lu', row.get('lc'))
                        stamp = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if type(stamp) in (int,float) and 1577836800 <= stamp <= self.clock()+300 else None
                        self.sensor(row['s'], stamp, baseline=initial)
                        initial = False
                elif message.get('type') == 'pong' and message.get('id') == waiting:
                    waiting = None
                if waiting is not None and now-ping_at > 20:
                    raise ValueError()
                if now-ping_at >= 30 and waiting is None:
                    waiting = next_ping; next_ping += 1; ping_at = now
                    ws.send(json.dumps({'id':waiting,'type':'ping'}))
                if not self.connected and now-ping_at > 15:
                    raise ValueError()
        finally:
            ws.close(); self.ws = None

    def start(self):
        if not self.enabled or self.error or self.running:
            return
        self.running = True
        def loop():
            try:
                while not self.stop.is_set():
                    try:
                        self.session()
                    except Exception:
                        with self.lock:
                            if not self.error.startswith('USB UPS history'):
                                self.error = 'USB UPS listener disconnected. Check the NUT integration and configured sensor; reconnecting.'
                    finally:
                        try:
                            self.disconnected()
                        except Exception:
                            pass
                    if self.error.startswith('USB UPS history') or self.stop.wait(30):
                        break
            finally:
                self.running = False
        self.thread = threading.Thread(target=loop, daemon=True, name='adr-usb-ups')
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.ws:
            self.ws.close()
        if self.thread:
            self.thread.join(timeout=2)

    def status(self):
        with self.lock:
            self.prune()
            return {'enabled':self.enabled,'listener_connected':self.connected,
                    'status':'reported' if self.connected and self.current != 'unverified' and not self.error else 'unverified',
                    'current':self.current if self.connected else 'unverified',
                    'latest':copy.deepcopy(self.latest), 'last_state':copy.deepcopy(self.last_state),
                    'events':copy.deepcopy(self.events[-50:]), 'error':self.error,
                    'limitations':list(LIMITATIONS)}
