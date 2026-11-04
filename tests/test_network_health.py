import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
import network_health as health
import switch_network as cli
import unifi_network
import unifi_gateway
import araknis
from diagnostic_hub import evidence
from service_report import metrics,compare,assess


class NetworkHealth(unittest.TestCase):
    def options(self):return {'unifi_gateway_host':'controller.example','unifi_gateway_api_key':'secret-key'}
    def devices(self):return [{ 'type':'usw','mac':'00:11:22:33:44:55','name':'Core','state':1,
        'stp_priority':'4096','stp_version':'rstp','password':'never-store',
        'port_table':[{'port_idx':1,'name':'Uplink','up':True,'stp_state':'blocking',
            'speed':1000,'rx_errors':3,'rx_multicast':20000,'rx_broadcast':50,'secret':'never-store'}]}]
    def normalized(self):
        out=health.base('UniFi','2026-11-04T01:00:00+00:00');out['target_id']='controller-a'
        health.unifi_devices(self.devices(),out);return out

    def test_blocking_and_large_counters_are_observations_not_loop_findings(self):
        out=self.normalized()
        self.assertEqual(out['findings'],[])
        self.assertIn('not a loop',out['observations'][0])
        for field in ('stp_root','igmp_memberships','igmp_querier','loop_events'):
            self.assertEqual(out['coverage'][field],'unverified')
        self.assertNotIn('never-store',json.dumps(out))

    def test_missing_and_malformed_fields_are_not_default_healthy_values(self):
        out=health.base('UniFi','now')
        health.unifi_devices([{'type':'usw','mac':'00:11:22:33:44:55','state':True,
            'port_table':[{'port_idx':1,'rx_errors':True,'speed':float('inf'),'stp_state':'new-format'}]}],out)
        device=out['devices'][0];self.assertEqual(device['state'],'unverified')
        self.assertEqual(device['ports'],[{'port':1,'name':'Port 1'}])
        self.assertEqual(out['findings'],[])

    def test_network_config_is_allowlisted_and_does_not_claim_joins(self):
        out=self.normalized()
        health.unifi_networks([{'name':'AV','vlan':30,'igmp_snooping':True,'x_password':'hidden',
            'dhcpd_dns_1':'private','querier':'invented'}],out)
        self.assertEqual(out['networks'],[{'name':'AV','vlan':30,'igmp_snooping':True}])
        self.assertEqual(out['coverage']['igmp_memberships'],'unverified')

    def test_adapter_reads_only_fixed_get_operations_and_preserves_partial_success(self):
        request=Mock(side_effect=[{'meta':{'rc':'ok'},'data':self.devices()},unifi_gateway.GatewayError('secret-key')])
        out=unifi_network.collect(self.options(),{'sha256':'a'*64},request)
        self.assertEqual([(c.args[2],c.args[3]) for c in request.call_args_list],[('GET','devices'),('GET','networks')])
        self.assertEqual(len(out['devices']),1)
        self.assertEqual(out['coverage']['multicast_settings'],'unverified')
        self.assertNotIn('secret-key',json.dumps(out))

    def test_missing_configuration_never_contacts_controller(self):
        request=Mock();out=unifi_network.collect({},None,request)
        request.assert_not_called();self.assertEqual(out['coverage']['devices'],'unverified')

    def test_trust_error_stops_all_further_network_requests(self):
        request=Mock(side_effect=unifi_gateway.GatewayTrustError('changed'))
        out=unifi_network.collect(self.options(),{'sha256':'a'*64},request)
        self.assertEqual(request.call_count,1)
        self.assertEqual(out['coverage']['devices'],'unverified')

    def test_gateway_rejects_wrong_methods_and_unapproved_pin_before_connect(self):
        config=unifi_gateway.settings(self.options())
        with patch('unifi_gateway.http.client.HTTPSConnection') as connection:
            for operation in ('devices','networks'):
                with self.assertRaises(unifi_gateway.GatewayError):unifi_gateway.request(config,{},'POST',operation)
                with self.assertRaises(unifi_gateway.GatewayError):unifi_gateway.request(config,{},'GET',operation)
            connection.assert_not_called()

    def test_comparisons_are_target_scoped_and_never_call_multicast_a_storm(self):
        old=self.normalized();new=copy.deepcopy(old)
        new['devices'][0]['ports'][0]['rx_multicast']+=10
        before=metrics('UniFi',{'network_health':old});after=metrics('UniFi',{'network_health':new})
        delta=compare(after,before)
        self.assertIn('Core port1: rx multicast increased by 10',delta[0])
        self.assertIn('no rate or cause inferred',delta[0])
        new['target_id']='controller-b'
        self.assertFalse(any('increased' in x for x in compare(metrics('UniFi',{'network_health':new}),before)))
        new['target_id']='controller-a';new['devices'][0]['ports'][0]['rx_multicast']=1
        self.assertTrue(any('reset or wrap' in x for x in compare(metrics('UniFi',{'network_health':new}),before)))

    def test_ask_evidence_contains_normalized_network_data_and_limits_not_raw_config(self):
        out=self.normalized();out['devices'][0]['password']='raw-secret'
        report={'format':'adr-service-report-v1','checks':[{'system':'UniFi','snapshot':{'network_health':out},'comparisons':['counter changed']}]}
        result=evidence(report,{})
        self.assertEqual(result['network_health'][0]['devices'][0]['name'],'Core')
        self.assertNotIn('raw-secret',json.dumps(result))
        self.assertEqual(result['summaries'][0]['comparisons'],['counter changed'])


class CLIHealth(unittest.TestCase):
    def test_help_walk_requires_every_advertised_parent_and_complete_cr(self):
        calls=[]
        helps={'show':'spanning-tree    STP\nip    IP\nreload    forbidden',
            'show spanning-tree':'<CR>    display\nsummary    summary',
            'show spanning-tree summary':'<CR>    display',
            'show ip':'igmp    group protocol','show ip igmp':'snooping    snooping',
            'show ip igmp snooping':'groups    groups',
            'show ip igmp snooping groups':'<CR>    display'}
        def request(command,help_only):
            calls.append((command,help_only))
            return (helps.get(command,'') if help_only else 'Root ID : 0011.2233.4455\nTopology changes : 12\nGi0/1 Altn BLK',False)
        result=cli.collect(request)
        self.assertIn(('show ip igmp snooping groups',False),calls)
        self.assertNotIn(('show ip',False),calls)
        self.assertFalse(any('reload' in c for c,_ in calls))
        parsed=cli.analyze(result)
        self.assertEqual(parsed['spanning_tree']['root_id'],'0011.2233.4455')
        self.assertEqual(parsed['spanning_tree']['topology_changes'],12)
        self.assertEqual(parsed['findings'],[])

    def test_truncated_help_no_execution_and_budget_is_bounded(self):
        request=Mock(return_value=('spanning-tree    STP\n<CR>    display',True))
        self.assertEqual(cli.collect(request)['readings'],[])
        request=Mock(return_value=('spanning-tree    STP\n<CR>    display',False))
        result=cli.collect(request,max_probes=1,max_reads=0)
        self.assertTrue(result['limit_reached']);self.assertEqual(request.call_count,1)

    def test_partial_or_error_response_does_not_supply_facts(self):
        for row in ({'truncated':True},{'error':'bad'},{'status':'command needs verification'}):
            item=dict(command='show spanning-tree',status='response received',response='Root ID : 0011.2233.4455',**{k:v for k,v in row.items() if k!='status'})
            item.update(row)
            self.assertEqual(cli.analyze({'readings':[item]})['spanning_tree'],{})

    def test_igmp_joins_require_multicast_address_vlan_and_port_not_prose(self):
        parsed=cli.analyze({'readings':[{'command':'show ip igmp snooping groups','status':'response received','response':
            '30 239.1.2.3 Gi0/1,Gi0/2\n30 192.168.1.2 Gi0/3\n5000 239.1.2.4 Gi0/4\nExample 239.9.9.9 Gi0/5'}]})
        self.assertEqual(parsed['multicast']['groups'],[{'vlan':30,'group':'239.1.2.3','ports':['Gi0/1','Gi0/2']}])
        self.assertNotIn('querier_address',parsed['multicast'])

    def test_help_cannot_inject_unknown_or_write_commands_into_transport(self):
        client=Mock()
        for command in ('show spanning-tree; reload','show ip igmp snooping groups 30','show running-config','show loop-protection reset'):
            with self.assertRaises(araknis.AraknisError):araknis.query(client,command)
        client.invoke_shell.assert_not_called()

    def test_auth_failure_stops_network_reads_and_preserves_health_snapshot(self):
        import paramiko
        with tempfile.TemporaryDirectory() as temp:
            key=Path(temp)/'keys';key.write_text('pinned')
            with patch('araknis.diagnostic_request',side_effect=paramiko.AuthenticationException()) as request:
                result=araknis.add_network_evidence({'readings':[{'name':'old health'}]},
                    {'host':'switch.example','port':22,'password':'secret'},key)
            self.assertEqual(request.call_count,1)
            self.assertEqual(result['readings'],[{'name':'old health'}])
            self.assertEqual(result['network_health']['coverage']['stp_root'],'unverified')


if __name__=='__main__':unittest.main()


class AuthenticationStop(unittest.TestCase):
    def test_no_second_controller_read_after_authentication_denial(self):
        request=Mock(side_effect=unifi_network.GatewayAuthenticationError('UniFi access denied (HTTP 403).'))
        options={'unifi_gateway_host':'192.0.2.5','unifi_gateway_api_key':'private-key'}
        result=unifi_network.collect(options,{'sha256':'a'*64},request)
        request.assert_called_once()
        self.assertIn('HTTP 403',' '.join(result['limitations']))
        self.assertNotIn('private-key',json.dumps(result))
