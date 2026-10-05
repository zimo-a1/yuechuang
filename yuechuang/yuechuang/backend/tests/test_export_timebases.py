import subprocess
from pathlib import Path
from core.agents.editor_agent import VideoEditorAgent
from upload_validation import ffmpeg_executable


async def test_export_keeps_later_scenes_with_mixed_timebases_and_cover_art(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path);exe=ffmpeg_executable()
    cover=tmp_path/'cover.jpg'
    subprocess.run([exe,'-v','error','-f','lavfi','-i','color=yellow:s=64x96','-frames:v','1',str(cover)],check=True,timeout=20)
    paths=[]
    for i,(color,scale) in enumerate([('blue','12288'),('red','12800'),('green','90000')]):
        path=tmp_path/f'{i}.mp4';args=[exe,'-v','error','-f','lavfi','-i',f'color={color}:s=64x96:r=10:d=1','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1']
        if i!=1:args+=['-i',str(cover)]
        args+=['-map','0:v','-map','1:a']
        if i!=1:args+=['-map','2:v']
        args+=['-c:v:0','libx264','-pix_fmt','yuv420p','-video_track_timescale',scale,'-c:a','aac']
        if i!=1:args+=['-c:v:1','mjpeg','-disposition:v:1','attached_pic']
        subprocess.run(args+['-t','1',str(path)],check=True,timeout=20);paths.append(path)
    result=await VideoEditorAgent().process({'session_id':'mixed','selected_clips':{str(i):str(p) for i,p in enumerate(paths)}})
    output=Path(result['payload']['final_video'])
    for seconds,channel,threshold in [(.5,2,200),(1.5,0,200),(2.5,1,90)]:
        pixel=subprocess.run([exe,'-v','error','-ss',str(seconds),'-i',str(output),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,check=True,timeout=20).stdout
        assert len(pixel)==3 and pixel[channel]>threshold, (seconds,pixel)
    raw=subprocess.run([exe,'-v','error','-i',str(output),'-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,check=True,timeout=20).stdout
    assert 28<=len(raw)//3<=33, 'export must stay around 3 seconds, with no long frozen tail'


async def test_export_separates_episodes_and_keeps_each_storyboard_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    executable = ffmpeg_executable()
    paths = {}
    for color in ('blue', 'red'):
        paths[color] = tmp_path / f'{color}.mp4'
        subprocess.run([
            executable, '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=64x96:r=10:d=1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(paths[color]),
        ], check=True, timeout=20)
    segments = [('a', 'blue'), ('b', 'red'), ('c', 'red'), ('d', 'blue')]
    data = {
        'storyboard': {'episodes': [
            {'episode_number': 1, 'act_title': '第一集', 'segments': [{'segment_id': 'a'}, {'segment_id': 'b'}]},
            {'episode_number': 2, 'act_title': '第二集', 'segments': [{'segment_id': 'c'}, {'segment_id': 'd'}]},
        ]},
        'video_generation': {'clips': [
            {'id': key, 'selected': str(paths[color])} for key, color in reversed(segments)
        ]},
    }
    result = await VideoEditorAgent().process({'session_id': 'two-episodes', '_session_artifacts': data})
    exports = result['payload']['final_videos']
    assert [(item['episode'], item['name']) for item in exports] == [(1, '第一集'), (2, '第二集')]
    assert len({item['path'] for item in exports}) == 2
    for item, first_channel, last_channel in zip(exports, (2, 0), (0, 2)):
        raw = subprocess.run([
            executable, '-v', 'error', '-i', item['path'], '-vf', 'scale=1:1',
            '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1',
        ], capture_output=True, check=True, timeout=20).stdout
        frames = [tuple(raw[index:index + 3]) for index in range(0, len(raw), 3)]
        assert len(frames) == 20
        assert all(frame[first_channel] > 200 for frame in frames[:10])
        assert all(frame[last_channel] > 200 for frame in frames[10:])
