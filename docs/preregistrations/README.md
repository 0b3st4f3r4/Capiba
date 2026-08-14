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
