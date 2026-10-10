"""
adapter_test.py — verifica o formato de requisição de cada adapter de LLM
contra um servidor HTTP local, sem chamar nenhum provider real.
"""
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Permite rodar como `python tests/test_agentes.py` a partir da raiz
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

recebido = {}
falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


RESPOSTAS = {
    # OpenAI / Groq / Ollama
    "openai": {
        "choices": [{"message": {"content": '{"ok": true}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    },
    # Anthropic
    "anthropic": {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": '{"ok": true}'}],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    },
    # Gemini
    "gemini": {
        "candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5,
                          "totalTokenCount": 15},
    },
}


class Mock(BaseHTTPRequestHandler):
    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length", 0))
        corpo = json.loads(self.rfile.read(tamanho) or b"{}")

        familia = self.path.strip("/").split("/")[0]
        recebido[familia] = {
            "path": self.path,
            "headers": dict(self.headers),
            "body": corpo,
        }

        payload = json.dumps(RESPOSTAS[familia]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


servidor = HTTPServer(("127.0.0.1", 8899), Mock)
threading.Thread(target=servidor.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8899"


async def principal():
    from src.agents import Agente

    print("\n=== Groq / OpenAI-compatível ===")
    agente = Agente(provider="groq", model="openai/gpt-oss-20b",
                    api_key="chave-groq", base_url=f"{BASE}/openai")
    r = await agente.run({"transcript": "texto do video"},
                         prompt="Analise", output_schema={"ok": "bool"})

    req = recebido["openai"]
    checar(r.success, "resposta interpretada com sucesso")
    checar(r.output == {"ok": True}, "JSON extraído do choices[0].message.content")
    checar(r.usage["total_tokens"] == 15, "usage mapeado")
    checar(req["path"] == "/openai/chat/completions", f"endpoint correto ({req['path']})")
    checar(req["headers"].get("Authorization") == "Bearer chave-groq",
           "credencial em Authorization: Bearer")
    checar(req["body"]["model"] == "openai/gpt-oss-20b", "modelo repassado")
    checar(req["body"]["response_format"] == {"type": "json_object"},
           "json_mode ativa response_format")
    checar(req["body"]["messages"][0]["role"] == "system", "system prompt como mensagem system")
    checar("Analise" in req["body"]["messages"][1]["content"],
           "prompt do usuário presente")
    checar("texto do video" in req["body"]["messages"][1]["content"],
           "dados de entrada serializados no prompt")
    checar('"ok"' in req["body"]["messages"][0]["content"],
           "output_schema descrito no system prompt")
    checar(req["body"]["temperature"] == 0, "temperatura repassada")

    print("\n=== Anthropic ===")
    agente = Agente(provider="anthropic", model="claude-opus-5",
                    api_key="chave-anthropic", base_url=f"{BASE}/anthropic")
    r = await agente.run("texto", prompt="Analise")

    req = recebido["anthropic"]
    checar(r.success and r.output == {"ok": True}, "resposta lida dos blocos de conteúdo")
    checar(r.usage["total_tokens"] == 15, "input_tokens + output_tokens somados")
    checar(req["path"] == "/anthropic/messages", f"endpoint correto ({req['path']})")
    checar(req["headers"].get("x-api-key") == "chave-anthropic", "credencial em x-api-key")
    checar(req["headers"].get("anthropic-version") == "2023-06-01", "header de versão presente")
    checar("temperature" not in req["body"],
           "temperature OMITIDO (rejeitado pelos modelos atuais)")
    checar("max_tokens" in req["body"], "max_tokens presente (obrigatório)")
    checar(isinstance(req["body"].get("system"), str), "system como campo de topo")
    checar(all(m["role"] != "system" for m in req["body"]["messages"]),
           "nenhuma mensagem com role system")
    checar("JSON válido" in req["body"]["system"], "exigência de JSON foi para a instrução")

    print("\n=== Gemini ===")
    agente = Agente(provider="gemini", model="gemini-3.5-flash-lite",
                    api_key="chave-gemini", base_url=f"{BASE}/gemini")
    r = await agente.run("texto", prompt="Analise")

    req = recebido["gemini"]
    checar(r.success and r.output == {"ok": True}, "resposta lida de candidates[0]")
    checar(r.usage["total_tokens"] == 15, "usageMetadata mapeado")
    checar("models/gemini-3.5-flash-lite:generateContent" in req["path"],
           f"endpoint correto ({req['path']})")
    checar("key=chave-gemini" in req["path"], "credencial na query string")
    checar(req["body"]["generationConfig"]["responseMimeType"] == "application/json",
           "json_mode → responseMimeType")
    checar("systemInstruction" in req["body"], "system prompt como systemInstruction")

    print("\n=== Tratamento de erro HTTP ===")

    class Erro(Mock):
        def do_POST(self):
            payload = json.dumps({"error": {"message": "chave inválida"}}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv_erro = HTTPServer(("127.0.0.1", 8898), Erro)
    threading.Thread(target=srv_erro.serve_forever, daemon=True).start()

    r = await Agente(provider="groq", api_key="x",
                     base_url="http://127.0.0.1:8898/openai").run("t", prompt="p")
    checar(r.success is False, "401 vira falha, não exceção")
    checar(r.error.type == "AuthenticationError", f"401 → AuthenticationError (obtido {r.error.type})")
    checar(r.error.retryable is False, "erro de autenticação não é reexecutável")
    checar("chave inválida" in r.error.message, "mensagem do provider preservada")

    class Limite(Mock):
        def do_POST(self):
            payload = json.dumps({"error": {"message": "rate limit"}}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv_limite = HTTPServer(("127.0.0.1", 8897), Limite)
    threading.Thread(target=srv_limite.serve_forever, daemon=True).start()

    r = await Agente(provider="groq", api_key="x",
                     base_url="http://127.0.0.1:8897/openai").run("t", prompt="p")
    checar(r.error.type == "RateLimitError" and r.error.retryable is True,
           "429 → RateLimitError reexecutável")

    print("\n=== Recusa da Anthropic (HTTP 200) ===")

    class Recusa(Mock):
        def do_POST(self):
            payload = json.dumps({
                "stop_reason": "refusal",
                "stop_details": {"type": "refusal", "category": "cyber"},
                "content": [],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv_recusa = HTTPServer(("127.0.0.1", 8896), Recusa)
    threading.Thread(target=srv_recusa.serve_forever, daemon=True).start()

    r = await Agente(provider="anthropic", api_key="x",
                     base_url="http://127.0.0.1:8896/anthropic").run("t", prompt="p")
    checar(r.success is False and r.error.type == "RefusalError",
           "stop_reason='refusal' com HTTP 200 vira erro explícito")

    print("\n=== Provider desconhecido ===")
    try:
        Agente(provider="inexistente")
        checar(False, "provider inválido deveria levantar ValueError")
    except ValueError as exc:
        checar("Disponíveis" in str(exc), "erro lista os providers disponíveis")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de adapter passaram.")
    print("=" * 62)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(principal()))
