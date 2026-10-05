import assert from 'node:assert/strict';
import test from 'node:test';
import {isValidChapterRange, searchChapters, selectChapterEndpoint, virtualChapterWindow} from '../lib/chapterSelection.ts';
import type {IPChapter} from '../lib/ipApi.ts';

const directory = (titles: string[]): IPChapter[] => titles.map((title, index) => ({
  id: `ch-${index + 1}`, title, start: index * 100, end: (index + 1) * 100,
  start_line: index * 10 + 1, characters: 100,
}));
const chapters = directory(['前言', '第1章 新的开始', '第2章 远行', '第12章 回家', '第1章 同名章节', 'CHAPTER 3 夜航']);
const ordinals = (query: string) => searchChapters(chapters, query).map(option => option.ordinal);

test('numeric search prioritizes directory order despite a preface shifting chapter titles', () => {
  assert.deepEqual(ordinals('1'), [1, 2, 4, 5]);
  assert.equal(searchChapters(chapters, '1')[0].chapter.title, '前言');
  assert.deepEqual(ordinals('2'), [2, 3, 4]);
  assert.deepEqual(ordinals('3'), [3, 6]);
});

test('hash search locates only an exact directory ordinal, including normalized digits', () => {
  assert.deepEqual(ordinals('#2'), [2]);
  assert.deepEqual(ordinals(' ＃ ００２ '), [2]);
  assert.equal(searchChapters(chapters, '#2')[0].chapter.title, '第1章 新的开始');
  for (const query of ['#0', '#7', '#9007199254740993']) assert.deepEqual(ordinals(query), []);
});

test('numeric title matches remain available without an ordinal and never duplicate the priority result', () => {
  assert.deepEqual(ordinals('12'), [4]);
  assert.deepEqual(ordinals('6'), [6]);
  assert.deepEqual(searchChapters(directory(['第1章', '第11章']), '1').map(option => option.ordinal), [1, 2]);
});

test('Chinese title search ignores spacing and normalizes full-width numbers and Latin case', () => {
  assert.deepEqual(ordinals(' 新　的\n开 始 '), [2]);
  assert.deepEqual(ordinals('第１２章'), [4]);
  assert.deepEqual(ordinals(' ｃｈａｐｔｅｒ　３ '), [6]);
  assert.deepEqual(ordinals('第1章'), [2, 5]);
  assert.deepEqual(ordinals('不存在的章名'), []);
});

test('blank searches preserve the entire directory and original chapter identities without mutation', () => {
  const before = structuredClone(chapters);
  for (const query of ['', ' \n\t　']) {
    const result = searchChapters(chapters, query);
    assert.deepEqual(result.map(option => option.ordinal), [1, 2, 3, 4, 5, 6]);
    result.forEach((option, index) => assert.equal(option.chapter, chapters[index]));
  }
  searchChapters(chapters, '2');
  assert.deepEqual(chapters, before);
  for (const query of ['', '1', '#1', '标题']) assert.deepEqual(searchChapters([], query), []);
});

test('large directories retain exact last-item access and original order of repeated titles', () => {
  const many = directory(Array.from({length: 2627}, (_, index) => index % 100 === 0 ? '重逢' : `第${index}章`));
  const last = searchChapters(many, '#2627');
  assert.equal(last.length, 1);
  assert.equal(last[0].chapter, many[2626]);
  assert.deepEqual(searchChapters(many, '重逢').map(option => option.ordinal), Array.from({length: 27}, (_, index) => index * 100 + 1));
});

test('chapter ranges accept complete, single-chapter and inclusive endpoint selections', () => {
  for (const range of [[1, 1, 1], [1, 2627, 2627], [2627, 2627, 2627], [2, 12, 2627]]) {
    assert.equal(isValidChapterRange(...range as [number, number, number]), true);
  }
});

test('chapter ranges reject empty, reversed, outside, fractional and non-finite boundaries', () => {
  const invalid = [[1, 0, 0], [1, 1, 0], [0, 1, 5], [-1, 2, 5], [3, 2, 5], [1, 6, 5],
    [1.5, 2, 5], [1, 2.5, 5], [1, 2, 5.5], [NaN, 2, 5], [1, NaN, 5], [1, 2, NaN],
    [1, Infinity, 5], [1, 2, Infinity], [1, 2, -5], [1, Number.MAX_SAFE_INTEGER + 1, Number.MAX_SAFE_INTEGER + 1]];
  for (const range of invalid) assert.equal(isValidChapterRange(...range as [number, number, number]), false);
});

test('two endpoint clicks select inclusive directory ranges in either direction', () => {
  assert.deepEqual(selectChapterEndpoint(null, 2, 6), {first: 2, last: 2, anchor: 2});
  assert.deepEqual(selectChapterEndpoint(2, 5, 6), {first: 2, last: 5, anchor: null});
  assert.deepEqual(selectChapterEndpoint(5, 2, 6), {first: 2, last: 5, anchor: null});
  assert.deepEqual(selectChapterEndpoint(2, 2, 6), {first: 2, last: 2, anchor: null});
  assert.equal(selectChapterEndpoint(null, 0, 6), null);
  assert.equal(selectChapterEndpoint(2, 7, 6), null);
  assert.equal(selectChapterEndpoint(null, 1, 0), null);
});

test('virtual directory bounds stay small and expose the last chapter after large jumps', () => {
  assert.deepEqual(virtualChapterWindow(0, 0), {start: 0, end: 0});
  assert.deepEqual(virtualChapterWindow(2627, 0), {start: 0, end: 9});
  for (const top of [0, 240, 72000, 157560, 999999]) {
    const window = virtualChapterWindow(2627, top);
    assert.ok(window.start >= 0 && window.end <= 2627 && window.end - window.start <= 13);
  }
  assert.equal(virtualChapterWindow(2627, 157560).end, 2627);
  assert.deepEqual(virtualChapterWindow(1, 60000), {start: 0, end: 1});
});
