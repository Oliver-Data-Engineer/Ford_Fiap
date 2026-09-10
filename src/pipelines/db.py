"""
db.py
Banco local (SQLite via SQLAlchemy) que guarda as tabelas geradas por cada
execução de pipeline — vídeos, canais, transcrições, comentários e insights.

É a mesma estrutura que a UI mostra no modo "Tabela" do Playground
(`GET /pipelines/runs/{run_id}/tables`): a interface e o banco descrevem
exatamente a mesma coisa, uma tabela por tipo de dado.

Escopo desta camada: persistência das camadas REFINED/TRUST de uma execução
já concluída. Não substitui `store.py` (prompts/pipelines/metadados de run
continuam em JSON) — é aditivo, por ora.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import Boolean, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from src.config import settings
from src.pipelines.models import PipelineRun

logger = logging.getLogger(__name__)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


# ─────────────────────────────────────────────────────────────────────────────
# Tabelas
# ─────────────────────────────────────────────────────────────────────────────

class VideoRow(Base):
    """Um vídeo por linha — já nasce com o dado do canal embutido (join do REFINED)."""

    __tablename__ = "videos"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    video_id: Mapped[str] = mapped_column(String, primary_key=True)
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True)

    titulo: Mapped[str | None] = mapped_column(Text, nullable=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    canal_id: Mapped[str | None] = mapped_column(String, nullable=True)
    canal_nome: Mapped[str | None] = mapped_column(String, nullable=True)
    publicado_em: Mapped[str | None] = mapped_column(String, nullable=True)
    idioma: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    duracao_segundos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comentarios: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canal_inscritos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canal_views_totais: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_agora)


class ChannelRow(Base):
    __tablename__ = "channels"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    canal_id: Mapped[str] = mapped_column(String, primary_key=True)

    nome: Mapped[str | None] = mapped_column(String, nullable=True)
    criado_em: Mapped[str | None] = mapped_column(String, nullable=True)
    inscritos: Mapped[int | None] = mapped_column(Integer, nullable=True)
    views_totais: Mapped[int | None] = mapped_column(Integer, nullable=True)
    videos_totais: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_agora)


class TranscriptRow(Base):
    __tablename__ = "transcripts"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    video_id: Mapped[str] = mapped_column(String, primary_key=True)

    language: Mapped[str | None] = mapped_column(String, nullable=True)
    gerada_automaticamente: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    clean_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    palavras: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caracteres: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_agora)


class CommentRow(Base):
    __tablename__ = "comments"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    comment_id: Mapped[str] = mapped_column(String, primary_key=True)

    video_id: Mapped[str | None] = mapped_column(String, nullable=True)
    autor: Mapped[str | None] = mapped_column(String, nullable=True)
    texto: Mapped[str | None] = mapped_column(Text, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    respostas: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publicado_em: Mapped[str | None] = mapped_column(String, nullable=True)
    refined_record_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_agora)


class InsightRow(Base):
    """Uma linha por chamada ao agente — o que a camada TRUST produziu."""

    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True)
    video_id: Mapped[str | None] = mapped_column(String, nullable=True)  # nulo p/ escopo "dataset"
    escopo: Mapped[str | None] = mapped_column(String, nullable=True)

    prompt_id: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_name: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_type: Mapped[str | None] = mapped_column(String, nullable=True)

    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)

    success: Mapped[bool] = mapped_column(Boolean, default=False)
    output_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_agora)


# ─────────────────────────────────────────────────────────────────────────────
# Engine / sessão
# ─────────────────────────────────────────────────────────────────────────────

_engine = None


def _obter_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(settings.database_url, future=True)
        Base.metadata.create_all(_engine)
    return _engine


def _sessao() -> Session:
    return Session(_obter_engine())


# ─────────────────────────────────────────────────────────────────────────────
# Escrita
# ─────────────────────────────────────────────────────────────────────────────

def salvar_tabelas_execucao(run: PipelineRun) -> None:
    """
    Persiste as tabelas geradas por uma execução (RAW→REFINED→TRUST já
    resolvido em `run.refined`/`run.trust`). Não faz nada se a execução não
    chegou a produzir REFINED (falhou antes disso).

    Chamado pelo executor após toda execução — Playground ad-hoc ou pipeline
    salva (manual/Airflow) — pelos mesmos motivos que unificam o resto da
    engine (Regra 4 e 10 da spec).
    """
    refined = run.refined or {}
    if not refined.get("videos"):
        return

    run_id = run.id
    pipeline_id = run.pipeline_id
    agora = _agora()

    with _sessao() as sessao:
        for video in refined.get("videos", []):
            sessao.merge(VideoRow(
                run_id=run_id,
                video_id=video.get("video_id"),
                pipeline_id=pipeline_id,
                titulo=video.get("titulo"),
                descricao=video.get("descricao"),
                canal_id=video.get("canal_id"),
                canal_nome=video.get("canal_nome"),
                publicado_em=video.get("publicado_em"),
                idioma=video.get("idioma"),
                thumbnail=video.get("thumbnail"),
                url=video.get("url"),
                duracao_segundos=video.get("duracao_segundos"),
                views=video.get("views"),
                likes=video.get("likes"),
                comentarios=video.get("comentarios"),
                canal_inscritos=video.get("canal_inscritos"),
                canal_views_totais=video.get("canal_views_totais"),
                created_at=agora,
            ))

        for canal_id, canal in (refined.get("channels") or {}).items():
            sessao.merge(ChannelRow(
                run_id=run_id,
                canal_id=canal_id,
                nome=canal.get("nome"),
                criado_em=canal.get("criado_em"),
                inscritos=canal.get("inscritos"),
                views_totais=canal.get("views_totais"),
                videos_totais=canal.get("videos_totais"),
                created_at=agora,
            ))

        for video_id, transcricao in (refined.get("transcripts") or {}).items():
            sessao.merge(TranscriptRow(
                run_id=run_id,
                video_id=video_id,
                language=transcricao.get("language"),
                gerada_automaticamente=transcricao.get("gerada_automaticamente"),
                clean_text=transcricao.get("clean_text"),
                palavras=transcricao.get("palavras"),
                caracteres=transcricao.get("caracteres"),
                created_at=agora,
            ))

        for video_id, lista in (refined.get("comments") or {}).items():
            for comentario in lista:
                sessao.merge(CommentRow(
                    run_id=run_id,
                    comment_id=comentario.get("comment_id"),
                    video_id=video_id,
                    autor=comentario.get("autor"),
                    texto=comentario.get("texto"),
                    likes=comentario.get("likes"),
                    respostas=comentario.get("respostas"),
                    publicado_em=comentario.get("publicado_em"),
                    refined_record_id=comentario.get("refined_record_id"),
                    created_at=agora,
                ))

        # Insights não têm chave natural estável entre reexecuções — substitui
        # o conjunto inteiro do run para manter a persistência idempotente.
        sessao.query(InsightRow).filter(InsightRow.run_id == run_id).delete()
        for analise in (run.trust or {}).get("analises", []):
            erro = analise.get("error") or {}
            source = analise.get("source") or {}
            sessao.add(InsightRow(
                run_id=run_id,
                pipeline_id=pipeline_id,
                video_id=source.get("video_id"),
                escopo=analise.get("escopo"),
                prompt_id=analise.get("prompt_id"),
                prompt_name=analise.get("prompt_name"),
                prompt_version=analise.get("prompt_version"),
                input_type=analise.get("input_type"),
                provider=(analise.get("agent") or {}).get("provider"),
                model=(analise.get("agent") or {}).get("model"),
                success=bool(analise.get("success")),
                output_json=(
                    json.dumps(analise.get("output"), ensure_ascii=False, default=str)
                    if analise.get("output") is not None else None
                ),
                error_type=erro.get("type"),
                error_message=erro.get("message"),
                created_at=agora,
            ))

        sessao.commit()

    logger.info("[db] tabelas da execução %s persistidas.", run_id)


# ─────────────────────────────────────────────────────────────────────────────
# Leitura
# ─────────────────────────────────────────────────────────────────────────────

def _serializar(linha) -> dict:
    """dict de uma linha SQLAlchemy, sem o estado interno da sessão."""
    return {c.name: getattr(linha, c.name) for c in linha.__table__.columns}


def tabelas_da_execucao(run_id: str) -> dict[str, list[dict]]:
    """
    As 5 tabelas persistidas para uma execução — alimenta o modo "Tabela"
    do Playground. Listas vazias se o run existe mas não gerou dados.
    """
    with _sessao() as sessao:
        return {
            "videos": [_serializar(r) for r in sessao.query(VideoRow).filter(VideoRow.run_id == run_id)],
            "channels": [_serializar(r) for r in sessao.query(ChannelRow).filter(ChannelRow.run_id == run_id)],
            "transcripts": [_serializar(r) for r in sessao.query(TranscriptRow).filter(TranscriptRow.run_id == run_id)],
            "comments": [_serializar(r) for r in sessao.query(CommentRow).filter(CommentRow.run_id == run_id)],
            "insights": [_serializar(r) for r in sessao.query(InsightRow).filter(InsightRow.run_id == run_id)],
        }
