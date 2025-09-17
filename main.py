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

def clean_and_parse_json(raw_response):
    """
    Função para limpar e parsear respostas JSON que podem vir malformadas
    """
    try:
        # Tentar parse direto primeiro
        return json.loads(raw_response)
    except json.JSONDecodeError:
        pass
    
    # Se falhou, tentar limpar a resposta
    try:
        # Remover possíveis prefixos/sufixos não-JSON
        json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
        if json_match:
            clean_json = json_match.group(0)
            
            # Tentar parse novamente
            try:
                return json.loads(clean_json)
            except json.JSONDecodeError:
                pass
            
            # Se ainda falhou, tentar corrigir estruturas comuns
            # Corrigir arrays sem chaves (como no seu exemplo)
            if '"perguntas": {' in clean_json and '": {' not in clean_json.replace('"perguntas": {', ''):
                # Detectar padrão incorreto e corrigir
                pergunta_matches = re.findall(r'"([^"]+)"(?:,|\s*})', clean_json)
                if pergunta_matches:
                    perguntas_dict = {}
                    for i, pergunta in enumerate(pergunta_matches, 1):
                        # Evitar duplicar a palavra "perguntas" na chave
                        if pergunta.lower() != "perguntas":
                            perguntas_dict[f"pergunta_{i}"] = pergunta
                    
                    return {"perguntas": perguntas_dict}
        
        # Se tudo falhou, extrair perguntas manualmente usando regex
        perguntas = re.findall(r'"([^"]*\?[^"]*)"', raw_response)
        if perguntas:
            perguntas_dict = {}
            count = 1
            for pergunta in perguntas:
                if len(pergunta.strip()) > 10:  # Filtrar strings muito curtas
                    perguntas_dict[f"pergunta_{count}"] = pergunta
                    count += 1
            
            if perguntas_dict:
                return {"perguntas": perguntas_dict}
    
    except Exception as e:
        print(f"Erro no parsing manual: {e}")
    
    # Se tudo falhou, retornar erro estruturado
    return {
        "perguntas": {
            "pergunta_1": "Não foi possível extrair perguntas do contexto fornecido.",
            "pergunta_2": "Por favor, tente novamente com um arquivo diferente.",
            "pergunta_3": "Verifique se o conteúdo do arquivo está legível."
        }
    }

# Função para interagir com o modelo LLaMA via Ollama
def ask_llama(question, context):
    # Prompt mais específico e com exemplos
    prompt = f"""
Você DEVE responder apenas em JSON válido. Não adicione texto antes ou depois do JSON.

Formato OBRIGATÓRIO:
{{
  "perguntas": {{
    "pergunta_1": "Primeira pergunta sobre o tema?",
    "pergunta_2": "Segunda pergunta sobre o tema?", 
    "pergunta_3": "Terceira pergunta sobre o tema?"
  }}
}}

REGRAS:
- Responda APENAS com o JSON acima
- As perguntas devem ser em português
- Cada pergunta deve terminar com "?"
- Use acentos corretos
- NÃO adicione texto explicativo

Baseado no contexto abaixo, crie 3 perguntas relevantes sobre: {question}

Contexto:
{context[:3000]}
"""

    try:
        response = requests.post(
            "http://localhost:11434/api/generate",
            json={"model": "llama3", "prompt": prompt, "stream": False},
            timeout=30  # Adicionar timeout
        )

        if response.status_code == 200:
            raw_data = response.json().get("response", "").strip()
            
            # Usar função robusta de parsing
            return clean_and_parse_json(raw_data)
        else:
            return {
                "perguntas": {
                    "pergunta_1": f"Erro na comunicação com o modelo (status: {response.status_code})",
                    "pergunta_2": "Tente novamente em alguns instantes.",
                    "pergunta_3": "Verifique se o serviço Ollama está funcionando."
                }
            }
    
    except requests.exceptions.Timeout:
        return {
            "perguntas": {
                "pergunta_1": "O modelo demorou muito para responder.",
                "pergunta_2": "Tente novamente com um arquivo menor.",
                "pergunta_3": "Verifique sua conexão com o Ollama."
            }
        }
    except requests.exceptions.RequestException as e:
        return {
            "perguntas": {
                "pergunta_1": f"Erro de conexão: {str(e)}",
                "pergunta_2": "Verifique se o Ollama está rodando.",
                "pergunta_3": "Confirme se o modelo llama3 está instalado."
            }
        }
    except Exception as e:
        return {
            "perguntas": {
                "pergunta_1": f"Erro inesperado: {str(e)}",
                "pergunta_2": "Tente novamente ou contate o suporte.",
                "pergunta_3": "Verifique os logs para mais detalhes."
            }
        }

@app.post("/ask")
async def ask_document_question(file: UploadFile = File(...), question: str = Form(...)):
    tmp_path = None
    try:
        # Validar tipo de arquivo
        if not file.filename:
            return JSONResponse(
                content={"error": "Nome do arquivo é obrigatório."}, 
                status_code=400
            )
        
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in [".pdf", ".docx", ".md"]:
            return JSONResponse(
                content={"error": f"Formato {ext} não suportado. Use .pdf, .docx ou .md"}, 
                status_code=400
            )

        # Salvar arquivo temporariamente
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
            content = await file.read()
            if len(content) == 0:
                return JSONResponse(
                    content={"error": "Arquivo vazio."}, 
                    status_code=400
                )
            tmp.write(content)
            tmp_path = tmp.name

        # Extrair texto com base na extensão
        try:
            if ext == ".pdf":
                text = extract_text_from_pdf(tmp_path)
            elif ext == ".docx":
                text = extract_text_from_docx(tmp_path)
            elif ext == ".md":
                text = extract_text_from_md(tmp_path)
        except Exception as e:
            return JSONResponse(
                content={"error": f"Erro ao extrair texto do arquivo: {str(e)}"}, 
                status_code=400
            )

        # Verificar se conseguiu extrair texto
        if not text or len(text.strip()) < 50:
            return JSONResponse(
                content={"error": "Não foi possível extrair texto suficiente do arquivo."}, 
                status_code=400
            )

        # Perguntar ao LLaMA - agora sempre retorna estrutura consistente
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
                pass  # Ignorar erros de limpeza