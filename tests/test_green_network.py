import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
import green_network as green
from agent import Pilot, page
from dashboard_visuals import network_panels
from diagnostic_hub import evidence
from service_report import assess


def healthy(speed=None):
    return {'source':'green', 'checked_at':green.stamp(), 'probes':[
        {'name':label, 'sent':5, 'received':5, 'loss_percent':0, 'latency_ms':7}
        for label,_ in green.TARGETS], 'dns':{'status':'resolved'}, 'readings':[],
        'speed_test':speed, 'limitations':[]}


class GreenTests(unittest.TestCase):
    def test_missing_ping_is_unknown_not_zero_loss(self):
        runner=Mock(side_effect=FileNotFoundError())
        result=green.collect(runner=runner)
        self.assertTrue(all(p['loss_percent'] is None for p in result['probes']))
        self.assertEqual(result['readings'], [])
        self.assertEqual(assess('Internet',result)['status'],'unverified')

    def test_fixed_probes_dns_and_sampled_loss(self):
        runner=Mock(side_effect=[
            SimpleNamespace(returncode=0,stdout='5 packets transmitted, 4 received\nrtt min/avg/max/mdev = 5.0/7.5/9.0/1.0 ms'),
            SimpleNamespace(returncode=1,stdout='5 packets transmitted, 0 received'),
            SimpleNamespace(returncode=0,stdout='93.184.215.14 STREAM example.com')])
        result=green.collect(runner=runner)
        self.assertEqual([p['loss_percent'] for p in result['probes']],[20,100])
        self.assertEqual(result['readings'][0]['state'],7.5)
        self.assertEqual(result['dns']['status'],'resolved')
        self.assertEqual(assess('Internet',result)['status'],'review')
        self.assertEqual(runner.call_args_list[0].args[0][-1],'1.1.1.1')
        self.assertFalse(any(c.kwargs.get('shell') for c in runner.call_args_list))

    def test_cached_speed_keeps_timestamp_and_stale_speed_is_not_current(self):
        runner=Mock(return_value=SimpleNamespace(returncode=0,stdout='dns'))
        current=green.stamp()
        row={'name':'Green speed test Download','state':900,'unit':'Mbit/s','last_updated':current}
        speed={'source':'green','readings':[row]}
        result=green.collect(speed,runner=runner)
        self.assertEqual([r for r in result['readings'] if 'speed test' in r['name']],[row])
        self.assertEqual(next(r for r in result['readings'] if 'speed test' in r['name'])['last_updated'],current)
        row['last_updated']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        self.assertEqual([r for r in green.collect(speed,runner=runner)['readings'] if 'speed test' in r['name']],[])

    def test_complete_transfers_only_and_no_redirect(self):
        response=Mock(status=200)
        response.read1.side_effect=[b'abcd',b'']
        connection=Mock(sock=None);connection.getresponse.return_value=response
        clock=Mock(side_effect=[0,1,2,3,5])
        rate,elapsed=green.transfer(connection,'download',4,90,clock)
        self.assertEqual(elapsed,4)
        self.assertEqual(connection.request.call_args.args[:2],('GET','/__down?bytes=4'))
        response.status=302
        with self.assertRaises(ValueError):green.transfer(connection,'download',4,90,lambda:0)
        connection.getresponse.return_value=SimpleNamespace(status=200,read1=Mock(side_effect=[b'ab',b'']))
        with self.assertRaises(ValueError):green.transfer(connection,'download',4,90,lambda:0)

    def test_partial_failure_never_reuses_previous_direction_value(self):
        factory=Mock()
        with patch.object(green,'transfer',side_effect=[(1,0.1),(50,2),TimeoutError()]):
            result=green.run(factory)
        self.assertFalse(result['fresh_values_confirmed'])
        self.assertEqual(len(result['readings']),1)
        self.assertIn('Download',result['readings'][0]['name'])
        self.assertEqual(factory.call_count,2)
        self.assertTrue(any('upload' in n for n in result['limitations']))
        self.assertEqual(factory.call_args.args[:2],('speed.cloudflare.com',443))

    def test_budget_and_warmup_not_presented_as_measurement(self):
        with patch.object(green,'transfer',return_value=(900,0.1)) as transfer:
            result=green.run(Mock())
        self.assertTrue(result['fresh_values_confirmed'])
        self.assertEqual(transfer.call_count,10)
        self.assertEqual(result['payload_bytes_completed'],267_000_000)
        with patch.object(green,'transfer',side_effect=TimeoutError()):
            self.assertEqual(green.run(Mock())['readings'],[])

    def test_green_and_unifi_results_remain_separate(self):
        snapshot=healthy()
        snapshot['readings']=[{'name':'Green speed test Download','state':650,'unit':'Mbit/s'}]
        uni={'internet_readings':[{'name':'UniFi speed test Download','state':940,'unit':'Mbit/s'}]}
        rendered=network_panels({'Internet':{'snapshot':snapshot},'UniFi':{'snapshot':uni}})
        self.assertIn('650 Mbit/s',rendered)
        self.assertNotIn('UniFi gateway measurements',rendered)
        self.assertNotIn('940 Mbit/s',rendered)
        gateway_rendered=network_panels({'UniFi':{'snapshot':uni}})
        self.assertIn('940 Mbit/s',gateway_rendered)
        self.assertNotIn('650 Mbit/s',gateway_rendered)
        self.assertIn('Measured from Green',rendered)
        self.assertIn('DNS: resolved',rendered)

    def test_no_gateway_config_required_persistence_and_ask_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);options=root/'options.json';options.write_text('{}')
            pilot=Pilot(root,options)
            with patch('agent.threading.Thread') as thread:
                pilot.start_network_job('internet_test')
                job=thread.call_args.kwargs['target']
            result={'source':'green','readings':[],'fresh_values_confirmed':False,'completed_at':green.stamp(),'limitations':[]}
            with patch('agent.run_green_test',return_value=result) as run, patch('agent.collect_green_network',return_value=healthy(result)), patch('agent.run_gateway_test') as gateway:
                job();run.assert_called_once();gateway.assert_not_called()
            self.assertIsNone(pilot.internet_test_error)
            restarted=Pilot(root,options)
            self.assertEqual(restarted.green_speed,result)
            self.assertNotIn('gateway-speedtest',page(pilot))
            out=evidence(None,{},individual={'Internet':pilot.green_snapshot})
            self.assertEqual(out['summaries'][0]['system'],'Internet')

    def test_manual_unifi_button_still_uses_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);options=root/'options.json';options.write_text('{}')
            pilot=Pilot(root,options)
            pilot.araknis_options['unifi_gateway_host']='gateway.example'
            self.assertIn('Run UniFi Gateway Speed Test',page(pilot))
            with patch('agent.threading.Thread') as thread:
                pilot.start_network_job('internet_test',source='unifi_gateway')
                job=thread.call_args.kwargs['target']
            with patch('agent.run_gateway_test',return_value={'readings':[]}) as gateway, patch('agent.run_green_test') as run:
                job();gateway.assert_called_once();run.assert_not_called()
            self.assertIsNone(pilot.green_speed)


if __name__=='__main__':unittest.main()

