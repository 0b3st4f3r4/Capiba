# PR-D-09 — Geografia anômala: fornecedor distante do ente comprador (O6)

- **Pré-registro**: bateria D-09
- **Criado em**: 2026-08-20
- **Última atualização**: 2026-08-20
- **Status**: aprovado e executado — bateria D-09 **success** (P1–P5 nas
  5 seeds), `docs/results/R-D-09.md`; P6/volume real pendentes
- **Alvo**: sinal `anomalous_geography` (novo `SignalType`) — fornecedor
  cuja sede municipal dista mais que `max_distance_km` do município do
  ente comprador. Backlog O6 (`docs/gaps.md` item 4). O operador AQL
  legado (`src/capiba/detection/graphs.py`, `anomalous_geography`) está
  morto — filtra vértices `type == "bid"` que o grafo nunca cria — e é
  substituído por uma função pura sobre o silver (fatia 2), no padrão de
  `political.py`.
- **Configuração**: `experiments/detect/D-09.json` (declarativa, seeds e
  âncoras inclusas; esqueleto criado junto a este registro, sem execução)

## 1. Pergunta

O cruzamento sede do fornecedor × município do ente, na semântica
declarada na seção 3 (distância haversine entre sedes municipais, limiar
estrito, score proporcional pré-registrado), (i) reproduz **exatamente**
os sinais plantados no regime sintético — inclusive as disciplinas
"sem coordenadas não sinaliza" e "abaixo do limiar não sinaliza" — e
(ii) permanece deterministicamente reproduzível entre seeds e execuções?

## 2. Regime medido e limitações (obrigatório)

- Dois regimes: **sintético exato** (offline, padrão D-01..D-08) e
  **volume real exploratório declarado** (após a infra de geografia da
  fatia 1 enriquecer o grafo e o silver estar acumulado). No real, **não
  há predição dura de contagem** — mede-se e publica-se a contagem e o
  invariante estrutural P6, sem banda a priori (lição de D-03/D-03b).
- **Distância entre sedes municipais, não entre endereços.** As
  coordenadas são as das sedes dos municípios (tabela de referência
  kelvins/municipios-brasileiros, MIT — fatia 1), não dos estabelecimentos.
  Um fornecedor de Olinda que atende Recife (4,9 km) não sinaliza; um
  fornecedor da sede de São Paulo que atende um município do interior
  paulista sinaliza mesmo com filial local — limitação declarada, o sinal
  é apontador de triagem editorial, não alegação de ilicitude.
- **Fornecedor PF fora do v1.** O dump RFB não traz endereço de CPF; o
  fornecedor pessoa física nunca sinaliza no v1 (declarado).
- **Sem gate temporal no v1**: todos os contratos do silver entram na
  agregação por (fornecedor, município comprador). Janela temporal é
  refinamento futuro (PR-D-09b), assim como a calibração do limiar de
  100 km em volume real.
- **Fronteira do limiar**: a comparação é estrita (`distance >
  max_distance_km`); desvios de ponto flutuante ≤ 0,001 km em torno do
  limiar são inobserváveis na bateria (limitação declarada) — o gate é
  coberto por casos bracket (83,396 km abaixo, 103,260 km acima).
- **Dependência da fatia 1**: de-para (cidade, UF) → IBGE/lat/long e a
  cadeia fornecedor → `establishments.municipio` (TOM) →
  `rfb_municipalities` → nome → coordenadas são pré-condição de
  engenharia, governada pelo ciclo BDD/TDD usual, não por este registro.

## 3. Semântica do sinal (declarada)

`geography.py` puro e determinístico, sobre o silver `contracts`
enriquecido pela cadeia de de-para da fatia 1:

- **Resolução de coordenadas**: comprador por (`buyer.city`, `buyer.uf`)
  normalizado (maiúsculas, sem acentos — mesma disciplina de
  `political._normalize_city`); fornecedor PJ por `supplier.cnpj` →
  `establishments` (matriz) → código TOM → nome do município →
  coordenadas. Qualquer elo ausente → par sem coordenadas → nunca
  sinaliza.
- **Distância**: haversine com R = 6371,0 km entre as sedes municipais,
  fórmula declarada:
  `2R·asin(√(sin²(Δφ/2) + cos φ1·cos φ2·sin²(Δλ/2)))`, graus → radianos.
- **Gate de distância**: sinal se `distance_km > max_distance_km` =
  **100,0** (estrito), placeholder de calibração pré-registrado.
- **Score**: `round(min(1.0, distance_km / 1000.0), 4)` — satura em 1,0 a
  partir de 1.000 km. Âncoras exatas na seção 5 e no `D-09.json`.
- **Emissão**: um sinal por (documento do fornecedor, município
  comprador) — todos os contratos do par compartilham a mesma distância
  (sedes fixas); `details` carrega distância, cidades e códigos IBGE dos
  dois municípios, contagem e soma dos contratos. Limiares vivem na
  config (`D-09.json`), nunca só em código.
- **LGPD**: coordenadas de sedes municipais são dado público; o `details`
  não carrega documento além do `entity_id` já usual nos sinais.

## 4. Desenho

**Regime sintético** (por seed; estrutura idêntica nas 5 seeds, que só
randomizam campos neutros). A bateria injeta uma tabela de municípios
sintética com coordenadas plantadas (municípios fictícios + Recife,
Olinda, João Pessoa e São Paulo com coordenadas reais de sede), de modo
que as distâncias são computáveis a priori pela fórmula da seção 3 —
valores pinados com 6 decimais no `D-09.json`:

- **G1** — fornecedor e comprador no mesmo município (0,000 km) → sem
  sinal.
- **G2** — Recife × Olinda (4,922050 km) → sem sinal.
- **G3** — par equatorial a 0,75° (83,396195 km < 100) → sem sinal
  (bracket inferior do gate).
- **G4** — Recife × João Pessoa (103,260266 km) → **sinal**, score
  **0,1033** (âncora; bracket superior do gate).
- **G5** — par equatorial a 1° (111,194927 km) → **sinal**, score
  **0,1112** (âncora).
- **G6** — par equatorial a 4,5° (500,377170 km) → **sinal**, score
  **0,5004** (âncora de meio de escala).
- **G7** — Recife × São Paulo (2.131,060660 km) → **sinal**, score
  **1,0000** (saturação).
- **G8** — fornecedor sem coordenadas (CNPJ sem estabelecimento, ou TOM
  desconhecido) → sem sinal.
- **G9** — comprador sem de-para (cidade fora da tabela) → sem sinal.
- **G10** — fornecedor PF (CPF) → sem sinal (v1).
- **Controle** — 20 pares fornecedor-comprador com distâncias
  pseudoaleatórias abaixo do limiar → zero sinais.

**Volume real**: após a fatia 1 e uma run do `detect`, mede-se a contagem
de sinais e verifica-se P6 por query no gold; publica-se no R-D-09 sem
banda a priori (exploratório declarado).

## 5. Predições (numéricas, falsificáveis)

Veredito por seed no sintético; a predição falha se divergir em qualquer
uma das 5 seeds. Distâncias e scores esperados estão pinados no
`D-09.json` com 6 decimais; o desvio tolerado é 1e-9 (antes do
arredondamento declarado do score).

- **P1 — conjunto exato de sinais.** Sinalizam exatamente G4, G5, G6 e
  G7; G1, G2, G3, G8, G9, G10 e os 20 controles não. *Refutada* com
  qualquer sinal a mais ou a menos.
- **P2 — gate de distância.** G2 e G3 sem sinal; G4 sinaliza. *Refutada*
  se distância ≤ 100 km sinalizar ou se 103,260266 km não sinalizar.
- **P3 — âncoras de score.** G4 → 0,1033; G5 → 0,1112; G6 → 0,5004; G7 →
  1,0000 (saturação). *Refutada* com desvio > 1e-9 em qualquer âncora.
- **P4 — disciplina de dado ausente.** G8, G9 e G10 sem sinal. *Refutada*
  se par sem coordenadas resolvíveis sinalizar.
- **P5 — determinismo.** A mesma seed reproduz bit a bit os mesmos
  sinais; execuções distintas divergem em zero casos.
- **P6 — invariante estrutural (pós-integração, dados reais).** Todo
  sinal `anomalous_geography` no gold tem as duas coordenadas resolvidas,
  distância recomputada > limiar e score consistente com a fórmula.
  Verificado por query após uma run do `detect`; resultado anexado ao
  R-D-09.

## 6. Controles e invariantes

- Controles internos: G1/G2/G3 (gate de distância), G8/G9/G10 (dado
  ausente) e os 20 pares abaixo do limiar.
- Invariante de composição: o sinal existe se e somente se ambas as
  coordenadas resolvem e a distância supera o limiar — testável
  recomputando a partir das linhas silver (`details` carrega os campos
  que fundamentam).
- Invariante de monotonicidade: alterar limiar, referência de score,
  incluir PF, endereços de filiais ou janela temporal exige PR de
  refinamento (`PR-D-09b`) com a justificativa medida.
- Os sinais existentes não podem ser afetados (guarda: baterias
  D-01..D-08 seguem verdes).

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 exatas nas 5 seeds (P6 após a
integração). Qualquer refutação é publicada em `docs/results/R-D-09.md`
com a causa investigada, e a forma corrigida vira `PR-D-09b.md` antes de
nova execução. O sucesso habilita — não autoriza automaticamente — a
calibração do limiar em volume real e a exposição do sinal nos marts.

## 8. Execução (fatia 2, após aprovação humana deste registro)

1. **Sinal**: `src/capiba/detection/geography.py` puro +
   `SignalType.ANOMALOUS_GEOGRAPHY`; emissão no `task_detect`
   (best-effort, padrão dos demais); operador AQL legado removido ou
   reescrito sobre os vértices enriquecidos (decisão de implementação,
   registrada em Revisões).
2. **Bateria**: runner `battery_geography.py` (dispatch `"geography"` em
   `scripts/detect_battery.py`) lendo `experiments/detect/D-09.json`;
   teste de regime `@pytest.mark.slow`.
3. **Publicação**: `docs/results/R-D-09.md` com o veredito (inclusive se
   refutada) e P6 anexado após a run real.

## Revisões

- 2026-08-20: criação (rascunho para revisão humana). Pesquisa de
  viabilidade: payload PNCP de contratos não traz município do fornecedor
  (confirmado no parser, `normalizer.py`); a geografia do fornecedor PJ
  vem do dump RFB (`establishments.municipio`, código TOM) com de-para
  TOM→nome do próprio dump (`Municipios.zip`); a referência de
  coordenadas é o CSV kelvins/municipios-brasileiros
  (MIT, ~5.570 municípios, IBGE + lat/long + SIAFI). Limiar de 100 km e
  referência de score de 1.000 km são placeholders de calibração
  pré-registrados, à espera de validação no regime sintético e, depois,
  de calibração em volume real.
- 2026-08-20: execução da fatia 2 (após aprovação humana). Decisão de
  implementação: o operador AQL legado `graphs.anomalous_geography` foi
  **removido**, não reescrito — estava morto (filtra vértices `bid` que o
  grafo nunca cria), usava aproximação planar euclidiana (× 111)
  incompatível com a fórmula haversine pré-registrada e uma versão
  reescrita sobre os vértices enriquecidos duplicaria a semântica sem
  consumidor (o `task_detect` emite o sinal pela função pura sobre o
  silver, padrão `political.py`). Predições intactas. Resultado: bateria
  D-09 **success** — P1–P5 exatas nas 5 seeds (`docs/results/R-D-09.md`);
  P6 (invariante no gold real) e o regime de volume real seguem pendentes
  por desenho.
