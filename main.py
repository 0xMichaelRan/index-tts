from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
import os
from datetime import datetime
from indextts.infer import IndexTTS
from indextts.infer_v2 import IndexTTS2

app = FastAPI()

# Initialize the TTS model
tts2 = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", use_fp16=True, use_cuda_kernel=True)

# @app.post("/infer_fast/")
# async def infer_v1(audio_prompt: UploadFile = File(...), text: str = Form(...)):

#     tts1 = IndexTTS(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", use_fp16=True, use_cuda_kernel=False)

#     try:
#         # Save the uploaded audio prompt to a temporary file
#         upload_dir = os.path.join("outputs", "audio_prompt")
#         output_dir = os.path.join("outputs", "tts_output")
#         os.makedirs(upload_dir, exist_ok=True)
#         os.makedirs(output_dir, exist_ok=True)

#         # Use the original filename for the uploaded audio
#         original_audio_filename = audio_prompt.filename
#         temp_audio_path = os.path.join(upload_dir, original_audio_filename)
#         with open(temp_audio_path, "wb") as temp_audio_file:
#             temp_audio_file.write(await audio_prompt.read())

#         # Define the output path for the generated audio file, using the input name as base
#         timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
#         output_filename = f"{timestamp}_{os.path.splitext(original_audio_filename)[0]}.wav"
#         output_path = os.path.join(output_dir, output_filename)

#         # Call the infer function
#         tts1.infer_fast(audio_prompt=temp_audio_path, text=text, output_path=output_path, verbose=False)

#         # Return the generated audio file, with a descriptive download name
#         return FileResponse(output_path, media_type="audio/wav", filename=output_filename)
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
#     finally:
#         # Clean up temporary files
#         # if os.path.exists(temp_audio_path):
#         #     os.remove(temp_audio_path)
#         pass

@app.post("/infer_v2/")
async def infer_v2(audio_prompt: UploadFile = File(...), text: str = Form(...)):
    try:
        # Save the uploaded audio prompt to a temporary file
        upload_dir = os.path.join("outputs", "audio_prompt")
        output_dir = os.path.join("outputs", "tts_output")
        os.makedirs(upload_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Use the original filename for the uploaded audio
        original_audio_filename = audio_prompt.filename
        temp_audio_path = os.path.join(upload_dir, original_audio_filename)
        with open(temp_audio_path, "wb") as temp_audio_file:
            temp_audio_file.write(await audio_prompt.read())

        # Define the output path for the generated audio file, using the input name as base
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        output_filename = f"{timestamp}_{os.path.splitext(original_audio_filename)[0]}.wav"
        output_path = os.path.join(output_dir, output_filename)

        # Call the infer function
        tts2.infer(spk_audio_prompt=temp_audio_path, text=text, output_path=output_path, verbose=True)

        # Return the generated audio file, with a descriptive download name
        return FileResponse(output_path, media_type="audio/wav", filename=output_filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temporary files
        # if os.path.exists(temp_audio_path):
        #     os.remove(temp_audio_path)
        pass

# To run: 
# uv add fastapi uvicorn
# uv run webui.py
# uv run python main.py
# old way: [conda-env] python indextts/infer.py
if __name__ == "__main__":
    import uvicorn
    print(f"Access the API at http://127.0.0.1:8848/docs#/")
    uvicorn.run(app, host="0.0.0.0", port=8848)
