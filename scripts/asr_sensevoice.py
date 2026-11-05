#!/usr/bin/env python3
# 用本机已有的 SenseVoice ONNX 模型做中文转写（sherpa-onnx + silero VAD）
import sys, wave, time, json
import numpy as np
import sherpa_onnx

WAV = sys.argv[1] if len(sys.argv) > 1 else "/root/workspace/bili-audio.wav"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/root/workspace/BV1x7xq62EB1-转写.md"
BASE = "/root/.dsh/speech-to-text/sensevoice/models"

with wave.open(WAV) as f:
    sr, n, ch = f.getframerate(), f.getnframes(), f.getnchannels()
    raw = f.readframes(n)
samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
if ch == 2:
    samples = samples.reshape(-1, 2).mean(axis=1)
print(f"音频 {len(samples)/sr/60:.1f} 分钟, sr={sr}", flush=True)
assert sr == 16000, "需要 16k 采样率"

recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
    model=f"{BASE}/sensevoice-onnx/model.int8.onnx",
    tokens=f"{BASE}/sensevoice-onnx/tokens.txt",
    num_threads=4, language="zh", use_itn=True, debug=False,
)
print("模型加载完成", flush=True)

cfg = sherpa_onnx.VadModelConfig()
cfg.silero_vad.model = f"{BASE}/silero/silero_vad.onnx"
cfg.silero_vad.threshold = 0.5
cfg.silero_vad.min_silence_duration = 0.4
cfg.silero_vad.min_speech_duration = 0.25
cfg.sample_rate = sr
vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)

def mmss(s):
    return f"[{int(s)//60:02d}:{int(s)%60:02d}]"

def handle(seg, start_sample, lines):
    st = recognizer.create_stream()
    st.accept_waveform(sr, seg)
    recognizer.decode_stream(st)
    txt = st.result.text.strip()
    if txt:
        lines.append(f"{mmss(start_sample/sr)} {txt}")
    return txt

lines, win = [], cfg.silero_vad.window_size
t0 = time.time()
for i in range(0, len(samples), win):
    vad.accept_waveform(samples[i:i+win])
    while not vad.empty():
        seg = vad.front.samples
        start = vad.front.start
        vad.pop()
        handle(seg, start, lines)
        if len(lines) % 20 == 0:
            print(f"  进度 {len(lines)} 段, {time.time()-t0:.0f}s", flush=True)
vad.flush()
while not vad.empty():
    seg, start = vad.front.samples, vad.front.start
    vad.pop()
    handle(seg, start, lines)

with open(OUT, "w", encoding="utf-8") as f:
    f.write("# 全网爆红的Ai换人视频生成教程（本地 SenseVoice 转写）\n\n")
    f.write(f"> 来源：本地 ASR 转写（非官方字幕），共 {len(lines)} 段\n\n")
    f.write("\n".join(lines))
print(f"完成：{len(lines)} 段，用时 {time.time()-t0:.0f}s -> {OUT}", flush=True)
