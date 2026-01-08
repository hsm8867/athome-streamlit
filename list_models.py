# list_models.py
from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

print("📋 사용 가능한 모델 목록:")
for model in client.models.list():
    print(f"- {model.name}")