import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
import device_identity as identity
from network_diagnostics import config, discover, retain
from diagnostic_hub import Hub
from unifi_gateway import GatewayAuthenticationError


class IdentityTests(unittest.TestCase):
    def test_arp_exact_valid_completed_unicast_matches_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'arp'
            p.write_text('IP address HW type Flags HW address Mask Device\n192.168.16.1 0x1 0x2 00:11:22:33:44:55 * eth0\n192.168.16.2 0x1 0x0 00:00:00:00:00:00 * eth0\n192.168.16.3 0x1 0x2 ff:ff:ff:ff:ff:ff * eth0\n')
            self.assertEqual(identity.arp_table(p),{'192.168.16.1':'00:11:22:33:44:55'})

    def test_controller_normalizes_only_identity_fields_and_fixed_get_routes(self):
        request=Mock(side_effect=[{'meta':{'rc':'ok'},'data':[
            {'ip':'192.168.16.5','mac':'00:11:22:33:44:55','hostname':'office-tv','oui':'Apple','password':'secret'},
            {'ip':'192.168.16.6','mac':'02:11:22:33:44:55','oui':'Wrong guessed vendor','hostname':'phone'},
            {'ip':'invalid','mac':'00:11:22:33:44:55'}]},
            {'meta':{'rc':'ok'},'data':[{'ip':'192.168.16.2','mac':'00:11:22:33:44:56','name':'Main switch','type':'usw'}]}])
        options={'unifi_gateway_host':'192.168.16.1','unifi_gateway_api_key':'test-only'}
        rows,notes=identity.controller_records(options,{},request)
        self.assertEqual(rows[0]['manufacturer'],'Apple')
        self.assertEqual(rows[1]['manufacturer'],'')
        self.assertEqual(rows[2]['device_type'],'Switch')
        self.assertNotIn('secret',json.dumps(rows))
        self.assertEqual([(c.args[2],c.args[3]) for c in request.call_args_list],[('GET','clients'),('GET','devices')])

    def test_no_controller_auth_fallback_and_local_results_still_available(self):
        request=Mock(side_effect=GatewayAuthenticationError('private-secret'))
        options={'unifi_gateway_host':'192.168.16.1','unifi_gateway_api_key':'test-only'}
        rows,notes=identity.controller_records(options,{},request)
        self.assertEqual(rows,[])
        request.assert_called_once()
        self.assertNotIn('private-secret',str(notes))

    def test_conflicting_mac_records_unknown_not_guessed(self):
        records=[{'ip':'192.168.16.2','mac':'00:11:22:33:44:55','hostname':'stale'}]
        resolver=Mock(return_value='new')
        rows,_=identity.identify(['192.168.16.2'],{}, {},records_fn=lambda *_:(records,[]),
            arp_fn=lambda:{'192.168.16.2':'00:11:22:33:44:66'},resolve_fn=resolver)
        self.assertEqual(rows[0]['mac'],'')
        self.assertEqual(rows[0]['hostname'],'')
        self.assertIn('Conflicting',rows[0]['identity_source'])
        resolver.assert_not_called()

    def test_missing_mac_does_not_use_gateway_mac_for_routed_client(self):
        rows,_=identity.identify(['192.168.20.5'],{}, {},records_fn=lambda *_:([],[]),
            arp_fn=lambda:{'192.168.16.1':'00:11:22:33:44:55'},resolve_fn=lambda _:'living-room-tv')
        self.assertEqual(rows[0]['mac'],'')
        self.assertEqual(rows[0]['manufacturer'],'')
        self.assertEqual(rows[0]['hostname'],'living-room-tv')

    def test_reverse_lookup_timeout_is_bounded_and_no_shell(self):
        runner=Mock(return_value=SimpleNamespace(returncode=0,stdout='"tv.local"'))
        self.assertEqual(identity.hostname('192.168.16.2',runner),'tv.local')
        self.assertEqual(runner.call_args.kwargs['timeout'],2)
        self.assertNotIn('shell',runner.call_args.kwargs)
        runner.side_effect=__import__('subprocess').TimeoutExpired('lookup',2)
        self.assertEqual(identity.hostname('192.168.16.2',runner),'')

    def test_web_probes_only_responders_two_ports_and_wattbox_exception(self):
        settings=config({'allowed_subnets':['192.168.16.0/30'],'wattbox_host':'192.168.16.1'})
        probe=Mock(side_effect=lambda ip,_:{'status':'responded' if ip.endswith('.1') else 'no_reply'})
        web=Mock(return_value={'port':80,'status':'reachable'})
        result=discover(settings,'192.168.16.0/30',probe,identify=lambda ips:([{'ip':ips[0],'hostname':'rack'}],[]),web_probe=web)
        web.assert_called_once_with('192.168.16.1',80)
        self.assertEqual(result['devices'][0]['hostname'],'rack')
        self.assertEqual(result['devices'][0]['services'],[{'port':80,'status':'reachable'}])

    def test_identity_lookup_failure_keeps_responding_addresses(self):
        settings=config({'allowed_subnets':['192.168.16.0/30']})
        result=discover(settings,'192.168.16.0/30',probe=lambda *_:{'status':'responded'},identify=Mock(side_effect=RuntimeError('private-secret')))
        self.assertEqual([r['ip'] for r in result['devices']],result['responding'])
        self.assertNotIn('private-secret',str(result))

    def test_new_discovery_clears_previous_ip_identity(self):
        history={}
        retain(history,{'kind':'discovery','checked_at':'first','responding':['192.168.16.2'],'devices':[{'ip':'192.168.16.2','mac':'00:11:22:33:44:55','manufacturer':'Old'}]})
        self.assertEqual(history['observed'][0]['manufacturer'],'Old')
        retain(history,{'kind':'discovery','checked_at':'second','responding':['192.168.16.2'],'devices':[{'ip':'192.168.16.2'}]})
        self.assertEqual(history['observed'][0]['manufacturer'],'')
        self.assertEqual(history['observed'][0]['mac'],'')

    def test_enriched_history_stays_below_reader_size_limit(self):
        history={}
        devices=[{'ip':'192.168.16.'+str(i),'manufacturer':'x'*100,'hostname':'h'*100,'mac':'00:11:22:33:44:55','identity_source':'s'*100} for i in range(1,255)]
        result={'kind':'discovery','checked_at':'now','responding':[d['ip'] for d in devices],'devices':devices}
        for _ in range(40):retain(history,result)
        self.assertLess(len(json.dumps(history).encode()),2*1024*1024)
        self.assertEqual(len(history['observed']),254)


class DiscoveryScheduleTests(unittest.TestCase):
    def test_only_selected_subnet_runs_at_twenty_minutes_without_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            options={'allowed_subnets':['192.168.16.0/30'],'diagnostics_enabled':True,'automatic_monitoring':True}
            hub=Hub(Path(d),options,lambda:{})
            hub.discovery_ready_at=0
            hub.discovery_last_attempt=1000
            with patch('diagnostic_hub.time.time',return_value=2200),patch.object(hub,'start') as start:
                hub.discovery_tick();start.assert_not_called()
                hub.discovery_subnet='192.168.16.0/30'
                hub.busy=True;hub.discovery_tick();start.assert_not_called()
                hub.busy=False;hub.discovery_tick()
                start.assert_called_once_with('discover',{'subnet':'192.168.16.0/30'})
                start.reset_mock();hub.options['automatic_monitoring']=False
                hub.discovery_tick();start.assert_not_called()

    def test_attempt_persists_and_survives_restart_without_catchup(self):
        with tempfile.TemporaryDirectory() as d:
            options={'allowed_subnets':['192.168.16.0/30'],'diagnostics_enabled':True}
            hub=Hub(Path(d),options,lambda:{})
            factory=Mock()
            hub.start('discover',{'subnet':'192.168.16.0/30'},thread_factory=factory)
            restarted=Hub(Path(d),options,lambda:{})
            self.assertEqual(restarted.discovery_subnet,'192.168.16.0/30')
            self.assertGreater(restarted.discovery_last_attempt,time.time()-5)
            with patch.object(restarted,'start') as start:
                restarted.discovery_tick();start.assert_not_called()


if __name__=='__main__':unittest.main()
