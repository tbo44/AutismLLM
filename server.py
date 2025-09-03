import os
import base64
import io
from typing import List, Optional, Union, Dict, Any
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import openai
try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

# Load environment variables
load_dotenv()

app = FastAPI(title="My Personal LLM API")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", "You are a helpful personal assistant.")

if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required")

# Initialize OpenAI client
client = openai.OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL
)

class Message(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ChatRequest(BaseModel):
    messages: List[Message]
    model: Optional[str] = None
    temperature: Optional[float] = 0.7

class ChatResponse(BaseModel):
    content: str
    model: str

@app.get("/")
async def health_check():
    return {"ok": True, "service": "FalconCanvas API"}

def extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF content"""
    if PyPDF2 is None:
        return "[PDF text extraction not available]"
    
    try:
        pdf_file = io.BytesIO(content)
        pdf_reader = PyPDF2.PdfReader(pdf_file)
        text = ""
        for page in pdf_reader.pages:
            text += page.extract_text() + "\n"
        return text.strip() or "[PDF contains no extractable text]"
    except Exception as e:
        return f"[Error extracting PDF text: {str(e)}]"

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        # Read file content
        content = await file.read()
        
        # Convert to base64
        base64_content = base64.b64encode(content).decode('utf-8')
        
        # Determine file type
        is_image = file.content_type and file.content_type.startswith('image/')
        is_pdf = file.content_type == 'application/pdf' or (file.filename and file.filename.endswith('.pdf'))
        
        # Extract text for PDFs
        extracted_text = None
        if is_pdf:
            extracted_text = extract_pdf_text(content)
        
        return {
            "filename": file.filename,
            "content_type": file.content_type,
            "size": len(content),
            "base64_content": base64_content,
            "is_image": is_image,
            "is_pdf": is_pdf,
            "extracted_text": extracted_text
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        # Use provided model or default
        model = request.model or MODEL_NAME
        
        # If we have image content, use a vision model
        has_images = any(
            isinstance(msg.content, list) and 
            any(item.get('type') == 'image_url' for item in msg.content if isinstance(item, dict))
            for msg in request.messages
        )
        
        if has_images and model == "llama-3.1-8b-instant":
            model = "llama-3.2-11b-vision-preview"  # Switch to vision model
        
        # Prepare messages
        messages = [msg.dict() for msg in request.messages]
        
        # Check if first message is system message, if not prepend one
        if not messages or messages[0]["role"] != "system":
            messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        
        # Call OpenAI API
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=request.temperature
        )
        
        content = response.choices[0].message.content
        
        return ChatResponse(
            content=content,
            model=model
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calling LLM API: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)