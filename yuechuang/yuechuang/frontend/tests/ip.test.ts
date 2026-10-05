import test from 'node:test';
import assert from 'node:assert/strict';
import {validateBoundaries,versionRestriction,type IPItem,type IPVersion} from '../lib/ipApi.ts';
test('chapter changes require full ordered coverage',()=>{
 assert.equal(validateBoundaries([{title:'前言',start_line:1},{title:'第一章',start_line:20}],20),true);
 for(const rows of [[],[{title:'第一章',start_line:2}],[{title:'',start_line:1}],[{title:'一',start_line:1},{title:'二',start_line:1}],[{title:'一',start_line:1},{title:'二',start_line:21}],[{title:'一',start_line:1},{title:'二',start_line:1.5}]])assert.equal(validateBoundaries(rows,20),false);
});
test('adaptation availability uses chosen version and declared rights',()=>{
 const item={rights_status:'owned',rights_basis:'原创测试文本',rights_scope:'本地测试'} as IPItem;
 assert.equal(versionRestriction(item,undefined),'尚未导入正文');
 assert.equal(versionRestriction(item,{content_scope:'unknown'} as IPVersion),'正文范围待确认');
 assert.equal(versionRestriction({...item,rights_status:'pending'},{content_scope:'full'} as IPVersion),'使用依据待核对');
 assert.equal(versionRestriction({...item,rights_scope:'  '},{content_scope:'full'} as IPVersion),'使用依据待核对');
 assert.equal(versionRestriction(item,{content_scope:'excerpt'} as IPVersion),'');
});
