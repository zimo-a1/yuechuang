"""Lossless half-open character ranges: no normalized, duplicated or lost text."""
from core.ip_library import digest


def split_chapters(text, chapters, limit=6000):
    if limit < 200:
        raise ValueError('chunk limit too small')
    chunks = []
    previous = chapters[0]['start']
    for chapter in chapters:
        if chapter['start'] != previous or not 0 <= chapter['start'] < chapter['end'] <= len(text):
            raise ValueError('invalid chapter coverage')
        start, end, number = chapter['start'], chapter['end'], 1
        while start < end:
            stop = min(start + limit, end)
            hard = False
            if stop < end:
                boundary = text.rfind('\n', start, stop)
                if boundary >= start + limit // 3:
                    stop = boundary + 1
                else:
                    hard = True
            chunks.append({'id':f"{chapter['id']}-p{number}", 'chapter_id':chapter['id'], 'title':chapter['title'],
                           'start':start, 'end':stop, 'characters':stop-start, 'hard_split':hard,
                           'sha256':digest(text[start:stop]), 'status':'pending', 'result':None})
            start, number = stop, number+1
        previous = end
    return chunks


def event_index(chunks):
    events = []
    for chunk in chunks:
        if chunk['status'] != 'completed':
            continue
        for index, event in enumerate(chunk['result']['events'], 1):
            events.append({**event, 'id':f"{chunk['id']}-e{index}", 'chunk_id':chunk['id'],
                           'chapter_id':chunk['chapter_id'], 'chapter_title':chunk['title'],
                           'chunk_start':chunk['start'], 'chunk_end':chunk['end']})
    return events
