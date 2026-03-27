# AI Archive — Descritivo Tecnico Completo

> Sistema local-first de captura, organizacao, curadoria e sincronizacao do historico de conversas ChatGPT e Gemini.
> Inclui correlacao com motor de curadoria standalone, daemon noturno e curation-engine empacotado.

**Ultima atualizacao:** 2026-03-27
**Localizacao principal:** `/home/pedro/ai-archive/`
**Linguagem:** Python 3.12+ | **Build:** Hatchling | **Gerenciador:** uv
**Linhas de codigo:** ~8.400 (src) + ~1.200 (tests) | **Testes:** 97 funcoes de teste passando
**Dependencias core:** Playwright, Pydantic v2, sentence-transformers, HDBSCAN, scikit-learn, BeautifulSoup4, Rich, Typer, orjson, SQLModel

---

## 1. Arquitetura Geral

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      CLI Typer (ai-archive)                              │
│  doctor · auth · crawl · normalize · cluster · curate · export · sync   │
└────────────────────────────┬─────────────────────────────────────────────┘
                             │
              ┌──────────────▼──────────────┐
              │     Browser Auth Layer       │
              │  attach_cdp | managed_profile│
              │  BrowserSession (Playwright) │
              └──────────┬──────────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
   ┌──────▼───────┐             ┌───────▼──────┐
   │   ChatGPT    │             │    Gemini     │
   │   Adapter    │             │   Adapter     │
   │ enumerate +  │             │ enumerate +   │
   │  extract     │             │  extract      │
   └──────┬───────┘             └───────┬───────┘
          └──────────────┬──────────────┘
                         │
              ┌──────────▼──────────────┐
              │     Pipeline 5 Estagios  │
              │  Crawl → Normalize →     │
              │  Cluster → Curate →      │
              │  Drive Sync              │
              └──────────┬──────────────┘
                         │
          ┌──────────────┴──────────────┐
          │                             │
   ┌──────▼───────┐             ┌───────▼──────┐
   │  Local Store │             │ Google Drive  │
   │  SQLite WAL  │             │ (opcional)    │
   │  data/       │             └───────────────┘
   └──────────────┘
```

O sistema opera inteiramente local. Nenhuma credencial e armazenada ou transmitida pelo codigo. Login e manual, em janela Chrome visivel. Google Drive e opt-in.

---

## 2. Pipeline de 5 Estagios

### 2.1 Estagio 1 — Crawl

**Arquivo:** `src/ai_archive/pipeline/crawl.py` (~221 linhas)
**Providers:** `providers/chatgpt.py` (~400 linhas), `providers/gemini.py` (~400 linhas)

O CrawlPipeline conecta ao Chrome via CDP (Chrome DevTools Protocol) na porta 9222, reusando a sessao ja autenticada do usuario. Para cada provider habilitado:

1. Detecta estado de autenticacao (login_indicator, challenge_indicator via seletores YAML)
2. Se nao autenticado, solicita login manual interativo com timeout de 300s
3. Enumera conversas da sidebar (DOM scraping com seletores configuraveis)
4. Para cada conversa: navega, extrai DOM HTML completo, parseia mensagens e code blocks
5. Computa hash SHA-256 do conteudo; em modo incremental, pula conversas inalteradas
6. Salva HTML raw em `data/raw/{provider}/YYYY/MM/{provider_id}.html`
7. Aplica jitter randomizado entre requisicoes (600-1400ms default) para anti-rate-limiting

**Backfill Mode** (`--backfill`):
- Exclusivo para ChatGPT
- Fase 1: SidebarHarvester (`chatgpt_backfill.py`, ~440 linhas) faz scroll lento no container da sidebar
- Duracao: minimo 45 minutos, maximo 90 minutos (configuravel)
- Scroll via JavaScript injetado que busca o scroll container por 3 estrategias (walk-up do link /c/, scan de overflow-y, fallback para nav)
- Persiste estado em `data/state/chatgpt_backfill_index.json` para resume
- Deteccao de estagnacao: para apos 8 rounds sem novas conversas
- Batch size: 25 conversas por lote, sleep 5-45s entre batches
- Fase 2: extracao normal das conversas harvested

**Seletores DOM:**
- Configuraveis em `config/selectors.chatgpt.yaml` e `config/selectors.gemini.yaml`
- Multiplos seletores por funcao (fallback chain)
- Screenshot + HTML snapshot automaticos em caso de erro

### 2.2 Estagio 2 — Normalize

**Arquivo:** `src/ai_archive/pipeline/normalize.py` (~88 linhas)

Converte HTML raw em artefatos estruturados:
- Normaliza whitespace de cada mensagem
- Computa content_hash por mensagem (SHA-256 truncado 16 chars) e por conversa (20 chars)
- Gera canonical_text truncado em 4.000 chars (usado como input para embeddings)
- Escreve JSON estruturado (Pydantic model_dump) e Markdown limpo
- Saida em `data/normalized/{provider}/YYYY/MM/`
- Upsert no SQLite com hash para deduplicacao

### 2.3 Estagio 3 — Cluster

**Arquivo:** `src/ai_archive/pipeline/cluster.py` (~277 linhas)

Agrupa conversas por topico via embeddings semanticos:

1. Carrega modelo sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`, suporta PT-BR nativamente)
2. Gera embeddings do canonical_text de cada conversa (batch_size=32)
3. Executa HDBSCAN (min_cluster_size=2, metrica euclidiana, selection_method=eom)
4. Fallback para AgglomerativeClustering (linkage=ward, max 20 clusters) se HDBSCAN falhar
5. Conversas noise (label -1 do HDBSCAN) viram topicos individuais
6. Para cada cluster: extrai keyphrases com YAKE (portugues-first com fallback), gera titulo, slug estavel, centroid embedding
7. Limpa topic_clusters e canonical_topic_docs antes de reescrever (evita acumulo de rows orfas)
8. Usa ConversationScorer para log de distribuicao T1/T2/T3

**Threshold de similaridade:** 0.82 (fixo, ajustavel via config)

### 2.4 Estagio 4 — Curate

**Arquivo:** `src/ai_archive/pipeline/curate.py` (~391 linhas)

Gera documentos canonicos por topico. O CurationPipeline para cada TopicCluster:

1. Carrega conversas do cluster
2. Pontua cada mensagem (0-1) considerando: presenca de codigo, palavras de conclusao, recencia
3. Seleciona melhor conteudo com dedup por fuzzy similarity (rapidfuzz token_sort_ratio > 85%)
4. Organiza em 7 secoes:
   - **Sumario Executivo** — top-3 mensagens assistant de maior score
   - **Decisoes e Conclusoes** — trechos com marcadores de conclusao (PT+EN)
   - **Melhor Conteudo** — top-10 trechos nao-redundantes com score
   - **Prompts Uteis** — top-8 mensagens user
   - **Code Snippets** — code blocks deduplicados por fingerprint (max 20/topico)
   - **Contradicoes** — deteccao via marcadores ("actually", "na verdade", "incorreto")
   - **Questoes Abertas** — mensagens user terminadas em ?
5. Escreve Markdown + manifest.json + sources.json + snippets/ por topico
6. Remove diretorios de topicos obsoletos (stale cleanup)

**Refinamento via LLM (opcional):**
- Providers suportados: OpenAI (gpt-4o-mini default), Gemini (gemini-1.5-flash default), Ollama
- Prompt curador estrategico: titulo descritivo, insights acionaveis, potencial B2B
- Graceful fallback: se LLM falhar, mantem output keyword-based
- Secao prepended: "Curadoria IA (provider/model)"

### 2.5 Estagio 5 — Drive Sync

**Arquivo:** `src/ai_archive/pipeline/drive_sync.py` (~130 linhas)

Upload incremental para Google Drive:
- OAuth 2.0 Desktop App (credentials.json + token.json com auto-refresh)
- Duas pastas configuradas: raw e curated
- Tracking via tabela drive_sync_entries (hash-based, so envia mudancas)
- Totalmente opcional (drive_enabled=false por default)

---

## 3. CDP Browser Hijack

O mecanismo central de extracao opera via Chrome DevTools Protocol:

- **attach_cdp** (modo default): Playwright conecta a um Chrome ja rodando com `--remote-debugging-port=9222`
- **managed_profile**: Playwright gerencia seu proprio perfil Chrome
- **storage_state_only**: Reutiliza cookies/localStorage salvos em `storage_state.json`

Nunca ha login automatizado. O usuario faz login manualmente (incluindo 2FA, passkeys, CAPTCHAs) numa janela Chrome visivel. O sistema apenas observa e extrai DOM apos autenticacao confirmada.

Slow-motion configuravel (150ms default entre acoes Playwright) para simular comportamento humano.

---

## 4. Scorer de Valor — 3 Tiers

**Arquivo:** `src/ai_archive/pipeline/scorer.py` (~201 linhas)

O ConversationScorer classifica cada conversa em 3 tiers via regex matching sobre titulo + primeiras 8 mensagens (500 chars cada):

| Tier | Label | Score Base | Conteudo |
|------|-------|-----------|----------|
| **T1** | high_value | 0.70-0.95 | Precatorios, legal tech, fintech, B2B/SaaS, MVP, deep research, agentes IA, prompt engineering, pipeline PDF, RAG, ChromaDB, custo de tokens, arquitetura de produto |
| **T2** | medium_value | 0.40 | Tecnico com padrao reutilizavel (Python, SQL, infra), sem match T1 ou T3 |
| **T3** | low_value | 0.10-0.20 | Gastronomia/receitas, exercicios fisicos, terapia, tarefas pontuais (revisar peticao, traduzir texto) |

**Modificadores:**
- Bonus +0.10 se conversa contem code blocks
- Penalidade -0.05 por mensagem abaixo de 3 (brevity penalty)
- T1 com ruido T3: score reduzido (base 0.55 vs 0.70)

Integrado no cluster pipeline para tagging e no curate pipeline para value_tier nos documentos canonicos.

---

## 5. Gemini Importer

**Arquivo:** `src/ai_archive/importers/gemini_html.py` (~830 linhas)
**Scanner:** `src/ai_archive/importers/gemini_scanner.py` (~210 linhas)

Importador offline para conversas Gemini salvas manualmente (Save Page As):

- **Formatos suportados:** HTML (paginas salvas do browser), JSON (Google Takeout), TXT (export plain-text)
- HTML parseado via BeautifulSoup: extrai mensagens, code blocks, titulo, timestamps
- Deduplicacao por hash SHA-256 contra banco existente
- Scanner automatico: varre ~/Downloads, /mnt/c/Users/pedro/Downloads/ (WSL) e caminhos extras
- Deteccao de arquivos ja importados vs novos
- `import gemini-downloads` importa; `import scan-gemini` escaneia sem importar (com flag `--import`)

---

## 6. Database SQLite WAL

**Arquivo:** `src/ai_archive/db.py` (~729 linhas)
**Banco:** `data/state/archive.db`

Schema com 8 tabelas:

| Tabela | Registros | Funcao |
|--------|-----------|--------|
| `conversations` | PK: id, UNIQUE(provider, provider_conversation_id) | Metadata + canonical_text + paths |
| `messages` | FK → conversations | Texto raw/normalizado, code_blocks JSON, hash |
| `conversation_snapshots` | Versoes historicas por hash | Tracking de mudancas ao longo do tempo |
| `topic_clusters` | Topicos com centroid_embedding JSON | Clusters semanticos |
| `canonical_topic_docs` | Documentos curados por topico | Paths de markdown/manifest |
| `drive_sync_entries` | Tracking de uploads Drive | Hash + status de sync |
| `crawl_runs` | Historico de execucoes | Metricas por run |
| `crawl_errors` | Erros detalhados por conversa | Screenshot path, traceback |

**Configuracao:**
- `PRAGMA journal_mode=WAL` — Write-Ahead Logging para concorrencia
- `PRAGMA foreign_keys=ON` — integridade referencial
- Upsert pattern com `ON CONFLICT DO UPDATE` em todas as tabelas principais
- Indices em provider, conversation_id, run_id

---

## 7. Modelos de Dominio

**Arquivo:** `src/ai_archive/models.py` (~214 linhas)

14 modelos Pydantic v2:
- `Conversation` — entidade central com 22 campos, hash auto-computado
- `Message` — role/text/code_blocks/attachments, hash SHA-256 truncado
- `CodeBlock` — language + code + ordinal
- `TopicCluster` — titulo/slug/tags/centroid_embedding/conversation_ids
- `CanonicalTopicDoc` — documento curado com refs e paths
- `CrawlRun` — metricas de execucao incluindo backfill fields
- `CrawlError` — traceback + screenshot + HTML snapshot
- `ConversationSnapshot` — versionamento com prior_hash
- `DriveSyncEntry`, `SelectorProfile`, `AuthStateInfo`, etc.

Enums: `Provider(chatgpt|gemini)`, `AuthMode(attach_cdp|managed_profile|storage_state_only)`, `ConversationStatus(active|archived|deleted|missing|incomplete)`, `MessageRole(user|assistant|system)`

---

## 8. CLI Reference

```
ai-archive doctor                           # Health check completo
ai-archive auth browser                     # Login manual no Chrome
ai-archive auth drive                       # OAuth Google Drive
ai-archive crawl [--provider X] [--limit N] [--full] [--backfill]
ai-archive normalize [--provider X]
ai-archive cluster
ai-archive curate
ai-archive drive sync
ai-archive sync all [--provider X] [--limit N]  # Pipeline completo
ai-archive reindex [--full]                 # Re-normalize + cluster + curate
ai-archive report                           # Tabela de estado do banco
ai-archive export [--output PATH]           # HTML estilizado para Windows Downloads
ai-archive import gemini-downloads [PATH]   # Importar Gemini offline
ai-archive import scan-gemini [--import]    # Escanear e importar Gemini
```

Export gera HTML self-contained dark-theme com index.html navegavel + busca, compativel com abertura direta no Windows.

---

## 9. Layout de Dados

```
data/
├── state/
│   ├── archive.db                          # SQLite WAL (source of truth)
│   ├── storage_state.json                  # Cookies/localStorage (SENSIVEL)
│   ├── chrome_profile/                     # Perfil Chrome managed
│   └── chatgpt_backfill_index.json         # Estado do harvester backfill
├── raw/{provider}/YYYY/MM/{id}.html        # Snapshots HTML cru
├── normalized/{provider}/YYYY/MM/
│   ├── {id}.json                           # JSON estruturado Pydantic
│   └── {id}.md                             # Markdown limpo
├── curated/topics/{slug}/
│   ├── {slug}.md                           # Documento canonico
│   ├── manifest.json                       # Metadata do topico
│   ├── sources.json                        # Referencias de conversas
│   └── snippets/snippet_00.py              # Code blocks extraidos
└── logs/
    ├── {run_id}_{provider}_error.png       # Screenshots de erro
    └── {run_id}_{provider}_error.html      # DOM snapshots de erro
```

---

## 10. Correlacao com Motor de Curadoria Standalone

O Motor de Curadoria Standalone (`/home/pedro/curation_engine.py` + 7 scripts) e o sistema de gestao de conhecimento que opera sobre a saida do AI Archive, organizando 1.200+ conversas em 50 Master Topics.

### 10.1 Composicao do Ecossistema

| Arquivo | Linhas | Funcao |
|---------|--------|--------|
| `curation_engine.py` | 4.138 | Motor principal: discovery, triage, routing, extracao, ChromaDB |
| `cli.py` | 437 | Interface Typer unificada (9 comandos) |
| `daemon_scheduler.py` | 680 | Night Watch daemon + Morning Briefing |
| `knowledge_graph.py` | 341 | Cross-linking semantico bidirecional |
| `topic_evolution.py` | 527 | Taxonomia Darwiniana (merge/split/promote/freeze) |
| `web_grounding.py` | 391 | Internet RAG via DuckDuckGo |
| `smart_append.py` | 252 | Append-only imutavel para dossies Markdown |
| `ai_validator.py` | 65 | Auditor dual-model (ChatGPT + Gemini) |
| **Total** | **~6.800** | |

### 10.2 Taxonomia de 50 Master Topics

O `curation_engine.py` define ate 50 Master Topics (dossies mestres) organizados em 3 grupos de quota:

- **CORE (70% do espaco):** Topicos que geram dinheiro ou produtividade direta — Precatorios e KYC, Pipeline PDF Juridico, Agentes IA e Orquestracao, Legal Tech, B2B/SaaS, etc.
- **WELLNESS (max 30%):** Saude, exercicios, culinaria, autoconhecimento
- **TRASH:** Configuracao/instalacao, troubleshooting pontual — descartados na triagem

Cada topic tem: description, keywords (para routing lexical), is_seed (protegido contra eviction), weight (prioridade), quota_group.

### 10.3 Pipeline de Curadoria (6 Fases)

1. **Descoberta** — 5 fontes (Windows Downloads, Linux home, WSL paths, etc.)
2. **Triagem pre-LLM** — Separacao TRASH vs GOLD via regras lexicais + llama3.2 rapido
3. **Roteamento** — command-r classifica cada chunk → Master Topic (com incubadora para novos)
4. **Extracao** — rules / preferences / expansions (regra 65% de conteudo acionavel)
5. **Persistencia** — ChromaDB com metadata master_topic + embedding all-MiniLM-L6-v2
6. **UI Handoff** — CLI dark checklist + Mega Prompts de consolidacao

Stack: ChromaDB (banco vetorial local), Ollama (LLM local com fallback mock), sentence-transformers (embeddings offline), LangChain text splitters (chunks 1.500 chars, overlap 250), Rich (UI terminal), Pydantic v2, httpx (async HTTP), aiofiles.

### 10.4 Night Watch Daemon

**Arquivo:** `daemon_scheduler.py`
**Horario:** 03:00 AM (APScheduler)
**Ciclo noturno (6 passos):**

1. **Delta Detection** — DeltaDetector consulta SQLite de idempotencia, identifica arquivos novos/modificados na janela de 26h (com 2h de margem de seguranca)
2. **Pipeline de curadoria** — Executa CurationEngine em modo delta (so arquivos novos)
3. **Atualizacao de Dossies** — SmartAppender injeta novos blocos datados nos Markdown existentes (pattern append-only, nunca reescreve historico)
4. **Cross-linking** — EntityMapBuilder + CrossLinker gera hyperlinks bidirecionais entre dossies via fuzzy matching (thefuzz, threshold 82%), estilo Obsidian wiki
5. **Web Grounding** — Para topicos de alta urgencia, busca DuckDuckGo com queries formuladas pelo LLM, sintetiza secao "Radar da Web"
6. **Evolucao de Topicos** — TopicEvolutionEngine verifica semanalmente: topicos frios (30+ dias), merge de sobrepostos (cosine > 0.78), promocao da Incubadora

**Morning Briefing:**
- JSON estruturado salvo em `morning_briefing.json`
- Contem: new_chunks_total, files_processed, files_trashed, topics_updated (com severity), structural_proposals (eviction/merge/promote), web_validations, incubadora_stats, errors
- Nenhuma decisao drastica e tomada automaticamente — propostas requerem `--approve`

### 10.5 Knowledge Graph — Cross-linking Semantico

**Arquivo:** `knowledge_graph.py`

Transforma o diretorio de dossies em wiki navegavel:
- Constroi mapa de entidades: topic_name → variacoes textuais
- Fuzzy match via thefuzz token_set_ratio (threshold 82%, minimo 5 chars)
- Injeta links bidirecionais no rodape: secao "Referencias Cruzadas"
- Formatos: Obsidian `[[Topic_Name]]`, Markdown `[Display](../file.md)`, Wiki `[[name|alias]]`

### 10.6 Topic Evolution — Taxonomia Darwiniana

**Arquivo:** `topic_evolution.py`

Gerencia o teto de 50 topicos com 3 operacoes:

| Operacao | Trigger | Acao |
|----------|---------|------|
| **Eviction** | Topico frio (30+ dias sem update, nao-seed) | Move para `legacy/`, libera slot |
| **Merge** | Cosine similarity > 0.78 entre dois topicos | Funde conteudo, preserva o mais forte |
| **Promote** | Incubadora com massa critica (8+ chunks, 3+ arquivos, 2+ runs OU score >= 0.65) | Eleva para Master Topic |

Nada e executado sem aprovacao humana (flag `--approve` ou UI interativa).

### 10.7 Web Grounding — Internet RAG

**Arquivo:** `web_grounding.py`

Fluxo de 5 passos:
1. LLM extrai entidades-chave do dossie
2. LLM formula 1-3 perguntas de busca
3. DuckDuckGo executa buscas (max 4 resultados, timeout 15s, zero API key)
4. Resultados injetados de volta no LLM
5. LLM produz secao "Radar da Web" no dossie

Provider: DuckDuckGoProvider (default, sem custo). Stub preparado para Tavily.

### 10.8 Smart Append — Dossies Imutaveis

**Arquivo:** `smart_append.py`

Padrao append-only para Markdown:
- Nunca reescreve conteudo existente
- Blocos datados: `## Atualizacao — DD/MM/AAAA`
- Secoes especiais: Radar da Web, Changelog de Evolucao
- DossieManager: CRUD em `output/dossies/`, com `legacy/` para arquivados

---

## 11. Curation Engine — Pacote Distribuivel

**Localizacao:** `/home/pedro/curation-engine/`
**Linguagem:** Python 3.11+ | **Build:** Hatchling | **Testes:** 53 funcoes passando

Versao empacotada e instalavel do motor de curadoria. Refatora o `curation_engine.py` standalone (4.138 linhas) em pacote com ~1.725 linhas de source.

### Componentes

| Classe | Responsabilidade |
|--------|-----------------|
| `CurationEngine` | Orquestrador assincrono (semaphore concorrencia=3) |
| `HTMLContentExtractor` | Parse multi-formato de exportacoes Gemini/ChatGPT |
| `TXTContentExtractor` | Fallback encoding chain: UTF-8 → UTF-8-sig → Windows-1252 → Latin-1 |
| `ConversationChunker` | RecursiveCharacterTextSplitter (1.500 chars, overlap 200) |
| `KnowledgeExtractor` | Dual-mode: Ollama (timeout 30s) ou mock lexical offline |
| `VectorStoreManager` | ChromaDB PersistentClient, insercao em batches de 50 |
| `IdempotencyTracker` | SQLite ACID com hash SHA-256 |
| `EnvironmentDetector` | Detecta Windows/WSL/Linux para paths |
| `LongPathResolver` | Mitigacao MAX_PATH do Windows |
| `ProcessingReport` | Modelo Pydantic com metricas de execucao |
| `Config` | data_dir, chroma_dir, ollama_url, ollama_model |

**Embedding model:** all-MiniLM-L6-v2 (22MB, 100% offline, CPU-only)
**Banco vetorial:** ChromaDB PersistentClient
**LLM:** Ollama com fallback para mock lexical (confidence < 0.3 indica mock)

### Integracao com AI Archive

O curation-engine le diretamente do `data/` do ai-archive:
```
AI_ARCHIVE_DATA_DIR=/home/pedro/ai-archive/data/
uv run curation-engine --data-dir /home/pedro/ai-archive/data/
```

Precedencia do data_dir: CLI arg > env var > ~/.local/share/ai-archive/data > ./data

---

## 12. Integracao com Nexus

O AI Archive e seus correlatos fazem parte do ecossistema Nexus como modulo de captura e curadoria de conhecimento pessoal. Fluxo de dados:

```
Chrome (ChatGPT/Gemini)
    │
    ▼
AI Archive (crawl → normalize → cluster → curate)
    │
    ├──► SQLite archive.db (source of truth local)
    ├──► Google Drive (mirror opcional)
    ├──► data/curated/ (documentos canonicos)
    │
    ▼
Curation Engine (standalone ou pacote)
    │
    ├──► ChromaDB (banco vetorial, embeddings offline)
    ├──► 50 Master Topics (dossies Markdown)
    ├──► Night Watch (daemon 03:00 AM)
    │       ├──► Morning Briefing (JSON)
    │       ├──► Cross-linking (wiki semantica)
    │       ├──► Web Grounding (DuckDuckGo RAG)
    │       └──► Topic Evolution (Darwin: merge/evict/promote)
    │
    ▼
Nexus (orquestrador geral)
```

---

## 13. Numeros

| Metrica | Valor |
|---------|-------|
| Linhas de codigo ai-archive (src) | ~8.400 |
| Linhas de codigo ai-archive (tests) | ~1.200 |
| Funcoes de teste ai-archive | 97 |
| Linhas motor standalone (8 arquivos) | ~6.800 |
| Linhas curation-engine pacote | ~1.725 |
| Funcoes de teste curation-engine | 53 |
| Total de linhas do ecossistema | ~18.100 |
| Total de testes passando | ~150 |
| Tabelas SQLite (archive.db) | 8 |
| Modelos Pydantic (ai-archive) | 14 |
| Comandos CLI ai-archive | 13 |
| Comandos CLI curadoria | 9 |
| Master Topics (teto) | 50 |
| Embedding model ai-archive | paraphrase-multilingual-MiniLM-L12-v2 |
| Embedding model curation-engine | all-MiniLM-L6-v2 (22MB) |
| Tier 1 patterns (scorer) | 28 regex |
| Tier 3 patterns (scorer) | 11 regex |
| Threshold clustering | 0.82 cosine similarity |
| Threshold cross-linking | 82 fuzzy token_set_ratio |
| Threshold merge topicos | 0.78 cosine similarity |
| Topico frio (eviction) | 30+ dias sem update |
| Massa critica incubadora | 8+ chunks, 3+ arquivos, 2+ runs |
| Chunk size | 1.500 chars (overlap 200-250) |
| Backfill duracao | 45-90 minutos |
| Night Watch horario | 03:00 AM |
| Delta window | 26 horas |
| Providers suportados | ChatGPT, Gemini |
| LLM curadoria suportados | OpenAI, Gemini, Ollama, none |

---

## 14. Dependencias Principais

### AI Archive (pyproject.toml)

| Pacote | Versao Min | Funcao |
|--------|-----------|--------|
| playwright | 1.44 | Browser automation CDP |
| pydantic | 2.7 | Modelos de dominio |
| typer | 0.12 | CLI framework |
| beautifulsoup4 | 4.12 | HTML parsing |
| sentence-transformers | 3.0 | Embeddings semanticos |
| hdbscan | 0.8 | Clustering density-based |
| scikit-learn | 1.4 | AgglomerativeClustering fallback |
| rapidfuzz | 3.8 | Deduplicacao fuzzy |
| keybert | 0.8 | Keyword extraction |
| yake | 0.4 | Keyphrase extraction PT/EN |
| orjson | 3.10 | JSON rapido |
| rich | 13.7 | Terminal UI |
| google-api-python-client | 2.130 | Google Drive API |
| sqlmodel | 0.18 | SQL helpers |
| markdownify | 0.12 | HTML→Markdown |

### Motor Standalone

chromadb, sentence-transformers, langchain-text-splitters, beautifulsoup4, lxml, aiofiles, httpx, pydantic, loguru, rich, thefuzz/rapidfuzz, apscheduler (daemon), duckduckgo-search (web grounding)

---

*Documento gerado em 2026-03-27. Referencia cruzada: INVENTARIO_COMPLETO_SISTEMAS.md, README.md, pyproject.toml de cada projeto.*
