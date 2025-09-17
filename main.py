from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import docx
import markdown
import os
import requests
import json
import tempfile
import subprocess

app = FastAPI()

# Função para extrair texto do PDF
def extract_text_from_pdf(file_path):
    text = ""
    with fitz.open(file_path) as pdf:
        for page in pdf:
            text += page.get_text()
    return text

# Função para extrair texto do .docx
def extract_text_from_docx(file_path):
    doc = docx.Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

# Função para extrair texto do .md
def extract_text_from_md(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return markdown.markdown(f.read())

# Função para interagir com o modelo LLaMA via Ollama
def ask_llama(question, context):
    prompt = f"""
Você é um assistente que responde **somente em JSON válido**.
Extraia apenas as perguntas mais relevantes do contexto abaixo e retorne no seguinte formato:

{{
  "perguntas": {{
    "pergunta_1": "...",
    "pergunta_2": "...",
    "pergunta_3": "..."
  }}
}}

Responda em português correto, sem erros de acentuação.

Contexto:
{context}

Pergunta do usuário:
{question}
"""

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": "llama3", "prompt": prompt, "stream": False}
    )

    if response.status_code == 200:
        try:
            data = response.json().get("response", "").strip()
            # Tentar converter direto em JSON
            return json.loads(data)
        except Exception:
            # Se vier resposta quebrada, devolve como string
            return {"erro": "A resposta não veio em JSON válido", "raw": data}
    else:
        return {"erro": f"Ollama retornou {response.status_code}", "detalhes": response.text}

@app.post("/ask")
async def ask_document_question(file: UploadFile = File(...), question: str = Form(...)):
    try:
        # Salvar arquivo temporariamente
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        # Extrair texto com base na extensão
        ext = os.path.splitext(file.filename)[1].lower()
        if ext == ".pdf":
            text = extract_text_from_pdf(tmp_path)
        elif ext == ".docx":
            text = extract_text_from_docx(tmp_path)
        elif ext == ".md":
            text = extract_text_from_md(tmp_path)
        else:
            return JSONResponse(content={"error": "Formato de arquivo não suportado."}, status_code=400)

        # Perguntar ao LLaMA
        answer = ask_llama(question, text)

        # Limpeza
        os.remove(tmp_path)

        return {"answer": answer}

    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
