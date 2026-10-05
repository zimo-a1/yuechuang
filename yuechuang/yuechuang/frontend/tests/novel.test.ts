import test from 'node:test';
import assert from 'node:assert/strict';
import {chapterProgress,accountEvents,novelActive,type NovelChunk,type NovelEvent,type NovelJob,type NovelOutline} from '../lib/novelApi.ts';

test('partial or failed chapters never show full progress',()=>{
  const chunks=[{status:'completed'},{status:'error'},{status:'interrupted'}] as NovelChunk[];
  assert.deepEqual(chapterProgress(chunks),{done:1,total:3,percent:33});
  assert.deepEqual(chapterProgress([]),{done:0,total:0,percent:0});
});
test('removing an event requires an omission reason while retaining previous reasons',()=>{
  const events=[{id:'a'},{id:'b'},{id:'c'}] as NovelEvent[];
  const outline={episodes:[{event_ids:['a']}],omissions:[{event_id:'b',reason:'支线节奏过慢'}]} as NovelOutline;
  assert.deepEqual(accountEvents(outline,events),[{event_id:'b',reason:'支线节奏过慢'},{event_id:'c',reason:''}]);
  outline.episodes[0].event_ids.push('b');
  assert.deepEqual(accountEvents(outline,events),[{event_id:'c',reason:''}]);
});
test('interrupted and stopped tasks wait for manual admission',()=>{
  for(const status of ['interrupted','stopped','error','completed'])assert.equal(novelActive({status} as NovelJob),false);
  for(const status of ['running','queued','stopping'])assert.equal(novelActive({status} as NovelJob),true);
});
