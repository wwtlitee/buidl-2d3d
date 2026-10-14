"""Decode every generated frame in the provider's private environment. Unclecow."""
import json
from pathlib import Path
import sys

def probe(path,max_frames=1000):
    import av
    count=0
    with av.open(str(path)) as container:
        videos=list(container.streams.video)
        if len(videos)!=1:raise ValueError('输出需包含一个视频轨道')
        stream=videos[0]
        width,height=stream.codec_context.width,stream.codec_context.height
        rate=float(stream.average_rate or 0)
        audio=[{'channels':s.codec_context.channels,'sampleRate':s.codec_context.sample_rate} for s in container.streams.audio]
        for frame in container.decode(video=0):
            count+=1
            if count>max_frames:raise ValueError('输出帧数超过请求范围')
            if frame.width!=width or frame.height!=height:raise ValueError('视频中途尺寸发生变化')
        if not count:raise ValueError('输出没有可解码的视频帧')
    return {'width':width,'height':height,'frames':count,'fps':rate,'audio':audio}

if __name__=='__main__':
    print(json.dumps(probe(Path(sys.argv[1]),int(sys.argv[2]) if len(sys.argv)>2 else 1000)))
