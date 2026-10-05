import {test} from 'node:test';
import assert from 'node:assert/strict';
import {stepComplete,stageForStep,stepForStage} from '../lib/studio.ts';
test('five user steps retain three independent video confirmation gates',()=>{
 assert.equal(stepForStage('reference_generation'),'videos');
 assert.equal(stageForStep('videos',[]),'storyboard');
 assert.equal(stageForStep('videos',['storyboard']),'reference_generation');
 assert.equal(stageForStep('videos',['storyboard','reference_generation']),'video_generation');
 assert.equal(stepComplete('videos',{},['storyboard','video_generation']),false);
 assert.equal(stepComplete('videos',{},['storyboard','reference_generation','video_generation']),true);
});
test('source edits invalidate rewrite completion',()=>{
 assert.equal(stepComplete('source',{idea:'source'},[]),true);
 assert.equal(stepComplete('rewrite',{source_changed:true},['script_generation']),false);
 assert.equal(stepComplete('rewrite',{},['script_generation']),true);
});

import {boundAssets,exportOrder,videoItemStatus} from '../lib/studioVideo.ts';
import type {AssetItem} from '../lib/workflowTypes.ts';
const person:AssetItem={id:'c1',name:'甲',description:'',selected:'person.png',versions:['person.png']};
const prop:AssetItem={id:'p1',name:'表',description:'',selected:'prop.png',versions:['prop.png']};
test('explicit reference selection can exclude auto matched character and persist an empty selection',()=>{
 const segment={segment_id:'s',characters:['甲'],shots:[]};const assets={characters:[person],props:[prop]};
 assert.deepEqual(boundAssets(segment,assets).map(a=>a.id),['c1']);
 assert.deepEqual(boundAssets({...segment,reference_asset_ids:['p1']},assets).map(a=>a.id),['p1']);
 assert.deepEqual(boundAssets({...segment,reference_asset_ids:[]},assets),[]);
});
test('export restores saved order while retaining newly available clips and excluding deleted ones',()=>{
 assert.deepEqual(exportOrder([person,prop],['gone','p1']),['p1','c1']);
});
test('failed regeneration remains retryable even when a prior usable version exists',()=>{
 assert.equal(videoItemStatus({...person,status:'failed'}),'failed');
 assert.equal(videoItemStatus({...person,status:'running'}),'running');
 assert.equal(videoItemStatus({...person,status:'done',generation_status:'failed'}),'failed');
 assert.equal(videoItemStatus(person),'done');assert.equal(videoItemStatus(),'pending');
});
