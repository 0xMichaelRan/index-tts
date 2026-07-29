from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
import os
import platform
from datetime import datetime
from indextts.infer import create_tts_engine

app = FastAPI()

# Initialize the TTS model based on platform
# macOS: Uses native AVFoundation TTS
# Windows/Linux: Uses GPU-based IndexTTS inference
print(f"Running on: {platform.system()}")
if platform.system() == "Darwin":
    print(">> Initializing macOS native TTS engine")
    tts = create_tts_engine(use_native_macos=True, language="en-US")
else:
    print(">> Initializing IndexTTS GPU inference engine")
    tts = create_tts_engine(
        use_native_macos=False,
        cfg_path="checkpoints/config.yaml",
        model_dir="checkpoints",
        is_fp16=True,
        use_cuda_kernel=False,
    )


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "IndexTTS FastAPI Server",
        "version": "1.5",
        "platform": platform.system(),
        "engine": "macOS AVFoundation TTS"
        if platform.system() == "Darwin"
        else "IndexTTS GPU Inference",
        "endpoints": {
            "/": "API information",
            "/health": "Health check",
            "/infer/": "TTS inference (POST with text and optional audio_prompt)",
        },
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "platform": platform.system(),
        "engine": "macOS_native" if platform.system() == "Darwin" else "indexTTS_gpu",
    }


@app.post("/infer/")
async def infer(audio_prompt: UploadFile = File(None), text: str = Form(...)):
    """
    Text-to-speech inference endpoint.

    On macOS: Uses native AVFoundation TTS (audio_prompt is optional/ignored)
    On Windows/Linux: Uses GPU-based IndexTTS with audio_prompt for voice cloning
    """
    try:
        output_dir = os.path.join("outputs", "tts_output")
        os.makedirs(output_dir, exist_ok=True)

        # Define the output path for the generated audio file
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

        if platform.system() == "Darwin":
            # macOS native TTS - audio_prompt is not used
            output_filename = f"{timestamp}_macos_tts.wav"
            output_path = os.path.join(output_dir, output_filename)

            print(f">> macOS TTS: Synthesizing text to {output_path}")
            tts.infer(
                audio_prompt=None,
                text=text,
                output_path=output_path,
                rate=0.5,
                pitch=1.0,
                volume=1.0,
            )
        else:
            # Windows/Linux GPU inference - requires audio_prompt
            if audio_prompt is None:
                raise HTTPException(
                    status_code=400,
                    detail="audio_prompt is required for GPU-based inference on Windows/Linux",
                )

            # Save the uploaded audio prompt to a temporary file
            upload_dir = os.path.join("outputs", "audio_prompt")
            os.makedirs(upload_dir, exist_ok=True)

            original_audio_filename = audio_prompt.filename
            temp_audio_path = os.path.join(upload_dir, original_audio_filename)
            with open(temp_audio_path, "wb") as temp_audio_file:
                temp_audio_file.write(await audio_prompt.read())

            output_filename = (
                f"{timestamp}_{os.path.splitext(original_audio_filename)[0]}.wav"
            )
            output_path = os.path.join(output_dir, output_filename)

            print(
                f">> GPU inference: Synthesizing text with voice from {original_audio_filename}"
            )
            # Call the infer_fast function (fast batch inference)
            tts.infer_fast(
                audio_prompt=temp_audio_path,
                text=text,
                output_path=output_path,
                verbose=False,
            )

        # Return the generated audio file
        return FileResponse(
            output_path, media_type="audio/wav", filename=output_filename
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temporary files (optional - commented out for debugging)
        # if platform.system() != "Darwin" and audio_prompt and os.path.exists(temp_audio_path):
        #     os.remove(temp_audio_path)
        pass


if __name__ == "__main__":
    import uvicorn

    print("Access the API at http://127.0.0.1:8848/docs#/")
    uvicorn.run(app, host="0.0.0.0", port=8848)
