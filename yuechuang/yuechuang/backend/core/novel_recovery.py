"""Deterministic compatibility and lossless identity aggregation from validated chunks."""
import copy
import unicodedata

from core.novel_contracts import Contract, Text, Bible, require, validate_people
from core.output_validation import validate_as
from pydantic import Field

REPAIR_ATTEMPTS = 2


class StorySummary(Contract):
    summary: Text
    open_threads: list[Text] = Field(max_length=40)


def known_fields(value, schema):
    """Ignore supplementary metadata, never coerce or fabricate required fields."""
    spec = schema.model_json_schema()

    def project(item, node):
        if '$ref' in node:
            node = spec['$defs'][node['$ref'].rsplit('/', 1)[-1]]
        if isinstance(item, dict) and 'properties' in node:
            return {key:project(val, node['properties'][key]) for key,val in item.items() if key in node['properties']}
        if isinstance(item, list) and 'items' in node:
            return [project(val, node['items']) for val in item]
        return item

    return project(value, spec)


def source_quote(quote, source):
    if quote in source:
        return quote
    # Only typography/whitespace equivalence; never fuzzy-match words or join spans.
    def indexed(text):
        chars, positions = [], []
        for index, char in enumerate(text):
            for normalized in unicodedata.normalize('NFKC', char).translate(str.maketrans('“”‘’', '\"\"\'\'')):
                if not normalized.isspace():
                    chars.append(normalized); positions.append(index)
        return ''.join(chars), positions

    haystack, positions = indexed(source)
    needle, _ = indexed(quote)
    if len(needle) < 2:
        return quote
    start = haystack.find(needle)
    if start < 0 or haystack.find(needle, start+1) >= 0:
        return quote
    return source[positions[start]:positions[start+len(needle)-1]+1]


def extraction_fields(value, schema, source):
    value = known_fields(value, schema)
    if isinstance(value, dict) and isinstance(value.get('events'), list):
        for event in value['events']:
            if isinstance(event, dict) and isinstance(event.get('quote'), str):
                quote = source_quote(event['quote'], source)
                # Evidence is a bounded contiguous excerpt, not the complete event text.
                if quote in source:
                    event['quote'] = quote[:180]
    return value


def source_registry(prior, batch):
    """Merge exact names and explicit aliases, not model-invented identity guesses."""
    documents = ([prior] if prior else []) + batch
    people = [person for doc in documents for person in doc['characters']]
    names = list(dict.fromkeys(p['name'] for p in people))
    parent = {name:name for name in names}
    owners = {}
    for person in people:
        for alias in person['aliases']:
            owners.setdefault(alias, set()).add(person['name'])

    def root(name):
        while parent[name] != name:
            name = parent[name]
        return name

    # A shared generic title does not establish that two characters are the same.
    for alias, candidates in owners.items():
        if alias in parent and len(candidates) == 1:
            owner = next(iter(candidates))
            if root(alias) != root(owner):
                parent[root(alias)] = root(owner)
    merged = {}
    for person in people:
        key = root(person['name'])
        target = merged.setdefault(key, {'name':key, 'aliases':[], 'description':person['description']})
        for name in [person['name'], *person['aliases']]:
            if name != key and name not in target['aliases']:
                target['aliases'].append(name)
        # Keep a verified, informative description; all chapter descriptions remain stored.
        if len(person['description']) > len(target['description']):
            target['description'] = person['description']
    relations, seen = [], set()
    for doc in documents:
        for relation in doc['relationships']:
            source, target = root(relation['source']), root(relation['target'])
            key = (source, target, relation['description'])
            if key not in seen:
                seen.add(key)
                relations.append({**relation, 'source':source, 'target':target})
    alias_owners = {}
    for person in merged.values():
        for alias in person['aliases']:
            alias_owners.setdefault(alias, set()).add(person['name'])
    ambiguities = [f'称谓“{alias}”在不同章节用于{ "、".join(sorted(values)) }，暂不合并这些人物。'
                   for alias,values in alias_owners.items() if len(values)>1]
    allowed = {name for person in people for name in [person['name'], *person['aliases']]}
    registry = {'characters':list(merged.values()), 'relationships':relations}
    # Bounds and source membership are checked before paying for a summary.
    validate_as(Bible, {'summary':'待汇总', 'open_threads':[], **registry})
    validate_people(registry, allowed, allow_shared_aliases=True)
    return registry, ambiguities


def assemble_summary(value, registry, ambiguities):
    result = {**value, **copy.deepcopy(registry)}
    result['open_threads'] = list(dict.fromkeys([*ambiguities, *result['open_threads']]))
    return validate_as(Bible, result)


def correction_feedback(diagnostic, kind=None, data=None):
    data = data or {}
    if kind == 'outline':
        capacity = data['capacity']
        return (f'上次输出未通过校验（{diagnostic}）。只输出 {data["episode_count"]} 集，'
                f'编号必须依次为 {list(range(1,data["episode_count"]+1))}，每集仅 {data["target_duration"]} 秒。'
                '不要把场景/未来剧情拆成额外集数，不要一次容纳全部原著事件。只选目录中少量关键 event_ids、资料中的人物主名。'
                '必须提供 omitted_event_reason，对所有未采用事件说明本轮聚焦与取舍理由；omissions 只列例外，没有则 []。'
                '选用与省略编号不能重复或重叠。summary/理由最多 2000 字，title 最多 120 字，'
                f'每集 characters 最多 {capacity["max_characters_per_episode"]} 人、event_ids 最多 {capacity["max_events_per_episode"]} 项，'
                '只关联本集确实拍摄的事件，不关联只提到背景的整章所有事件。'
                '每集 complete_event 用 2—120 字写一件在本集内做完的事，各集不得相同，同一条 event_id 不得出现在两集。'
                '第 1 集 hook 必填，2—120 字，写开场钩子；其余集 hook 用空字符串。返回完整 JSON。')
    if kind == 'script':
        return (f'上次输出未通过校验（{diagnostic}）。只写选定一集，所有场景 duration 必须为整数且相加恰好 {data["target_duration"]} 秒。'
                f'所有 speaker 只能从 {data["selected_episode"]["characters"]} 中逐字选择，不得用旁白、职位、姓名加括号。'
                '若为内心声/画外音，speaker 仍写对应人物主名，在 action 说明声音形式；未知背景人物仅作非对白的环境交代。'
                '完整覆盖 selected_episode.event_ids；本场所有有台词的人列入 characters。'
                '每句对白只输出 speaker/text，原文或创作标记由程序核对；单句去掉空白后不超过 35 字，全部对白按每秒 4.5 字必须塞得进本集时长。'
                '每场 action 至少 4 字，写看得见的动作，不要只写一句引号里的话。没有原著事件的场景 creative_addition 必须为 true。返回完整 JSON。')
    if kind == 'direction':
        return (f'上次输出未通过校验（{diagnostic}）。focus_characters 只能来自 bible 人物主名，'
                f'情节应适合 {data["episode_count"]} 集、每集 {data["target_duration"]} 秒。'
                'premise/tone/changes 不超过 2000 字，focus_characters 为 1—30 个姓名。只返回完整 JSON。')
    if diagnostic == 'quote_not_in_source':
        return '上次引文不在原文中。quote 必须是原文内连续 2—180 字的短句，逐字复制，不拼接或改写。'
    if diagnostic in {'ambiguous_aliases', 'unknown_relationship', 'duplicate_people'}:
        return '上次人物对应有误。每人仅一条，别名不能重复或与主名相同；关系端点只能使用本次人物表中的主名。'
    return '上次输出未通过校验。只返回 schema 要求的完整 JSON；不遗漏必填字段，不添加字段，摘要和描述不超过 2000 字，引文不超过 180 字。'
