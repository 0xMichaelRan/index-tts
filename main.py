from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse
import os
import argparse
from datetime import datetime
from indextts.infer import IndexTTS
from indextts.infer_v2 import IndexTTS2

app = FastAPI()

# Global variables for TTS models
tts1 = None
tts2 = None
model_version = "v2.0"  # Default to v2.0
checkpoint_dir = "checkpoints_v20"  # Default checkpoint directory


def initialize_models(version="v2.0"):
    """Initialize TTS models based on version"""
    global tts1, tts2, model_version, checkpoint_dir

    model_version = version
    if version == "v1.5":
        checkpoint_dir = "checkpoints_v15"
        config_path = os.path.join(checkpoint_dir, "config.yaml")
        print(f"Initializing IndexTTS v1.5 with checkpoint directory: {checkpoint_dir}")
        tts1 = IndexTTS(
            cfg_path=config_path,
            model_dir=checkpoint_dir,
            use_fp16=True,
            use_cuda_kernel=True,
        )
        tts2 = None
    else:  # v2.0 (default)
        checkpoint_dir = "checkpoints_v20"
        config_path = os.path.join(checkpoint_dir, "config.yaml")
        print(f"Initializing IndexTTS v2.0 with checkpoint directory: {checkpoint_dir}")
        tts2 = IndexTTS2(
            cfg_path=config_path,
            model_dir=checkpoint_dir,
            use_fp16=True,
            use_cuda_kernel=True,
            use_deepspeed=True,
        )
        tts1 = None


@app.post("/infer_v15/")
async def infer_v15(audio_prompt: UploadFile = File(...), text: str = Form(...)):
    """IndexTTS v1.5 inference endpoint"""
    global tts1

    if tts1 is None:
        raise HTTPException(
            status_code=500,
            detail="IndexTTS v1.5 model not initialized. Please restart with --version v1.5",
        )

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
        output_filename = f"{timestamp}_indextts1.5_{original_audio_filename}.wav"
        output_path = os.path.join(output_dir, output_filename)

        # Call the infer function
        tts1.infer_fast(
            audio_prompt=temp_audio_path,
            text=text,
            output_path=output_path,
            verbose=False,
        )

        # Return the generated audio file, with a descriptive download name
        return FileResponse(
            output_path, media_type="audio/wav", filename=output_filename
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temporary files
        # if os.path.exists(temp_audio_path):
        #     os.remove(temp_audio_path)
        pass


@app.post("/infer_v2/")
async def infer_v2(audio_prompt: UploadFile = File(...), text: str = Form(...)):
    """IndexTTS v2.0 inference endpoint"""
    global tts2

    if tts2 is None:
        raise HTTPException(
            status_code=500,
            detail="IndexTTS v2.0 model not initialized. Please restart with --version v2.0",
        )

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
        output_filename = f"{timestamp}_indextts2.0_{original_audio_filename}.wav"
        output_path = os.path.join(output_dir, output_filename)

        # Call the infer function
        tts2.infer(
            spk_audio_prompt=temp_audio_path,
            text=text,
            output_path=output_path,
            verbose=True,
        )

        # Return the generated audio file, with a descriptive download name
        return FileResponse(
            output_path, media_type="audio/wav", filename=output_filename
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Clean up temporary files
        # if os.path.exists(temp_audio_path):
        #     os.remove(temp_audio_path)
        pass


@app.post("/infer/")
async def infer_auto(audio_prompt: UploadFile = File(...), text: str = Form(...)):
    """Auto-detect currently loaded model and run TTS synthesis"""
    global tts1, tts2, model_version

    # Determine which model is currently loaded
    if tts1 is not None:
        current_model = "v1.5"
        tts_instance = tts1
    elif tts2 is not None:
        current_model = "v2.0"
        tts_instance = tts2
    else:
        raise HTTPException(
            status_code=500, detail="No IndexTTS model is currently initialized"
        )

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
        output_filename = f"{timestamp}_{current_model}_{original_audio_filename}.wav"
        output_path = os.path.join(output_dir, output_filename)

        # Call the appropriate infer function based on the model version
        if current_model == "v1.5":
            tts_instance.infer_fast(
                audio_prompt=temp_audio_path,
                text=text,
                output_path=output_path,
                verbose=False,
            )
        else:  # v2.0
            tts_instance.infer(
                spk_audio_prompt=temp_audio_path,
                text=text,
                output_path=output_path,
                verbose=True,
            )

        # Return the generated audio file, with a descriptive download name
        return FileResponse(
            output_path, media_type="audio/wav", filename=output_filename
        )
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
# uv run python main.py --version v2.0  (default)
# uv run python main.py --version v1.5  (for IndexTTS v1.5)
# old way: [conda-env] python indextts/infer.py
if __name__ == "__main__":
    import uvicorn

    # Parse command line arguments
    parser = argparse.ArgumentParser(description="IndexTTS API Server")
    parser.add_argument(
        "--version",
        choices=["v1.5", "v2.0"],
        default="v2.0",
        help="Model version to use (default: v2.0)",
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=8848, help="Port to bind to (default: 8848)"
    )
    args = parser.parse_args()

    # Initialize the selected model
    print(f"Starting IndexTTS API Server with model version: {args.version}")
    initialize_models(args.version)

    print(f"Access the API at http://127.0.0.1:{args.port}/docs#/")
    print(f"Available endpoints:")
    print(f"  - POST /infer/ - Auto-detect current model and run inference")
    if args.version == "v1.5":
        print(f"  - POST /infer_v15/ - IndexTTS v1.5 inference")
    else:
        print(f"  - POST /infer_v2/ - IndexTTS v2.0 inference")

    uvicorn.run(app, host=args.host, port=args.port)
