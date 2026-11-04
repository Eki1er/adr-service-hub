import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
from usb_ups import USBUPS, operating, iso
from diagnostic_hub import evidence
from agent import Pilot

OPTIONS={'nut_status_entity':'sensor.test_ups_status_data','nut_ups_name':'test_ups'}

class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'events.json';self.now=time.time()
        self.j=USBUPS(self.path,OPTIONS,clock=lambda:self.now)
    def test_baseline_outage_restore_dedup_and_reload(self):
        self.j.sensor('OL',iso(self.now),baseline=True)
        self.now+=2;self.j.notification({'ups_name':'test_ups','notify_type':'ONBATT'},iso(self.now))
        self.j.sensor('OB',iso(self.now))
        self.now+=3;self.j.notification({'ups_name':'test_ups','notify_type':'ONLINE'},iso(self.now))
        self.j.sensor('OL',iso(self.now))
        self.assertEqual([e['kind'] for e in self.j.events],['baseline','on_battery','line_power_restored'])
        restored=USBUPS(self.path,OPTIONS,clock=lambda:self.now)
        self.assertEqual(restored.events,self.j.events)
        self.assertEqual(restored.status()['current'],'unverified')
    def test_gap_is_not_restoration(self):
        self.j.sensor('OB');self.j.disconnected();self.j.sensor('OL',baseline=True)
        self.assertEqual([e['kind'] for e in self.j.events],['baseline','monitoring_gap','baseline'])
    def test_native_reports_during_gap_retained_without_invented_duration(self):
        self.j.notification({'ups_name':'test_ups','notify_type':'ONLINE'},iso(self.now))
        self.assertEqual(self.j.events[-1]['source'],'nut_notification')
        self.assertNotIn('duration',self.j.events[-1])
    def test_unrelated_and_unknown_notifications_ignored(self):
        for ups,kind in [('other','ONBATT'),('test_ups','invented')]:
            self.j.notification({'ups_name':ups,'notify_type':kind,'notify_msg':'secret'})
        self.assertEqual(self.j.events,[])
    def test_late_state_cannot_erase_new_native_outage(self):
        self.j.sensor('OL',iso(self.now-10));self.j.notification({'ups_name':'test_ups','notify_type':'ONBATT'},iso(self.now))
        self.j.sensor('OL',iso(self.now-5));self.assertEqual(self.j.current,'on_battery')
    def test_unknown_conflicting_and_missing_flags_are_unverified(self):
        for raw in ['unknown','unavailable','OL OB','LB','',None]:
            self.assertEqual(operating(raw),'unverified')
        self.assertEqual(operating('OB LB'),'on_battery')
    def test_target_change_never_reuses_history(self):
        self.j.sensor('OB')
        other=USBUPS(self.path,dict(OPTIONS,nut_ups_name='other'))
        self.assertEqual(other.events,[])
    def test_bounds_and_corrupt_history(self):
        for _ in range(520):self.j.record('baseline','line_power','status_sensor')
        self.assertEqual(len(self.j.events),512)
        self.path.write_text('{broken')
        broken=USBUPS(self.path,OPTIONS);self.assertTrue(broken.error)
        self.assertEqual(self.path.read_text(),'{broken')
    def test_no_other_device_actions_and_ask_works_without_report(self):
        self.j.sensor('OB');self.j.connected=True
        result=evidence(None,{},usb_ups=self.j.status())
        self.assertEqual(result['usb_ups']['current'],'on_battery')
        self.assertNotIn('nut_status_entity',json.dumps(result))
    def test_configuration_and_wattbox_precedence(self):
        self.assertFalse(USBUPS(self.path,{}).enabled)
        self.assertTrue(USBUPS(self.path,{'nut_status_entity':'http://bad'}).error)
        pilot=object.__new__(Pilot);pilot.araknis_options={'ups_monitoring_enabled':True};pilot.usb_ups=self.j
        self.assertFalse(pilot.wattbox_ups_monitoring_enabled())
        pilot.usb_ups.enabled=False;self.assertTrue(pilot.wattbox_ups_monitoring_enabled())

class FakeWS:
    def __init__(self,journal,platform='nut'):
        self.j=journal;self.sent=[];self.closed=False
        self.rows=[{'type':'auth_required'},{'type':'auth_ok'},
            {'id':1,'success':True,'result':[{'entity_id':OPTIONS['nut_status_entity'],'platform':platform}]},
            {'id':2,'type':'result','success':True},{'id':3,'type':'result','success':True},
            {'id':3,'type':'event','event':{'a':{OPTIONS['nut_status_entity']:{'s':'OL','lu':time.time()}}}},
            {'id':2,'type':'event','event':{'data':{'ups_name':'test_ups','notify_type':'ONBATT'},'time_fired':iso(time.time()+1)}},
            {'id':3,'type':'event','event':{'c':{OPTIONS['nut_status_entity']:{'+':{'s':'OB','lu':time.time()+1}}}}}]
    def send(self,text):self.sent.append(json.loads(text))
    def recv(self):
        row=self.rows.pop(0)
        if not self.rows:self.j.stop.set()
        return json.dumps(row)
    def close(self):self.closed=True

class SubscriptionTests(unittest.TestCase):
    def test_filters_registry_state_and_nut_stream_without_actions(self):
        with tempfile.TemporaryDirectory() as td:
            j=USBUPS(Path(td)/'events.json',OPTIONS);ws=FakeWS(j);j.connect=lambda *a,**k:ws
            with patch('usb_ups.token_value',return_value='secret'):j.session()
            self.assertEqual([e['kind'] for e in j.events],['baseline','on_battery'])
            self.assertEqual(ws.sent[-1],{'id':3,'type':'subscribe_entities','entity_ids':[OPTIONS['nut_status_entity']]})
            self.assertTrue(ws.closed)
            self.assertNotIn('secret',(Path(td)/'events.json').read_text())
    def test_sensor_must_belong_to_nut(self):
        with tempfile.TemporaryDirectory() as td:
            j=USBUPS(Path(td)/'events.json',OPTIONS);ws=FakeWS(j,platform='other');j.connect=lambda *a,**k:ws
            with patch('usb_ups.token_value',return_value='secret'):
                with self.assertRaises(ValueError):j.session()
            self.assertTrue(ws.closed);self.assertEqual(len(ws.sent),2)
