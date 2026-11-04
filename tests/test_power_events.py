import copy
import io
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]/'adr_service_agent';sys.path.insert(0,str(ROOT))
from wattbox import WattBox,parse_ups,ProtocolError
from power_events import PowerJournal
from agent import Pilot,Handler
from diagnostic_hub import evidence
from service_report import assess
from test_pilot import inventory,FakeBox

TARGET='a'*64

def sample(lost=False):return parse_ups('1','87.5,25,Good,'+str(lost)+',18,True,False')

class Protocol(unittest.TestCase):
    def test_documented_fields_are_strictly_parsed(self):
        s=sample(True);self.assertTrue(s['power_lost']);self.assertEqual(s['charge_percent'],87.5)
        self.assertEqual(s['runtime_minutes'],18);self.assertEqual(parse_ups('0'),{'connected':False})
        for connected,text in [('x',''),('1','90,10,Good,0,12,True,False'),('1','101,10,Good,True,12,True,False'),
            ('1','90,NaN,Good,True,12,True,False'),('1','90,10,Unknown,True,12,True,False'),
            ('1','90,10,Good,True,-1,True,False'),('1','90,10,Good,True,12,True,False,extra')]:
            with self.subTest(text=text),self.assertRaises(ProtocolError):parse_ups(connected,text)

    def test_no_ups_status_query_without_confirmed_connection(self):
        box=WattBox({},'unused');box.query=Mock(return_value='0')
        self.assertEqual(box.ups_status(),{'connected':False});box.query.assert_called_once_with('UPSConnection')
        box.query=Mock(return_value='unknown')
        with self.assertRaises(ProtocolError):box.ups_status()
        box.query.assert_called_once_with('UPSConnection')
        box.query=Mock(side_effect=['1','90,20,Good,True,12,True,False'])
        self.assertTrue(box.ups_status()['power_lost']);self.assertEqual(box.query.call_count,2)

    def test_fresh_response_required_unsolicited_never_satisfies_query(self):
        box=WattBox({},'unused');box.channel=Mock()
        box._line=Mock(side_effect=['~UPSStatus=90,20,Good,True,12,True,False','?UPSStatus=90,20,Good,False,12,True,False'])
        self.assertIn('Good,False',box.query('UPSStatus'));box.channel.sendall.assert_called_once_with(b'?UPSStatus\n')
        with self.assertRaises(ValueError):box.query('UPSShutdown')

class Journal(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'power.json'
        self.time=1800000000.;self.journal=PowerJournal(self.path,15,lambda:self.time)
    def tick(self,seconds=15):self.time+=seconds

    def test_baseline_is_not_an_outage_and_transitions_have_observation_bounds(self):
        self.journal.observe(TARGET,sample());self.tick();self.journal.observe(TARGET,sample(True))
        self.tick();self.journal.observe(TARGET,sample())
        s=self.journal.status(TARGET);self.assertEqual([e['kind'] for e in s['events']],['initial_observation','input_power_lost','input_power_restored'])
        self.assertIn('previous_observation_at',s['events'][-1]);self.assertNotIn('outage_duration',s['events'][-1])
        self.assertEqual(s['current']['power_lost'],False)
        for _ in range(4):self.tick();self.journal.observe(TARGET,sample())
        self.assertEqual(len(self.journal.status(TARGET)['events']),3)

    def test_errors_and_busy_gaps_do_not_fabricate_restoration(self):
        self.journal.observe(TARGET,sample(True));self.tick();self.journal.unverified()
        self.assertIsNone(self.journal.status(TARGET)['current'])
        self.tick();self.journal.observe(TARGET,sample())
        self.assertEqual(self.journal.status(TARGET)['events'][-1]['kind'],'observation_after_gap')
        self.tick(90);self.journal.observe(TARGET,sample(True))
        self.assertEqual(self.journal.status(TARGET)['events'][-1]['kind'],'observation_after_gap')

    def test_restart_restores_history_but_requires_fresh_read(self):
        self.journal.observe(TARGET,sample(True));self.tick()
        new=PowerJournal(self.path,15,lambda:self.time);self.assertEqual(new.status(TARGET)['status'],'unverified')
        new.observe(TARGET,sample());self.assertEqual(new.status(TARGET)['events'][-1]['kind'],'observation_after_gap')
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)

    def test_target_change_never_attributes_previous_history(self):
        self.journal.observe(TARGET,sample(True));self.assertEqual(self.journal.status('b'*64)['events'],[])
        self.tick();self.journal.observe('b'*64,sample())
        self.assertEqual(len(self.journal.status('b'*64)['events']),1)
        self.assertEqual(self.journal.status('b'*64)['events'][0]['kind'],'initial_observation')

    def test_stale_and_future_samples_are_unverified(self):
        self.journal.observe(TARGET,sample());self.tick(46)
        self.assertIsNone(self.journal.status(TARGET)['current'])
        self.time-=60;self.assertIsNone(self.journal.status(TARGET)['current'])

    def test_corruption_is_not_deleted_or_exposed(self):
        self.path.write_text('{private broken data');raw=self.path.read_bytes()
        new=PowerJournal(self.path);self.assertTrue(new.error);self.assertNotIn('private',new.error)
        with self.assertRaises(ProtocolError):new.observe(TARGET,sample())
        self.assertEqual(self.path.read_bytes(),raw)

    def test_extra_saved_fields_are_rejected_and_no_raw_console_in_history(self):
        self.journal.observe(TARGET,sample());data=json.loads(self.path.read_text())
        self.assertNotIn('response',self.path.read_text());data['events'][0]['password']='fake-private'
        self.path.write_text(json.dumps(data));self.assertTrue(PowerJournal(self.path).error)

    def test_periodic_samples_do_not_write_every_poll_and_history_is_capped(self):
        with patch('power_events.save') as save:
            self.journal.observe(TARGET,sample());save.assert_called_once()
            for _ in range(10):self.tick();self.journal.observe(TARGET,sample())
            self.assertEqual(save.call_count,1)
            self.tick(151);self.journal.observe(TARGET,sample());self.assertEqual(save.call_count,2)
        for i in range(530):self.tick();self.journal.observe(TARGET,sample(i%2==0))
        self.assertLessEqual(len(self.journal.data['events']),512)
        self.tick(31*86400);self.journal.observe(TARGET,sample());self.assertEqual(len(self.journal.status(TARGET)['events']),1)

class Wiring(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.options={'wattbox_host':'192.0.2.10','ssh_port':22,'username':'test','password':'fake-secret',
            'test_outlet':11,'expected_outlet_label':'Apple TV','device_alias':'Apple TV','allow_reboot':False,
            'ups_monitoring_enabled':True,'automatic_monitoring':False}
        (self.root/'options.json').write_text(json.dumps(self.options));(self.root/'known_hosts').touch()
        self.box=FakeBox(inventory());self.box.ups_status=Mock(return_value=sample())
        self.pilot=Pilot(self.root,self.root/'options.json',lambda *_:self.box)

    def test_metering_success_and_failure_preserve_outlets_and_history_gaps(self):
        self.box.power_status=Mock(return_value={'current_a':1.25,'power_w':140.0,'voltage_v':119.5,'safe_voltage':True})
        snapshot=self.pilot.service_jobs()[0][2]()
        self.assertEqual(snapshot['electrical']['voltage_v'],119.5)
        self.assertEqual(snapshot['electrical_history']['series'][1]['points'][-1]['value'],1.25)
        self.assertNotIn('power_history',snapshot)
        self.box.power_status.side_effect=ProtocolError('private device response')
        snapshot=self.pilot.service_jobs()[0][2]()
        self.assertEqual(len(snapshot['outlets']),18)
        self.assertNotIn('electrical',snapshot)
        self.assertIsNone(snapshot['electrical_history']['series'][0]['points'][-1]['value'])
        self.assertNotIn('private device response',json.dumps(snapshot))
        self.assertEqual(self.box.resets,[])

    def test_existing_inventory_is_kept_even_if_ups_read_fails(self):
        self.box.ups_status.side_effect=ProtocolError('raw private response')
        snapshot=self.pilot.service_jobs()[0][2]()
        self.assertEqual(len(snapshot['outlets']),18);self.assertEqual(snapshot['power_monitor']['status'],'unverified')
        self.assertNotIn('raw private',json.dumps(snapshot));self.assertEqual(self.box.resets,[])

    def test_status_only_added_when_enabled_and_startup_sends_nothing(self):
        self.box.ups_status.assert_not_called();self.assertEqual(self.pilot.power_status()['status'],'unverified')
        self.pilot.service_jobs()[0][2]();self.box.ups_status.assert_called_once()
        self.pilot.araknis_options['ups_monitoring_enabled']=False
        snapshot=self.pilot.service_jobs()[0][2]();self.assertNotIn('power_monitor',snapshot)

    def test_fast_poll_shares_monitor_loop_without_cloud_or_power_actions(self):
        self.pilot.ups_next_due=0
        with patch('agent.threading.Thread') as thread:
            self.pilot.power_tick();run=thread.call_args.kwargs['target']
        self.assertTrue(self.pilot.ups_running);self.assertTrue(self.pilot.monitoring_busy())
        run();self.assertFalse(self.pilot.ups_running);self.box.ups_status.assert_called_once()
        self.assertEqual(self.box.resets,[]);self.assertEqual(self.pilot.power_status()['status'],'verified')
        self.assertEqual(self.pilot.monitor.enabled,False)

    def test_busy_and_confirmation_defer_poll_and_failure_backs_off(self):
        self.pilot.ups_next_due=0;self.pilot.power_pending={'created':time.monotonic()}
        with patch('agent.threading.Thread') as thread:self.pilot.power_tick();thread.assert_not_called()
        self.pilot.power_pending=None;self.box.ups_status.side_effect=TimeoutError('secret')
        with patch('agent.threading.Thread') as thread:
            self.pilot.power_tick();run=thread.call_args.kwargs['target']
        run();self.assertGreater(self.pilot.ups_next_due,time.time()+290)
        self.assertEqual(self.pilot.power_status()['status'],'unverified')

    def test_fresh_power_events_in_ask_evidence_without_a_service_report(self):
        self.pilot.read_ups(self.box)
        sent=self.pilot.diagnostics.report_provider();self.assertIn('power_monitor',sent)
        self.assertEqual(sent['power_monitor']['current']['charge_percent'],87.5)
        self.assertNotIn('fake-secret',json.dumps(sent));self.assertEqual(len(sent['power_monitor']['events']),1)

    def test_service_findings_do_not_call_unknown_an_outage(self):
        result=assess('WattBox',{'outlets':[],'power_monitor':{'enabled':True,'status':'unverified','current':None}})
        self.assertEqual(result['service_findings'],[]);self.assertIn('UPS status is unverified',result['data_limitations'][0])
        result=assess('WattBox',{'outlets':[],'power_monitor':{'enabled':True,'status':'verified','current':sample(True)}})
        self.assertEqual(result['status'],'review');self.assertIn('input power lost',result['service_findings'][0])
