from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import docx
import markdown
import os
import requests
import json
import tempfile
import re

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

def clean_and_fix_json(json_text):
    """
    Limpa e corrige problemas comuns no JSON retornado pelo Ollama
    """
    try:
        # Remove caracteres de controle e espaços desnecessários
        cleaned = json_text.strip()
        
        # Remove possíveis prefixos/sufixos que não são JSON
        # Procura pelo primeiro { e último }
        start = cleaned.find('{')
        end = cleaned.rfind('}')
        
        if start != -1 and end != -1 and start < end:
            cleaned = cleaned[start:end+1]
        
        # Corrige aspas não fechadas - procura por padrões como: "texto
        # seguido de nova linha ou fim sem fechamento
        cleaned = re.sub(r'"([^"]*?)$', r'"\1"', cleaned, flags=re.MULTILINE)
        
        # Corrige vírgulas faltando antes de nova linha com aspas
        cleaned = re.sub(r'"\s*\n\s*"', '",\n  "', cleaned)
        
        # Corrige última entrada sem vírgula (remove vírgula antes do })
        cleaned = re.sub(r',(\s*})', r'\1', cleaned)
        
        # Tenta fazer o parse
        return json.loads(cleaned)
        
    except Exception as e:
        print(f"Erro ao limpar JSON: {e}")
        return None

def extract_json_manually(text):
    """
    Extrai perguntas manualmente quando o JSON está malformado
    """
    try:
        questions = {}
        # Procura por padrões como "pergunta_X": "texto"
        pattern = r'"(pergunta_\d+)"\s*:\s*"([^"]*)"'
        matches = re.findall(pattern, text)
        
        for key, value in matches:
            questions[key] = value
            
        if questions:
            return {"perguntas": questions}
        
        return None
    except Exception as e:
        print(f"Erro na extração manual: {e}")
        return None

def validate_and_parse_json(response_text):
    """
    Valida e faz parse do JSON com múltiplas tentativas
    """
    # Tentativa 1: Parse direto
    try:
        result = json.loads(response_text)
        if "perguntas" in result:
            return result
    except:
        pass
    
    # Tentativa 2: Limpeza e correção
    cleaned_result = clean_and_fix_json(response_text)
    if cleaned_result and "perguntas" in cleaned_result:
        return cleaned_result
    
    # Tentativa 3: Extração manual
    manual_result = extract_json_manually(response_text)
    if manual_result:
        return manual_result
    
    # Se tudo falhar, retorna erro com o texto original
    return {
        "erro": "Não foi possível fazer parse do JSON",
        "raw": response_text,
        "sugestao": "O modelo pode estar retornando texto incompleto ou malformado"
    }

# Função para interagir com o modelo LLaMA via Ollama
def ask_llama(question, context):
    prompt = f"""
Você é um assistente que cria perguntas baseadas em documentos.

IMPORTANTE: Responda APENAS com JSON válido no formato exato abaixo:

{{
  "perguntas": {{
    "pergunta_1": "Primeira pergunta aqui",
    "pergunta_2": "Segunda pergunta aqui",
    "pergunta_3": "Terceira pergunta aqui"
  }}
}}

Regras:
1. Use APENAS o formato JSON mostrado acima
2. Não adicione texto antes ou depois do JSON
3. Todas as aspas devem estar fechadas corretamente
4. Não quebre linhas no meio das perguntas
5. Crie a quantidade de perguntas que aparece no prompt
6. Use português correto

Tipo de perguntas solicitadas: {question}

Contexto do documento:
{context[:3000]}...
"""

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": "llama3", 
                "prompt": prompt, 
                "stream": False,
                "options": {
                    "temperature": 0.3,  # Menor temperatura para mais consistência
                    "top_p": 0.9
                }
            },
            timeout=120  # Timeout maior para documentos longos
        )

        if response.status_code == 200:
            data = response.json().get("response", "").strip()
            
            # Usa a função de validação e parse melhorada
            return validate_and_parse_json(data)
        else:
            return {
                "erro": f"Ollama retornou status {response.status_code}", 
                "detalhes": response.text
            }
            
    except requests.exceptions.Timeout:
        return {"erro": "Timeout na requisição ao Ollama"}
    except requests.exceptions.RequestException as e:
        return {"erro": f"Erro na requisição: {str(e)}"}
    except Exception as e:
        return {"erro": f"Erro inesperado: {str(e)}"}

@app.post("/ask")
async def ask_document_question(file: UploadFile = File(...), question: str = Form(...)):
    tmp_path = None
    try:
        # Validação do arquivo
        if not file.filename:
            return JSONResponse(
                content={"error": "Nome do arquivo é obrigatório"}, 
                status_code=400
            )
        
        # Salvar arquivo temporariamente
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Extrair texto com base na extensão
        ext = os.path.splitext(file.filename)[1].lower()
        text = ""
        
        if ext == ".pdf":
            text = extract_text_from_pdf(tmp_path)
        elif ext in [".docx", ".doc"]:
            text = extract_text_from_docx(tmp_path)
        elif ext == ".md":
            text = extract_text_from_md(tmp_path)
        else:
            return JSONResponse(
                content={
                    "error": "Formato de arquivo não suportado.", 
                    "suportados": [".pdf", ".docx", ".doc", ".md"]
                }, 
                status_code=400
            )

        # Validação do texto extraído
        if not text or len(text.strip()) < 10:
            return JSONResponse(
                content={"error": "Documento vazio ou muito pequeno"}, 
                status_code=400
            )

        # Perguntar ao LLaMA
        answer = ask_llama(question, text)

        return {"answer": answer}

    except Exception as e:
        return JSONResponse(
            content={"error": f"Erro interno: {str(e)}"}, 
            status_code=500
        )
    finally:
        # Limpeza garantida
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

# Endpoint adicional para testar a conectividade com o Ollama
@app.get("/health/ollama")
async def check_ollama():
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            return {
                "status": "ok", 
                "models_available": [model.get("name") for model in models]
            }
        else:
            return {"status": "error", "message": "Ollama não está respondendo corretamente"}
    except Exception as e:
        return {"status": "error", "message": f"Não foi possível conectar ao Ollama: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)