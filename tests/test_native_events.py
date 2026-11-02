import copy
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1]/'adr_service_agent';sys.path.insert(0,str(ROOT))
from native_events import NativeEvents,sources,source_time,MAX_IMPORT
from wattbox import ProtocolError
from agent import Pilot,Handler,page

NOW=1800000000.
SOURCE={'host':'192.168.50.1','vendor':'unifi','name':'Gateway'}
CEF=b'<134>1 2027-01-15T08:00:00Z gateway unifi - - - CEF:0|Ubiquiti|UniFi Network|9.3|502|WAN Offline|7|rt=1800000000000 msg=WAN uplink lost reason=link'

class Native(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.path=self.root/'native.json';self.options={'native_events_enabled':True,'native_event_sources':[copy.deepcopy(SOURCE)],'password':'fake-private-secret'}
        self.time=NOW;self.n=NativeEvents(self.path,self.options,lambda:self.time)
    def test_sources_are_bounded_private_and_unique(self):
        for host in ('8.8.8.8','0.0.0.0','example.com','192.168.50.1/24','::1'):
            with self.subTest(host=host),self.assertRaises(ProtocolError):sources({'native_event_sources':[dict(SOURCE,host=host)]})
        with self.assertRaises(ProtocolError):sources({'native_event_sources':[SOURCE,SOURCE]})
        with self.assertRaises(ProtocolError):sources({'native_event_sources':[SOURCE]*17})
        with self.assertRaises(ProtocolError):sources({'native_event_sources':[dict(SOURCE,vendor='other')]})

    def test_disabled_binds_nothing_and_rejects_messages(self):
        n=NativeEvents(self.path,dict(self.options,native_events_enabled=False));factory=Mock();n.start(factory)
        factory.assert_not_called();self.assertFalse(n.accept(CEF,SOURCE['host']))
        self.assertEqual(n.status()['events'],[])

    def test_cef_preserves_device_time_and_receipt_time_separately(self):
        self.n.accept(CEF,SOURCE['host']);e=self.n.status()['events'][0]
        self.assertEqual(e['event_name'],'WAN Offline');self.assertEqual(e['event_code'],'502')
        self.assertEqual(e['message'],'WAN uplink lost');self.assertEqual(e['severity'],'cef:7')
        self.assertEqual(e['device_time'],'2027-01-15T08:00:00+00:00');self.assertEqual(e['received_at'],e['device_time'])
        self.time+=3600;self.n.accept(CEF.replace(b'502',b'503'),SOURCE['host'])
        self.assertNotEqual(self.n.status()['events'][-1]['device_time'],self.n.status()['events'][-1]['received_at'])

    def test_ruckus_legacy_timestamp_does_not_invent_year_or_timezone(self):
        options=dict(self.options,native_event_sources=[dict(SOURCE,vendor='ruckus')]);n=NativeEvents(self.path,options,lambda:self.time)
        n.accept(b'<132>Nov  2 15:00:00 master AP disconnected',SOURCE['host']);e=n.status()['events'][0]
        self.assertEqual(e['vendor'],'ruckus');self.assertIsNone(e['device_time']);self.assertEqual(e['time_basis'],'receipt_only')
        self.assertIn('Nov',e['device_time_text'])

    def test_unallowlisted_and_oversized_or_control_data_are_rejected(self):
        self.assertFalse(self.n.accept(CEF,'192.168.50.2'))
        self.assertFalse(self.n.accept(b'x'*8193,SOURCE['host']))
        self.assertFalse(self.n.accept(b'<999>bad',SOURCE['host']))
        self.assertFalse(self.n.accept(b'\x00bad',SOURCE['host']))
        self.assertEqual(self.n.status()['events'],[])

    def test_credential_redaction_before_storage_and_import(self):
        self.n.accept(b'<134>log password=another-secret token=private api_key=private fake-private-secret {"password":"json-secret"} Bearer bearer-secret',SOURCE['host'])
        self.n.flush(force=True);text=self.path.read_text()
        for private in ('another-secret','fake-private-secret','token=private','api_key=private','json-secret','bearer-secret'):self.assertNotIn(private,text)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_historical_import_is_labeled_and_does_not_prove_live_feed(self):
        source_id=self.n.status()['sources'][0]['id'];count=self.n.import_text(source_id,'2026-11-02T12:00:00-05:00 Input power lost')
        self.assertEqual(count,1);s=self.n.status();e=s['events'][0]
        self.assertEqual(e['transport'],'file');self.assertEqual(e['authenticity'],'user_supplied_export')
        self.assertEqual(e['device_time'],'2026-11-02T17:00:00+00:00');self.assertNotEqual(e['received_at'],e['device_time'])
        self.assertEqual(s['sources'][0]['coverage'],'delivery_unverified')
        self.assertIsNone(s['sources'][0]['last_received_at'])

    def test_import_validates_entire_batch_before_mutating(self):
        source=self.n.status()['sources'][0]['id']
        for text in ('x\n'+'z'*8193,'x\n'*257,'x'*(MAX_IMPORT+1)):
            with self.assertRaises(ProtocolError):self.n.import_text(source,text)
        self.assertEqual(self.n.status()['events'],[])

    def test_repeat_timestamped_event_deduplicated_but_untimed_messages_preserved(self):
        self.n.accept(CEF,SOURCE['host']);self.n.accept(CEF,SOURCE['host'])
        self.assertEqual(len(self.n.status()['events']),1);self.assertEqual(self.n.status()['duplicates'],1)
        self.n.accept(b'same untimed report',SOURCE['host']);self.n.accept(b'same untimed report',SOURCE['host'])
        self.assertEqual(len(self.n.status()['events']),3)

    def test_rate_and_retention_bounds(self):
        for i in range(25):self.n.accept(('message '+str(i)).encode(),SOURCE['host'])
        self.assertEqual(len(self.n.status()['events']),20);self.assertEqual(self.n.status()['rate_dropped'],5)
        for i in range(1005):self.time+=1.1;self.n.accept(('message '+str(i)).encode(),SOURCE['host'])
        self.assertEqual(len(self.n.events),1000)
        self.time+=31*86400;self.assertEqual(self.n.status()['events'],[])

    def test_persistence_restart_and_target_config_changes(self):
        self.n.accept(CEF,SOURCE['host']);self.n.flush(force=True)
        n=NativeEvents(self.path,self.options,lambda:self.time)
        self.assertEqual(len(n.status()['events']),1);self.assertEqual(n.status()['sources'][0]['coverage'],'delivery_unverified')
        n=NativeEvents(self.path,dict(self.options,native_event_sources=[dict(SOURCE,name='Different client')]),lambda:self.time)
        self.assertEqual(n.status()['events'],[])
        self.path.write_text('broken secret');n=NativeEvents(self.path,self.options)
        self.assertTrue(n.error);self.assertEqual(self.path.read_text(),'broken secret')
        self.assertFalse(n.accept(CEF,SOURCE['host']))

    def test_future_or_timezone_free_device_time_remains_unverified(self):
        for raw in ('2099-01-01T00:00:00Z','2026-11-02T12:00:00','Nov 2 12:00:00','bad'):
            self.assertIsNone(source_time(raw,NOW))

    def test_unicode_history_stays_within_serialized_storage_limit(self):
        for i in range(500):
            self.time+=1.1;self.n.accept(('message '+str(i)+' '+'\U0001f642'*800).encode(),SOURCE['host'])
        self.n.flush(force=True)
        self.assertLessEqual(self.path.stat().st_size,2*1024*1024)
        self.assertFalse(NativeEvents(self.path,self.options,lambda:self.time).error)

    def test_listener_one_thread_no_public_socket_when_disabled_and_clean_close(self):
        sock=Mock();thread=Mock();self.n.start(Mock(return_value=sock),Mock(return_value=thread))
        sock.bind.assert_called_once_with(('0.0.0.0',5514));thread.start.assert_called_once();self.assertTrue(self.n.running)
        self.n.start();thread.start.assert_called_once();self.n.close();sock.close.assert_called_once();thread.join.assert_called_once()
        failed=NativeEvents(self.root/'other',self.options)
        failed.start(Mock(side_effect=OSError('raw secret')));self.assertNotIn('raw secret',failed.status()['error'])

class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.options={'native_events_enabled':True,'native_event_sources':[SOURCE],'automatic_monitoring':False}
        (self.root/'options.json').write_text(json.dumps(self.options));self.pilot=Pilot(self.root,self.root/'options.json')
    def handler(self,csrf=True,peer='172.30.32.2'):
        h=Handler.__new__(Handler);h.server=SimpleNamespace(pilot=self.pilot);h.client_address=(peer,1);h.path='/native-import'
        body=b'2026-11-02T12:00:00-05:00 UPS reports input lost';h.rfile=io.BytesIO(body);h.wfile=io.BytesIO();h.connection=Mock()
        h.headers={'Content-Length':str(len(body)),'Content-Type':'text/plain','X-ADR-CSRF':self.pilot.csrf if csrf else 'bad',
            'X-ADR-Event-Source':self.pilot.native_events.status()['sources'][0]['id']}
        h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();h.send_error=Mock();return h
    def test_import_requires_peer_and_csrf_before_body_read(self):
        h=self.handler(peer='127.0.0.1');h.do_POST();h.send_error.assert_called_once();self.assertEqual(h.rfile.tell(),0)
        h=self.handler(csrf=False);h.do_POST();h.send_response.assert_called_with(403);self.assertEqual(h.rfile.tell(),0)
        h=self.handler();h.do_POST();h.send_response.assert_called_with(200);self.assertEqual(json.loads(h.wfile.getvalue())['imported'],1)
    def test_native_events_reach_ask_without_running_device_commands(self):
        self.pilot.native_events.accept(CEF,SOURCE['host'])
        out=self.pilot.diagnostics.report_provider();self.assertEqual(out['native_events']['events'][0]['event_name'],'WAN Offline')
        self.assertIn('native_events',out);self.assertNotIn('health_status',out['native_events']);self.assertIn('No events received',out['native_events']['limitations'][0])
        self.assertIn('id="adr-native-history"',page(self.pilot))
    def test_bind_and_import_are_explicit_no_source_reconfiguration(self):
        self.assertFalse(self.pilot.native_events.running)
        h=self.handler();h.headers['X-ADR-Event-Source']='not-configured';h.do_POST();self.assertEqual(self.pilot.native_events.events,[])
        h=self.handler();h.headers['Content-Length']=str(MAX_IMPORT+1);h.do_POST();self.assertEqual(h.rfile.tell(),0)
