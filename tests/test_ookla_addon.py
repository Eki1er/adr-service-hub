import copy
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
import ookla_addon as ookla
from agent import Pilot, page


def rows(date=None):
    date=date or datetime.now(timezone.utc).isoformat()
    return {k:{'entity_id':'sensor.speedtest_'+k,'state':v,'last_updated':date,
               'attributes':{'server_id':'62090','server_name':'Optimum Online',
                             'unit_of_measurement':'ms' if k=='ping' else 'Mbit/s',
                             'bytes_received':100_000_000,'bytes_sent':100_000_000}}
            for k,v in [('download','781.07'),('upload','861.42'),('ping','7.882')]}


def reader(data):
    return lambda token,path:copy.deepcopy(data[path.rsplit('_',1)[-1]])


class OoklaTests(unittest.TestCase):
    def test_actual_fixture_units_values_and_original_timestamps(self):
        data=rows();result=ookla.read('test',reader(data))
        self.assertTrue(result['fresh_values_confirmed'])
        self.assertEqual([r['state'] for r in result['readings']],[781.07,861.42,7.882])
        self.assertEqual(result['server_id'],'62090')
        self.assertEqual(result['readings'][0]['last_updated'],data['download']['last_updated'])

    def test_reject_builtin_attributes_null_values_and_incomplete_results(self):
        for edit in ('bytes','server','state','missing','old','skew','boolean'):
            with self.subTest(edit=edit):
                data=rows()
                if edit=='bytes':data['download']['attributes'].pop('bytes_received')
                if edit=='server':data['upload']['attributes']['server_id']='13098'
                if edit=='state':data['ping']['state']='nan'
                if edit=='missing':data['download']['entity_id']='sensor.other'
                if edit=='old':data['upload']['last_updated']=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
                if edit=='skew':data['upload']['last_updated']=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
                if edit=='boolean':data['upload']['state']=True
                result=ookla.read('test',reader(data))
                self.assertFalse(result['fresh_values_confirmed'])
                self.assertEqual(result['readings'],[])

    def test_single_fixed_addon_start_without_update_entity_or_retry(self):
        response=Mock(status=200);response.read.return_value=b'[]'
        opener=MagicMock();opener.open.return_value.__enter__.return_value=response
        with patch.object(ookla,'build_opener',return_value=opener):ookla.start('test')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'http://supervisor/core/api/services/hassio/addon_start')
        self.assertEqual(request.data,b'{"addon": "6b87c29e_speedtest_addon"}')
        self.assertNotIn(b'update_entity',request.data)
        opener.open.side_effect=TimeoutError('secret-value')
        with patch.object(ookla,'build_opener',return_value=opener):
            with self.assertRaises(ookla.InternetTestError) as caught:ookla.start('test')
        self.assertNotIn('secret-value',str(caught.exception))
        self.assertEqual(opener.open.call_count,2)

    def test_new_result_all_three_required_and_stale_poll_does_not_pass(self):
        before=ookla.read('test',reader(rows()))
        start=Mock()
        original_read=ookla.read
        def begin(token):
            nonlocal after
            after=original_read('test',reader(rows()))
        after=None
        with patch.object(ookla,'read',side_effect=lambda *_: after if after else before):
            result=ookla.run('test',start_fn=begin,clock=lambda:0,wait=Mock())
        self.assertTrue(result['fresh_values_confirmed'])
        ticks=iter([0,0,151])
        with patch.object(ookla,'read',return_value=before):
            with self.assertRaises(ookla.InternetTestError):
                ookla.run('test',start_fn=start,clock=lambda:next(ticks),wait=Mock())
        start.assert_called_once()

    def test_pilot_selected_provider_avoids_cloudflare_and_gateway(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);options=root/'options.json'
            options.write_text('{"green_speedtest_provider":"ookla_addon"}')
            pilot=Pilot(root,options)
            result=ookla.read('test',reader(rows()))
            self.assertIn('Run Ookla Speed Test',page(pilot))
            with patch('agent.threading.Thread') as thread:
                pilot.start_network_job('internet_test')
                job=thread.call_args.kwargs['target']
            snapshot={'source':'green','checked_at':ookla.stamp(),'readings':result['readings']}
            with patch('agent.run_ookla_addon',return_value=result) as run, patch('agent.read_ookla_addon',return_value=result), patch('agent.collect_green_network',return_value=snapshot), patch('agent.run_green_test') as cloudflare, patch('agent.run_gateway_test') as gateway:
                job();run.assert_called_once();cloudflare.assert_not_called();gateway.assert_not_called()
            self.assertIsNone(pilot.internet_test_error)
            self.assertEqual(pilot.internet_test['provider'],'ookla_addon')


if __name__=='__main__':unittest.main()
