# Dados vendored

## `municipios.csv`

Municípios brasileiros com código IBGE, latitude/longitude e código SIAFI.

- Fonte: [kelvins/Municipios-Brasileiros](https://github.com/kelvins/Municipios-Brasileiros)
  (`csv/municipios.csv`)
- Licença: [MIT](https://github.com/kelvins/Municipios-Brasileiros/blob/master/LICENSE)
  (© kelvins e contribuidores)
- Uso: referência geográfica do sinal `anomalous_geography` —
  de-para (nome, UF) → (IBGE, lat/long, SIAFI) em
  `src/capiba/ingestion/geography.py` e carga da silver `municipalities`.
- Nota: o diretório se chama `reference/` e não `data/` porque o
  `.gitignore` raiz ignora qualquer diretório `data/` (volumes locais de
  cluster) e o hatchling respeita o `.gitignore` ao montar o wheel.
