"""Read-only discovery identity evidence. No vendor guesses from names or open ports."""
import ipaddress
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from unifi_gateway import settings, request, GatewayError


def text(value):
    return ''.join(c for c in value if c.isprintable())[:100] if isinstance(value, str) else ''


def mac(value):
    value = str(value or '').lower().replace('-', ':')
    if not re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', value):
        return ''
    if int(value[:2], 16) & 1 or value == '00:00:00:00:00:00':
        return ''
    return value


def arp_table(path=Path('/proc/net/arp')):
    """Exact IP matches only; never substitute a routed next-hop MAC."""
    rows = {}
    try:
        for line in path.read_text()[:262144].splitlines()[1:]:
            fields = line.split()
            if len(fields) != 6 or int(fields[2], 16) & 2 == 0:
                continue
            ip = str(ipaddress.IPv4Address(fields[0]))
            address = mac(fields[3])
            if address:
                rows[ip] = address
    except (OSError, ValueError):
        pass
    return rows


def hostname(ip, runner=subprocess.run):
    # Bound each reverse lookup in a child; DNS failure cannot hang a worker.
    code = 'import json,socket,sys;print(json.dumps(socket.gethostbyaddr(sys.argv[1])[0]))'
    try:
        result = runner([sys.executable, '-c', code, ip], capture_output=True, text=True, timeout=2)
        name = json.loads(result.stdout[:4096]) if result.returncode == 0 else None
        return text(name)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ''


def controller_records(options, pin, request_fn=request):
    records, notes = [], []
    if not options.get('unifi_gateway_host') or not options.get('unifi_gateway_api_key'):
        return records, ['Gateway device records are not configured; manufacturer may be unavailable.']
    try:
        config = settings(options)
        # Existing request validates the pinned certificate before sending the key.
        for operation in ('clients', 'devices'):
            body = request_fn(config, pin, 'GET', operation)
            raw_rows = body.get('data')
            if body.get('meta', {}).get('rc') != 'ok' or not isinstance(raw_rows, list) or len(raw_rows) > 2048:
                raise ValueError()
            for raw in raw_rows:
                if not isinstance(raw, dict):
                    continue
                try:
                    ip = str(ipaddress.IPv4Address(raw.get('ip')))
                except (ValueError, TypeError, ipaddress.AddressValueError):
                    continue
                address = mac(raw.get('mac'))
                if not address:
                    continue
                managed = operation == 'devices' and raw.get('type') in ('usw','uap','ugw','udm','uxg')
                manufacturer = 'Ubiquiti' if managed else text(raw.get('device_vendor'))
                # OUI does not identify a manufacturer for a locally assigned MAC.
                if not manufacturer and not int(address[:2], 16) & 2:
                    manufacturer = text(raw.get('oui'))
                record = {'ip':ip, 'mac':address, 'hostname':text(raw.get('hostname')),
                          'name':text(raw.get('name')), 'manufacturer':manufacturer,
                          'device_type':{'usw':'Switch','uap':'Access point','ugw':'Gateway','udm':'Gateway','uxg':'Gateway'}.get(raw.get('type'), '') if managed else text(raw.get('device_type')),
                          'identity_source':'UniFi managed device' if managed else 'UniFi client record'}
                records.append(record)
    except GatewayError:
        notes.append('Gateway identity read unavailable. Check the existing gateway connection and approved certificate; no login fallback or retry was made.')
    except (ValueError, TypeError, AttributeError):
        notes.append('Gateway identity response could not be verified; available local evidence is shown.')
    return records, notes


def identify(ips, options, pin, records_fn=controller_records, arp_fn=arp_table, resolve_fn=hostname):
    # Called only after the explicitly scoped ICMP discovery, for responding IPs.
    records, notes = records_fn(options, pin)
    neighbors = arp_fn()
    devices = []
    def one(ip):
        rows = [r for r in records if r.get('ip') == ip]
        addresses = {r['mac'] for r in rows}
        if neighbors.get(ip):
            addresses.add(neighbors[ip])
        device = {'ip':ip, 'mac':'', 'hostname':'', 'name':'', 'manufacturer':'', 'device_type':'', 'identity_source':''}
        if len(addresses) > 1:
            device['identity_source'] = 'Conflicting MAC records; identity unverified'
            return device
        if rows:
            # Merge only records that agree on the exact IP and MAC.
            for row in sorted(rows, key=lambda r:r.get('identity_source') == 'UniFi managed device'):
                for key in ('mac','hostname','name','manufacturer','device_type','identity_source'):
                    if row.get(key):
                        device[key] = text(row[key])
        elif addresses:
            device.update(mac=next(iter(addresses)), identity_source='Local ARP table')
        if not device['hostname']:
            device['hostname'] = resolve_fn(ip)
            if device['hostname']:
                device['identity_source'] += ('; ' if device['identity_source'] else '')+'Reverse DNS'
        return device
    with ThreadPoolExecutor(max_workers=4) as pool:
        devices = list(pool.map(one, ips[:254]))
    notes += ['Identity fields are reported records, not a device login or functional check.',
              'Routed devices may have no local MAC record. Randomized MACs and missing gateway records may leave the manufacturer unknown.']
    return devices, notes
