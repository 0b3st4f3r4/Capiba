"""Offline sample payloads for the ingestion sources.

Chunk: mock
Responsibility: Provide deterministic sample records in the raw shape of
each source (PNCP, Transparency Portal) so pipelines can run end-to-end
without external APIs — used by the ``mock_*`` entries of the pipeline
source registry and by ``scripts/ingestion.py --mock``.

Dependencies: none
"""

from __future__ import annotations

from typing import Any


def mock_pncp() -> list[dict[str, Any]]:
    """Returns a sample PNCP contract payload (raw ``/v1/contratos`` shape)."""
    return [
        {
            "numeroControlePNCP": "12345678000190-1-000001/2026",
            "numeroCompra": "001/2026",
            "anoCompra": 2026,
            "processo": "P001/2026",
            "modalidadeId": 6,
            "modalidadeNome": "Pregão - Eletrônico",
            "situacaoCompraId": 1,
            "situacaoCompraNome": "Divulgada no PNCP",
            "objetoCompra": "Aquisição de material de escritório",
            "valorTotalHomologado": 15000.00,
            "dataPublicacaoPncp": "2026-01-15",
            "dataAssinatura": "2026-01-15",
            "dataVigenciaInicio": "2026-01-15",
            "dataVigenciaFim": "2026-12-31",
            "tipoPessoa": "PJ",
            "niFornecedor": "98765432000196",
            "nomeRazaoSocialFornecedor": "Fornecedora Exemplo Ltda",
            "valorInicial": 15000.00,
            "valorGlobal": 15000.00,
            "orgaoEntidade": {
                "cnpj": "12345678000190",
                "razaosocial": "Prefeitura Municipal de Exemplo",
                "esferaId": "M",
            },
            "unidadeOrgao": {
                "codigoUnidade": "123456",
                "nomeUnidade": "Secretaria Municipal de Administração",
                "ufSigla": "MG",
                "municipioNome": "Belo Horizonte",
            },
        },
    ]


def mock_transparency() -> list[dict[str, Any]]:
    """Returns a sample Transparency Portal payload (raw contracts shape)."""
    return [
        {
            "id": "T001",
            "numeroContrato": "001/2026",
            "numeroProcesso": "P002/2026",
            "objeto": "Serviços de limpeza",
            "valorInicial": 50000.00,
            "dataAssinatura": "2026-02-01",
            "dataVigenciaInicio": "2026-02-01",
            "dataVigenciaFim": "2026-12-31",
            "modalidade": "Dispensa",
            "situacao": "concluido",
            "orgao": {
                "codigoSIAFI": "123456",
                "nome": "Prefeitura Municipal de Exemplo",
                "esfera": "municipal",
                "uf": "MG",
                "municipio": "Belo Horizonte",
            },
            "fornecedor": {
                "cnpj": "98765432000196",
                "razaoSocial": "Limpeza Total Ltda",
            },
        },
    ]
