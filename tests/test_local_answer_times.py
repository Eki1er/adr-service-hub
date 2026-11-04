import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
from diagnostic_analysis import local_answer_times as convert
from zoneinfo import ZoneInfo
from dashboard import configure_site_timezone, display_time

def local_answer_times(value):return convert(value,ZoneInfo("America/New_York"))

class LocalTimes(unittest.TestCase):
    def test_winter_previous_date_and_seconds(self):
        self.assertEqual(local_answer_times('2026-11-04T02:46:51+00:00'),'Nov 3, 2026 at 9:46:51 PM EST')
    def test_summer_daylight_time(self):
        self.assertEqual(local_answer_times('2026-07-04T02:46:51Z'),'Jul 3, 2026 at 10:46:51 PM EDT')
    def test_repeated_fall_hour_keeps_offset_distinction(self):
        self.assertTrue(local_answer_times('2026-11-01T05:30:00Z').endswith('EDT'))
        self.assertTrue(local_answer_times('2026-11-01T06:30:00Z').endswith('EST'))
    def test_nested_copy_preserves_storage_numbers_and_unknown_zones(self):
        raw={'events':[{'reported_at':'2026-11-04T02:46:51.123Z','received_at':'2026-11-04T03:05:27Z'}],
            'seconds':300,'unknown':'2026-11-03T20:10:00','source_text':'Nov 3 20:10:00'}
        old=copy.deepcopy(raw);answer=local_answer_times(raw)
        self.assertEqual(raw,old);self.assertIn('EST',answer['events'][0]['received_at'])
        self.assertEqual(answer['seconds'],300);self.assertEqual(answer['unknown'],raw['unknown'])
        self.assertEqual(answer['source_text'],raw['source_text'])

    def test_device_location_not_technician_timezone(self):
        self.assertEqual(convert('2026-11-04T02:46:51Z',ZoneInfo('America/Los_Angeles')),'Nov 3, 2026 at 6:46:51 PM PST')
        self.assertEqual(convert('2026-11-04T02:46:51Z',ZoneInfo('Asia/Tokyo')),'Nov 4, 2026 at 11:46:51 AM JST')
        try:
            configure_site_timezone({'site_timezone':'America/Los_Angeles'})
            self.assertIn('PST',display_time('2026-11-04T02:46:51Z'))
            configure_site_timezone({'site_timezone':'Bad/Zone'})
            self.assertEqual(display_time('2026-11-04T02:46:51Z'),'Configure site timezone')
        finally:configure_site_timezone({})
