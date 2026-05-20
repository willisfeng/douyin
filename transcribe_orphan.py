import os, sys
sys.path.insert(0, '.')
from transcriber import transcribe
from funasr import AutoModel

d = '/tmp/recordings'
if not os.path.exists(d):
    print('No recordings found')
    sys.exit(0)

# Load model ONCE for all files
print('Loading SenseVoice model once...')
model = AutoModel(
    model="iic/SenseVoiceSmall",
    disable_update=True,
    device="cpu",
)
print('Model loaded')

for f in os.listdir(d):
    if f.endswith('.wav'):
        wav = os.path.join(d, f)
        base = f[:-4]
        srt_p = os.path.join(d, base + '.srt')
        if os.path.exists(srt_p):
            print('Already transcribed:', f)
            continue
        print('Transcribing:', f)
        try:
            txt, srt = transcribe(wav, model=model)
            print('Done:', f)
        except Exception as e:
            print('Error transcribing', f, ':', e)
