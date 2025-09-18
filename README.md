# 🧠 AskMyDoc — AI-Powered Document Q&A with FastAPI & Ollama

Welcome to **AskMyDoc**, a smart and simple FastAPI app that lets you ask questions about your documents — and get answers powered by **LLaMA**, running locally via [Ollama](https://ollama.com). Whether it's a PDF, DOCX, or Markdown file, this app extracts the content and lets AI do the thinking for you.

---

## 🚀 Features

- 📄 Supports `.pdf`, `.docx`, and `.md` files
- 🤖 Uses **LLaMA** via **Ollama** for local AI inference
- ⚡ FastAPI backend for quick and scalable performance
- 🧪 Clean architecture with modular text extraction
- 🔐 No cloud dependency — your data stays local!

---

## 🧰 Requirements

Before you begin, make sure you have:

- Python 3.8+
- [Ollama installed locally](https://ollama.com/download)
- A LLaMA model pulled (e.g. `ollama pull llama3`)
- Git (optional, for cloning)

---

## 🐍 Setting Up Your Python Environment

Let’s get your virtual environment ready:

```bash
# Clone the repository
git clone https://github.com/your-username/askmydoc.git
cd askmydoc

# Create a virtual environment
python -m venv venv

# Activate it
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Request example

```bash
POST

Body:form-data

key           |       value
------------------------------
files          |       UploadFile
questions  |       Faça 15 perguntas em relação a esse documento

```



