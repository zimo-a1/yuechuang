"""Real local import/binding with synthetic text only; no supplier calls."""
import io
import json
import zipfile
from copy import deepcopy
from uuid import uuid4
import httpx
import pytest
from core.ip_parser import parse_novel, chapter_index
from core.ip_library import IPLibrary
from core.orchestrator import WorkflowEngine
from upload_validation import InvalidUpload

META={'title':'测试用星港档案','author':'合成测试','genre':'科幻','synopsis':'只用于功能验收的合成文本','rights_status':'owned','rights_basis':'程序合成测试文本','rights_scope':'本地功能测试','content_scope':'full'}
TEXT='前言说明\n第一章 港口\n林舟看向远处的信号灯。\n第二章 来信\n信中记着返航的时间。\n'


async def test_catalog_order_pagination_rights_and_repeat_preserve_edits(api):
    client, engine, library = api
    records = [{'title': f'测试目录 {n}', 'author': '测试作者', 'genre': '测试', 'synopsis': '测试资料',
                'catalog': {'source': '示例作品目录', 'source_url': 'https://catalog.example.com/yuechuang/ip-library/home',
                            'source_id': f'test-{n}', 'position': n, 'fetched_at': '2026-10-01T00:00:00Z', 'cover_path': ''}}
               for n in range(1, 51)]
    assert library.import_catalog(records) == {'created': 50, 'skipped': 0}
    rows = []
    for page, count in ((1, 24), (2, 24), (3, 2)):
        response = await client.get(f'/api/ip?page={page}')
        assert response.status_code == 200
        result = response.json()
        assert result['total'] == 50 and len(result['items']) == count
        rows.extend(result['items'])
    assert [row['title'] for row in rows] == [r['title'] for r in records]
    assert all(row['availability'] == 'missing' and row['rights_status'] == 'pending' and not row['latest_version'] for row in rows)
    ip_id = rows[0]['id']
    metadata = {k: v for k, v in META.items() if k != 'content_scope'}
    library.update(ip_id, metadata, 1)
    parsed = {'text': TEXT, 'characters': len(TEXT), 'line_count': len(TEXT.splitlines()),
              'encoding': 'utf-8', 'warnings': [], 'chapters': chapter_index(TEXT)}
    library.import_file({'content_scope': 'full'}, parsed, TEXT.encode(), 'test.txt', str(uuid4()), ip_id, 2)
    before = library.detail(ip_id)
    assert library.import_catalog(records) == {'created': 0, 'skipped': 50}
    assert IPLibrary(library.path.parent).detail(ip_id) == before
    assert before['item']['catalog'] == records[0]['catalog'] and not engine.list_saved_sessions()
    forged = await client.post('/api/ip', data={'metadata': json.dumps({**META, 'catalog': records[0]['catalog']})}, headers={'Idempotency-Key': str(uuid4())})
    assert forged.status_code == 422


def test_catalog_snapshot_validation(tmp_path):
    from scripts.import_ip_catalog import read_records
    from pydantic import ValidationError
    catalog = {'source': '测试', 'source_url': 'https://catalog.example.com/yuechuang/ip-library/home',
               'source_id': '1', 'position': 1, 'fetched_at': '2026-10-01', 'cover_path': ''}
    file = tmp_path / 'catalog.json'
    file.write_text(json.dumps([{'title': '测试', 'catalog': catalog}]))
    assert len(read_records(file)) == 1
    for changes in ({'source_url': 'javascript:alert(1)'}, {'source_url': 'https://catalog.example.com.evil.test/'},
                    {'cover_path': '/ip-covers/../../private.png'}, {'cover_path': 'https://external.test/cover.jpg'}):
        file.write_text(json.dumps([{'title': '测试', 'catalog': {**catalog, **changes}}]))
        with pytest.raises(ValidationError):
            read_records(file)
    file.write_text(json.dumps([{'title': '测试', 'catalog': catalog}] * 2))
    with pytest.raises(ValueError, match='不得重复'):
        read_records(file)


async def test_genre_filter_covers_all_pages_and_combines_with_search_status(api):
    client, _, library = api
    records = [{'title': f'航线 {n}', 'author': '特定作者' if n == 40 else '合成作者',
                'genre': '都市' if n <= 30 else '科幻', 'synopsis': '科幻题材研究者的故事',
                'catalog': {'source': '测试目录', 'source_url': 'https://catalog.example.com/yuechuang/ip-library/home',
                            'source_id': f'genre-{n}', 'position': n, 'fetched_at': '2026-10-01', 'cover_path': ''}}
               for n in range(1, 51)]
    library.import_catalog(records)
    all_data = (await client.get('/api/ip')).json()
    assert all_data['genres'] == ['都市', '科幻']
    assert all(item['genre'] == '都市' for item in all_data['items'])
    selected = (await client.get('/api/ip', params={'genre': '科幻'})).json()
    assert selected['total'] == 20 and len(selected['items']) == 20
    assert all(item['genre'] == '科幻' for item in selected['items'])
    page2 = (await client.get('/api/ip', params={'genre': '都市', 'page': 2})).json()
    assert page2['total'] == 30 and len(page2['items']) == 6
    combined = (await client.get('/api/ip', params={'genre': '科幻', 'q': '特定作者', 'status': 'missing'})).json()
    assert combined['total'] == 1 and combined['items'][0]['title'] == '航线 40'
    for params in ({'genre': '科幻', 'status': 'ready'}, {'genre': '不存在类型'}):
        empty = (await client.get('/api/ip', params=params)).json()
        assert empty['total'] == 0 and empty['genres'] == all_data['genres']
    assert (await client.get('/api/ip', params={'genre': '字' * 81})).status_code == 422

@pytest.fixture
async def api(tmp_path,monkeypatch):
    from api.app import app
    from api.routers import ip_library,workflow,sessions
    engine=WorkflowEngine(session_dir=str(tmp_path/'legacy'),database_path=tmp_path/'workflow.sqlite3')
    library=IPLibrary(tmp_path/'library')
    monkeypatch.setattr(ip_library,'get_library',lambda:library)
    for module in (ip_library,workflow,sessions):monkeypatch.setattr(module,'workflow_engine',engine)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        yield client,engine,library
    engine.store.close()

async def upload(client,text=TEXT,metadata=None,key=None):
    return await client.post('/api/ip',data={'metadata':json.dumps(metadata or META)},files={'file':('novel.txt',text.encode())},headers={'Idempotency-Key':key or str(uuid4())})

@pytest.mark.parametrize('size',[30000,50000,100000,6_500_000])
def test_full_text_coverage_and_tail(tmp_path,size):
    text=('第一章 开始\n'+'角色走过长廊。\n'*(size//8+1))[:size-15]+'\n第二章 终点\n尾字核对。'
    path=tmp_path/'novel.txt';path.write_text(text,encoding='utf-8');parsed=parse_novel(path)
    assert parsed['text']==text and parsed['text'].endswith('尾字核对。')
    assert ''.join(text[c['start']:c['end']] for c in parsed['chapters'])==text
    assert parsed['chapters'][0]['start']==0 and parsed['chapters'][-1]['end']==len(text)

@pytest.mark.parametrize('encoding',['utf-8-sig','gbk','utf-16'])
def test_encodings_and_crlf(tmp_path,encoding):
    path=tmp_path/'source.txt';path.write_bytes(TEXT.replace('\n','\r\n').encode(encoding))
    assert parse_novel(path)['text']==TEXT

def test_gb18030_and_prose_not_mistaken_for_volume(tmp_path):
    text = '第一卷 开端\n第1章 登场\n第一部分，是对乐曲的叙述。\n第二部分，继续叙述。\n第三部分，仍是正文。\n第2章 结尾\n罕见字符：𠀀\n'
    path = tmp_path / 'novel.txt'
    path.write_bytes(text.encode('gb18030'))
    parsed = parse_novel(path)
    assert parsed['text'] == text and parsed['encoding'] == 'gb18030'
    assert [c['title'] for c in parsed['chapters']] == ['第一卷 开端', '第1章 登场', '第2章 结尾']

def test_large_text_limit_still_rejects_without_truncation(tmp_path):
    from core.ip_parser import MAX_CHARACTERS
    path = tmp_path / 'too-long.txt'
    path.write_text('a' * (MAX_CHARACTERS + 1), encoding='utf-8')
    with pytest.raises(InvalidUpload, match='1000 万'):
        parse_novel(path)

async def test_local_cover_featured_order_and_metadata_preservation(api):
    from core.ip_library import LibraryError
    client, engine, library = api
    first = (await upload(client)).json()['item']
    library.set_presentation(first['id'], '/ip-covers/local-example.png', True, first['revision'])
    await upload(client, metadata={**META, 'title':'后来导入的作品'})
    listing = (await client.get('/api/ip')).json()
    assert listing['items'][0]['id'] == first['id']
    assert listing['items'][0]['cover_path'] == '/ip-covers/local-example.png'
    update = {k:v for k,v in META.items() if k != 'content_scope'}
    updated = await client.patch('/api/ip/' + first['id'], json={**update,'title':'示例改名','expected_revision':2})
    assert updated.status_code == 200
    assert updated.json()['item']['featured'] is True
    assert updated.json()['item']['cover_path'] == '/ip-covers/local-example.png'
    assert updated.json()['versions'][0]['characters'] == len(TEXT)
    for path in ('/ip-covers/../private.png', 'https://external.test/image.jpg'):
        with pytest.raises(LibraryError):
            library.set_presentation(first['id'], path, True, 3)
    assert IPLibrary(library.path.parent).list()['items'][0]['id'] == first['id']

def docx_bytes(body):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        z.writestr('[Content_Types].xml','<Types/>')
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'+body+'</w:body></w:document>')
    return out.getvalue()

def test_docx_paragraph_table_order_and_no_silent_omissions(tmp_path):
    path=tmp_path/'novel.docx'
    path.write_bytes(docx_bytes('<w:p><w:r><w:t>第一章 开端</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格正文</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>末尾</w:t></w:r></w:p>'))
    assert parse_novel(path)['text']=='第一章 开端\n表格正文\n末尾'
    path.write_bytes(docx_bytes('<w:p><w:ins><w:r><w:t>未接受修订</w:t></w:r></w:ins></w:p>'))
    with pytest.raises(InvalidUpload):parse_novel(path)

def test_invalid_files_fallback_and_boundaries(tmp_path):
    path=tmp_path/'bad.docx';path.write_bytes(b'not a zip')
    with pytest.raises(InvalidUpload):parse_novel(path)
    path=tmp_path/'bad.txt';path.write_bytes(b'\0\x01bad')
    with pytest.raises(InvalidUpload):parse_novel(path)
    path.write_text('没有章节标题\n正文仍须完整\n');parsed=parse_novel(path)
    assert parsed['warnings'] and parsed['chapters'][0]['end']==len(parsed['text'])
    rows=chapter_index(parsed['text'],[{'title':'第一部分','start_line':1},{'title':'第二部分','start_line':2}])
    assert parsed['text'][rows[1]['start']:rows[1]['end']]=='正文仍须完整\n'
    for boundaries in ([],[{'title':'错误','start_line':2}],[{'title':'一','start_line':1},{'title':'二','start_line':1}]):
        with pytest.raises(InvalidUpload):chapter_index(parsed['text'],boundaries)

async def test_import_replay_preview_original_and_restart(api):
    client,engine,library=api;key=str(uuid4());response=await upload(client,key=key)
    assert response.status_code==201,response.text
    data=response.json();ip_id=data['item']['id'];version=data['versions'][0]
    assert data['item']['availability']=='ready' and data['item']['chapter_count']==3
    assert 'text' not in version and 'original' not in version
    assert (await upload(client,key=key)).status_code==200
    assert (await upload(client,text=TEXT+'不同正文',key=key)).status_code==409
    content=''
    for chapter in version['chapters']:
        result=await client.get(f"/api/ip/{ip_id}/versions/{version['id']}/chapters/{chapter['id']}")
        assert result.status_code==200;content+=result.json()['text']
    assert content==TEXT
    original=await client.get(f"/api/ip/{ip_id}/versions/{version['id']}/original")
    assert original.content==TEXT.encode() and 'attachment' in original.headers['content-disposition']
    assert IPLibrary(library.path.parent).detail(ip_id)==library.detail(ip_id)
    assert not engine.list_saved_sessions()

async def test_placeholder_and_rights_gate_and_errors(api):
    client,engine,library=api;headers={'Idempotency-Key':str(uuid4())}
    placeholder=await client.post('/api/ip',data={'metadata':json.dumps(META)},headers=headers)
    assert placeholder.status_code==201 and placeholder.json()['item']['availability']=='missing'
    result=(await upload(client,metadata={**META,'rights_status':'pending'})).json();ip_id=result['item']['id'];v=result['versions'][0]
    body={'version_id':v['id'],'expected_revision':1,'title':'改编项目','direction':'测试','first_chapter':1,'last_chapter':2}
    denied=await client.post(f'/api/ip/{ip_id}/projects',json=body,headers={'Idempotency-Key':str(uuid4())})
    assert denied.status_code==409 and denied.json()['error']['code']=='IP_NOT_READY'
    assert not engine.list_saved_sessions()
    invalid=await client.post('/api/ip',data={'metadata':'秘密不可回显'},headers=headers)
    assert invalid.status_code==422 and '秘密不可回显' not in invalid.text and 'error' in invalid.json()
    invalid=await client.patch(f'/api/ip/{ip_id}',json={'title':'敏感内容不可回显'})
    assert invalid.status_code==422 and '敏感内容' not in invalid.text
    assert (await client.get('/api/ip/missing')).status_code==404
    unsupported=await client.post('/api/ip',data={'metadata':json.dumps(META)},files={'file':('bad.pdf',b'%PDF')},headers=headers)
    assert unsupported.status_code==422

async def test_binding_multiple_projects_version_update_and_inheritance(api):
    client,engine,library=api;data=(await upload(client)).json();ip_id=data['item']['id'];v=data['versions'][0]
    body={'version_id':v['id'],'expected_revision':1,'title':'星港试制','direction':'聚焦来信','first_chapter':2,'last_chapter':3,'episodes':3,'target_duration':90}
    key=str(uuid4());result=await client.post(f'/api/ip/{ip_id}/projects',json=body,headers={'Idempotency-Key':key})
    assert result.status_code==201,result.text
    item=result.json();sid=item['session_id'];binding=deepcopy(item['meta']['ip_binding'])
    assert item['meta']['source_type']=='ip' and not item['meta']['idea'] and binding['start']==v['chapters'][1]['start']
    assert binding['rights_basis']==META['rights_basis'] and engine.store.jobs(sid)==[]
    another=await client.post(f'/api/ip/{ip_id}/projects',json={**body,'title':'另一个方向'},headers={'Idempotency-Key':str(uuid4())})
    assert another.status_code==201 and another.json()['session_id']!=sid
    episode=await client.post(f'/api/projects/{sid}/episodes',json={},headers={'Idempotency-Key':str(uuid4())})
    assert episode.status_code==201
    assert episode.json()['meta']['ip_binding']==binding and episode.json()['meta']['source_type']=='ip'
    from core.workflow_rules import WorkflowConflict
    with pytest.raises(WorkflowConflict,match='长篇分析'):
        engine.prepare_stage_execution(episode.json()['session_id'],'script_generation',{'ip_binding':None})
    new=await client.post(f'/api/ip/{ip_id}/versions',data={'expected_revision':1,'content_scope':'excerpt'},files={'file':('v2.txt',(TEXT+'新版结尾').encode())},headers={'Idempotency-Key':str(uuid4())})
    assert new.status_code==200 and len(new.json()['versions'])==2
    assert engine.get_state(sid).meta['ip_binding']==binding
    replay=await client.post(f'/api/ip/{ip_id}/projects',json=body,headers={'Idempotency-Key':key})
    assert replay.status_code==200 and replay.json()['session_id']==sid
    detail=(await client.get(f'/api/ip/{ip_id}')).json()
    assert len(detail['projects'])==2 and all(p['version_id']==v['id'] for p in detail['projects'])

async def test_edit_chapters_new_version_conflict_and_search(api):
    client,engine,library=api;data=(await upload(client)).json();ip_id=data['item']['id'];v=data['versions'][0]
    body={'expected_revision':1,'version_id':v['id'],'chapters':[{'title':'开场','start_line':1},{'title':'信号','start_line':3}]};headers={'Idempotency-Key':str(uuid4())}
    response=await client.post(f'/api/ip/{ip_id}/chapters',json=body,headers=headers)
    assert response.status_code==200,response.text
    assert len(response.json()['versions'])==2 and response.json()['item']['revision']==2
    new=response.json()['versions'][0];assert new['text_sha256']==v['text_sha256'] and new['sha256']==v['sha256']
    assert library.version(ip_id,v['id'])['chapters']==v['chapters']
    assert (await client.post(f'/api/ip/{ip_id}/chapters',json=body,headers=headers)).status_code==200
    assert (await client.post(f'/api/ip/{ip_id}/chapters',json=body,headers={'Idempotency-Key':str(uuid4())})).status_code==409
    metadata={k:v for k,v in META.items() if k!='content_scope'}
    assert (await client.patch(f'/api/ip/{ip_id}',json={**metadata,'expected_revision':1})).status_code==409
    assert (await client.get('/api/ip?q=星港&status=ready')).json()['total']==1
    assert (await client.get('/api/ip?q=不存在')).json()['total']==0

async def test_paged_chapter_reconstruction_and_size_guard(api,monkeypatch):
    client,engine,library=api;text='没有分章标题\n'+'中文测试🙂'*2200
    data=(await upload(client,text=text)).json();v=data['versions'][0];ip_id=data['item']['id'];chunks=[];offset=0
    while offset is not None:
        result=(await client.get(f"/api/ip/{ip_id}/versions/{v['id']}/chapters/ch-1?offset={offset}&limit=1000")).json()
        chunks.append(result['text']);offset=result['next_offset']
    assert ''.join(chunks)==text
    from api.routers import ip_library
    monkeypatch.setattr(ip_library,'LIMIT',10)
    denied=await upload(client)
    assert denied.status_code==413 and library.list()['total']==1

async def test_transactions_reject_partial_write(api,monkeypatch):
    client,engine,library=api
    import sqlite3
    def fail(*args,**kwargs):raise sqlite3.OperationalError('private path must not appear')
    monkeypatch.setattr(library,'_add_version',fail)
    result=await upload(client)
    assert result.status_code==500 and 'private path' not in result.text
    assert library.list()['total']==0

async def test_selected_version_readiness_invalid_range_and_direct_generation(api):
    client,engine,library=api
    data=(await upload(client,metadata={**META,'content_scope':'unknown'})).json();ip_id=data['item']['id'];old=data['versions'][0]
    new=(await client.post(f'/api/ip/{ip_id}/versions',data={'expected_revision':1,'content_scope':'full'},files={'file':('new.txt',TEXT.encode())},headers={'Idempotency-Key':str(uuid4())})).json()['versions'][0]
    body={'version_id':old['id'],'expected_revision':2,'title':'测试项目','direction':'测试','first_chapter':1,'last_chapter':1}
    assert (await client.post(f'/api/ip/{ip_id}/projects',json=body,headers={'Idempotency-Key':str(uuid4())})).status_code==409
    body.update(version_id=new['id'],first_chapter=3,last_chapter=2)
    assert (await client.post(f'/api/ip/{ip_id}/projects',json=body,headers={'Idempotency-Key':str(uuid4())})).status_code==422
    body.update(first_chapter=1,last_chapter=3)
    parent=(await client.post(f'/api/ip/{ip_id}/projects',json=body,headers={'Idempotency-Key':str(uuid4())})).json()['session_id']
    episode=(await client.post(f'/api/projects/{parent}/episodes',json={},headers={'Idempotency-Key':str(uuid4())})).json()['session_id']
    denied=await client.post(f'/api/project/{episode}/execute/script_generation',json={'idea':'attempt bypass','ip_binding':None})
    assert denied.status_code==409 and denied.json()['error']['code']=='IP_ANALYSIS_PENDING'
    from core.tasks import TaskManager
    from core.workflow_rules import WorkflowConflict
    manager=TaskManager(engine)
    try:
        with pytest.raises(WorkflowConflict):manager.submit(episode,'script_generation',{})
        assert not engine.store.jobs()
    finally:await manager.shutdown()

@pytest.mark.parametrize('case',['comments','footnotes','header1'])
def test_docx_auxiliary_text_is_not_silently_dropped(tmp_path,case):
    path=tmp_path/'notes.docx';path.write_bytes(docx_bytes('<w:p><w:r><w:t>正文</w:t></w:r></w:p>'))
    with zipfile.ZipFile(path,'a') as z:
        z.writestr(f'word/{case}.xml','<w:root xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:t>不能漏掉的文字</w:t></w:root>')
    with pytest.raises(InvalidUpload):parse_novel(path)

def test_parser_limit_rejects_without_truncation(tmp_path,monkeypatch):
    from core import ip_parser
    monkeypatch.setattr(ip_parser,'MAX_CHARACTERS',10)
    path=tmp_path/'too-long.txt';path.write_text('这是一段超过长度限制的测试文字')
    with pytest.raises(InvalidUpload,match='未截断'):parse_novel(path)

def test_blank_preface_and_trailing_empty_chapter(tmp_path):
    path=tmp_path/'blank.txt';path.write_text('\n\n第一章 起点\n正文\n')
    parsed=parse_novel(path)
    assert len(parsed['chapters'])==1 and parsed['chapters'][0]['start']==0
    assert parsed['chapters'][0]['end']==len(parsed['text'])
    with pytest.raises(InvalidUpload,match='空白'):
        chapter_index('第一章\n正文\n',[{'title':'正文','start_line':1},{'title':'空章','start_line':3}])
