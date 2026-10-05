import {test} from 'node:test';
import assert from 'node:assert/strict';
import {projectPresentation,projectCover,relativeProjectTime} from '../lib/projectPresentation.ts';
import {PROJECT_STAGES} from '../lib/projects.ts';

test('presentation groups filters without hiding failures, stop or review',()=>{
  for(const [status,tone,label] of [['error','error','需要处理'],['stopped','stopped','已停止'],['waiting','review','待确认']]){
    const value=projectPresentation({status:{script_generation:status}});
    assert.equal(value.filter,'running');assert.equal(value.tone,tone);assert.equal(value.label,label);
  }
  assert.equal(projectPresentation({status:{init:'completed'}}).filter,'todo');
  assert.equal(projectPresentation({status:{},confirmed_stages:PROJECT_STAGES}).filter,'done');
});
test('container aggregates children without calling partial success complete',()=>{
  const value=projectPresentation({status:{},meta:{project_container:true}},[
    {status:{},confirmed_stages:PROJECT_STAGES}, {status:{video_generation:'error'},confirmed_stages:['script_generation']},
  ]);
  assert.equal(value.filter,'running');assert.equal(value.tone,'error');
  assert.equal(value.progress.completed,7);assert.equal(value.progress.total,12);
});
test('active and failed novel jobs are not shown as untouched drafts',()=>{
  const project={status:{},novel_task:{id:'n',status:'running',action:'analyze',step:'analysis',requests:1,max_requests:2}};
  assert.equal(projectPresentation(project).label,'改编中');
  assert.equal(projectPresentation({...project,novel_task:{...project.novel_task,status:'error'}}).tone,'error');
});
test('covers use selected assets and do not leak another episode cover',()=>{
  const assets=[{selected:'/a.png',kind:'characters',session_id:'a'},{selected:'/b.png',kind:'settings',session_id:'b'}];
  assert.equal(projectCover(assets),'/b.png');assert.equal(projectCover(assets,'a'),'/a.png');
  assert.equal(projectCover(assets,'c'),undefined);assert.equal(projectCover([{selected:''}]),undefined);
});
test('relative time handles calendar boundaries and unknown values',()=>{
  const now=new Date(2026,9,4,12).getTime();
  assert.equal(relativeProjectTime((now-1000)/1000,now),'刚刚');
  assert.equal(relativeProjectTime(new Date(2026,9,3,23).getTime()/1000,now),'昨天');
  assert.equal(relativeProjectTime(new Date(2026,8,1).getTime()/1000,now),'09-01');
  assert.equal(relativeProjectTime(undefined,now),'历史项目');
});
