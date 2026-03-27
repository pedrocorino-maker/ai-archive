"""AI Archive — ConversationScorer.

Classifica cada conversa em 3 tiers de valor estratégico para Pedro:

  TIER_1 (alto valor):
    - Precatório / legal tech / fintech jurídica
    - Negócios, B2B, SaaS, MVP, pitch deck
    - Deep research / deep think / pesquisa profunda
    - Agentes de IA, multi-agente, orquestração
    - Prompt engineering, mega-prompts, meta-prompts
    - Pipeline PDF, corpus, OCR, extração processual
    - Comparação multi-AI, benchmark, custo de tokens
    - Ideas que podem evoluir para produto (0.1% de 0.1%)

  TIER_2 (valor médio):
    - Técnico com padrão reutilizável (Python, SQL, infra)
    - Legal com padrão (mas não apenas revisão pontual)
    - Aprendizado com aplicação prática

  TIER_3 (baixo valor — arquivar, não deletar):
    - Gastronomia / receitas / restaurantes
    - Exercícios físicos / saúde pessoal sem produto
    - Tarefas pontuais sem padrão reaproveitável
    - Terapia / autoconhecimento sem produto
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import Conversation


# ---------------------------------------------------------------------------
# Padrões de classificação
# ---------------------------------------------------------------------------

_TIER1_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Precatório / jurídico / fintech
    r"precat[oó]ri",
    r"\bRPV\b",
    r"requisi[cç][aã]o de pagamento",
    r"uber\s*de\s*precat",
    r"legal\s*tech|legaltech",
    r"fintech\s*jur[ií]d",
    r"G2A|legal\s*claims",
    r"INSS\s*acident",
    r"precat[oó]rio\s*(federal|estadual|municipal|aliment)",
    r"hiper-prec|hiperprec",
    # Negócios / produto / B2B
    r"\bB2B\b|\bB2C\b|\bSaaS\b",
    r"\bMVP\b|\bstartup\b",
    r"modelo\s*de\s*neg[oó]cio",
    r"pitch\s*deck",
    r"valuation|investidor|aportar",
    r"plataforma\s*de|produto\s*digital",
    r"ideias?\s*de\s*(neg[oó]cio|produto|empresa)",
    r"0[\.,]1\s*%|top\s*0[\.,]1|melhores?\s*usu[aá]rios?",
    # Deep research / cognição
    r"deep\s*research|deep\s*think|pesquisa\s*profunda",
    r"deep\s*search|busca\s*profunda",
    r"multi.?AI|multi.?modelos|benchmark\s*(de\s*)?AI",
    r"comparar?\s*(modelos|IAs|claude|gemini|chatgpt)",
    # Agentes / orquestração
    r"agente\s*(de\s*IA|master|orquestr|mestre)",
    r"multi.?agente|orquestr[aâ]",
    r"agente\s*instalador|agente\s*pesquisa",
    r"agentes?\s*para\s*oportunidades",
    # Prompt engineering
    r"prompt\s*(engineer|complex|mestre|master|mega|meta)",
    r"mega.?prompt|meta.?prompt",
    r"cadeia\s*de\s*prompts?|chain.?of.?prompt",
    r"quebrar\s*(o\s*)?prompt|dividir\s*(o\s*)?prompt",
    r"prompt\s*de\s*prompts?",
    # Pipeline / extração / PDF
    r"pipeline\s*(PDF|process|jur[ií]d|extract)",
    r"extrat(or|or)\s*PDF|PDF\s*extract",
    r"corpus\s*(textual|JSONL|jur[ií]d)",
    r"OCR\s*(process|jur[ií]d|piepline)",
    r"law_pdf|legalpdf",
    # Custo / otimização de tokens
    r"custo\s*de\s*tokens?|economiz\w+\s*tokens?",
    r"modelo\s*mais?\s*barato|claude\s*haiku|gpt.?mini|gemini\s*flash",
    r"agregar\s*(resultado|output|resposta).*AI",
    # Arquitetura / infra de produto
    r"microsservi[cç]|event.?driven|arquitetura\s*(de\s*)?(produto|sistema)",
    r"chromadb|banco\s*vetorial|RAG\b|embeddings?\s*(local|offline)",
    # CV / perfil executivo IA
    r"AI.?augmented.*CV|currículo.*IA|executivo.*IA",
]]

_TIER3_PATTERNS: list[re.Pattern] = [re.compile(p, re.IGNORECASE) for p in [
    # Gastronomia
    r"restaurante|jantar|almo[cç]o|caf[eé]\s*(da\s*manh[aã])?",
    r"receita\s*de|culin[aá]ria|confeit\w+",
    r"leite\s*condensado|brigadeiro|bolo\s*de",
    r"onde\s*(comer|jantar|ir\s*comer)",
    # Exercício / saúde pessoal (sem produto)
    r"heel\s*raise|agachamento|muscula[cç][aã]o|academia\b",
    r"exerc[ií]cio\s+(f[ií]sico|principal|de)",
    r"treino\s+(de\s*)?(perna|bra[cç]o|costa|peito)",
    # Terapia / autoconhecimento (sem produto)
    r"autoconhecimento\s*terap[eê]utico",
    r"medita[cç][aã]o\s*guiada",
    r"terapia\s*(cognitiva|comportamental)",
    # Tarefas ultra-pontuais
    r"(revise|corrija|melhore)\s*(esta|essa|o)\s*(peti[cç][aã]o|peça|carta)\b",
    r"analise\s*(esta|essa)\s*cl[aá]usula\b",
    r"tradu[zç]a?\s*(este|esse|esta|essa)\s*(texto|documento|trecho)\b",
]]


# ---------------------------------------------------------------------------
# Dataclass de resultado
# ---------------------------------------------------------------------------

@dataclass
class ScoreResult:
    tier: int          # 1, 2 ou 3
    score: float       # 0.0 – 1.0 (maior = mais valioso)
    matched_tier1: list[str]
    matched_tier3: list[str]
    label: str         # "high_value" | "medium_value" | "low_value"


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ConversationScorer:
    """Classifica conversas por valor estratégico para curadoria e clustering."""

    def score(self, conv: "Conversation") -> ScoreResult:
        # Texto de análise: título + primeiras mensagens
        from ..models import MessageRole
        title = (conv.title or "").lower()
        snippets = [
            (m.normalized_text or m.raw_text)[:500]
            for m in (conv.messages or [])[:8]
        ]
        full_text = title + " " + " ".join(snippets)

        t1_hits = [p.pattern for p in _TIER1_PATTERNS if p.search(full_text)]
        t3_hits = [p.pattern for p in _TIER3_PATTERNS if p.search(full_text)]

        # Penalidades: conversa muito curta
        msg_count = conv.message_count or len(conv.messages or [])
        brevity_penalty = max(0.0, (3 - msg_count) * 0.05)

        # Bônus: conversa tem código → valor técnico
        has_code = any(m.code_blocks for m in (conv.messages or []))
        code_bonus = 0.10 if has_code else 0.0

        # Bônus: tem múltiplos providers (cross-AI)
        # (não disponível na conversa individual, mas futuro)

        if t1_hits and not t3_hits:
            base = 0.70 + min(len(t1_hits) * 0.05, 0.25)
            tier = 1
        elif t1_hits and t3_hits:
            # T1 ganha, mas penalizado por ruído
            base = 0.55 + min(len(t1_hits) * 0.04, 0.15)
            tier = 1
        elif t3_hits and not t1_hits:
            base = 0.10 + min(len(t3_hits) * 0.02, 0.10)
            tier = 3
        else:
            # Sem match claro → TIER_2 por default
            base = 0.40
            tier = 2

        score = min(1.0, max(0.0, base + code_bonus - brevity_penalty))
        label = {1: "high_value", 2: "medium_value", 3: "low_value"}[tier]

        return ScoreResult(
            tier=tier,
            score=round(score, 3),
            matched_tier1=t1_hits,
            matched_tier3=t3_hits,
            label=label,
        )

    def score_batch(
        self, convs: list["Conversation"]
    ) -> dict[str, ScoreResult]:
        """Retorna dict conversation.id -> ScoreResult."""
        return {c.id: self.score(c) for c in convs}

    def filter_for_cluster(
        self,
        convs: list["Conversation"],
        min_tier: int = 3,
    ) -> list["Conversation"]:
        """Remove conversas abaixo de min_tier do clustering.
        min_tier=3 → inclui tudo; =2 → exclui TIER_3; =1 → só TIER_1.
        """
        results = self.score_batch(convs)
        return [c for c in convs if results[c.id].tier <= min_tier]
