"""Router-independent, bounded measurements made by the local service host.

No controller credentials, HA speed-test sensors, shell, or arbitrary URLs.
Throughput is application goodput to Cloudflare, not an ISP plan measurement.
"""
import http.client
import os
import re
import ssl
import subprocess
import time
from datetime import datetime, timezone

HOST = 'speed.cloudflare.com'
TARGETS = (('Cloudflare', '1.1.1.1'), ('Google', '8.8.8.8'))
LIMITATIONS = [
    'Measured from the Green/service host, not from the phone or Wi-Fi clients.',
    'Short ICMP samples can be filtered or deprioritized; missed replies do not establish an ISP fault.',
]
SPEED_LIMITATIONS = [
    'HTTPS throughput to Cloudflare includes protocol overhead and may be limited by the Green, its Ethernet link, other traffic or the test endpoint.',
    'This bounded test is not Ookla Speedtest and does not establish subscribed ISP speed or maximum line capacity.',
]


def stamp():
    return datetime.now(timezone.utc).isoformat()


def probe(label, address, runner=subprocess.run):
    row = {'name': label, 'address': address, 'sent': None, 'received': None,
           'loss_percent': None, 'latency_ms': None, 'status': 'unverified'}
    try:
        result = runner(['ping', '-n', '-c', '5', '-W', '1', '-w', '7', address],
                        capture_output=True, text=True, timeout=9,
                        env=dict(os.environ, LC_ALL='C'))
        counts = re.search(r'(\d+) packets transmitted, (\d+) (?:packets )?received', result.stdout)
        if not counts or result.returncode not in (0, 1):
            return row
        sent, received = map(int, counts.groups())
        if sent != 5 or not 0 <= received <= sent:
            return row
        row.update(sent=sent, received=received, loss_percent=round(100*(sent-received)/sent, 1),
                   status='responded' if received else 'no replies')
        latency = re.search(r'(?:rtt|round-trip)[^=]*= [\d.]+/([\d.]+)/', result.stdout)
        if latency and received:
            row['latency_ms'] = float(latency.group(1))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return row


def collect(speed=None, runner=subprocess.run, clock=time.monotonic):
    started = stamp()
    probes = [probe(label, address, runner) for label, address in TARGETS]
    dns = {'name': 'example.com', 'status': 'unverified', 'elapsed_ms': None}
    before = clock()
    try:
        result = runner(['getent', 'ahostsv4', 'example.com'], capture_output=True,
                        text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            dns.update(status='resolved', elapsed_ms=round((clock()-before)*1000, 1))
    except (OSError, subprocess.SubprocessError):
        pass
    completed = stamp()
    readings = []
    for row in probes:
        if row['latency_ms'] is not None:
            readings.append({'name': 'Green '+row['name']+' latency', 'state': row['latency_ms'],
                             'unit': 'ms', 'last_updated': completed})
    # A speed sample retains its own timestamp, never the current probe time.
    if isinstance(speed, dict) and speed.get('source') == 'green':
        for reading in speed.get('readings', [])[:2]:
            try:
                age = (datetime.now(timezone.utc)-datetime.fromisoformat(reading['last_updated'])).total_seconds()
                if 0 <= age <= 86400:
                    readings.append(dict(reading))
            except (ValueError, TypeError, KeyError):
                pass
    return {'source': 'green', 'started_at': started, 'checked_at': completed,
            'probes': probes, 'dns': dns, 'readings': readings,
            'speed_test': speed, 'limitations': LIMITATIONS +
            ['DNS uses the host resolver; its answer may be cached.']}


def transfer(connection, direction, size, deadline, clock=time.monotonic):
    """Count only complete, successful transfers, with a shared total deadline."""
    remaining = deadline-clock()
    if remaining <= 0:
        raise TimeoutError()
    connection.timeout = min(10, remaining)
    if connection.sock:
        connection.sock.settimeout(connection.timeout)
    started = clock()
    if direction == 'download':
        connection.request('GET', '/__down?bytes='+str(size),
                           headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-store'})
    else:
        connection.request('POST', '/__up', body=b'0'*size,
                           headers={'Content-Type': 'application/octet-stream', 'Accept-Encoding': 'identity'})
    response = connection.getresponse()
    if response.status != 200:
        raise ValueError('Unexpected response')
    total, limit = 0, size if direction == 'download' else 65536
    while True:
        if clock() >= deadline:
            raise TimeoutError()
        chunk = response.read1(min(262144, limit-total+1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError('Oversized response')
    elapsed = clock()-started
    if direction == 'download' and total != size:
        raise ValueError('Incomplete transfer')
    if elapsed <= 0:
        raise ValueError('Invalid duration')
    return round(size*8/elapsed/1_000_000, 2), elapsed


def run(connection_factory=http.client.HTTPSConnection, clock=time.monotonic):
    """One bounded test, no retries; each failed direction stays unverified.

    Max payload: 181 MB down + 86 MB up, including warmups. At most 90s
    plus one bounded socket operation. No redirects or system proxies.
    """
    started, deadline = stamp(), clock()+90
    readings, errors, bytes_sent = [], [], 0
    for direction, sizes in (('download', (1_000_000, 5_000_000, 25_000_000, 50_000_000, 100_000_000)),
                             ('upload', (1_000_000, 5_000_000, 10_000_000, 20_000_000, 50_000_000))):
        connection = connection_factory(HOST, 443, context=ssl.create_default_context(), timeout=10)
        measured = None
        try:
            for index, size in enumerate(sizes):
                rate, elapsed = transfer(connection, direction, size, deadline, clock)
                bytes_sent += size
                if index:  # Warmup cannot be presented as a throughput result.
                    measured = rate
                    if elapsed >= 2:
                        break
        except Exception:
            measured = None
            errors.append('Green '+direction+' throughput was not confirmed. No retry was made.')
        finally:
            connection.close()
        if measured is not None:
            readings.append({'name': 'Green speed test '+direction.capitalize(), 'state': measured,
                             'unit': 'Mbit/s', 'last_updated': stamp()})
    return {'source': 'green', 'endpoint': HOST, 'started_at': started, 'completed_at': stamp(),
            'readings': readings, 'fresh_values_confirmed': len(readings) == 2,
            'quality_issues': [], 'payload_bytes_completed': bytes_sent,
            'limitations': SPEED_LIMITATIONS + errors}
