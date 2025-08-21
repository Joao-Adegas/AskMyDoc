from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import docx
import markdown
import os
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
    prompt = f"""Use o contexto abaixo para responder à pergunta:
    
Contexto:
{context}

Pergunta:
{question}

Resposta:"""

    # Usando a API do Ollama local (ex: http://localhost:11434)
    result = subprocess.run(
        ["ollama", "run", "llama3", prompt],
        capture_output=True,
        text=True
    )
    return result.stdout.strip()

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
