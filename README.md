# Motor de Busca de Debêntures

Ferramenta de busca de debêntures brasileiras por ISIN, código de ativo ou
nome da empresa emissora — retorna uma ficha completa do ativo (não um
dashboard de múltiplos ativos), com cada campo marcado com sua fonte ou
"indisponível". Uso interno/pessoal, sem autenticação multiusuário.

## Status (Fase 1 — busca + integração SND + arquitetura de providers)

O que está implementado e testado:

- Modelo de dados (`models.py`): `Debenture`, `Issuer`, `MarketPriceSnapshot`,
  `Event`, `Document`, `ManualInput`, com `SourcedValue` carregando
  proveniência (fonte + timestamp) em todo campo de característica.
- Interfaces de provider segregadas por capacidade (`providers/base.py`):
  `SearchProvider`, `CharacteristicsProvider`, `MarketDataProvider`,
  `EventsProvider`, `DocumentsProvider`.
- `DebentureAggregator` (`aggregator.py`): resolve a busca, detecta
  ambiguidade (múltiplas séries), faz fan-out nos providers disponíveis e
  faz merge por precedência — um provider indisponível ou que falhe numa
  chamada nunca derruba a ficha inteira, e nenhum campo é sobrescrito por
  "indisponível".
- Cache local em SQLite com TTL (`cache.py`) e cliente HTTP com rate limit
  (`http_client.py`) — a base para nunca rebater o SND com força bruta.
- `ManualInputProvider`: overrides manuais (rating, taxa, quantidade em
  mercado), sempre disponível, com precedência máxima no merge.
- `SndScraperProvider`: **stub estruturado, não verificado contra o site
  real** — ver seção "Limitação de rede" abaixo.
- CLI (`python -m debenture_search`) para validar o fluxo ponta a ponta
  sem UI.
- 18 testes unitários cobrindo aggregator, cache e providers (todos
  rodam sem rede).

## Limitação de rede neste ambiente

O ambiente onde a Fase 1 foi desenvolvida tem o egress de rede bloqueado
para `debentures.com.br` (política do ambiente remoto Claude Code, não é
algo contornável nem algo que deva ser contornado). Isso significa que:

- **Não foi possível abrir as páginas reais do SND** para confirmar URLs,
  método de busca (GET/POST), nomes de campos de formulário, e estrutura
  HTML das tabelas de estoque/preços.
- `providers/snd.py` foi escrito com a arquitetura completa (rate limit,
  cache, contrato com o resto do sistema, tratamento de erro) mas com os
  seletores CSS e parâmetros de busca marcados como `TODO(verificar)` —
  eles são placeholders plausíveis, não confirmados.
- Os testes de `providers/snd.py` usam fixtures HTML **sintéticas**
  (escritas à mão para bater com os seletores placeholder), não capturas
  reais do site — ver aviso no topo de `tests/test_snd_provider.py`.
- Quando o SND real não bate com um seletor esperado, o provider levanta
  `SndParsingError` com uma mensagem clara, em vez de retornar um dado
  errado silenciosamente.

### Próximo passo para destravar isso

Rodar este projeto num ambiente com acesso à internet (ex.: sua máquina
local) e:

1. Abrir manualmente as páginas de "Estoque por Ativo" e "Preços de
   Negociação" em debentures.com.br para um ativo conhecido (ex.: um
   código de debênture que você já acompanha).
2. Salvar o HTML de resposta em `tests/fixtures/` (substituindo as
   fixtures sintéticas) e ajustar as URLs/seletores em `providers/snd.py`
   (`_Endpoints`, `_build_search_params`, `_parse_search_results_html`,
   `_parse_estoque_html`, `_parse_precos_html`) até os testes passarem
   com dados reais.
3. Rodar `python -m debenture_search busca <ISIN ou código>` e comparar
   manualmente com o que aparece no site.

Isso é trabalho de ajuste de seletores sobre uma arquitetura já pronta —
não é para refazer o desenho do provider.

## Uso

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Buscar (tenta o SND; ainda vai falhar até os seletores serem verificados,
# ver seção acima — mas roda ponta a ponta contra o merge de fontes)
.venv/bin/python -m debenture_search busca TEPA23
.venv/bin/python -m debenture_search busca BRTEPADBS001 --tipo isin
.venv/bin/python -m debenture_search busca "Tegma" --tipo emissor

# Registrar um dado que nenhuma fonte automática cobre (ex.: rating)
.venv/bin/python -m debenture_search manual-set TEPA23 rating "AA-" \
    --fonte "Fitch, 03/2026" --tipo codigo_ativo

# Rodar a suíte de testes (não depende de rede)
.venv/bin/python -m pytest
```

Por padrão os dados locais (cache e overrides manuais) ficam em
`~/.debenture_search/`. Configurável via `DEBENTURE_SEARCH_DATA_DIR`.

## Plano de fases

1. **Fase 1 (esta)** — modelo de dados, interfaces de provider, SND
   (estoque/situação/mercado secundário), cache, CLI de validação ponta a
   ponta.
2. **Fase 2** — UI da ficha do ativo (busca única + seções + fonte por
   campo + tela de desambiguação), input manual via tela.
3. **Fase 3** — `AnbimaAPIProvider` (características completas + preço
   indicativo), plugável via `ANBIMA_API_KEY`; se ausente, a fonte fica
   desligada sem quebrar o resto do sistema.
4. **Fase 4** — `CvmDocumentsProvider` (prospecto, escritura, fatos
   relevantes), complementar/opcional.
5. **Fase 5 (opcional)** — exportação da ficha, histórico de buscas,
   testes automatizados de regressão do scraper.

## O que este projeto não faz (por design)

- Não é um dashboard/grid de múltiplos ativos — é busca de um ativo por
  vez.
- Não faz scraping paralelo ou agressivo do SND — é consulta pontual
  disparada pela busca do usuário, com rate limit e cache.
- Não inventa nem estima dado ausente — campo sem fonte fica
  "indisponível", nunca um valor calculado.
- Não tenta contornar a exigência de credencial da API paga da ANBIMA.
- Não tem autenticação multiusuário.
