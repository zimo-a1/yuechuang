"""Print a reviewable 12-second plan. This command has NO execution/network mode."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agents.video_agent import VideoDirectorAgent
from core.output_validation import validate_storyboard


def build_preview():
    segments = []
    # Original diagnostic dialogue, not a quotation/adaptation of the novel.
    for index, (speaker, listener, line) in enumerate([
        ("青年", "同伴", "我决定明天出发。"),
        ("同伴", "青年", "好，我陪你去车站。"),
    ], 1):
        segments.append({
            "segment_id": f"dialogue_{index:02d}", "segment_number": index,
            "episode_number": 1, "total_duration": 6, "location": "无文字背景的室内",
            "characters": ["青年", "同伴"],
            "shots": [{"shot_number": 1, "shot_type": "中景", "duration": 6,
                       "content": f"两人相对站立，平视侧机位拍摄{speaker}，另一人作为倾听者入画。"}],
            "shot_control": {"version": 1, "beats": [{
                "unit_id": f"U{index:03d}", "kind": "dialogue", "start": 0.5, "end": 3.5,
                "speaker": speaker, "listeners": [listener], "text": line,
            }]},
        })
    validated = validate_storyboard([{"episode_number": 1, "segments": segments}])[0]
    return {
        "status": "preview_only_not_generated", "network_requests": 0,
        "proposed_generation": {"model": "grok-imagine-video-1.5", "video_calls": 2,
                                "seconds_each": 6, "total_seconds": 12, "new_images": 0,
                                "llm_calls": 0, "concurrency": 1, "automatic_retries": 0},
        "prerequisites": ["复用经人工检查无乱码文字的现有参考图", "配置供应商可读取的公网 HTTPS 图片地址",
                          "核实费用并确认两次视频创建后再运行；本脚本不能执行生成"],
        "acceptance": ["每人只说自己的句子，无齐声复述", "视线朝向对话对象，倾听者嘴唇闭合",
                       "无模型生成的乱码文字", "动作自然，角色外观保持一致"],
        "episode": validated,
        "prompts": [VideoDirectorAgent()._assemble_prompt(segment, "写实电影，自然光") for segment in segments],
    }


if __name__ == "__main__":
    print(json.dumps(build_preview(), ensure_ascii=False, indent=2))
