"""AI Archive — GeminiScanner.

Varre diretórios (Linux e Windows via /mnt/c/) em busca de arquivos
de conversas Gemini que ainda NÃO foram importados para o archive.

Caminhos inspecionados por default:
  ~/Downloads/
  ~/Downloads/AI-Archives/
  /mnt/c/Users/<user>/Downloads/           (Windows via WSL)
  /mnt/c/Users/<user>/Downloads/AI-Archives/
  /mnt/c/Users/<user>/Documents/

Uso via CLI:
  uv run ai-archive import scan-gemini [--path PATH] [--import]
"""
from __future__ import annotations

import getpass
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..logging_config import get_logger

logger = get_logger("importers.gemini_scanner")


# ---------------------------------------------------------------------------
# Caminhos candidatos
# ---------------------------------------------------------------------------

def _default_search_paths() -> list[Path]:
    home = Path.home()
    username = getpass.getuser()
    candidates = [
        home / "Downloads",
        home / "Downloads" / "AI-Archives",
        home / "Downloads" / "AI-Archives" / "legacy",
        # Windows via WSL
        Path(f"/mnt/c/Users/{username}/Downloads"),
        Path(f"/mnt/c/Users/{username}/Downloads/AI-Archives"),
        Path(f"/mnt/c/Users/{username}/Downloads/AI-Archives/legacy"),
        Path(f"/mnt/c/Users/{username}/Documents"),
        Path(f"/mnt/c/Users/{username}/Documents/consolidacao"),
    ]
    return [p for p in candidates if p.exists()]


# ---------------------------------------------------------------------------
# Resultado do scan
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    total_found: int = 0
    already_imported: int = 0
    new_files: list[Path] = field(default_factory=list)
    error_files: list[str] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return len(self.new_files)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class GeminiScanner:
    """Detecta arquivos Gemini não importados nos caminhos padrão."""

    # Padrões de nome de arquivo tipicamente Gemini
    _GEMINI_STEM_RE = re.compile(
        r"(gemini|bard|google[-_]ai|AAAi|AI[-_]Archive"
        r"|precat[oó]rio|legal|agente|prompt|deep[-_]research"
        r"|precatório|juridico|jur[ií]dico"
        r"|\w+[-_]\d{4}-\d{2}-\d{2}[-_]\d{2}-\d{2}-\d{2})",  # timestamp suffix
        re.IGNORECASE,
    )

    def __init__(self, db_conn: Any, settings: Any) -> None:
        self._db = db_conn
        self._settings = settings
        self._known_ids = self._load_known_ids()

    def _load_known_ids(self) -> set[str]:
        """Carrega todos os provider_conversation_id já no banco."""
        try:
            cur = self._db.execute(
                "SELECT provider_conversation_id FROM conversations WHERE provider='gemini'"
            )
            return {row[0] for row in cur.fetchall()}
        except Exception as exc:
            logger.warning("Could not load known IDs: %s", exc)
            return set()

    def _file_provider_id(self, path: Path) -> str:
        """Calcula o provider_conversation_id que o GeminiDownloadImporter geraria."""
        stem = path.stem
        stem_no_ts = re.sub(r"-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", stem).strip("-_")
        if stem_no_ts:
            return "gemini-" + hashlib.sha256(stem_no_ts.encode()).hexdigest()[:16]
        return "gemini-" + hashlib.sha256(stem.encode()).hexdigest()[:16]

    def _is_likely_gemini(self, path: Path) -> bool:
        """Heurística: é provável conversa Gemini?"""
        # Exclude known non-conversation files
        skip_stems = {"index", "readme", "como-usar", "como_usar", "changelog", "license"}
        if path.stem.lower().replace("-", "_") in skip_stems:
            return False
        # Exclude ai-archive export outputs (topics HTML)
        if "ai-archive" in str(path) and "topics" in str(path):
            return False
        # Timestamp suffix (Gemini export format) is a strong signal
        if re.search(r"-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.(html|txt)$", path.name, re.I):
            return True
        # Filename matches known patterns
        if self._GEMINI_STEM_RE.search(path.stem):
            return True
        # HTML file larger than 10KB — might be a saved conversation
        if path.suffix.lower() in (".html", ".htm") and path.stat().st_size > 10_000:
            return True
        return False

    def scan(self, extra_paths: list[Path] | None = None) -> ScanResult:
        """Escaneia os caminhos padrão + extra_paths e retorna arquivos não importados."""
        search_paths = _default_search_paths()
        if extra_paths:
            search_paths += [p for p in extra_paths if p.exists()]

        result = ScanResult()
        seen_stems: set[str] = set()  # evitar contar .html e .txt do mesmo arquivo duas vezes

        for base in search_paths:
            logger.info("Scanning: %s", base)
            for suffix in ("*.html", "*.htm", "*.txt", "*.json"):
                for path in base.glob(suffix):
                    if not path.is_file():
                        continue
                    if not self._is_likely_gemini(path):
                        continue

                    result.total_found += 1
                    pid = self._file_provider_id(path)

                    # Dedup: .html e .txt do mesmo stem já contam como 1
                    stem_key = re.sub(r"-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}$", "", path.stem)
                    if stem_key in seen_stems:
                        # Preferir .html sobre .txt (mais rico); se já temos .html, pular .txt
                        continue
                    seen_stems.add(stem_key)

                    if pid in self._known_ids:
                        result.already_imported += 1
                    else:
                        result.new_files.append(path)

        logger.info(
            "Scan complete: %d total, %d already imported, %d new",
            result.total_found, result.already_imported, result.new_count,
        )
        return result

    def import_new(
        self,
        result: ScanResult,
        dry_run: bool = False,
    ) -> tuple[int, int]:
        """Importa os arquivos novos encontrados pelo scan.

        Retorna (imported, errors).
        """
        if not result.new_files:
            logger.info("Nenhum arquivo novo para importar.")
            return 0, 0

        if dry_run:
            logger.info("[dry-run] Importaria %d arquivos:", result.new_count)
            for p in result.new_files:
                logger.info("  %s", p)
            return 0, 0

        from .gemini_html import GeminiDownloadImporter
        importer = GeminiDownloadImporter(self._settings, self._db)

        imported = 0
        errors = 0
        for path in result.new_files:
            try:
                stats = importer.import_path(path)
                imported += stats.imported
                errors += stats.errors
            except Exception as exc:
                errors += 1
                logger.warning("Falha ao importar %s: %s", path, exc)

        logger.info("Import concluído: %d importados, %d erros", imported, errors)
        return imported, errors
