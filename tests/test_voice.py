import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]/'adr_service_agent'
sys.path.insert(0,str(ROOT))
import voice_input as voice
import diagnostic_hub as hub
from agent import Pilot, Handler, page, VIEW_SCRIPT, VIEW_SCRIPT_HASH
from network_diagnostics import DiagnosticError

AUDIO=b'\x00\x00\x00\x18ftypM4A '+b'\x00'*32

class Voice(unittest.TestCase):
    def options(self):return {'voice_enabled':True,'cloud_analysis_enabled':True,'openai_api_key':'fake-test-key'}
    def test_single_multipart_request_fixed_endpoint_and_safe_filename(self):
        conn=Mock();conn.getresponse.return_value=SimpleNamespace(status=200,read=Mock(return_value=b'{"text":"Check the office"}'))
        factory=Mock(return_value=conn)
        text=voice.transcribe(self.options(),AUDIO,'audio/mp4;codecs=mp4a',True,factory)
        self.assertEqual(text,'Check the office');factory.assert_called_once()
        self.assertEqual(factory.call_args.args,('api.openai.com',443));self.assertEqual(factory.call_args.kwargs['timeout'],45)
        args,kw=conn.request.call_args;self.assertEqual(args,('POST','/v1/audio/transcriptions'))
        self.assertIn(b'gpt-transcribe',kw['body']);self.assertIn(b'filename="question.mp4"',kw['body'])
        self.assertIn(AUDIO,kw['body']);self.assertNotIn('fake-test-key',kw['body'].decode(errors='replace'))
        conn.getresponse.return_value.read.assert_called_once_with(32769);conn.close.assert_called_once()

    def test_opt_in_and_bounded_supported_audio(self):
        factory=Mock()
        for opt,audio,mime,confirmed in [(dict(self.options(),voice_enabled=False),AUDIO,'audio/mp4',True),
            (dict(self.options(),cloud_analysis_enabled=False),AUDIO,'audio/mp4',True),
            (self.options(),AUDIO,'audio/mp4',False),(self.options(),b'x'*12,'audio/mp4',True),
            (self.options(),AUDIO,'text/plain',True),(self.options(),b'x'*(voice.MAX_AUDIO+1),'audio/mp4',True)]:
            with self.subTest(mime=mime),self.assertRaises(DiagnosticError):voice.transcribe(opt,audio,mime,confirmed,factory)
        factory.assert_not_called()

    def test_error_responses_are_safe_without_retries(self):
        for status,body in [(403,b'fake-test-key secret'),(429,b'raw private error'),(200,b'{"text":""}'),
            (200,json.dumps({'text':'a'*1001}).encode()),(200,b'{"text":"ok"}'+b' '*32769),(200,b'not-json')]:
            c=Mock();c.getresponse.return_value=SimpleNamespace(status=status,read=lambda _:body)
            with self.subTest(status=status),self.assertRaises(DiagnosticError) as error:
                voice.transcribe(self.options(),AUDIO,'audio/mp4',True,Mock(return_value=c))
            self.assertNotIn('fake-test-key',str(error.exception));self.assertNotIn('raw private',str(error.exception))
            c.request.assert_called_once();c.close.assert_called_once()

    def test_response_redacts_key_and_connect_errors(self):
        c=Mock();c.getresponse.return_value=SimpleNamespace(status=200,read=lambda _:b'{"text":"fake-test-key"}')
        self.assertEqual(voice.transcribe(self.options(),AUDIO,'audio/mp4',True,Mock(return_value=c)),'[redacted]')
        with self.assertRaises(DiagnosticError):voice.transcribe(self.options(),AUDIO,'audio/mp4',True,Mock(side_effect=OSError('private')))

class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.options={'voice_enabled':True,'cloud_analysis_enabled':True,'openai_api_key':'fake-test-key','automatic_monitoring':False}
        (self.root/'options.json').write_text(json.dumps(self.options));self.pilot=Pilot(self.root,self.root/'options.json')
    def inline(self,**kw):return SimpleNamespace(start=kw['target'])

    def test_transcription_does_not_analyze_or_control_and_does_not_store_audio(self):
        with patch.object(hub,'transcribe',return_value='Check the office') as transcribe,patch.object(hub,'ask') as ask:
            self.pilot.diagnostics.start_voice(AUDIO,'audio/mp4',True,'recording_1',self.inline)
            transcribe.assert_called_once();ask.assert_not_called()
        s=self.pilot.diagnostics.status();self.assertEqual(s['transcript'],'Check the office');self.assertEqual(s['transcript_id'],'recording_1')
        self.assertFalse(s['busy']);self.assertEqual(s['answer'],'')
        files=list((self.root/'diagnostics').glob('*'));self.assertEqual([f.name for f in files],['voice_attempts.json'])
        self.assertNotIn(AUDIO,files[0].read_bytes())
        restarted=hub.Hub(self.root,self.options,lambda:{})
        self.assertEqual(restarted.voice_quota['count'],1);self.assertEqual(restarted.transcript,'')

    def test_voice_attempts_and_busy_block_other_jobs(self):
        factory=Mock(return_value=SimpleNamespace(start=lambda:None))
        self.pilot.diagnostics.start_voice(AUDIO,'audio/mp4',True,'recording',factory)
        self.assertTrue(self.pilot.monitoring_busy())
        with self.assertRaises(DiagnosticError):self.pilot.diagnostics.start('ask',{'question':'test','confirm':'yes'})
        with self.assertRaises(DiagnosticError):self.pilot.diagnostics.start_voice(AUDIO,'audio/mp4',True,'another',factory)
        self.assertEqual(self.pilot.diagnostics.voice_quota['count'],1)

    def test_reviewed_question_can_be_submitted_immediately(self):
        h=self.pilot.diagnostics;h.result={'kind':'target_checks','targets':[],'dns':{'status':'resolved'}}
        with patch.object(hub,'transcribe',return_value='Question'),patch.object(hub,'ask',return_value='Answer') as ask:
            h.start_voice(AUDIO,'audio/mp4',True,'recording',self.inline)
            h.start('ask',{'question':'Question','confirm':'yes'},self.inline)
            ask.assert_called_once();self.assertEqual(h.answer,'Answer')

    def test_quota_is_separate_bounded_and_reserved_on_failure(self):
        h=self.pilot.diagnostics
        with self.assertRaises(DiagnosticError):h.start_voice(AUDIO,'audio/mp4',True,'recording',Mock(side_effect=RuntimeError('secret')))
        self.assertFalse(h.busy);self.assertEqual(h.voice_quota['count'],1);self.assertEqual(h.quota,{})
        h.last_voice_started=0;h.voice_quota['count']=100
        with patch.object(hub,'transcribe') as transcribe:
            with self.assertRaises(DiagnosticError):h.start_voice(AUDIO,'audio/mp4',True,'recording',self.inline)
            transcribe.assert_not_called()

    def handler(self,audio=AUDIO,peer='172.30.32.2',csrf=True):
        h=Handler.__new__(Handler);h.server=SimpleNamespace(pilot=self.pilot);h.client_address=(peer,123)
        h.path='/voice-transcribe';h.rfile=io.BytesIO(audio);h.wfile=io.BytesIO();h.connection=Mock()
        h.headers={'Content-Length':str(len(audio)),'Content-Type':'audio/mp4','X-ADR-Voice-Consent':'yes',
            'X-ADR-Recording-ID':'recording','X-ADR-CSRF':self.pilot.csrf if csrf else 'bad'}
        h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();h.send_error=Mock();return h

    def test_peer_csrf_and_consent_before_reading_recording(self):
        with patch.object(self.pilot.diagnostics,'start_voice') as start:
            h=self.handler(peer='127.0.0.1');h.headers['X-Forwarded-For']='172.30.32.2';h.do_POST();h.send_error.assert_called_once()
            self.assertEqual(h.rfile.tell(),0)
            h=self.handler(csrf=False);h.do_POST();self.assertEqual(h.rfile.tell(),0);h.send_response.assert_called_with(403)
            h=self.handler();h.headers['X-ADR-Voice-Consent']='no';h.do_POST();self.assertEqual(h.rfile.tell(),0)
            start.assert_not_called()

    def test_authenticated_upload_and_size_and_operation_guards(self):
        with patch.object(self.pilot.diagnostics,'start_voice') as start:
            h=self.handler();h.do_POST();start.assert_called_once_with(AUDIO,'audio/mp4',True,'recording');h.send_response.assert_called_with(202)
            start.reset_mock();h=self.handler();h.headers['Content-Length']=str(voice.MAX_AUDIO+1);h.do_POST();self.assertEqual(h.rfile.tell(),0)
            self.pilot.active=True;h=self.handler();h.do_POST();self.assertEqual(h.rfile.tell(),0);start.assert_not_called()

    def test_voice_controls_script_hash_and_no_key_in_page(self):
        import base64,hashlib
        text=page(self.pilot)
        for name in ('adr-record','adr-read-answer','adr-use-transcript'):self.assertIn('id="'+name+'"',text)
        self.assertNotIn('fake-test-key',text)
        self.assertEqual(VIEW_SCRIPT_HASH,base64.b64encode(hashlib.sha256(VIEW_SCRIPT.encode()).digest()).decode())
        self.assertIn("voice-transcribe",VIEW_SCRIPT)
