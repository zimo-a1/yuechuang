import assert from 'node:assert/strict';
import test from 'node:test';
import {settingsEqual,connectionDraft,defaultOptions,modelCapability,supportsField,type SettingsDocument,type ServiceConnection} from '../lib/settingsApi.ts';

const connection:ServiceConnection={id:'one',name:'离线账号一',kind:'video',provider:'tudou',base_url:'https://example.test',models:['grok'],enabled:true,enable_proxy:false,credentials:{api_key:true},test:{status:'untested',message:'未测试',tested_at:null}};
const catalog=[{id:'grok',name:'视频',provider:'tudou',type:['video'],capabilities:{api_contract_verified:true,adapter_ability_types:['text_to_video','start_end_i2v'],resolutions:['720P']}}];
test('editing keeps credential fields empty and clear remains an explicit operation',()=>{const draft=connectionDraft(connection);assert.deepEqual(draft.credentials,{});assert.deepEqual(draft.clear_credentials,[]);assert.equal(draft.name,connection.name);});
test('same model on two services remains separately selectable; disabled service is absent',()=>{const doc={connections:[connection,{...connection,id:'two',name:'离线账号二'},{...connection,id:'disabled',enabled:false}],catalog} as SettingsDocument;const result=defaultOptions(doc,'video_text');assert.deepEqual(result.map(r=>r.id),['svc/one/grok','svc/two/grok','grok']);assert.match(result[1].label,/账号二/);});
test('mode filters use explicit adapter metadata including legacy start-end label',()=>{assert.equal(supportsField(catalog[0],'video_start_end'),true);assert.equal(supportsField(catalog[0],'video_first_frame'),false);assert.equal(supportsField(catalog[0],'llm'),false);});
test('service alias shares known capabilities; unknown models remain unknown',()=>{assert.deepEqual(modelCapability('svc/one/grok',catalog)?.resolutions,['720P']);assert.equal(modelCapability('grok-new-model',catalog),undefined);});

test("saved defaults compare by value, independent of API field ordering",()=>{assert.equal(settingsEqual({models:{llm:"a"},language:"zh-CN",enable_concurrency:false},{enable_concurrency:false,language:"zh-CN",models:{llm:"a"}}),true);assert.equal(settingsEqual({models:{llm:"a"}},{models:{llm:"b"}}),false);});
