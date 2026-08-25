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
- `SndScraperProvider`: **parsing verificado contra HTML real** do SND
  (capturado via HAR pelo usuário em 25/08/2026 — ver
  `tests/fixtures/snd_*.html`). Busca por nome de emissor (via lista
  estática de ~1.466 emissores embutida na página, cacheada localmente),
  por código de ativo (rota direta `caracteristicas_d.asp`) e
  características completas (indexador, spread, garantia, classe,
  quantidades, valor nominal, agentes, situação, rating quando houver) —
  não é mais um stub. Detalhes e incertezas remanescentes na seção
  "Cobertura real do SND" abaixo.
- CLI (`python -m debenture_search`) validada ponta a ponta com cache
  pré-populado (sem rede).
- 28 testes automatizados (parsing puro + integração do provider + cache +
  aggregator), todos rodam sem rede.

## Cobertura real do SND

O fluxo real do site foi mapeado via HAR de tráfego de rede (não
documentação oficial — o SND não expõe uma). O que está confirmado:

- **Busca por nome de emissor**: funciona bem. A lista completa de
  emissores (nome → CNPJ) vem embutida como `<select>` estático numa única
  página, baixada e cacheada uma vez — a busca em si é local (substring,
  sem acento/caixa), não bate no servidor por letra digitada.
- **Busca por código de ativo**: rota direta via
  `caracteristicas_d.asp?tip_deb={publicas|privadas}&selecao=<código>`, sem
  precisar resolver o emissor antes. Essa mesma página já traz a ficha de
  características completa.
- **Busca por ISIN direto** (sem saber o código do ativo): **não
  encontramos um caminho no SND para isso** — o site não expõe busca
  global por ISIN, só por emissor ou por código de ativo. `search()` tenta
  passar o ISIN como `selecao=` (mesma rota do código de ativo) como
  aposta best-effort, mas isso não foi confirmado contra o site real —
  pode simplesmente não funcionar, retornando lista vazia (comportamento
  honesto, não seria erro).
- **"Não encontrado"**: a heurística usada (`_caracteristicas_encontrou_ativo`)
  não foi validada contra uma página real de "ativo não existe", porque não
  capturamos uma — está documentada como best-effort no código.
- **Eventos futuros** (próxima repactuação/amortização): a página de
  características tem esses dados brutos (tabela de amortização/prêmio),
  mas o parsing para `EventsProvider` não foi implementado nesta fase —
  fica pra quando isso for de fato necessário.

### Descontinuação anunciada do SND

O próprio debentures.com.br hoje exibe um aviso: "Em breve, este site será
descontinuado. Para consultar informações sobre emissões e estoque de
debêntures, migre para o ANBIMA Data". Não há data anunciada. Isso significa
que `SndScraperProvider` é uma fonte de prazo de validade desconhecido — vale
implementar e usar enquanto durar, mas não vale investir em robustez além do
necessário (ex.: não construir testes de regressão elaborados pra layout que
pode sumir a qualquer momento — ver Fase 5).

## Decisão: ANBIMA Data (portal público) fica fora do escopo

Investigamos `data.anbima.com.br` (a página web para onde o SND está
redirecionando) como possível substituto do SND. Resultado, via HAR de
tráfego de rede capturado pelo usuário:

- A busca e as características completas vêm de uma API JSON real
  (`data-api.prd.anbima.com.br/web-bff/v1/debentures`), com exatamente os
  campos que precisamos (ISIN, indexador, remuneração, garantia, emissor,
  datas, quantidades, agente fiduciário etc.).
- Toda chamada a essa API exige um header `g-google-authorization`: um JWT
  assinado (HS256) que embrulha um token de verificação do Google reCAPTCHA,
  gerado no navegador a cada sessão.

Ou seja, apesar de a tela parecer "pública, sem login", a API por trás dela
é deliberadamente protegida contra automação. Chamar essa API
programaticamente exigiria forjar ou replicar esse token — isso é
precisamente a "engenharia reversa do ANBIMA Data público" que o escopo
original do projeto proíbe (ver seção "O que este projeto não faz"). **Decisão
confirmada com o usuário: não construir nenhum provider contra
`data.anbima.com.br` ou sua API.** Campos de característica completos ficam
"indisponível" a menos que:
(a) a `AnbimaAPIProvider` da Fase 3 seja ligada com credencial paga oficial,
ou (b) o dado seja colado manualmente via `ManualInputProvider` (o usuário
consultando no próprio navegador, como fez para diagnosticar isso).

## Uso

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Buscar (bate no SND de verdade; funciona melhor por código de ativo ou
# nome de emissor — busca só por ISIN pode não resolver, ver seção acima)
.venv/bin/python -m debenture_search busca BODY12 --tipo codigo_ativo
.venv/bin/python -m debenture_search busca "Bodytech" --tipo emissor

# Registrar um dado que nenhuma fonte automática cobre (ex.: rating)
.venv/bin/python -m debenture_search manual-set BODY12 rating "AA-" \
    --fonte "Fitch, 03/2026" --tipo codigo_ativo

# Rodar a suíte de testes (não depende de rede)
.venv/bin/python -m pytest
```

Por padrão os dados locais (cache e overrides manuais) ficam em
`~/.debenture_search/`. Configurável via `DEBENTURE_SEARCH_DATA_DIR`.

## Plano de fases

1. **Fase 1 (concluída)** — modelo de dados, interfaces de provider, SND
   (características/situação/mercado secundário) com parsing validado
   contra HTML real, cache, CLI de validação ponta a ponta.
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
