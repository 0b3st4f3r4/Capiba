# Pré-registros — experimentos de detecção

Doutrina leve para experimentos sobre os sinais de fraude do Capiba,
adaptada do programa Tanajura (`~/Projects/Tanajura/docs/charter.md`).
Aplica-se a **experimentos de detecção**: novos sinais, calibração de
limiares, validação de operadores contra verdade de terreno. Features de
engenharia seguem cobertas pelo ciclo BDD/TDD usual (ver AGENTS.md) —
este diretório não substitui testes, governa *alegações empíricas*.

## Regras

1. **Nenhuma bateria sem pré-registro.** Predição numérica falsificável,
   métricas primárias, critérios de sucesso **e de refutação**, controles
   e seeds declarados vivem num `PR-D-NN.md` aqui **antes** de qualquer
   execução. Emendas só datadas e justificadas.
2. **Resultados negativos são resultados.** Refutação bem medida é
   publicada com o mesmo rigor em `docs/results/R-D-NN.md`, seguida de
   refinamento pré-registrado (`PR-D-NNb.md`) quando a forma é corrigida.
3. **Tudo é declarativo.** A configuração da bateria vive em
   `experiments/detect/D-NN.json` (seeds inclusas); nenhum parâmetro vive
   só em código ou linha de comando.
4. **Âncoras exatas primeiro.** Sempre que um valor for computável a
   priori (HHI com shares conhecidos, taxa de falso-positivo de um teste
   qui-quadrado), a predição é o valor exato, com o desvio tolerado
   declarado — nunca uma banda vaga.
5. **Limitação obrigatória.** Todo pré-registro declara o regime medido
   (sintético × real, escopo dos operadores) e o que ele **não** prova.

## Convenções

- Identificadores estáveis: bateria `D-NN`, pré-registro `PR-D-NN`,
  resultado `R-D-NN`, refinamentos com sufixo `b`, `c`, ...
- Prosa em português (vírgula decimal); código, configurações e valores
  numéricos de config em inglês (ponto decimal).
- Cabeçalho com data de criação e última atualização; revisões
  substanciais geram entrada datada na seção de Revisões.

## Índice de baterias

| Ciclo | Pré-registro | Veredito | Resultado | Objeto |
|---|---|---|---|---|
| D-01 | PR-D-01.md | refutado (P2, P3) | R-D-01.md | Semântica dos sinais estatísticos no regime sintético |
| D-01b | PR-D-01b.md | success | R-D-01b.md | Forma corrigida da D-01 (reconciliação pipeline↔API) |
| D-02 | PR-D-02.md | success | R-D-02.md | Validação de `detect_collusion` e `trace_ownership` |
| D-03 | PR-D-03.md | inconclusivo | R-D-03.md | Calibração do `collusion_network` em volume real |
| D-03b | PR-D-03b.md | inconclusivo | R-D-03b.md | Refinamento por co-ocorrência entre compradores |
| D-03c | PR-D-03c.md | refutado (hipótese de escala) | R-D-03c.md | Blocking de recall exato do `collusion_network` |
| D-03d | PR-D-03d.md | success | R-D-03d.md | Emissão ranqueada top-K com orçamento editorial |
| D-04 | PR-D-04.md | success | R-D-04.md | CRI de Fazekas & Kocsis por contrato |
| D-05 | PR-D-05.md | success | R-D-05.md | Red flags de aditivos contratuais |
| D-05b | PR-D-05b.md | em aberto (piloto ativo) | — | Plano B: termos contratuais PNCP por contrato |
| D-06 | PR-D-06.md | success | R-D-06.md | `sanctioned_supplier` (match exato por documento) |
| D-06b | PR-D-06b.md | success | R-D-06b.md | Screening fuzzy nome + documento mascarado |
| D-07 | PR-D-07.md | refutado (banda de revocação) | R-D-07.md | Entity resolution de sócios e fornecedores |
| D-07b | PR-D-07b.md | success | R-D-07b.md | Recalibração da banda de revocação da D-07 |
| D-08 | PR-D-08.md | success | R-D-08.md | `political_connection` (doações TSE × contratos) |
| D-08b | PR-D-08b.md | em aberto | — | Migração da fonte TSE para a Base dos Dados (paridade `tse_parity`) |
| D-09 | PR-D-09.md | success | R-D-09.md | `anomalous_geography` (distância fornecedor × comprador) |
| D-10 | PR-D-10.md | exploratório | — | `notice_clone` sobre o corpus do Querido Diário (sem R-D-10; P2 refutada no exploratório) |
| D-10b | PR-D-10b.md | success | R-D-10b.md | Forma corrigida da P2 (rank ≤ 4) do `notice_clone` |
| D-11 | PR-D-11.md | em aberto (sem bateria) | — | ML supervisionado sobre os rótulos de triagem |
| D-12 | PR-D-12.md | refutado | R-D-12.md | Piloto de screening de PEPs via yente/OpenSanctions |
