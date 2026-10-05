"""Recorded public contract shapes, fake transport only; never billable requests."""
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

from config import Config
from core.service_settings import SettingsError, model_alias, validate_defaults
from models.media_toapis import ToAPIsClient, image_request, video_request, validate_reference
from tests.test_service_settings import settings_store, add  # noqa: F401

URL = 'https://assets.example.test/reference.png'

class Response:
    def __init__(self, data=None, status=200, headers=None):
        self.status_code, self.headers, self.value = status, headers or {}, data
    def json(self):
        return self.value

class Transport:
    proxies = {}
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr('models.media_toapis.time.sleep', lambda _: None)
    monkeypatch.setattr('models.media_toapis.random.uniform', lambda *a: 0)
    return lambda responses: ToAPIsClient('offline-only-secret', 'https://toapis.example.test/v1', transport=Transport(responses), poll_interval=0, max_wait=1)

@pytest.mark.parametrize('model', ['nano_banana','gpt-image-2'])
def test_image_contract(model):
    body = image_request('two people', model, [URL], '9:16', '2K')
    assert body['n'] == 1 and body['model'] == model and body['size'] == '9:16'
    if model == 'nano_banana':
        assert body['image_urls'] == [{'url':URL}]
        assert body['metadata'] == {'resolution':'1K'}
    else:
        assert body['reference_images'] == [URL]
        assert body['resolution'] == '2k'

@pytest.mark.parametrize('model,refs,prompt', [('nano_banana',[URL]*15,'x'),('gpt-image-2',[URL]*7,'x'),('nano_banana',[],'x'*1001),('gpt-image-2',[],'x'*32001)])
def test_images_fail_before_any_transport(model,refs,prompt):
    with pytest.raises(SettingsError): image_request(prompt, model, refs)

@pytest.mark.parametrize('model,resolution', [('grok-video-1.5','720P'),('MiniMax-H3-Max-Turbo','768P')])
def test_text_first_reference_modes(model,resolution):
    base = dict(model=model, resolution=resolution, duration=6)
    text = video_request('a scene', **base)
    first = video_request('a scene', image_path=URL, **base)
    reference = video_request('a scene', reference_image_paths=[URL], **base)
    if model.startswith('grok'):
        assert [b['video_generation_mode'] for b in (text,first,reference)] == ['text_to_video','first_frame_image_to_video','reference_images_to_video']
        assert first['image'] == URL and reference['reference_images'] == [URL]
    else:
        assert text['resolution'] == '768P'
        assert first['aspect_ratio'] == 'adaptive'
        assert first['image_with_roles'][0]['role'] == 'first_frame'
        assert reference['image_with_roles'][0]['role'] == 'reference_image'
        end = video_request('a scene', image_path=URL, last_image_path=URL, **base)
        assert [r['role'] for r in end['image_with_roles']] == ['first_frame','last_frame']

@pytest.mark.parametrize('options', [
 {'duration':1.5}, {'duration':True}, {'duration':16}, {'resolution':'1080P'},
 {'last_image_path':URL}, {'image_path':URL,'last_image_path':URL},
 {'image_path':URL,'reference_image_paths':[URL]}, {'reference_image_paths':[URL]*8},
 {'model':'MiniMax-H3-Max-Turbo','resolution':'720P'},
 {'model':'MiniMax-H3-Max-Turbo','resolution':'768P','duration':4},
])
def test_video_rejects_invalid_no_paid_request(options):
    with pytest.raises(SettingsError): video_request('scene', **options)

@pytest.mark.parametrize('url',['http://127.0.0.1/a.png','https://localhost/a.png','data:image/png;base64,xx','https://user:password@assets.example.test/a'])
def test_reference_validation(url):
    with pytest.raises(SettingsError): validate_reference(url)


def complete():
    return Response({'status':'completed','result':{'data':[{'url':'https://assets.example.test/result.png'}]}})

def test_async_pending_rate_limit_complete_and_receipt(client,tmp_path):
    c = client([Response({'id':'task_123'}),Response(status=429,headers={'Retry-After':'0'}),Response({'status':'in_progress'}),complete()])
    url,receipt,path = c.generate('images', image_request('x','gpt-image-2'), tmp_path)
    assert url.endswith('/result.png') and receipt['task_id']=='task_123'
    assert [x[0] for x in c.http.calls] == ['POST','GET','GET','GET']
    assert c.http.calls[0][1]=='https://toapis.example.test/v1/images/generations'
    assert c.http.calls[1][1].endswith('/task_123')
    stored = path.read_text()
    assert 'offline-only-secret' not in stored and 'prompt' not in stored


def test_submit_timeout_resumes_by_business_id_without_post(client,tmp_path):
    body=image_request('private prompt', 'gpt-image-2')
    c=client([requests.Timeout('must not leak')])
    with pytest.raises(SettingsError,match='不会自动重提'): c.generate('images',body,tmp_path)
    assert len(c.http.calls)==1
    restarted=client([complete()])
    _,receipt,_=restarted.generate('images',body,tmp_path)
    assert restarted.http.calls[0][0]=='GET'
    assert restarted.http.calls[0][1].endswith('/'+receipt['business_id'])


def test_download_failure_resumes_existing_task(client,tmp_path,monkeypatch):
    body=image_request('x','gpt-image-2')
    c=client([Response({'id':'t1'}),complete()])
    c.generate('images',body,tmp_path)
    resumed=client([complete()])
    resumed.generate('images',body,tmp_path)
    assert [r[0] for r in resumed.http.calls]==['GET']
    assert resumed.http.calls[0][1].endswith('/t1')


def test_local_upload_transforms_reference_and_deduplicates(client,tmp_path):
    ref=tmp_path/'reference.png';Image.new('RGB',(10,10)).save(ref)
    c=client([Response({'success':True,'data':{'url':URL}}),Response({'id':'t2'}),complete()])
    body=video_request('scene',model='MiniMax-H3-Max-Turbo',resolution='768P',image_path=str(ref),last_image_path=str(ref))
    c.generate('videos',body,tmp_path)
    uploads=[x for x in c.http.calls if '/uploads/' in x[1]]
    assert len(uploads)==1
    payload=c.http.calls[1][2]['json']
    assert [r['url'] for r in payload['image_with_roles']]==[URL,URL]
    assert payload['resolution']=='768P'


def test_provider_failure_redacted_and_no_create_retry(client,tmp_path):
    c=client([Response({'error':'secret provider details'},status=503)])
    with pytest.raises(SettingsError) as exc: c.generate('images',image_request('x','gpt-image-2'),tmp_path)
    assert 'secret provider details' not in str(exc.value)
    assert len(c.http.calls)==1


def test_named_services_route_exact_models_and_freeze(settings_store,monkeypatch,tmp_path):
    from models.image_client import ImageClient
    from models.video_client import VideoClient
    seen=[]
    monkeypatch.setattr(ToAPIsClient,'generate_image', lambda self,**kw: seen.append((self.api_key,self.base_url,kw)) or ['offline.png'])
    monkeypatch.setattr(ToAPIsClient,'generate_video', lambda self,**kw: seen.append((self.api_key,self.base_url,kw)) or 'offline.mp4')
    image,values=add(settings_store,provider='toapis',kind='image',models=['nano_banana','toapis/gpt-image-2'])
    video,_=add(settings_store,provider='toapis',kind='video',models=['grok-video-1.5','MiniMax-H3-Max-Turbo'])
    old=ImageClient()
    settings_store.save_connection(settings_store.read()['revision'],{**values,'credentials':{'api_key':'new-key'}},image['id'])
    for model in ['nano_banana','toapis/gpt-image-2']:
        old.generate_image('offline',model=model_alias(image['id'],model),save_dir=str(tmp_path))
        assert seen[-1][0]=='offline-test-only' and seen[-1][2]['model']==model.removeprefix('toapis/')
    for model,res in [('grok-video-1.5','720P'),('MiniMax-H3-Max-Turbo','768P')]:
        VideoClient().generate_video('offline',None,str(tmp_path/'a.mp4'),model=model_alias(video['id'],model),resolution=res,duration=6)
        assert seen[-1][2]['model']==model


def test_set_default_switches_resolution_atomically(settings_store):
    conn,_=add(settings_store,provider='toapis',kind='video',models=['grok-video-1.5','MiniMax-H3-Max-Turbo'])
    for model,res in [('MiniMax-H3-Max-Turbo','768P'),('grok-video-1.5','720P')]:
        doc=settings_store.set_default(settings_store.read()['revision'],conn['id'],model)
        assert doc['defaults']['video_resolution']==res
        validate_defaults(doc['defaults'],settings_store.read())


def test_download_validates_media_and_omits_credentials(client,tmp_path,monkeypatch):
    import io
    content=io.BytesIO();Image.new('RGB',(12,9)).save(content,format='PNG')
    calls=[]
    class Download:
        status_code=200
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def get(self,url,**kw): calls.append((url,kw)); return self
        def iter_content(self,*a): yield content.getvalue()
    monkeypatch.setattr('models.media_toapis.requests.Session',Download)
    c=client([]);target=tmp_path/'output.png'
    c.download(URL,target,'images')
    with Image.open(target) as image: assert image.size==(12,9)
    assert 'headers' not in calls[0][1]
    assert 'offline-only-secret' not in str(calls)
    content=io.BytesIO(b'<html>provider failure</html>')
    before=target.read_bytes()
    with pytest.raises(SettingsError,match='校验失败'): c.download(URL,target,'images')
    assert target.read_bytes()==before


def test_failed_task_stops_polling(client,tmp_path):
    c=client([Response({'id':'f1'}),Response({'status':'failed','error':{'message':'secret vendor body'}})])
    with pytest.raises(SettingsError,match='未自动重试'): c.generate('videos',video_request('scene'),tmp_path)
    assert len(c.http.calls)==2
    assert 'secret vendor body' not in ''.join(p.read_text() for p in tmp_path.rglob('*.json'))


def test_registry_preserves_original_openai_route(settings_store,monkeypatch,tmp_path):
    from models.config_model import MODEL_CONFIG
    from models.image_client import ImageClient
    from models.image_gpt import ImageGPT
    assert MODEL_CONFIG['models']['gpt-image-2']['provider']=='openai'
    assert MODEL_CONFIG['models']['toapis/gpt-image-2']['provider']=='toapis'
    monkeypatch.setattr(Config,'OPENAI_API_KEY','offline-key')
    result=tmp_path/'legacy.png';result.touch()
    seen=[]
    monkeypatch.setattr(ImageGPT,'generate_image',lambda self,**kw: seen.append(kw) or str(result))
    ImageClient().generate_image('offline',model='gpt-image-2',save_dir=str(tmp_path))
    assert seen[0]['model']=='gpt-image-2'

@pytest.mark.parametrize('kind',['character','reference'])
def test_agent_does_not_repeat_provider_failure(kind,tmp_path,monkeypatch):
    from core.agents.character_agent import CharacterDesignerAgent
    from core.agents.reference_agent import ReferenceGeneratorAgent
    class FailedClient:
        calls=0
        def generate_image(self,**kw):
            self.calls+=1
            raise SettingsError('TOAPIS_GENERATION_FAILED','生成失败，未自动重试')
    client=FailedClient()
    if kind=='character':
        agent=CharacterDesignerAgent()
        monkeypatch.setattr(agent,'_next_version_path',lambda *a:str(tmp_path/'a.png'))
        with pytest.raises(SettingsError):
            agent._generate_one(client,'a','name','description','props','realistic','human','toapis/gpt-image-2','unused','session',max_iterations=3)
    else:
        agent=ReferenceGeneratorAgent()
        monkeypatch.setattr(agent,'_next_version_path',lambda *a:str(tmp_path/'a.png'))
        with pytest.raises(SettingsError):
            agent._generate_one(client,'session',{'segment_id':'s1'},'prompt',[],'realistic','toapis/gpt-image-2','toapis/gpt-image-2',max_versions=3)
    assert client.calls==1
