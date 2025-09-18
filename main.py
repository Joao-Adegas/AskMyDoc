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
    
AI_CONFIG = {
    "model": "llama3",
    "temperature": 0.3,
    "top_p": 0.9,
    "top_k": 40,
    "repeat_penalty": 1.1,
    "seed": None,
    "num_ctx": 4096,
    "num_predict": 2048,
    "stop": None,
    "tfs_z": 1.0,
    "typical_p": 1.0,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "mirostat": 0,
    "mirostat_tau": 5.0,
    "mirostat_eta": 0.1,
    "penalize_newline": True,
    "numa": False
}
    
@app.get("/ai/config")
async def get_ai_config():
    """
    Retorna as configurações atuais da IA e descrições de cada parâmetro
    """
    try:
        # Busca informações do modelo atual no Ollama
        ollama_info = {}
        try:
            response = requests.get("http://localhost:11434/api/show", 
                                  json={"name": AI_CONFIG["model"]}, 
                                  timeout=5)
            if response.status_code == 200:
                model_info = response.json()
                ollama_info = {
                    "model_info": {
                        "name": model_info.get("model", "N/A"),
                        "size": model_info.get("size", "N/A"),
                        "modified_at": model_info.get("modified_at", "N/A"),
                        "parameter_size": model_info.get("details", {}).get("parameter_size", "N/A"),
                        "quantization_level": model_info.get("details", {}).get("quantization_level", "N/A")
                    }
                }
        except:
            ollama_info = {"model_info": "Não foi possível obter informações do modelo"}

        return {
            "current_config": AI_CONFIG,
            "ollama_model_info": ollama_info,
            "parameters_description": {
                "model": {
                    "current": AI_CONFIG["model"],
                    "description": "Nome do modelo a ser usado",
                    "type": "string",
                    "examples": ["llama3", "llama3.1", "mistral", "codellama"]
                },
                "temperature": {
                    "current": AI_CONFIG["temperature"],
                    "description": "Controla a criatividade/aleatoriedade das respostas",
                    "type": "float",
                    "range": "0.0 - 2.0",
                    "recommendations": {
                        "0.1-0.3": "Respostas consistentes e focadas",
                        "0.5-0.7": "Balanceado entre criatividade e coerência", 
                        "0.8-1.2": "Mais criativo e variado",
                        "1.3-2.0": "Muito criativo, pode ser incoerente"
                    }
                },
                "top_p": {
                    "current": AI_CONFIG["top_p"],
                    "description": "Amostragem nucleus - considera apenas tokens com probabilidade acumulada até este valor",
                    "type": "float",
                    "range": "0.0 - 1.0",
                    "recommendations": {
                        "0.1-0.3": "Muito focado, pouca variação",
                        "0.7-0.9": "Boa variedade mantendo qualidade",
                        "0.95-1.0": "Máxima variedade possível"
                    }
                },
                "top_k": {
                    "current": AI_CONFIG["top_k"],
                    "description": "Limita a seleção aos K tokens mais prováveis",
                    "type": "integer",
                    "range": "1 - 100",
                    "recommendations": {
                        "10-20": "Muito focado",
                        "40-60": "Balanceado (recomendado)",
                        "80-100": "Mais variado"
                    }
                },
                "repeat_penalty": {
                    "current": AI_CONFIG["repeat_penalty"],
                    "description": "Penaliza repetições de palavras/frases",
                    "type": "float", 
                    "range": "0.0 - 2.0",
                    "recommendations": {
                        "1.0": "Sem penalização",
                        "1.1-1.2": "Penalização leve (recomendado)",
                        "1.3-1.5": "Penalização moderada",
                        "1.6+": "Penalização forte"
                    }
                },
                "seed": {
                    "current": AI_CONFIG["seed"],
                    "description": "Semente para reproduzibilidade (null = aleatório)",
                    "type": "integer ou null",
                    "note": "Use um número fixo para respostas reproduzíveis"
                },
                "num_ctx": {
                    "current": AI_CONFIG["num_ctx"],
                    "description": "Tamanho do contexto (quantos tokens o modelo pode 'lembrar')",
                    "type": "integer",
                    "range": "512 - 32768",
                    "recommendations": {
                        "2048": "Documentos pequenos",
                        "4096": "Padrão - boa para maioria dos casos",
                        "8192": "Documentos médios",
                        "16384+": "Documentos grandes (usa mais memória)"
                    }
                },
                "num_predict": {
                    "current": AI_CONFIG["num_predict"],
                    "description": "Máximo de tokens que o modelo pode gerar na resposta",
                    "type": "integer",
                    "range": "1 - 4096",
                    "note": "-1 = sem limite"
                },
                "presence_penalty": {
                    "current": AI_CONFIG["presence_penalty"],
                    "description": "Penaliza tokens que já apareceram (encoraja novos tópicos)",
                    "type": "float",
                    "range": "-2.0 - 2.0"
                },
                "frequency_penalty": {
                    "current": AI_CONFIG["frequency_penalty"],
                    "description": "Penaliza tokens baseado na frequência de aparição",
                    "type": "float",
                    "range": "-2.0 - 2.0"
                },
                "mirostat": {
                    "current": AI_CONFIG["mirostat"],
                    "description": "Algoritmo de amostragem Mirostat (0=desabilitado, 1=Mirostat 1, 2=Mirostat 2)",
                    "type": "integer",
                    "range": "0 - 2"
                },
                "mirostat_tau": {
                    "current": AI_CONFIG["mirostat_tau"],
                    "description": "Controla coerência vs diversidade no Mirostat",
                    "type": "float",
                    "range": "0.0 - 10.0"
                },
                "mirostat_eta": {
                    "current": AI_CONFIG["mirostat_eta"],
                    "description": "Taxa de aprendizado do Mirostat",
                    "type": "float",
                    "range": "0.0 - 1.0"
                }
            },
            "presets": {
                "creative": {
                    "description": "Para respostas criativas e variadas",
                    "config": {
                        "temperature": 0.8,
                        "top_p": 0.9,
                        "top_k": 60,
                        "repeat_penalty": 1.1
                    }
                },
                "consistent": {
                    "description": "Para respostas consistentes e focadas",
                    "config": {
                        "temperature": 0.2,
                        "top_p": 0.8,
                        "top_k": 30,
                        "repeat_penalty": 1.15
                    }
                },
                "balanced": {
                    "description": "Balanceado entre criatividade e consistência",
                    "config": {
                        "temperature": 0.5,
                        "top_p": 0.85,
                        "top_k": 40,
                        "repeat_penalty": 1.1
                    }
                },
                "analytical": {
                    "description": "Para análises detalhadas e técnicas",
                    "config": {
                        "temperature": 0.3,
                        "top_p": 0.9,
                        "top_k": 50,
                        "repeat_penalty": 1.05
                    }
                }
            },
            "usage_tips": [
                "Para perguntas técnicas, use temperature baixa (0.1-0.3)",
                "Para conteúdo criativo, use temperature alta (0.7-1.0)", 
                "Se as respostas estão muito repetitivas, aumente repeat_penalty",
                "Se as respostas estão muito incoerentes, diminua temperature e top_p",
                "Para documentos grandes, aumente num_ctx",
                "Use seed fixo para respostas reproduzíveis em testes"
            ]
        }
        
    except Exception as e:
        return JSONResponse(
            content={"error": f"Erro ao obter configurações: {str(e)}"}, 
            status_code=500
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)