## Instructions

```bash
# Linux system setup
apt update
apt install ffmpeg
apt install git-lfs
git lfs install
git clone git@github.com:0xMichaelRan/index-tts.git

# uv environment setup
curl -LsSf https://astral.sh/uv/install.sh | sh
uv venv --python 3.11
uv pip install -r requirements.txt
uv run --no-project python --version
uv pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118

# Download the model
uv tool install "modelscope"
modelscope download --model IndexTeam/IndexTTS-1.5 --local_dir checkpoints

# Test official webui
source .venv/bin/activate
python webui.py

# Run web service at port 8848
python run-indextts-1-5.py
```

#### Web Demo
```bash
pip install -e ".[webui]" --no-build-isolation
python webui.py

# use another model version:
python webui.py --model_dir IndexTTS-1.5
```

Open your browser and visit `http://127.0.0.1:7860` to see the demo.

#### Sample Code
```python
from indextts.infer import IndexTTS
tts = IndexTTS(model_dir="checkpoints",cfg_path="checkpoints/config.yaml")
voice="reference_voice.wav"
text="大家好，我现在正在bilibili 体验 ai 科技，说实话，来之前我绝对想不到！AI技术已经发展到这样匪夷所思的地步了！比如说，现在正在说话的其实是B站为我现场复刻的数字分身，简直就是平行宇宙的另一个我了。如果大家也想体验更多深入的AIGC功能，可以访问 bilibili studio，相信我，你们也会吃惊的。"
tts.infer(voice, text, output_path)
```

### quick start

```
cd git/index-tts && git checkout index-tts-1.5 && source .venv/bin/activate && python run-indextts-1-5.py
```