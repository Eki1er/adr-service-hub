import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from urllib.parse import urlencode
ROOT=Path(__file__).resolve().parents[1]/'adr_service_agent';sys.path.insert(0,str(ROOT))
from agent import Pilot,Handler,page
from diagnostic_hub import daily_limit
from network_diagnostics import DiagnosticError

class ConversationFlow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.options={'cloud_analysis_enabled':True,'voice_enabled':True,'automatic_monitoring':False}
        self.path=self.root/'options.json';self.path.write_text(json.dumps(self.options));self.pilot=Pilot(self.root,self.path)
        self.hub=self.pilot.diagnostics;self.hub.result={'kind':'target_checks','targets':[],'dns':{'status':'resolved'}}
    def inline(self,**kw):return SimpleNamespace(start=kw['target'])

    def test_daily_limit_defaults_and_zero_unlimited(self):
        self.assertEqual(daily_limit({},'daily_analysis_limit'),100)
        self.assertEqual(daily_limit({'daily_analysis_limit':0},'daily_analysis_limit'),0)
        for bad in (True,-1,10001,'0'):
            self.assertEqual(daily_limit({'daily_analysis_limit':bad},'daily_analysis_limit'),100)
        day=time.strftime('%Y-%m-%d',time.gmtime());self.hub.quota={'day':day,'count':10}
        with patch('diagnostic_hub.ask',return_value='Reply') as ask:
            self.hub.start('ask',{'question':'status','confirm':'yes'},self.inline)
            ask.assert_called_once();self.assertEqual(self.hub.quota['count'],11)
        self.assertTrue(self.hub.answer_id)

    def test_configurable_cap_and_unlimited_preserve_existing_counters(self):
        day=time.strftime('%Y-%m-%d',time.gmtime());self.hub.options['daily_analysis_limit']=25;self.hub.quota={'day':day,'count':25}
        with self.assertRaises(DiagnosticError):self.hub.start('ask',{'question':'status','confirm':'yes'},self.inline)
        self.hub.options['daily_analysis_limit']=0;self.hub.quota['count']=10000
        with patch('diagnostic_hub.ask',return_value='Reply'):
            self.hub.start('ask',{'question':'status','confirm':'yes'},self.inline)
        self.assertEqual(self.hub.quota['count'],10001)
        self.hub.last_started=0;first=self.hub.answer_id
        with patch('diagnostic_hub.ask',return_value='Reply'):
            self.hub.start('ask',{'question':'status','confirm':'yes'},self.inline)
        self.assertNotEqual(first,self.hub.answer_id)

    def test_removed_checkboxes_and_cloud_still_opt_in(self):
        text=page(self.pilot)
        for name in ('adr-analysis-consent','adr-voice-consent','adr-read-replies'):self.assertNotIn('id="'+name+'"',text)
        self.hub.options['cloud_analysis_enabled']=False
        with patch('diagnostic_hub.ask') as ask:
            with self.assertRaises(DiagnosticError):self.hub.start('ask',{'question':'status','confirm':'yes'},self.inline)
            ask.assert_not_called()

    def handler(self,action):
        body=urlencode({'csrf':self.pilot.csrf}).encode()
        h=Handler.__new__(Handler);h.server=SimpleNamespace(pilot=self.pilot);h.client_address=('172.30.32.2',123)
        h.path='/'+action;h.rfile=io.BytesIO(body);h.wfile=io.BytesIO();h.headers={'Content-Length':str(len(body)),'Content-Type':'application/x-www-form-urlencoded'}
        h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();h.send_error=Mock();h.send_page=Mock();return h

    def test_araknis_read_during_ups_poll_and_power_action_still_blocked(self):
        self.pilot.ups_running=True;self.pilot.check_araknis=Mock()
        h=self.handler('araknis-check');h.do_POST();self.pilot.check_araknis.assert_called_once();h.send_page.assert_called_once_with()
        with patch('agent.prepare_outlet') as prepare:
            h=self.handler('outlet-prepare');h.do_POST();prepare.assert_not_called();self.assertEqual(h.send_page.call_args.args,(400,))
        self.pilot.service_running=True;self.pilot.check_araknis.reset_mock()
        h=self.handler('araknis-check');h.do_POST();self.pilot.check_araknis.assert_not_called()
