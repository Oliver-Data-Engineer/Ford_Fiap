"""
test_db.py — verifica a camada SQLAlchemy (src/pipelines/db.py) sem rede.

Cobre: persistência das 5 tabelas a partir de um PipelineRun, o join de
canal já embutido na tabela de vídeos, idempotência (rodar duas vezes não
duplica linhas) e o formato consumido pelo endpoint /pipelines/runs/{id}/tables.
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.config as config

TEMP = Path(tempfile.mkdtemp(prefix="fordfiap_test_db_"))
config.DATA_DIR = TEMP / "data"

from src.pipelines import db
from src.pipelines.models import PipelineRun

falhas = []


def checar(condicao, descricao):
    print(("  OK   " if condicao else "  FALHA") + f"  {descricao}")
    if not condicao:
        falhas.append(descricao)


def montar_run() -> PipelineRun:
    run = PipelineRun(pipeline_id="pipeline_teste", pipeline_name="Teste DB")
    run.refined = {
        "videos": [
            {
                "video_id": "vidA",
                "titulo": "Review Ford Territory",
                "descricao": "descrição",
                "canal_id": "canalX",
                "canal_nome": "Canal X",
                "publicado_em": "2026-03-01T00:00:00Z",
                "idioma": "pt",
                "thumbnail": "https://img/a.jpg",
                "url": "https://youtube.com/watch?v=vidA",
                "duracao_segundos": 600,
                "views": 5000,
                "likes": 300,
                "comentarios": 20,
                "canal_inscritos": 100000,
                "canal_views_totais": 9000000,
            },
            {
                "video_id": "vidB",
                "titulo": "Avaliação Territory",
                "canal_id": "canalX",
                "canal_nome": "Canal X",
                "views": 800,
                "likes": 40,
                "comentarios": 5,
                "canal_inscritos": 100000,
                "canal_views_totais": 9000000,
            },
        ],
        "channels": {
            "canalX": {
                "nome": "Canal X",
                "criado_em": "2019-01-01T00:00:00Z",
                "inscritos": 100000,
                "views_totais": 9000000,
                "videos_totais": 300,
            }
        },
        "transcripts": {
            "vidA": {
                "language": "pt",
                "gerada_automaticamente": True,
                "clean_text": "Esse carro e muito bom",
                "palavras": 5,
                "caracteres": 22,
            }
        },
        "comments": {
            "vidA": [
                {
                    "comment_id": "cmt1",
                    "autor": "usuario1",
                    "texto": "Muito bom o carro",
                    "likes": 10,
                    "respostas": 1,
                    "publicado_em": "2026-03-02T00:00:00Z",
                    "refined_record_id": "cmt_abc123",
                }
            ]
        },
    }
    run.trust = {
        "analises": [
            {
                "prompt_id": "prompt1",
                "prompt_name": "Percepção",
                "prompt_version": 2,
                "input_type": "transcript",
                "escopo": "vidA",
                "source": {"video_id": "vidA"},
                "agent": {"provider": "groq", "model": "modelo-teste"},
                "success": True,
                "output": {"velocidade": "boa"},
                "error": None,
            },
            {
                "prompt_id": "prompt2",
                "prompt_name": "Sentimento",
                "prompt_version": 1,
                "input_type": "dataset",
                "escopo": "dataset",
                "source": {"video_ids": ["vidA", "vidB"]},
                "agent": {"provider": "groq", "model": "modelo-teste"},
                "success": False,
                "output": None,
                "error": {"type": "RateLimitError", "message": "limite atingido"},
            },
        ]
    }
    return run


def principal() -> int:
    print("\n=== 1. Persistência das 5 tabelas ===")

    run = montar_run()
    db.salvar_tabelas_execucao(run)

    tabelas = db.tabelas_da_execucao(run.id)

    checar(set(tabelas.keys()) == {"videos", "channels", "transcripts", "comments", "insights"},
           "as 5 tabelas estão presentes na resposta")
    checar(len(tabelas["videos"]) == 2, f"2 vídeos persistidos (obtido {len(tabelas['videos'])})")
    checar(len(tabelas["channels"]) == 1, "1 canal persistido")
    checar(len(tabelas["transcripts"]) == 1, "1 transcrição persistida")
    checar(len(tabelas["comments"]) == 1, "1 comentário persistido")
    checar(len(tabelas["insights"]) == 2, "2 insights persistidos")

    print("\n=== 2. Junção de canal já embutida na tabela de vídeos ===")

    video_a = next(v for v in tabelas["videos"] if v["video_id"] == "vidA")
    checar(video_a["canal_inscritos"] == 100000,
           "vídeo carrega canal_inscritos sem precisar de join manual")
    checar(video_a["canal_views_totais"] == 9000000,
           "vídeo carrega canal_views_totais sem precisar de join manual")
    checar(video_a["canal_nome"] == "Canal X", "vídeo carrega o nome do canal")
    checar(video_a["run_id"] == run.id, "linha marcada com o run_id de origem")
    checar(video_a["pipeline_id"] == "pipeline_teste", "linha marcada com o pipeline_id de origem")

    print("\n=== 3. Insights: sucesso e falha, escopo vídeo e dataset ===")

    insight_ok = next(i for i in tabelas["insights"] if i["prompt_id"] == "prompt1")
    checar(insight_ok["success"] is True, "insight bem-sucedido marcado success=True")
    checar(insight_ok["video_id"] == "vidA", "insight de escopo por vídeo referencia o video_id")
    checar('"velocidade"' in (insight_ok["output_json"] or ""),
           "output serializado como JSON string")

    insight_falha = next(i for i in tabelas["insights"] if i["prompt_id"] == "prompt2")
    checar(insight_falha["success"] is False, "insight falho marcado success=False")
    checar(insight_falha["video_id"] is None, "insight de escopo 'dataset' não tem video_id")
    checar(insight_falha["error_type"] == "RateLimitError", "tipo do erro preservado")
    checar(insight_falha["error_message"] == "limite atingido", "mensagem do erro preservada")

    print("\n=== 4. Idempotência (rodar duas vezes não duplica) ===")

    db.salvar_tabelas_execucao(run)
    db.salvar_tabelas_execucao(run)
    tabelas2 = db.tabelas_da_execucao(run.id)

    checar(len(tabelas2["videos"]) == 2, "vídeos não duplicaram após rodar 3x")
    checar(len(tabelas2["channels"]) == 1, "canais não duplicaram após rodar 3x")
    checar(len(tabelas2["transcripts"]) == 1, "transcrições não duplicaram após rodar 3x")
    checar(len(tabelas2["comments"]) == 1, "comentários não duplicaram após rodar 3x")
    checar(len(tabelas2["insights"]) == 2, "insights não duplicaram após rodar 3x (delete+insert)")

    print("\n=== 5. Execução sem REFINED não persiste nada ===")

    run_vazio = PipelineRun(pipeline_id="pipeline_x")
    db.salvar_tabelas_execucao(run_vazio)  # não deve levantar exceção
    tabelas_vazio = db.tabelas_da_execucao(run_vazio.id)
    checar(all(len(v) == 0 for v in tabelas_vazio.values()),
           "run sem REFINED não grava nenhuma linha")

    print("\n=== 6. Execuções diferentes não se misturam ===")

    outro = montar_run()
    outro.refined["videos"] = [outro.refined["videos"][0]]  # só 1 vídeo neste run
    db.salvar_tabelas_execucao(outro)

    tabelas_outro = db.tabelas_da_execucao(outro.id)
    checar(len(tabelas_outro["videos"]) == 1, "run diferente tem sua própria contagem de vídeos")
    checar(db.tabelas_da_execucao(run.id)["videos"].__len__() == 2,
           "run original permanece com 2 vídeos (sem contaminação entre runs)")

    print("\n" + "=" * 62)
    if falhas:
        print(f"{len(falhas)} VERIFICAÇÃO(ÕES) FALHARAM:")
        for f in falhas:
            print(f"  - {f}")
    else:
        print("Todas as verificações de banco passaram.")
    print("=" * 62)

    shutil.rmtree(TEMP, ignore_errors=True)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(principal())
