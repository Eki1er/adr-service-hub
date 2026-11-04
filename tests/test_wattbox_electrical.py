import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
from wattbox import WattBox,ProtocolError,parse_power_status
from power_history import ElectricalHistory
from dashboard_visuals import visuals

class MeteringTests(unittest.TestCase):
    def test_documented_field_order_and_read_only_query(self):
        box=WattBox({},None);box.channel=Mock();box._line=Mock(return_value='?PowerStatus=1.25,140.0,119.5,1')
        result=box.power_status()
        self.assertEqual(result,{'current_a':1.25,'power_w':140.0,'voltage_v':119.5,'safe_voltage':True})
        box.channel.sendall.assert_called_once_with(b'?PowerStatus\n')
        for text in ('1,2,3','nan,1,120,1','-1,1,120,1','1,2,1200,1','1,2,120,True','1,2,120,1,extra'):
            with self.assertRaises(ProtocolError):parse_power_status(text)
    def test_actual_units_persist_with_null_gap_and_target_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            now=[1793790000.0];path=Path(d)/'meter.json';history=ElectricalHistory(path,lambda:now[0])
            first=history.observe('a',parse_power_status('1.25,140,119.5,1'))
            self.assertEqual(first['series'][0]['points'][0]['value'],119.5)
            self.assertEqual(first['series'][1]['points'][0]['value'],1.25)
            now[0]+=1200;history.observe('a',None)
            now[0]+=1200;history=ElectricalHistory(path,lambda:now[0]);last=history.observe('a',parse_power_status('0,0,120,1'))
            self.assertEqual([p['value'] for p in last['series'][1]['points']],[1.25,None,0])
            self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(len(history.observe('b',None)['series'][0]['points']),1)
    def test_corrupt_storage_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'meter.json';path.write_text('{bad')
            result=ElectricalHistory(path).observe('a',None)
            self.assertTrue(result['error']);self.assertEqual(path.read_text(),'{bad')
    def test_graphs_use_electrical_units_and_ignore_saved_outlet_graphs(self):
        output=visuals('WattBox',{'outlets':[{'number':1,'on':True}],
            'electrical':parse_power_status('1.25,140,119.5,1'),
            'power_history':{'series':[{'name':'WattBox outlets reported on','points':[]}]},
            'electrical_history':{'series':[{'name':'WattBox voltage','unit':'V','points':[
                {'time':'2026-11-04T12:00:00+00:00','value':119.5}]}]}},[])
        self.assertIn('119.5 V',output);self.assertIn('1.25 A',output)
        self.assertIn('WattBox voltage history',output);self.assertNotIn('outlets reported on',output)
        self.assertNotIn('outlet-strip',output)
