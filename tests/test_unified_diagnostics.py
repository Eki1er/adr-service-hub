import copy
import hashlib
import base64
import importlib.util
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]/'adr_service_agent'
sys.path.insert(0,str(ROOT))
import network_diagnostics as diag
import diagnostic_analysis as ai
import diagnostic_hub as hub
from agent import Pilot, Handler, page, ProtocolError


class Tools(unittest.TestCase):
    def options(self):return {'allowed_subnets':['192.168.50.0/24'],'targets':['192.168.50.10'],'dns_name':'example.com'}

    def test_only_explicit_small_private_subnets(self):
        for subnet in ('0.0.0.0/0','8.8.8.0/24','192.168.0.0/16','127.0.0.0/24','169.254.1.0/24','::1/128'):
            with self.subTest(subnet=subnet),self.assertRaises(diag.DiagnosticError):
                diag.config({'allowed_subnets':[subnet]})
        for target in ('192.168.51.1','192.168.50.0','192.168.50.255','--help','host.example','192.168.50.1; reboot'):
            with self.subTest(target=target),self.assertRaises(diag.DiagnosticError):
                diag.config(dict(self.options(),targets=[target]))
        self.assertEqual(diag.config(self.options())['targets'],['192.168.50.10'])

    def test_config_bounds(self):
        for options in (dict(self.options(),targets=['192.168.50.10']*17),dict(self.options(),dns_name='bad\nname'),dict(self.options(),monitor_interval_minutes=True)):
            with self.assertRaises(diag.DiagnosticError):diag.config(options)

    def test_surrounding_whitespace_is_normalized(self):
        c=diag.config({'allowed_subnets':[' 192.168.50.0/24 '],
            'targets':[' 192.168.50.1\n'],'dns_name':' example.com '})
        self.assertEqual(c['targets'],['192.168.50.1'])
        self.assertEqual(c['dns_name'],'example.com')

    def test_joined_addresses_have_actionable_error(self):
        with self.assertRaises(diag.DiagnosticError) as error:
            diag.config(dict(self.options(),targets=['192.168.50.1,192.168.50.2']))
        self.assertIn('Add each address separately',str(error.exception))

    def test_ping_fixed_args_and_short_sample(self):
        runner=Mock(return_value=SimpleNamespace(stdout='3 packets transmitted, 2 received, 33% packet loss\nrtt min/avg/max/mdev = 1.0/2.0/3.0/1.0 ms',returncode=0))
        r=diag.ping('192.168.50.10',runner=runner)
        self.assertEqual(r['received'],2);self.assertEqual(r['average_ms'],2);self.assertEqual(r['loss_percent'],33.3)
        args,kwargs=runner.call_args
        self.assertEqual(args[0],['ping','-n','-c','3','-W','1','-w','4','--','192.168.50.10'])
        self.assertNotIn('shell',kwargs);self.assertEqual(kwargs['timeout'],5)

    def test_ping_unavailable_never_invents_loss(self):
        runner=Mock(side_effect=OSError('no permission'))
        r=diag.ping('192.168.50.10',runner=runner)
        self.assertEqual(r['status'],'unverified');self.assertIsNone(r['loss_percent'])
        runner=Mock(return_value=SimpleNamespace(stdout='3 packets transmitted, 0 received',returncode=1))
        self.assertEqual(diag.ping('192.168.50.10',runner=runner)['status'],'no_reply')

    def test_tcp_is_connect_only(self):
        connection=Mock();connection.__enter__=Mock();connection.__exit__=Mock(return_value=False)
        connector=Mock(return_value=connection)
        self.assertEqual(diag.tcp('192.168.50.10',443,connector)['status'],'reachable')
        connection.send.assert_not_called();connection.recv.assert_not_called()
        with self.assertRaises(diag.DiagnosticError):diag.tcp('192.168.50.10',22,connector)

    def test_dns_bounded_child_and_failures(self):
        runner=Mock(return_value=SimpleNamespace(stdout='["192.0.2.1"]',returncode=0))
        self.assertEqual(diag.dns('example.com',runner)['status'],'resolved')
        self.assertEqual(runner.call_args.kwargs['timeout'],5)
        runner.side_effect=TimeoutError()
        self.assertEqual(diag.dns('example.com',runner)['status'],'unverified')

    def test_check_uses_only_configured_targets_and_ports(self):
        ping=Mock(return_value={'status':'responded'});tcp=Mock(return_value={'status':'reachable'});dns=Mock(return_value={'status':'resolved'})
        c=diag.config(self.options());r=diag.check(c,ping,tcp,dns)
        ping.assert_called_once_with('192.168.50.10')
        self.assertEqual([x.args[1] for x in tcp.call_args_list],[80,443]);dns.assert_called_once_with('example.com')
        self.assertEqual(len(r['targets']),1)

    def test_discovery_one_configured_subnet_only(self):
        c=diag.config({'allowed_subnets':['192.168.50.0/30']})
        probe=Mock(side_effect=lambda ip,count:{'status':'responded' if ip.endswith('.1') else 'no_reply'})
        r=diag.discover(c,'192.168.50.0/30',probe)
        self.assertEqual(r['responding'],['192.168.50.1']);self.assertEqual(r['tested'],2)
        self.assertNotIn('offline',json.dumps(r).lower().split('limitations')[0])
        with self.assertRaises(diag.DiagnosticError):diag.discover(c,'192.168.51.0/24',probe)

    def test_retention_caps_and_prunes_observed(self):
        old={'saved_at':time.time()-8*86400,'result':{'kind':'discovery','responding':['192.168.50.1'],'checked_at':'old'}}
        h={'samples':[old]};diag.retain(h)
        self.assertEqual(h['observed'],[])
        r={'kind':'discovery','responding':['192.168.50.2'],'checked_at':'new'}
        for _ in range(110):diag.retain(h,r)
        self.assertEqual(len(h['samples']),96);self.assertEqual(h['observed'][0]['ip'],'192.168.50.2')

    def test_report_import_has_fixed_fields_only(self):
        r={'format':'adr-service-report-v1','checks':[{'system':'WattBox','snapshot':{'password':'secret','readings':[{'response':'private console'}],'outlets':[{'number':1,'label':'TV','on':True,'password':'secret'}]}}]}
        result=diag.import_report(r);self.assertNotIn('secret',json.dumps(result));self.assertNotIn('private console',json.dumps(result))
        self.assertTrue(result['systems'][0]['readings'][0]['on'])

    def test_report_import_nested_controller(self):
        r={'format':'adr-service-report-v1','checks':[{'system':'Ruckus','snapshot':{'controller':{'access_points':[{'name':'Office','online':None,'connected_clients':25}]}}}]}
        reading=diag.import_report(r)['systems'][0]['readings'][0]
        self.assertIsNone(reading['online']);self.assertEqual(reading['connected_clients'],25)

    def test_import_keeps_speed_and_post_action_timestamps(self):
        r={'format':'adr-service-report-v1','checks':[
            {'system':'WattBox','completed_at':'original','post_action_verified_at':'fresh','snapshot':{'outlets':[]}},
            {'system':'UniFi','snapshot':{'sensors':[],'internet_readings':[{'name':'Download','state':'918','unit':'Mbit/s','last_updated':'test-time','password':'never-copy'}]}}]}
        result=diag.import_report(r)
        self.assertEqual(result['systems'][0]['checked_at'],'fresh')
        self.assertEqual(result['systems'][1]['readings'][0]['state'],'918')
        self.assertNotIn('never-copy',json.dumps(result))

    def test_private_atomic_file(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'history.json';diag.save(path,{'test':1})
            self.assertEqual(path.stat().st_mode&0o777,0o600);self.assertFalse(path.with_suffix('.tmp').exists())


class Analysis(unittest.TestCase):
    def opts(self):return {'cloud_analysis_enabled':True,'openai_api_key':'fake-test-key','openai_model':'test-model'}
    def connection(self,body,status=200):
        c=Mock();response=Mock();response.status=status;response.read.return_value=json.dumps(body).encode();c.getresponse.return_value=response
        return c,Mock(return_value=c)

    def test_one_request_no_tools_and_no_storage(self):
        c,factory=self.connection({'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Review DNS.'}]}]})
        result=ai.ask(self.opts(),'Why slow?',{'dns':'unverified'},True,factory)
        self.assertEqual(result,'Review DNS.');c.request.assert_called_once()
        args,kwargs=c.request.call_args;self.assertEqual(args[:2],('POST','/v1/responses'))
        payload=json.loads(kwargs['body']);self.assertFalse(payload['store']);self.assertNotIn('tools',payload)
        self.assertNotIn('fake-test-key',kwargs['body']);self.assertEqual(payload['max_output_tokens'],1500)
        self.assertIsNotNone(factory.call_args.kwargs['context']);c.close.assert_called_once()

    def test_cloud_requires_both_config_and_explicit_consent(self):
        factory=Mock()
        for opts,confirm in (({},True),(self.opts(),False)):
            with self.assertRaises(diag.DiagnosticError):ai.ask(opts,'question',{},confirm,factory)
        factory.assert_not_called()

    def test_cloud_errors_never_leak_or_retry(self):
        c,factory=self.connection({'secret':'fake-test-key'},429)
        with self.assertRaises(diag.DiagnosticError) as error:ai.ask(self.opts(),'question',{},True,factory)
        self.assertNotIn('fake-test-key',str(error.exception));c.request.assert_called_once()

    def test_partial_answer_and_tool_calls_not_executed(self):
        c,factory=self.connection({'status':'incomplete','output':[{'type':'function_call','name':'reboot'}]})
        with self.assertRaises(diag.DiagnosticError):ai.ask(self.opts(),'question',{},True,factory)
        c.request.assert_called_once()


class Unified(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'options.json'
        self.options={'diagnostics_enabled':True,'allowed_subnets':['192.168.50.0/24'],
            'targets':['192.168.50.10'],'cloud_analysis_enabled':True,
            'openai_api_key':'private-test-key','openai_model':'test-model','automatic_monitoring':False}
        self.path.write_text(json.dumps(self.options));self.pilot=Pilot(self.root,self.path)

    def report(self):
        return {'format':'adr-service-report-v1','completed_at':'2026-11-01T12:00:00+00:00','checks':[
            {'system':'WattBox','status':'verified','completed_at':'2026-11-01T11:59:00+00:00',
             'post_action_verified_at':'2026-11-01T11:59:30+00:00',
             'snapshot':{'outlets':[{'number':1,'label':'private-test-key','on':True}],
                'response':'RAW CONSOLE','password':'DO NOT SHARE'}}]}

    def inline(self,**kw):
        return SimpleNamespace(start=kw['target'])

    def test_startup_and_page_are_read_only_and_use_one_interface(self):
        with patch.object(hub,'check') as check,patch.object(hub,'ask') as ask:
            pilot=Pilot(self.root,self.path);text=page(pilot)
            check.assert_not_called();ask.assert_not_called()
        self.assertIn('id="diagnostics"',text);self.assertIn('id="ask-adr"',text)
        self.assertNotIn('private-test-key',text);self.assertNotIn('Import report',text)
        self.assertNotIn('Diagnostics Preview',text)
        self.assertEqual(text.count('id="adr-question-form"'),1)

    def test_current_service_report_replaces_manual_import_and_redacts(self):
        self.pilot.service_report=self.report()
        with patch.object(hub,'ask',return_value='A short answer') as ask:
            self.pilot.diagnostics.start('ask',{'question':'Check power','confirm':'yes'},self.inline)
        sent=ask.call_args.args[2];data=json.dumps(sent)
        self.assertNotIn('private-test-key',data);self.assertNotIn('RAW CONSOLE',data)
        self.assertNotIn('DO NOT SHARE',data)
        self.assertIn('2026-11-01T11:59:30+00:00',data)
        self.assertEqual(self.pilot.diagnostics.answer,'A short answer')
        self.assertFalse(self.pilot.diagnostics.busy)
        self.assertEqual(hub.Hub(self.root,self.options,lambda:{}).quota['count'],1)

    def test_disabled_and_unconfirmed_requests_never_call_ai(self):
        self.pilot.service_report=self.report()
        with patch.object(hub,'ask') as ask:
            for fields in ({'question':'check'},{'question':'check','confirm':'no'}):
                with self.assertRaises(diag.DiagnosticError):self.pilot.diagnostics.start('ask',fields,self.inline)
            ask.assert_not_called()
        self.assertFalse((self.root/'diagnostics/analysis_attempts.json').exists())

    def test_scheduled_jobs_include_local_checks_only_when_enabled(self):
        jobs=self.pilot.service_jobs()
        self.assertEqual(jobs[-1][0],'Network diagnostics')
        with patch.object(hub,'check',return_value={'kind':'target_checks','checked_at':'now','targets':[],
                'dns':{'status':'resolved'}}) as check,patch.object(hub,'ask') as ask:
            jobs[-1][2]()
            check.assert_called_once();ask.assert_not_called()
        self.assertTrue((self.root/'diagnostics/history.json').exists())
        self.pilot.araknis_options['diagnostics_enabled']=False
        self.assertNotIn('Network diagnostics',[j[0] for j in self.pilot.service_jobs()])

    def test_existing_installation_needs_no_new_settings_to_start(self):
        self.path.write_text('{}');pilot=Pilot(self.root,self.path)
        self.assertFalse(pilot.diagnostics.error)
        self.assertFalse(pilot.diagnostics.status()['enabled'])
        self.assertEqual(len(pilot.service_jobs()),10)
        with patch.object(hub,'check') as check:
            with self.assertRaises(diag.DiagnosticError):pilot.diagnostics.start('check',{})
            check.assert_not_called()

    def test_invalid_diagnostics_do_not_break_service_dashboard(self):
        self.path.write_text(json.dumps(dict(self.options,allowed_subnets=['8.8.8.0/24'])))
        pilot=Pilot(self.root,self.path)
        self.assertTrue(pilot.diagnostics.status()['blocked'])
        self.assertIn('Ask ADR',page(pilot))
        self.assertIn('allowed_subnets',pilot.diagnostics.status()['progress'])

    def test_busy_jobs_block_monitor_and_all_checks(self):
        self.pilot.diagnostics.busy=True
        self.assertTrue(self.pilot.monitoring_busy())
        with self.assertRaises(ProtocolError):self.pilot.start_service_checks()
        self.assertIsNone(self.pilot.monitoring_tick())

    def test_quota_and_failed_thread_start_do_not_retry(self):
        self.pilot.service_report=self.report()
        with patch.object(hub,'ask') as ask:
            factory=Mock(side_effect=RuntimeError('secret'))
            with self.assertRaises(diag.DiagnosticError):
                self.pilot.diagnostics.start('ask',{'question':'check','confirm':'yes'},factory)
            ask.assert_not_called()
        self.assertFalse(self.pilot.diagnostics.busy)
        self.assertEqual(self.pilot.diagnostics.quota['count'],1)
        self.pilot.diagnostics.last_started=0
        self.pilot.diagnostics.quota['count']=100
        with self.assertRaises(diag.DiagnosticError):self.pilot.diagnostics.start('ask',{'question':'check','confirm':'yes'})

    def handler(self,path,method='GET',body='',peer='172.30.32.2'):
        from urllib.parse import urlencode
        h=Handler.__new__(Handler);h.server=SimpleNamespace(pilot=self.pilot)
        h.client_address=(peer,1234);h.path=path;h.wfile=io.BytesIO()
        h.rfile=io.BytesIO(body.encode());h.headers={'Content-Length':str(len(body.encode())),
            'Content-Type':'application/x-www-form-urlencoded'}
        h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();h.send_error=Mock()
        h.send_page=Mock();return h

    def test_state_get_never_starts_checks_and_actual_peer_is_required(self):
        with patch.object(self.pilot.diagnostics,'start') as start:
            h=self.handler('/diagnostic-state');h.do_GET()
            self.assertFalse(json.loads(h.wfile.getvalue())['busy']);start.assert_not_called()
            h=self.handler('/diagnostic-state',peer='127.0.0.1');h.headers['X-Forwarded-For']='172.30.32.2';h.do_GET()
            h.send_error.assert_called_once();self.assertEqual(h.wfile.getvalue(),b'')

    def test_csrf_and_global_action_guards(self):
        from urllib.parse import urlencode
        with patch.object(self.pilot.diagnostics,'start') as start:
            h=self.handler('/diagnostic-check',body=urlencode({'csrf':'bad'}));h.do_POST();start.assert_not_called()
            body=urlencode({'csrf':self.pilot.csrf})
            self.pilot.active=True;h=self.handler('/diagnostic-check',body=body);h.do_POST();start.assert_not_called()
            self.pilot.active=False;h=self.handler('/diagnostic-check',body=body);h.do_POST()
            start.assert_called_once_with('check',{});h.send_response.assert_called_once_with(202)
            start.reset_mock();self.pilot.power_pending={'created':time.monotonic()}
            h=self.handler('/diagnostic-check',body=body);h.do_POST();start.assert_not_called()

    def test_new_configuration_and_build_use_one_process(self):
        import yaml
        c=yaml.safe_load((ROOT/'config.yaml').read_text())
        self.assertEqual(c['slug'],'adr_service_agent');self.assertEqual(c['privileged'],['NET_RAW'])
        self.assertFalse(c['host_network']);self.assertFalse(c['hassio_api'])
        self.assertIn('openai_api_key',c['schema']);self.assertFalse(c['options']['diagnostics_enabled'])
        docker=(ROOT/'Dockerfile').read_text()
        self.assertIn('iputils-ping',docker);self.assertIn('diagnostic_hub.py',docker)
        self.assertEqual(docker.count('CMD '),1)
