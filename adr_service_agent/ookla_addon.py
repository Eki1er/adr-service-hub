"""Read the known SpeedTest add-on's three fixed sensors; start only that app.

The add-on runs Ookla and posts server metadata plus transfer-byte attributes.
Do not call update_entity: that invokes Home Assistant's Python test instead.
"""
import json
import math
import time
from datetime import datetime, timezone
from urllib.request import Request, build_opener, ProxyHandler

from crestron import get_json
from ruckus import token_value
from sonos import NoRedirect
from internet_test import InternetTestError

ADDON = '6b87c29e_speedtest_addon'
KINDS = ('download', 'upload', 'ping')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read(token=None, get_fn=get_json):
    token = token if token is not None else token_value()
    result = {'source':'green', 'provider':'ookla_addon', 'readings':[],
              'completed_at':stamp(), 'fresh_values_confirmed':False, 'quality_issues':[],
              'limitations':['Measured by the Ookla add-on on Green, not the phone or Wi-Fi clients.',
                  'Disable automatic polling on the original Speedtest.net integration to prevent competing test results.']}
    try:
        rows, times, servers = [], [], []
        for kind in KINDS:
            entity = 'sensor.speedtest_'+kind
            raw = get_fn(token, 'states/'+entity)
            if not isinstance(raw,dict) or raw.get('entity_id') != entity:
                raise ValueError()
            attrs = raw.get('attributes',{})
            unit = 'ms' if kind == 'ping' else 'Mbit/s'
            if attrs.get('unit_of_measurement') != unit or isinstance(raw.get('state'),bool):
                raise ValueError()
            value = float(raw['state'])
            if not math.isfinite(value) or value < 0 or value == 1800000:
                raise ValueError()
            server = str(attrs.get('server_id',''))
            if not server.isdigit() or len(server)>10 or not isinstance(attrs.get('server_name'),str):
                raise ValueError()
            # Known add-on fields: never attribute plain built-in sensor states to Ookla.
            if kind != 'ping':
                count = attrs.get('bytes_received' if kind=='download' else 'bytes_sent')
                if type(count) is not int or count <= 0:
                    raise ValueError()
            date = datetime.fromisoformat(raw['last_updated'].replace('Z','+00:00'))
            if date.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-date).total_seconds() <= 86400:
                raise ValueError()
            times.append(date); servers.append(server)
            rows.append({'name':'Green Ookla speed test '+kind.capitalize(), 'entity_id':entity,
                         'state':value, 'unit':unit, 'last_updated':date.isoformat()})
        if len(set(servers)) != 1 or (max(times)-min(times)).total_seconds()>15:
            raise ValueError()
        result.update(readings=rows, completed_at=max(times).isoformat(),
                      server_id=servers[0], fresh_values_confirmed=True)
    except Exception:
        result['limitations'].append('Matching recent Ookla add-on results were not confirmed. Check the add-on log and Speedtest.net polling settings. No other speed-test source was substituted.')
    return result


def start(token):
    request = Request('http://supervisor/core/api/services/hassio/addon_start',
                      data=json.dumps({'addon':ADDON}).encode(), method='POST',
                      headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    try:
        with build_opener(ProxyHandler({}),NoRedirect()).open(request,timeout=15) as response:
            if response.status != 200 or len(response.read(65537))>65536:
                raise ValueError()
    except Exception:
        raise InternetTestError('The Ookla add-on start request was not confirmed. It may still be running; check its log. No retry was made.') from None


def run(token=None, get_fn=get_json, start_fn=start, clock=time.monotonic, wait=time.sleep):
    token = token if token is not None else token_value()
    before = read(token,get_fn)
    previous = {r['entity_id']:r['last_updated'] for r in before['readings']}
    started = datetime.now(timezone.utc)
    start_fn(token)  # One fixed app start, never an app restart or update_entity.
    deadline = clock()+150
    while clock()<deadline:
        result = read(token,get_fn)
        if result['fresh_values_confirmed'] and all(
            datetime.fromisoformat(r['last_updated'])>=started and
            r['last_updated']!=previous.get(r['entity_id']) for r in result['readings']):
            result['started_at']=started.isoformat()
            return result
        wait(3)
    raise InternetTestError('No new complete Ookla result was confirmed within 150 seconds. The add-on may still be running; check its log. No retry or substitute test was made.')
