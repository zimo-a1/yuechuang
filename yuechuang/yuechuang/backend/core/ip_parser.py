"""Local, loss-aware novel parsing. No model transport or public file paths."""
import codecs
from bisect import bisect_right
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from upload_validation import InvalidUpload, validate_upload

MAX_CHARACTERS = 10_000_000
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
HEADING = re.compile(r'^[ \t　]*(?:第[零〇一二三四五六七八九十百千万两\d]+(?:[章回节卷篇]|部(?!分))(?:[ \t　:：.、—-].*|[^\s]{0,60})|(?:chapter|CHAPTER|Chapter)\s+\d+\b.*|序章|序言|楔子|尾声|后记)[ \t　]*$', re.M)


def chapter_index(text, boundaries=None):
    """Half-open ranges partition every character, including preface/whitespace."""
    lines = text.split("\n")
    starts = [0]
    for line in lines[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    if boundaries is not None:
        positions = [(c['start_line'], c['title'].strip()) for c in boundaries]
        if not positions or positions[0][0] != 1 or any(not title for _, title in positions):
            raise InvalidUpload('第一章须从第 1 行开始，每章须填写标题')
        if any(line < 1 or line > len(lines) for line, _ in positions) or any(b[0] <= a[0] for a, b in zip(positions, positions[1:])):
            raise InvalidUpload('章节起始行须严格递增，且位于正文范围内')
        found = [(starts[line - 1], title, line) for line, title in positions]
    else:
        found = [(m.start(), m.group().strip(), bisect_right(starts, m.start())) for m in HEADING.finditer(text) if len(m.group().strip()) <= 100]
        if found and found[0][0] and not text[:found[0][0]].strip():
            found[0] = (0, found[0][1], 1)
        elif not found or found[0][0] != 0:
            found.insert(0, (0, '前言' if found else '正文（待分章）', 1))
    if len(found) > 10000:
        raise InvalidUpload('识别章节超过 1 万个，请整理标题后重新导入；本次未截断正文')
    chapters = []
    for i, (start, title, line) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        if end <= start or (boundaries is not None and not text[start:end].strip()):
            raise InvalidUpload('章节不能仅包含空白或位于正文末尾之后，请调整分界')
        chapters.append({'id': f'ch-{i+1}', 'title': title, 'start': start, 'end': end,
                         'start_line': line, 'characters': end-start})
    return chapters


def parse_novel(path):
    ext = Path(path).suffix.lower()
    if ext not in {'.txt', '.docx'}:
        raise InvalidUpload('IP 正文仅支持 TXT、DOCX 文件')
    validate_upload(path)
    warnings = []
    if ext == '.txt':
        raw = Path(path).read_bytes()
        encodings = ['utf-16'] if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) else ['utf-8-sig', 'gbk', 'gb18030']
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise InvalidUpload('无法解码正文，请另存为 UTF-8 TXT')
    else:
        encoding = 'docx'
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
            forbidden = {'del', 'ins', 'moveFrom', 'moveTo', 'drawing', 'pict', 'object', 'altChunk', 'txbxContent', 'sym', 'fldSimple', 'fldChar'}
            if any(node.tag.rsplit('}', 1)[-1] in forbidden for node in root.iter()):
                raise InvalidUpload('Word 包含修订、图片、文本框或动态域；请接受修订并另存为纯正文 TXT 后导入')
            for name in archive.namelist():
                if re.match(r'word/(footnotes|endnotes|comments|header\d*|footer\d*)\.xml$', name):
                    data = archive.read(name)
                    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                        raise InvalidUpload('Word 包含不支持的 XML 定义')
                    if any((node.text or '').strip() for node in ET.fromstring(data).iter(W+'t')):
                        raise InvalidUpload('Word 含页眉、页脚、批注或注释文字；请整理为纯正文 TXT 后导入，避免漏读')
            paragraphs = []
            for p in root.iter(W+'p'):
                paragraphs.append(''.join(node.text or '' if node.tag == W+'t' else '\t' if node.tag == W+'tab' else '\n' if node.tag in {W+'br', W+'cr'} else '' for node in p.iter()))
            text = '\n'.join(paragraphs)
        warnings.append('已按文档顺序提取段落和表格文字；不保留 Word 排版。原文件可下载核对。')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    if not text.strip():
        raise InvalidUpload('文件中没有可导入的正文')
    if len(text) > MAX_CHARACTERS:
        raise InvalidUpload('正文超过 1000 万字符，请拆分文件；本次未导入，未截断')
    chapters = chapter_index(text)
    if len(chapters) == 1 and chapters[0]['title'] == '正文（待分章）':
        warnings.append('未识别到章节标题，全文已保留。请在章节管理中按起始行分章。')
    if len(text) > 100_000:
        warnings.append('长篇正文已完整保留，请核对章节及首尾；改编时建议先选择少量章节试跑。')
    return {'text': text, 'chapters': chapters, 'encoding': encoding, 'warnings': warnings,
            'characters': len(text), 'line_count': text.count('\n') + 1}
