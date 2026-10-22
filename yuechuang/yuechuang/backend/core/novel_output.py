"""Safe novel failure diagnostics without retaining supplier text or reasoning."""
from core.output_validation import ModelOutputError, decode_json, validate_as


class NovelOutputError(ModelOutputError):
    def __init__(self, diagnostic):
        super().__init__(diagnostic)
        if diagnostic == 'output_truncated':
            self.code = 'MODEL_OUTPUT_TRUNCATED'
            self.message = '模型达到输出上限，回答被截断，本次结果未采用；已完成分片保留。请缩小范围或检查文本模型配置后重试。'
        elif diagnostic == 'reasoning_only':
            self.code = 'MODEL_REASONING_ONLY'
            self.message = '模型只返回了思考过程，没有给出分析结果；已完成分片保留。请检查文本模型的思考设置后重试。'
        elif diagnostic == 'empty_response':
            self.message = '模型返回了空内容，本次没有可用结果；已完成分片保留，可只重试失败章节。'
        elif diagnostic in {'incomplete_response', 'content_filter'}:
            self.message = '文本服务未正常完成回答，本次结果未采用；已完成分片保留，请检查服务状态或输入后重试。'
        elif diagnostic == 'quote_not_in_source':
            self.message = '模型提供的引文与原文不一致，本次结果未采用；已完成分片保留，可只重试失败章节。'
        elif diagnostic == 'episode_numbers_mismatch':
            self.message = '模型生成的集数或编号与当前设置不一致；本次大纲未采用，原著分析和已确认方向保留。'
        elif diagnostic == 'duration_sum_mismatch':
            self.message = '模型生成的场景总时长与本集目标不一致；本次剧本未采用，已确认大纲保留。'
        elif diagnostic in {'outline_event_capacity', 'outline_character_capacity'}:
            self.message = '本集选用的事件或出场人物过多，超出当前时长的生成范围；本次建议未采用，已完成结果保留。'
        elif diagnostic == 'event_reused_across_episodes':
            self.message = '同一条原著事件被放进了不止一集；本次大纲未采用，已确认方向保留。'
        elif diagnostic == 'incomplete_episode_event':
            self.message = '有一集没有写清本集内做完的一件事；本次大纲未采用，已确认方向保留。'
        elif diagnostic == 'repeated_episode_event':
            self.message = '有两集写了同一件要完成的事；本次大纲未采用，已确认方向保留。'
        elif diagnostic == 'missing_opening_hook':
            self.message = '第 1 集缺少开场钩子；本次大纲未采用，已确认方向保留。'
        elif diagnostic == 'scene_without_action':
            self.message = '有一场没有可拍的动作；本次剧本未采用，已确认大纲保留。'
        elif diagnostic == 'line_too_long':
            self.message = '有一句对白超过 35 字，一口说不完；本次剧本未采用，已确认大纲保留。'
        elif diagnostic == 'dialogue_too_long':
            self.message = '对白按语速算下来超过本集时长；本次剧本未采用，已确认大纲保留。'
        elif diagnostic in {'unknown_speaker', 'unknown_scene_character'}:
            self.message = '剧本出现了无法对应到已确认大纲的人物或说话人；本次剧本未采用，已确认大纲保留。'
        elif diagnostic == 'unmarked_new_dialogue':
            self.message = '剧本中的部分对白与对应原文不一致，却被标记为原文；本次剧本未采用，已完成结果保留。'
        else:
            self.message = '模型返回的格式或人物、事件对应关系未通过校验；已完成结果保留，请核对后重试失败步骤。'


def validate_response(raw, usage, schema, validator, normalizer=None):
    finish = usage.get('finish_reason')
    if finish == 'length':
        raise NovelOutputError('output_truncated')
    if finish not in {None, 'unknown', 'stop'}:
        raise NovelOutputError('content_filter' if finish == 'content_filter' else 'incomplete_response')
    if isinstance(raw, str) and not raw.strip() and (usage.get('has_reasoning') or usage.get('reasoning_tokens')):
        raise NovelOutputError('reasoning_only')
    try:
        value = decode_json(raw)
        return validator(validate_as(schema, normalizer(value) if normalizer else value))
    except ModelOutputError as exc:
        raise NovelOutputError(exc.diagnostic) from None


def legacy_recovery_hint(job):
    """An inference for old records, never a fabricated finish_reason."""
    if (job.get('error') or {}).get('code') != 'MODEL_OUTPUT_INVALID' or 'deepseek' not in job['plan']['model']:
        return ''
    limit = job['plan']['max_output_tokens'] // max(1, job['plan']['max_requests'])
    failed = next((c for c in reversed(job['calls']) if c['status'] == 'error'), {})
    if 'finish_reason' not in failed and limit and (failed.get('output_tokens') or 0) >= limit * .99:
        return '上次输出接近单次上限，可能未生成完整回答；旧记录未保存结束原因。DeepSeek 改编现已启用 JSON 输出并关闭思考模式，可继续分析已选范围，已完成内容不会重复生成。'
    return ''
