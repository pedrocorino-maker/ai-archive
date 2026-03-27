"""AI Archive — Export Pipeline.

Converts curated topic Markdown files to styled HTML and copies them to
a user-readable output folder (default: Windows Downloads\\ai-archive\\).

Run with:  uv run ai-archive export
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown as md_lib
import yaml

from ..logging_config import get_logger

logger = get_logger("pipeline.export")

# ---------------------------------------------------------------------------
# HTML page template (self-contained, no external dependencies)
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
  :root {{
    --bg:      #0d0d0d;
    --card:    #161616;
    --accent:  #60a5fa;
    --text:    #e2e8f0;
    --muted:   #94a3b8;
    --border:  #2a2a2a;
    --code-bg: #1e1e2e;
    --radius:  8px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.7;
    padding: 2rem 1rem;
  }}
  .page {{ max-width: 860px; margin: 0 auto; }}
  header {{
    border-bottom: 2px solid var(--accent);
    padding-bottom: 1rem;
    margin-bottom: 2rem;
  }}
  header h1 {{ font-size: 1.8rem; color: var(--accent); }}
  header .meta {{ color: var(--muted); font-size: 0.85rem; margin-top: .3rem; }}
  .tag {{
    display: inline-block;
    background: #1f2937;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1px 8px;
    font-size: 0.78rem;
    margin: 2px;
    color: var(--muted);
  }}
  .tag-tier1 {{ background: #1a2e1a; border-color: #22c55e; color: #86efac; }}
  .tag-tier3 {{ background: #1f1a1a; border-color: #4b5563; color: #6b7280; }}
  .content h2 {{
    font-size: 1.15rem;
    color: var(--accent);
    margin: 2rem 0 .6rem;
    padding-bottom: .3rem;
    border-bottom: 1px solid var(--border);
  }}
  .content h3 {{ font-size: 1rem; margin: 1.2rem 0 .4rem; color: #cbd5e1; }}
  .content p {{ margin: .6rem 0; }}
  .content ul, .content ol {{ margin: .6rem 0 .6rem 1.5rem; }}
  .content li {{ margin: .25rem 0; }}
  .content blockquote {{
    border-left: 3px solid var(--accent);
    margin: .8rem 0;
    padding: .4rem 1rem;
    background: #111827;
    border-radius: 0 var(--radius) var(--radius) 0;
    color: var(--muted);
    font-style: italic;
  }}
  .content pre {{
    background: #0a0a12;
    color: #cdd6f4;
    border-radius: var(--radius);
    padding: 1rem 1.2rem;
    overflow-x: auto;
    margin: .8rem 0;
    font-size: .85rem;
    line-height: 1.5;
    border: 1px solid var(--border);
  }}
  .content code {{
    background: var(--code-bg);
    padding: 1px 5px;
    border-radius: 3px;
    font-size: .88em;
    font-family: "Cascadia Code", "Fira Code", "Courier New", monospace;
    color: #a5f3fc;
  }}
  .content pre code {{
    background: none;
    padding: 0;
    font-size: inherit;
    color: #cdd6f4;
  }}
  .content a {{ color: var(--accent); text-decoration: none; }}
  .content a:hover {{ text-decoration: underline; }}
  .back-link {{
    display: inline-block;
    margin-bottom: 1.5rem;
    color: var(--accent);
    font-size: .9rem;
    text-decoration: none;
  }}
  .back-link:hover {{ text-decoration: underline; }}
  footer {{
    margin-top: 3rem;
    padding-top: 1rem;
    border-top: 1px solid var(--border);
    color: var(--muted);
    font-size: .8rem;
    text-align: center;
  }}
</style>
</head>
<body>
<div class="page">
  <a class="back-link" href="../index.html">← Voltar ao índice</a>
  <header>
    <h1>{title}</h1>
    <div class="meta">
      {providers_line}
      {tags_line}
      {updated_line}
    </div>
  </header>
  <div class="content">
    {body_html}
  </div>
  <footer>Gerado pelo AI Archive em {generated_at}</footer>
</div>
</body>
</html>
"""

_INDEX_TEMPLATE = """\
<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AI Archive — Índice de Tópicos</title>
<style>
  :root {{
    --bg:      #0d0d0d;
    --card:    #161616;
    --accent:  #60a5fa;
    --text:    #e2e8f0;
    --muted:   #94a3b8;
    --border:  #2a2a2a;
    --code-bg: #1e1e2e;
    --radius:  8px;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg); color: var(--text); line-height: 1.6;
    padding: 2rem 1rem;
  }}
  .page {{ max-width: 900px; margin: 0 auto; }}
  header {{ border-bottom: 2px solid var(--accent); padding-bottom: 1rem; margin-bottom: 2rem; }}
  header h1 {{ font-size: 2rem; color: var(--accent); }}
  header p {{ color: var(--muted); margin-top: .4rem; }}
  .stats {{ display: flex; gap: 2rem; margin-bottom: 2rem; }}
  .stat {{ background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
           padding: .8rem 1.4rem; text-align: center; }}
  .stat .num {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
  .stat .lbl {{ font-size: .8rem; color: var(--muted); text-transform: uppercase; letter-spacing: .05em; }}
  .search-box {{
    width: 100%; padding: .6rem 1rem; font-size: 1rem;
    background: var(--card); color: var(--text);
    border: 1px solid var(--border); border-radius: var(--radius);
    margin-bottom: 1.5rem; outline: none;
  }}
  .search-box:focus {{ border-color: var(--accent); }}
  .search-box::placeholder {{ color: var(--muted); }}
  .topic-grid {{ display: grid; gap: .7rem; }}
  .topic-card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 1rem 1.2rem;
    text-decoration: none; color: inherit;
    transition: border-color .15s, background .15s;
  }}
  .topic-card:hover {{ border-color: var(--accent); background: #1a1a1a; }}
  .topic-card h3 {{ color: var(--accent); font-size: 1rem; margin-bottom: .3rem; }}
  .topic-card .meta {{ font-size: .8rem; color: var(--muted); }}
  .topic-card.tier1 {{ border-left: 3px solid #22c55e; }}
  .topic-card.tier3 {{ opacity: .65; }}
  .tag {{
    display: inline-block; background: #1f2937;
    border: 1px solid var(--border); border-radius: 4px;
    padding: 1px 7px; font-size: .75rem; margin: 1px; color: var(--muted);
  }}
  .provider-badge {{
    display: inline-block; padding: 1px 8px; border-radius: 10px;
    font-size: .72rem; font-weight: 600; margin-right: 4px;
  }}
  .provider-chatgpt {{ background: #052e16; color: #86efac; border: 1px solid #166534; }}
  .provider-gemini  {{ background: #0c1a3a; color: #93c5fd; border: 1px solid #1e3a8a; }}
  footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--border);
            color: var(--muted); font-size: .8rem; text-align: center; }}
</style>
</head>
<body>
<div class="page">
  <header>
    <h1>🗂 AI Archive</h1>
    <p>Histórico de conversas com IA — organizado por tópico</p>
  </header>
  <div class="stats">
    <div class="stat"><div class="num">{total_topics}</div><div class="lbl">Tópicos</div></div>
    <div class="stat"><div class="num">{total_convs}</div><div class="lbl">Conversas</div></div>
    <div class="stat"><div class="num">{generated_date}</div><div class="lbl">Atualizado</div></div>
  </div>
  <input class="search-box" type="text" placeholder="Buscar tópico..." id="search" oninput="filterCards()"/>
  <div class="topic-grid" id="grid">
    {cards_html}
  </div>
  <footer>Gerado pelo AI Archive em {generated_at}</footer>
</div>
<script>
function filterCards() {{
  const q = document.getElementById('search').value.toLowerCase();
  document.querySelectorAll('.topic-card').forEach(card => {{
    card.style.display = card.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML front-matter from body text."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                meta = yaml.safe_load(parts[1]) or {}
                return meta, parts[2].strip()
            except Exception:
                pass
    return {}, text.strip()


def _md_to_html(body: str) -> str:
    """Convert Markdown body to HTML."""
    return md_lib.markdown(
        body,
        extensions=["fenced_code", "tables", "nl2br"],
    )


def _provider_badges(providers: list[str]) -> str:
    badges = []
    for p in providers:
        cls = f"provider-{p.lower()}"
        badges.append(f'<span class="provider-badge {cls}">{p.upper()}</span>')
    return " ".join(badges)


def _tag_spans(tags: list[str]) -> str:
    return " ".join(f'<span class="tag">{t}</span>' for t in tags[:10])


# ---------------------------------------------------------------------------
# Main export class
# ---------------------------------------------------------------------------

class ExportPipeline:
    """Converts curated topics to HTML and copies to a Windows-readable folder."""

    def __init__(self, settings: object, output_dir: Path | None = None) -> None:
        self._settings = settings
        self._curated_dir: Path = settings.curated_dir / "topics"  # type: ignore[attr-defined]
        # Default: Windows Downloads\ai-archive
        self._out: Path = output_dir or Path("/mnt/c/Users/pedro/Downloads/ai-archive")

    def run(self) -> dict[str, int]:
        """Export all topics. Returns stats dict."""
        if not self._curated_dir.exists():
            logger.warning("Curated topics dir not found: %s", self._curated_dir)
            return {"exported": 0, "skipped": 0, "errors": 0}

        topics_out = self._out / "topics"
        topics_out.mkdir(parents=True, exist_ok=True)

        now = datetime.now(tz=timezone.utc)
        now_str = now.strftime("%d/%m/%Y %H:%M")
        date_str = now.strftime("%d/%m/%Y")

        topic_dirs = sorted(
            d for d in self._curated_dir.iterdir() if d.is_dir()
        )

        exported = skipped = errors = 0
        index_cards: list[str] = []
        total_convs = 0

        for topic_dir in topic_dirs:
            md_files = list(topic_dir.glob("*.md"))
            if not md_files:
                skipped += 1
                continue

            md_path = md_files[0]
            try:
                raw = md_path.read_text(encoding="utf-8")
                meta, body = _parse_frontmatter(raw)

                title = meta.get("title", topic_dir.name)
                tags: list[str] = meta.get("tags") or []
                providers: list[str] = meta.get("providers") or []
                conv_count: int = meta.get("conversation_count", 1)
                updated_at: str = str(meta.get("updated_at", "") or "")
                slug: str = meta.get("slug", topic_dir.name)

                total_convs += conv_count

                # -- per-topic HTML --
                body_html = _md_to_html(body)
                providers_line = _provider_badges(providers) if providers else ""
                tags_line = _tag_spans(tags) if tags else ""
                updated_line = (
                    f'<span style="margin-left:.5rem">Atualizado: {updated_at[:10]}</span>'
                    if updated_at else ""
                )

                page_html = _HTML_TEMPLATE.format(
                    title=title,
                    providers_line=providers_line,
                    tags_line=tags_line,
                    updated_line=updated_line,
                    body_html=body_html,
                    generated_at=now_str,
                )

                out_file = topics_out / f"{slug}.html"
                out_file.write_text(page_html, encoding="utf-8")

                # -- index card --
                provider_badges_html = _provider_badges(providers)
                # Tier class for visual priority
                tier_class = ""
                if any("tier1" in t for t in tags):
                    tier_class = " tier1"
                elif any("tier3" in t for t in tags):
                    tier_class = " tier3"
                # Filter out internal tier/score tags from display
                display_tags = [t for t in tags if not t.startswith(("tier", "score_"))]
                tags_html = _tag_spans(display_tags[:5])
                card = (
                    f'<a class="topic-card{tier_class}" href="topics/{slug}.html">'
                    f'<h3>{title}</h3>'
                    f'<div class="meta">'
                    f'{provider_badges_html} '
                    f'<span>{conv_count} conversa(s)</span>'
                    f'</div>'
                    f'<div style="margin-top:.4rem">{tags_html}</div>'
                    f'</a>'
                )
                index_cards.append(card)
                exported += 1
                logger.info("Exported: %s → %s", slug, out_file.name)

            except Exception as exc:
                logger.warning("Error exporting %s: %s", topic_dir.name, exc)
                errors += 1

        # -- write index.html --
        index_html = _INDEX_TEMPLATE.format(
            total_topics=exported,
            total_convs=total_convs,
            generated_date=date_str,
            generated_at=now_str,
            cards_html="\n    ".join(index_cards),
        )
        index_path = self._out / "index.html"
        index_path.write_text(index_html, encoding="utf-8")

        # -- write COMO-USAR.txt --
        self._write_guide()

        logger.info(
            "Export complete: %d topics → %s  (skipped=%d errors=%d)",
            exported, self._out, skipped, errors,
        )
        return {"exported": exported, "skipped": skipped, "errors": errors}

    def _write_guide(self) -> None:
        guide = self._out / "COMO-USAR.txt"
        guide.write_text(
            """\
╔══════════════════════════════════════════════════════════════════════════╗
║                    AI ARCHIVE — GUIA RÁPIDO                             ║
╚══════════════════════════════════════════════════════════════════════════╝

O que é isso?
─────────────
Esta pasta contém TODAS as suas conversas com ChatGPT e Gemini,
organizadas por assunto (tópico), em formato HTML (página web).

Como abrir os arquivos?
────────────────────────
• Abra o arquivo  index.html  para ver a LISTA COMPLETA de tópicos.
  → Clique duas vezes no arquivo → ele abre no seu navegador (Chrome, Edge, etc.)
  → Use a caixa de busca para filtrar por assunto

• Cada tópico tem sua própria página HTML com:
  - Resumo executivo do assunto
  - Conclusões e decisões importantes
  - Trechos de código (se houver)
  - Links para as conversas originais
  - Tags e palavras-chave

Como atualizar com novas conversas?
─────────────────────────────────────
No terminal do WSL (Ubuntu), dentro da pasta  ~/ai-archive, rode:

  uv run ai-archive crawl --provider chatgpt          ← captura novas conversas
  uv run ai-archive crawl --provider chatgpt --backfill --full   ← captura TODAS (demora)
  uv run ai-archive normalize                          ← processa o texto
  uv run ai-archive cluster                            ← agrupa por assunto
  uv run ai-archive curate                             ← gera os resumos
  uv run ai-archive export                             ← atualiza esta pasta

Ou rode tudo de uma vez:
  uv run ai-archive crawl --provider chatgpt && uv run ai-archive normalize && uv run ai-archive cluster && uv run ai-archive curate && uv run ai-archive export

Como importar conversas do Gemini salvas manualmente?
──────────────────────────────────────────────────────
Salve a página do Gemini (Ctrl+S no navegador) como .html na pasta Downloads.
Depois rode:
  uv run ai-archive import gemini-downloads

Onde ficam os arquivos?
────────────────────────
• index.html         → Página inicial com todos os tópicos
• topics/            → Uma página HTML por tópico
• COMO-USAR.txt      → Este arquivo

Os arquivos são SEMPRE substituídos pela versão mais recente ao rodar "export".

──────────────────────────────────────────────────────────────────────────
Dúvidas? Consulte a documentação em:  ~/ai-archive/README.md
""",
            encoding="utf-8",
        )
