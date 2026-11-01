"""One bounded audio transcription request. No recordings are written to disk."""
import http.client
import json
import re
import secrets
import ssl
from network_diagnostics import DiagnosticError

MAX_AUDIO=2*1024*1024
TYPES={'audio/mp4':'mp4','audio/webm':'webm','audio/wav':'wav','audio/mpeg':'mp3'}


def validate(audio, content_type):
    mime=content_type.split(';',1)[0].strip().lower()
    if mime not in TYPES or not isinstance(audio,bytes) or not 12<=len(audio)<=MAX_AUDIO:
        raise DiagnosticError('Use an MP4, WebM, WAV or MP3 recording smaller than 2 MB.')
    signatures={'audio/mp4':audio[4:8]==b'ftyp','audio/webm':audio[:4]==b'\x1a\x45\xdf\xa3',
        'audio/wav':audio[:4]==b'RIFF' and audio[8:12]==b'WAVE',
        'audio/mpeg':audio[:3]==b'ID3' or (audio[0]==255 and audio[1]&224==224)}
    if not signatures[mime]:raise DiagnosticError('Recording format could not be verified. Try recording again.')
    return mime


def transcribe(options,audio,content_type,confirmed,connection_factory=http.client.HTTPSConnection):
    if options.get('voice_enabled') is not True or options.get('cloud_analysis_enabled') is not True or not confirmed:
        raise DiagnosticError('Enable voice and confirm sending this recording.')
    mime=validate(audio,content_type)
    key=options.get('openai_api_key','')
    if not isinstance(key,str) or not re.fullmatch(r'[\x21-\x7e]{1,1024}',key):
        raise DiagnosticError('Set the API key in Configuration.')
    boundary='adr-'+secrets.token_hex(24)
    head=('--'+boundary+'\r\nContent-Disposition: form-data; name="model"\r\n\r\ngpt-transcribe\r\n'
        '--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="question.'+TYPES[mime]+'"\r\n'
        'Content-Type: '+mime+'\r\n\r\n').encode()
    body=head+audio+('\r\n--'+boundary+'--\r\n').encode()
    connection=None
    try:
        connection=connection_factory('api.openai.com',443,context=ssl.create_default_context(),timeout=45)
        connection.request('POST','/v1/audio/transcriptions',body=body,
            headers={'Authorization':'Bearer '+key,'Content-Type':'multipart/form-data; boundary='+boundary})
        response=connection.getresponse();raw=response.read(32769)
        if response.status in (401,403):
            raise DiagnosticError('Voice access was rejected. Check the API key’s audio transcription permission and model access. No retry was made.')
        if response.status!=200 or len(raw)>32768:
            raise DiagnosticError('Transcription could not complete. Check API access and billing. No retry was made.')
        text=json.loads(raw).get('text')
        if not isinstance(text,str) or not 1<=len(text.strip())<=1000:
            raise DiagnosticError('No usable question received. Try a shorter recording or type the question.')
        return text.strip().replace(key,'[redacted]')
    except DiagnosticError:raise
    except Exception:
        raise DiagnosticError('Transcription could not complete. No retry was made.') from None
    finally:
        if connection is not None:connection.close()
