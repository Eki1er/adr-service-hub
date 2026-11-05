"""Opt-in, bounded ICMP trial. No device writes, speed tests, or shell commands."""
import copy
import ipaddress
import json
import math
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from green_network import probe

DURATION = 24 * 3600
BASELINE = 5 * 60
INTERVAL = 60
RETENTION = 7 * 86400
MAX_SAMPLES = 10080
MAX_BYTES = 12 * 1024 * 1024


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def gateway_address(options):
    try:
        address = ipaddress.ip_address(str(options.get('unifi_gateway_host', '')).strip())
        if not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
            raise ValueError()
        return str(address)
    except ValueError:
        raise ValueError('Set the local gateway IP in unifi_gateway_host in app Configuration, then restart the app.') from None


class NetworkTrial:
    def __init__(self, path, options, clock=time.time, probe_fn=probe, monotonic=time.monotonic):
        self.path, self.options, self.clock, self.probe = Path(path), options, clock, probe_fn
        self.lock = threading.RLock()
        self.current, self.sessions, self.samples = None, [], []
        self.error, self.busy, self.next_due = '', False, 0
        self.closed = False
        self.monotonic, self.stop_at = monotonic, None
        try:
            if self.path.stat().st_size > MAX_BYTES:
                raise ValueError()
            saved = json.loads(self.path.read_text())
            if saved.get('format') != 'adr-network-trial-v1':
                raise ValueError()
            self.sessions, self.samples = saved['sessions'], saved['samples']
            if not isinstance(self.sessions, list) or not isinstance(self.samples, list):
                raise ValueError()
            # Validate before rendering or scheduling anything from persisted data.
            for session in self.sessions:
                if not isinstance(session, dict) or not isinstance(session['id'], str):
                    raise ValueError()
                if not all(type(session[k]) in (int, float) and math.isfinite(session[k]) for k in ('started', 'deadline')):
                    raise ValueError()
                if not isinstance(session['targets'], list) or len(session['targets']) != 3:
                    raise ValueError()
                for target in session['targets']:
                    if not isinstance(target, list) or len(target) != 2 or not all(isinstance(x, str) for x in target):
                        raise ValueError()
            for sample in self.samples:
                if not isinstance(sample, dict) or not isinstance(sample['rows'], list) or len(sample['rows']) != 3:
                    raise ValueError()
                if type(sample['time']) not in (int, float) or not math.isfinite(sample['time']):
                    raise ValueError()
                if not isinstance(sample['session'], str) or not all(isinstance(row, dict) for row in sample['rows']):
                    raise ValueError()
            self._prune()
            self.current = self.sessions[-1] if self.sessions else None
            # Restarts never silently start/resume a trial or invent missing samples.
            if self.current and self.current.get('state') == 'running':
                self.current.update(state='interrupted', ended=clock())
                self._save()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.current, self.sessions, self.samples = None, [], []
            self.error = 'Saved network monitoring history could not be read. The existing file was left unchanged.'

    @property
    def running(self):
        with self.lock:
            return bool(self.current and self.current.get('state') == 'running')

    def _prune(self):
        cutoff = self.clock() - RETENTION
        self.samples = [s for s in self.samples if s['time'] >= cutoff][-MAX_SAMPLES:]
        self.sessions = [s for s in self.sessions if s['deadline'] >= cutoff][-32:]
        ids = {s['id'] for s in self.sessions}
        self.samples = [s for s in self.samples if s['session'] in ids]

    def _save(self):
        temp = self.path.with_suffix('.tmp')
        with temp.open('w', encoding='utf-8') as stream:
            os.chmod(temp, 0o600)
            json.dump({'format': 'adr-network-trial-v1', 'sessions': self.sessions, 'samples': self.samples}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self.path)

    def _persist(self):
        try:
            self._save()
        except OSError:
            if self.current:
                self.current.update(state='storage error', ended=self.clock())
            self.error = 'Network monitoring stopped because history could not be saved. Check app storage before starting again.'
            return False
        return True

    def start(self):
        with self.lock:
            if self.running:
                return False  # Duplicate Start never extends the deadline.
            if self.closed or self.busy:
                raise ValueError('Wait for the previous ping sample to finish.')
            if self.error:
                raise ValueError(self.error)
            address = gateway_address(self.options)
            now = self.clock()
            self._prune()
            self.current = {'id': secrets.token_hex(8), 'started': now, 'deadline': now + DURATION,
                            'state': 'running', 'targets': [['Gateway', address], ['Cloudflare', '1.1.1.1'], ['Google', '8.8.8.8']]}
            self.sessions.append(self.current)
            self.stop_at = self.monotonic() + DURATION
            self.next_due = now
            if not self._persist():
                raise ValueError(self.error)
            return True

    def stop(self, state='stopped'):
        with self.lock:
            if self.running:
                self.current.update(state=state, ended=min(self.clock(), self.current['deadline']))
                self._persist()

    def tick(self):
        with self.lock:
            if not self.running or self.closed:
                return
            now = self.clock()
            if now >= self.current['deadline'] or self.monotonic() >= self.stop_at:
                self.stop('completed')
                return
            if now < self.current['started']:
                self.stop('clock changed')
                return
            if self.busy or now < self.next_due:
                return
            session = copy.deepcopy(self.current)
            self.busy, self.next_due = True, now + INTERVAL
            try:
                threading.Thread(target=self._collect, args=(session,), daemon=True, name='adr-network-trial').start()
            except Exception:
                self.busy = False
                self.stop('failed')
                self.error = 'Network monitoring could not start its ping sample. No automatic retry was made.'

    def _collect(self, session):
        try:
            def read(target):
                try:
                    raw = self.probe(*target)
                    sent, received, latency = raw.get('sent'), raw.get('received'), raw.get('latency_ms')
                    valid = type(sent) is int and sent == 5 and type(received) is int and 0 <= received <= sent
                    latency = latency if valid and received and type(latency) in (int, float) and math.isfinite(latency) and 0 <= latency <= 10000 else None
                    return {'sent': sent if valid else None, 'received': received if valid else None,
                            'latency_ms': latency, 'loss_percent': round(100 * (sent-received)/sent, 1) if valid else None}
                except Exception:
                    return {'sent': None, 'received': None, 'latency_ms': None, 'loss_percent': None}
            with ThreadPoolExecutor(max_workers=3) as pool:
                rows = list(pool.map(read, session['targets']))
            with self.lock:
                now = self.clock()
                if self.running and self.current['id'] == session['id'] and not self.closed:
                    if now >= session['deadline'] or self.monotonic() >= self.stop_at:
                        self.stop('completed')
                    elif now < session['started']:
                        self.stop('clock changed')
                    else:
                        self.samples.append({'session': session['id'], 'time': now, 'rows': rows,
                                             'baseline': now < session['started'] + BASELINE})
                        self._prune()
                        self._persist()
        except Exception:
            with self.lock:
                self.stop('failed')
                self.error = 'Network monitoring stopped after a collection error. No automatic retry was made.'
        finally:
            with self.lock:
                self.busy = False

    def snapshot(self):
        with self.lock:
            cutoff = self.clock() - RETENTION
            current = copy.deepcopy(self.current)
            samples = [s for s in self.samples if s['time'] >= cutoff and current and s['session'] == current['id']]
            return {'current': current, 'samples': copy.deepcopy(samples), 'error': self.error,
                    'sessions': copy.deepcopy([s for s in self.sessions if s['deadline'] >= cutoff]),
                    'busy': self.busy, 'now': self.clock()}

    def close(self):
        with self.lock:
            self.stop('interrupted')
            self.closed = True
