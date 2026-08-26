# Motor de Busca de Debêntures

Ferramenta de busca de debêntures brasileiras por ISIN, código de ativo ou
nome da empresa emissora — retorna uma ficha completa do ativo (não um
dashboard de múltiplos ativos), com cada campo marcado com sua fonte ou
"indisponível". Uso interno/pessoal, sem autenticação multiusuário.

## Status (Fase 1 + Fase 2 + Fase 3 + Fase 4 concluídas)

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
- **UI web** (`web.py`, FastAPI + Jinja2): tela de busca única, ficha do
  ativo em seções (Identificação, Características, Situação, Mercado
  Secundário, Eventos, Documentos) com badge de fonte ou "indisponível"
  campo a campo, tela de desambiguação quando a busca por emissor retorna
  mais de uma série, e tela de input manual (o dado colado tem precedência
  sobre a fonte automática, refletido na ficha imediatamente). Validada
  navegando o fluxo real com Playwright (busca → ficha → adicionar dado
  manual → ficha atualizada → desambiguação → nada encontrado).
- **Seção "Emissão" na ficha**: forma, regime de registro na CVM (ex.:
  "DISPENSA ICVM 476/09 em 10/06/2013"), atos societários que aprovaram,
  início de distribuição, banco mandatário, agente fiduciário, instituição
  depositária e coordenador líder — todos vindos do SND, mesma página de
  características. Adicionado a pedido do usuário ("como foi realizada a
  emissão", "banco coordenador").
- **Alerta de "Registro Excluído do SND"**: a pedido do usuário
  ("informe rápido se há problema com a debênture"). O SND mantém uma
  lista GLOBAL de registros excluídos (`registrosexcluidos_r.asp`, todos
  os emissores, não por ativo) com data de exclusão e, às vezes, motivo —
  já vimos motivos como "PROCESSO DEVOLVIDO PELA CVM" e emissores anotados
  como "EM RECUPERAÇÃO JUDICIAL" direto no nome. Essa lista é baixada e
  cacheada uma única vez (não por busca) e cruzada localmente com o ativo
  da ficha. Quando bate, um banner de alerta aparece no topo da ficha,
  antes de qualquer outra seção — com a ressalva de que exclusão não
  significa necessariamente inadimplência (pode ser vencimento normal).
- **Alerta de "Vencimento Antecipado Declarado"**: mesmo padrão, usando
  `vencimentosantecipados_r.asp` (lista global). Sinal de problema mais
  direto que registro excluído (normalmente indica quebra de covenant).
  Só a detecção de "nenhum resultado" foi validada contra página real —
  ver "Cobertura real do SND" abaixo pro que falta confirmar.
- **Eventos de repactuação na ficha**: `EventsProvider` implementado
  usando `repactuacoes_r.asp` (lista global, 54 registros reais de
  1995-2010 na amostra capturada) — mostra tipo, data e deliberação
  (ex.: "RCA - 16/10/1995") na seção Eventos da ficha, mais recente
  primeiro.
- **Alerta de "Inadimplência Corrente"**: mesmo padrão, usando
  `inadimplencias_r.asp` (lista global, só Ativo + Motivo, sem data — é
  retrato do estado atual). É o sinal de problema mais direto de todos —
  aparece primeiro entre os banners de alerta. Confirmado que o envio em
  branco funciona sem exigir data (diferente das outras duas). Só a
  detecção de "nenhum resultado" foi validada contra página real (não
  havia nenhuma inadimplência corrente no momento da captura) — mesma
  cautela de "Vencimentos Antecipados" pro parsing de linha populada.
- **`AnbimaAPIProvider`** (Fase 3, **validado ponta a ponta com credencial
  sandbox real** — não é mais só "contrato confirmado no papel"): implementa
  `MarketDataProvider`, não `CharacteristicsProvider` — o único endpoint de
  Debêntures documentado no Swagger real (`/v1/debentures/mercado-secundario`)
  devolve preço/taxa de mercado, não dado cadastral, então quem cobre
  características continua sendo só o SND. Plugável via `ANBIMA_CLIENT_ID`
  + `ANBIMA_CLIENT_SECRET` (e `ANBIMA_AMBIENTE`, padrão `sandbox`) — sem
  as duas env vars, `is_available()` retorna `False` e o aggregator ignora
  a fonte automaticamente. Fluxo de autenticação (OAuth2
  `client_credentials`: `POST /oauth/access-token` com
  `Authorization: Basic base64(client_id:client_secret)`, depois todo
  request de dado exige os headers `client_id` e `access_token`) confirmado
  rodando de verdade (fora deste sandbox de desenvolvimento, que segue
  bloqueado pro domínio da API) com a credencial sandbox aprovada do
  usuário: token obtido, `/v1/debentures/mercado-secundario` retornou a
  lista real do dia com exatamente os campos documentados no Swagger
  (`codigo_ativo`, `emissor`, `taxa_indicativa`, `pu`, etc.), e
  `AnbimaAPIProvider.fetch_market_data()` mapeou certinho pra
  `MarketPriceSnapshot` (`pu_medio`, `taxa_indicativa`, fonte com data de
  referência). `/v1/debentures/mercado-secundario` não filtra por
  ativo/ISIN, só devolve a lista inteira do dia — o filtro pelo
  `codigo_ativo` buscado é feito localmente, com a lista cacheada em
  memória por dia. Novo campo `taxa_indicativa` em `MarketPriceSnapshot`,
  exibido na tabela de Mercado Secundário da ficha.

  Pegadinha real encontrada durante a validação: o Client ID do app tem um
  caractere (`I` maiúsculo) visualmente idêntico a `l` minúsculo na fonte
  do portal — eu tinha transcrito errado a partir de um print antigo, o
  que gerou `400 Bad Request` ("Invalid client_id") por dias até
  copiarmos o valor de verdade em vez de ler/digitar. Lição: credencial
  sempre se copia, nunca se transcreve visualmente.
- **`CvmDocumentsProvider`** (Fase 4, contrato real confirmado): Fatos
  Relevantes via o Portal de Dados Abertos da CVM
  (`dados.cvm.gov.br/dados/CIA_ABERTA/DOC/IPE/DADOS/ipe_cia_aberta_{ano}.zip`)
  — um ZIP por ano civil com um CSV (`;`) de todos os documentos periódicos
  e eventuais de companhias abertas. Confirmado por download real do
  usuário (ano 2025, 19,2 MB): colunas `CNPJ_Companhia`, `Nome_Companhia`,
  `Categoria`, `Assunto`, `Data_Entrega`, `Link_Download`, entre outras; o
  link de um documento foi aberto manualmente e mostrou o PDF direto, sem
  login. Filtra só `Categoria == "Fato Relevante"` e casa **por CNPJ**, não
  por nome (texto livre, inconsistente entre CVM e SND) — por isso o SND
  passou a expor `Debenture.emissor_cnpj` de verdade (já extraía esse CNPJ
  internamente pra consultar preços, só nunca tinha sido colocado no
  campo do modelo) e `DocumentsProvider.fetch_documents()` ganhou um
  segundo parâmetro (`emissor_cnpj`) que o aggregator preenche com o CNPJ
  já resolvido pelas fontes de características, antes de chamar as fontes
  de documento. Sem CNPJ resolvido, a fonte não arrisca casar por nome —
  fica vazia. Cobre o ano corrente + 4 anteriores (cache local de 24h por
  arquivo anual); Fatos Relevantes mais antigos ficam de fora, documentado
  aqui, não escondido. Prospecto e Escritura de Emissão continuam
  indisponíveis — vêm de outro sistema da CVM (documentos de oferta
  pública), ainda não investigado.
- 73 testes automatizados (parsing + integração dos providers + cache +
  aggregator + rotas web), todos rodam sem rede.

Um bug real foi encontrado durante a validação visual da Fase 2 e corrigido:
a heurística de "isso parece um código de ativo?" na busca (usada pra
decidir se o termo digitado é ISIN, código ou nome de emissor) classificava
qualquer palavra de 4 a 8 letras maiúsculas como código de ativo — então
buscar por "BODYTECH" dava match nesse regex e tentava consultar o SND como
se fosse um ticker, em vez de cair na busca por nome. Corrigido exigindo
pelo menos um dígito no código (ver `query_parsing.py` e
`tests/test_query_parsing.py`) — códigos reais de debênture sempre têm
número de série (ex.: BODY12, TEPA23).

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
- **CNPJ do emissor e número da emissão nem sempre vêm do
  `<link rel="canonical">`**: esses dois campos são extraídos de um link
  técnico que o SND embute na página apontando pro ANBIMA Data (não
  aparecem em texto simples em lugar nenhum). Confirmado num caso real
  (Americanas, `AMERE2`) que **nem toda debênture tem esse link** —
  aparentemente depende de a debênture estar mapeada no ANBIMA Data.
  Quando falta, `emissor_cnpj` cai pra um fallback (match por nome contra
  a mesma lista estática de emissores usada na busca por nome, só quando
  há exatamente um candidato — nunca um chute em caso de ambiguidade);
  `numero_emissao` não tem fonte alternativa conhecida e fica
  "indisponível" nesses casos. Isso também afetava
  `MarketDataProvider.fetch_market_data` (que também precisa do CNPJ pra
  consultar preços) — mesma correção resolveu os dois.
- **"Não encontrado"**: a heurística usada (`_caracteristicas_encontrou_ativo`)
  não foi validada contra uma página real de "ativo não existe", porque não
  capturamos uma — está documentada como best-effort no código.
- **Amortizações futuras**: a página de características tem esses dados
  brutos (tabela de amortização/prêmio), mas o parsing pra virar `Event`
  não foi implementado — só repactuação foi (ver acima).
- **Vencimentos Antecipados e Inadimplências — parsing de linha
  populada não verificado**: toda consulta feita até agora nas duas
  páginas voltou "Não existe resposta para os itens selecionados." —
  real e útil (sabemos que não há nenhum caso ativo hoje), mas nunca
  vimos nenhuma das duas páginas COM um registro de verdade.
  `_parse_vencimentos_antecipados_html` e `_parse_inadimplencias_html`
  levantam `SndParsingError` de propósito se a página tiver conteúdo
  diferente desse caso vazio conhecido, em vez de arriscar um parsing de
  coluna nunca confirmado — então os alertas correspondentes podem não
  funcionar quando (se) um caso real aparecer, até alguém capturar um HAR
  com resultado populado e o parsing de linha ser implementado de fato.

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

# Subir a UI web (http://127.0.0.1:8000)
.venv/bin/uvicorn debenture_search.web:app --reload

# Opcional: ligar a fonte ANBIMA Feed (preço/taxa indicativa de mercado).
# Sem essas duas env vars a fonte fica desligada e o resto funciona igual.
# NUNCA commitar valores reais — exportar só localmente.
export ANBIMA_CLIENT_ID="..."
export ANBIMA_CLIENT_SECRET="..."
```

Por padrão os dados locais (cache e overrides manuais) ficam em
`~/.debenture_search/`. Configurável via `DEBENTURE_SEARCH_DATA_DIR`.

## Hospedagem (Render)

O projeto foi desenhado como ferramenta de uso pessoal, sem sistema de
contas — ao colocar a UI web numa URL pública, `WEB_AUTH_USERNAME` +
`WEB_AUTH_PASSWORD` (ver `web.py`/`config.py`) ligam um gate de HTTP
Basic Auth simples na frente de toda a aplicação; sem as duas env vars a
UI continua aberta (comportamento de desenvolvimento local, sem mudança).

`render.yaml` já descreve o serviço pro [Render](https://render.com)
(plano gratuito): conectar o repositório lá detecta o blueprint sozinho.
Nada de credencial fica no arquivo — `ANBIMA_CLIENT_ID`,
`ANBIMA_CLIENT_SECRET`, `WEB_AUTH_USERNAME` e `WEB_AUTH_PASSWORD` são
pedidos pelo próprio Render na hora do deploy e ficam só no painel dele
(`sync: false` no blueprint).

Limitação conhecida do plano gratuito: sem disco persistente, então o
cache SQLite e os overrides manuais (`ManualInputProvider`) são perdidos
a cada redeploy/reinício da instância — para uso pessoal ocasional isso é
aceitável (o cache só reconsulta o SND; overrides manuais precisariam ser
recadastrados). Resolver isso definitivamente exigiria um disco
persistente (plano pago) ou trocar o SQLite local por um banco externo —
fora do escopo por ora.

## Plano de fases

1. **Fase 1 (concluída)** — modelo de dados, interfaces de provider, SND
   (características/situação/mercado secundário) com parsing validado
   contra HTML real, cache, CLI de validação ponta a ponta.
2. **Fase 2 (concluída)** — UI web (FastAPI + Jinja2): tela única de busca,
   ficha do ativo em seções com fonte/indisponível por campo, tela de
   desambiguação, tela de input manual. Validada com Playwright contra o
   fluxo real (busca → ficha → adicionar dado manual → desambiguação →
   nada encontrado).
3. **Fase 3 (concluída — validada ponta a ponta com credencial sandbox
   real)** — `AnbimaAPIProvider` (preço/taxa indicativa de mercado via
   `/v1/debentures/mercado-secundario`), plugável via
   `ANBIMA_CLIENT_ID`/`ANBIMA_CLIENT_SECRET`; sem eles, a fonte fica
   desligada sem quebrar o resto do sistema (confirmado por teste).
   Autenticação, URL e schema de resposta rodaram de verdade contra o
   ambiente sandbox da ANBIMA (fora deste ambiente de desenvolvimento,
   que segue bloqueado pro domínio) — ver `providers/anbima_api.py` para
   o contrato completo.
4. **Fase 4 (concluída — Fato Relevante; Prospecto/Escritura pendentes)** —
   `CvmDocumentsProvider` via Dados Abertos da CVM (dataset IPE), casando
   por CNPJ do emissor (agora exposto pelo SND). Prospecto e Escritura de
   Emissão ficam pra depois — vêm de outro sistema da CVM, ainda não
   investigado.
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
