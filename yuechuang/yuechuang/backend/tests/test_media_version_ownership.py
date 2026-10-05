from pathlib import Path
import pytest
from core.agents.character_agent import CharacterDesignerAgent
from core.agents.reference_agent import ReferenceGeneratorAgent
from core.agents.video_agent import VideoDirectorAgent


@pytest.mark.parametrize('kind', ['character', 'reference', 'video'])
def test_versions_never_include_another_id_with_same_prefix(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    if kind == 'character':
        agent = CharacterDesignerAgent(); root = Path(agent._asset_base('s'))/'characters'
        versions = lambda: agent._list_versions('s', 'characters', 'char_1')
        next_path = lambda: agent._next_version_path('s', 'characters', 'char_1')
        item='char_1';ext='.png'
    elif kind == 'reference':
        agent=ReferenceGeneratorAgent();root=Path(agent._scenes_base('s'))
        versions=lambda: agent._list_versions('s','seg_01_1')
        next_path=lambda: agent._next_version_path('s','seg_01_1')
        item='seg_01_1';ext='.jpg'
    else:
        agent=VideoDirectorAgent();root=Path(agent._video_base('s'))
        versions=lambda: agent._list_versions('s','seg_01_1')
        next_path=lambda: agent._next_version_path('s','seg_01_1')
        item='seg_01_1';ext='.mp4'
    root.mkdir(parents=True)
    for suffix in ['0','0_v99','_final','_backup']:(root/(item+suffix+ext)).write_bytes(b'other')
    assert versions()==[]
    assert Path(next_path()).name == item+ext
    expected=[item+ext,item+'_v2'+ext,item+'_upload_v3'+ext]
    for name in expected:(root/name).write_bytes(b'own')
    assert {Path(p).name for p in versions()}==set(expected)
    assert Path(next_path()).name==item+'_v4'+ext
