"""Detection battery runner (bateria D-10).

Responsibility: Generate the synthetic gazette corpus with the planted
cases N0-N6 (per the declarative config ``experiments/detect/D-10.json``),
segment the editions with ``capiba.ingestion.gazette_segments``, compute
the ``notice_clone`` signals with ``capiba.detection.notice_clone``
in-process and evaluate the pre-registered predictions P1-P7
(``docs/preregistrations/PR-D-10.md``). P6b/P8 (real pilot sample) and P9
(post-integration invariant) live outside this runner.

The detection input is the segmentation output of the synthetic editions
(full chain: edition text -> segments -> notices -> signals), so the
battery exercises the segmentation as a pre-condition of the signal. The
encoder is injectable: the official run uses the sentence-transformers
model pinned in the config; fast tests inject a deterministic stub.

Case structure is identical across the seeds — they only randomize
neutral fields (org names, process numbers, values, dates). Design note
registered in the PR Revisões: the N0 pairs (bit-a-bit copies) carry **no
extractable process number**, otherwise the reedition veto (same process
number on both sides) would forbid the exact-copy anchor; N4 covers the
veto discipline.

Doctrine: no battery without a pre-registration. The config is the
single source of parameters (seeds included); raw outputs are versioned
under ``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from capiba.detection.notice_clone import (
    ENCODER_MODEL,
    EncoderFn,
    Notice,
    candidate_pairs,
    cosine_similarity,
    default_encoder,
    notice_clone_signals,
    notice_id,
    valid_notices,
)
from capiba.ingestion.gazette_segments import segment_edition

RUN_DATE = date(2026, 8, 21)  # fixed run date; historicals fall in the window

_ORGAOS = [
    "Prefeitura Municipal de Alto Esperança",
    "Prefeitura Municipal de Bela Vista do Sul",
    "Secretaria Municipal de Infraestrutura de Porto das Flores",
    "Secretaria Municipal de Saúde de Vila Rica",
    "Consórcio Intermunicipal de Saúde do Vale",
    "Autarquia Municipal de Tecnologia de Santa Luzia",
]

_DOMAINS = ("obras", "saude", "ti")

# One object phrase per (domain, index): the 18 clone sources are the
# unique (domain, intro) pairs, each with its own object, so no two
# sources share phrasing beyond generic boilerplate.
_OBJECT_PHRASES = {
    "obras": [
        "contratação de empresa de engenharia para execução de obras de pavimentação asfáltica e drenagem pluvial em vias urbanas do município",
        "construção de calçadas e passeios públicos com piso de concreto intertravado nos bairros centrais",
        "reforma e ampliação de prédios públicos municipais, incluindo instalações elétricas e hidráulicas",
        "construção de pontes e passarelas de concreto armado sobre os rios do perímetro urbano",
        "serviços de terraplenagem e contenção de encostas em áreas de risco do município",
        "recuperação de estradas vicinais e caminhos rurais de acesso às comunidades do interior",
    ],
    "saude": [
        "aquisição de medicamentos para a rede municipal de saúde, com distribuição às unidades básicas",
        "aquisição de equipamentos hospitalares e de diagnóstico por imagem para o hospital municipal",
        "contratação de serviços médicos especializados em cardiologia para atendimento ambulatorial",
        "aquisição de insumos e materiais odontológicos para as clínicas da rede municipal",
        "contratação de serviços de vigilância sanitária e análises laboratoriais de água e alimentos",
        "aquisição de vacinas e imunobiológicos para o calendário municipal de imunização",
    ],
    "ti": [
        "aquisição de computadores e periféricos para as secretarias municipais",
        "contratação de serviços de conectividade e link dedicado de internet para os prédios públicos",
        "licenciamento de software de gestão integrada para a administração municipal",
        "contratação de serviços de segurança da informação e backup em nuvem",
        "modernização do parque de impressoras e scanners das repartições municipais",
        "implantação de rede sem fio nos prédios públicos e praças do município",
    ],
}

_HEADERS = [
    "EDITAL Nº {num}/{ano}",
    "AVISO DE LICITACAO",
    "EDITAL DE PREGÃO ELETRÔNICO Nº {num}/{ano}",
    "AVISO DE LICITACAO Nº {num}/{ano}",
    "EXTRATO DE EDITAL",
    "EDITAL Nº {num}/{ano}",
]

_BODIES = [
    "A {orgao} torna público que realizará pregão eletrônico para {objeto}, conforme as especificações do projeto básico e o cronograma físico-financeiro anexo. O valor estimado é de R$ {valor}. O certame ocorrerá no dia {data}, às 10 horas, na sede da {orgao}. Os interessados devem apresentar a documentação exigida neste edital e seus anexos.{processo}",
    "A {orgao} comunica à sociedade a abertura de licitação na modalidade pregão presencial destinada a {objeto}, conforme as especificações do projeto básico. O orçamento estimado é de R$ {valor}. A sessão pública será realizada no dia {data}, às 14 horas, no auditório da prefeitura. A documentação de habilitação consta deste aviso e de seus anexos.{processo}",
    "A {orgao} faz saber que promoverá licitação para {objeto}, observadas as condições do projeto básico e seus anexos. Valor estimado: R$ {valor}. Data de abertura: {data}, às 9 horas. Os licitantes apresentarão os documentos conforme as exigências deste edital.{processo}",
    "Fica a comunidade informada de que a {orgao} realizará disputa eletrônica para {objeto}, conforme as especificações do projeto básico. O valor de referência é R$ {valor}. O evento acontecerá em {data}, às 15 horas, com transmissão pela internet. A habilitação segue as exigências deste instrumento convocatório e de seus anexos.{processo}",
    "A {orgao} informa que se encontra aberto certame para {objeto}, conforme o projeto básico e o cronograma físico-financeiro anexo. Estimativa financeira de R$ {valor}. A abertura das propostas ocorrerá no dia {data}, às 11 horas. Os interessados devem consultar os anexos deste extrato para a documentação completa.{processo}",
    "A {orgao} anuncia a realização de licitação eletrônica tendo por objeto {objeto}, conforme as especificações do projeto básico e seus anexos. O montante previsto é de R$ {valor}. O certame será aberto em {data}, às 16 horas. A documentação exigida encontra-se neste edital e em seus anexos.{processo}",
]

# Standardized minuta template shared by both sides of every N3 pair
# (same formal structure, distinct object/values — the structural
# false-positive control).
_MINUTA_HEADER = "AVISO DE LICITACAO"
_MINUTA_BODY = (
    "A {orgao}, no uso de suas atribuições legais e em conformidade com a Lei nº "
    "14.133, de 1º de abril de 2021, torna público a todos os interessados a "
    "abertura de licitação, na modalidade pregão eletrônico, destinada a "
    "{objeto}, pelo valor estimado de R$ {valor}, observadas as condições "
    "estabelecidas neste instrumento convocatório e seus anexos, que ficam à "
    "disposição dos interessados no portal oficial do município. A sessão "
    "pública de abertura ocorrerá no dia {data}, às 10 horas, por meio "
    "eletrônico.{processo}"
)

# Deterministic paraphrase operations of the N2 cases: fixed synonym map
# plus reversal of the middle sentences (PR-D-10 § 4: paragraph
# reordering + synonyms + abbreviations).
_SYNONYMS = (
    ("torna público", "comunica"),
    ("comunica à sociedade", "torna do conhecimento público"),
    ("faz saber", "informa"),
    ("realizará", "promoverá"),
    ("pregão eletrônico", "certame eletrônico"),
    ("documentação", "documentos"),
    ("no dia", "na data de"),
    ("devem apresentar", "apresentarão"),
    ("interessados", "licitantes"),
    ("seus anexos", "os documentos anexos"),
)


def _nup(rng: random.Random) -> str:
    """Draws a synthetic NUP-format process number."""
    return (
        f"{rng.randrange(10000, 99999)}.{rng.randrange(100000, 999999)}"
        f"/{rng.randrange(2023, 2027)}-{rng.randrange(10, 99)}"
    )


def _money(rng: random.Random) -> str:
    """Draws a synthetic BRL amount in pt-BR format."""
    value = rng.uniform(50_000.0, 5_000_000.0)
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _date_str(rng: random.Random) -> str:
    """Draws a synthetic future session date."""
    day = RUN_DATE + timedelta(days=rng.randint(15, 120))
    return day.strftime("%d/%m/%Y")


def _slots(rng: random.Random, *, with_process: bool = True) -> dict[str, Any]:
    """Draws the neutral slot values of a notice (seed-randomized)."""
    return {
        "num": rng.randint(1, 999),
        "ano": rng.randint(2023, 2026),
        "orgao": rng.choice(_ORGAOS),
        "valor": _money(rng),
        "data": _date_str(rng),
        "processo": f" Processo nº {_nup(rng)}." if with_process else "",
    }


def _render(intro: int, objeto: str, slots: dict[str, Any]) -> str:
    """Renders a notice: header line + single-paragraph body."""
    header = _HEADERS[intro].format(**slots)
    body = _BODIES[intro].format(objeto=objeto, **slots)
    return f"{header}\n{body}"


def _paraphrase(text: str) -> str:
    """Applies the fixed N2 paraphrase operations to a notice text."""
    paraphrased = text
    for source, target in _SYNONYMS:
        paraphrased = paraphrased.replace(source, target)
    header, _, body = paraphrased.partition("\n")
    sentences = [s for s in body.split(". ") if s]
    if len(sentences) >= 4:
        sentences = [sentences[0], *reversed(sentences[1:-1]), sentences[-1]]
    return f"{header}\n{'. '.join(sentences)}"


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic gazette corpus for one seed.

    The case structure is fixed by the pre-registration (PR-D-10 § 4);
    the seed only randomizes neutral fields (org names, process numbers,
    values, dates). Notices are assembled into edition texts (preamble +
    marker headers) so the detection input is the segmentation output.

    Returns:
        ``editions`` (date, edition id, integral text, N6 flag), the
        ground-truth ``meta`` (planted pairs by case, addressed by text +
        date) and the fixed ``run_date``.
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    thresholds = config["thresholds"]
    window = int(thresholds["window_days"])
    counts = {case["id"]: case["count"] for case in config["synthetic"]["cases"]}

    historical: list[tuple[str, date]] = []  # (text, date)
    new_notices: list[str] = []
    meta_pairs: list[dict[str, str]] = []

    def hist_date() -> date:
        return RUN_DATE - timedelta(days=rng.randint(1, window - 1))

    def plant_pair(case: str, hist_text: str, new_text: str, when: date) -> None:
        historical.append((hist_text, when))
        new_notices.append(new_text)
        meta_pairs.append(
            {
                "case": case,
                "new_text": new_text,
                "hist_text": hist_text,
                "hist_date": when.isoformat(),
            }
        )

    # Clone sources: the 18 unique (domain, intro) combinations.
    sources = [(domain, intro) for domain in _DOMAINS for intro in range(6)]
    source_iter = iter(sources)

    # N0 — bit-a-bit copies WITHOUT an extractable process number (a copy
    # carrying the same process number would hit the reedition veto; N4
    # covers that discipline). Exact-copy anchor: cosine 1.0, rank 1.
    for _ in range(int(counts.get("N0", 0))):
        domain, intro = next(source_iter)
        text = _render(
            intro,
            _OBJECT_PHRASES[domain][intro],
            _slots(rng, with_process=False),
        )
        plant_pair("N0", text, text, hist_date())

    # N1 — verbal clones: same template/object, only slots change.
    for _ in range(int(counts.get("N1", 0))):
        domain, intro = next(source_iter)
        objeto = _OBJECT_PHRASES[domain][intro]
        plant_pair(
            "N1",
            _render(intro, objeto, _slots(rng)),
            _render(intro, objeto, _slots(rng)),
            hist_date(),
        )

    # N2 — paraphrased clones: re-rendered with new slots, then the fixed
    # paraphrase operations (synonyms + sentence reordering).
    for _ in range(int(counts.get("N2", 0))):
        domain, intro = next(source_iter)
        objeto = _OBJECT_PHRASES[domain][intro]
        plant_pair(
            "N2",
            _render(intro, objeto, _slots(rng)),
            _paraphrase(_render(intro, objeto, _slots(rng))),
            hist_date(),
        )

    # N3 — standardized minuta pairs: same formal structure, distinct
    # objects/values (structural false-positive control).
    all_objects = [p for phrases in _OBJECT_PHRASES.values() for p in phrases]
    for _ in range(int(counts.get("N3", 0))):
        objeto = rng.choice(all_objects)
        hist_slots = _slots(rng)
        new_slots = _slots(rng)
        plant_pair(
            "N3",
            f"{_MINUTA_HEADER}\n" + _MINUTA_BODY.format(objeto=objeto, **hist_slots),
            f"{_MINUTA_HEADER}\n" + _MINUTA_BODY.format(objeto=objeto, **new_slots),
            hist_date(),
        )

    # N4 — reeditions: same process number on both sides, altered text
    # (veto — never signals).
    for k in range(int(counts.get("N4", 0))):
        domain = _DOMAINS[k % 3]
        intro = k % 6
        objeto = _OBJECT_PHRASES[domain][intro]
        process = f" Processo nº {_nup(rng)}."
        orgao = rng.choice(_ORGAOS)
        base_slots = _slots(rng)
        base_slots.update({"orgao": orgao, "processo": process})
        new_slots = _slots(rng)
        new_slots.update({"orgao": orgao, "processo": process})
        plant_pair(
            "N4",
            _render(intro, objeto, base_slots),
            _paraphrase(_render(intro, objeto, new_slots)),
            hist_date(),
        )

    # N5 — the remaining base notices: distinct domains (saúde x obras x
    # TI), absence-of-signal control.
    consumed = sum(int(counts.get(c, 0)) for c in ("N0", "N1", "N2"))
    remaining = int(config["synthetic"]["base_notices"]) - consumed
    for k in range(remaining):
        domain = _DOMAINS[k % 3]
        intro = (k // 3) % 6
        first, second = rng.sample(_OBJECT_PHRASES[domain], 2)
        objeto = f"{first} e também {second}"
        historical.append((_render(intro, objeto, _slots(rng)), hist_date()))

    # N6 — one edition with the planted notices and markers: segmentation
    # must recover exactly the declared number of units.
    n6_units = int(
        next(c for c in config["synthetic"]["cases"] if c["id"] == "N6")["expected"][
            "units"
        ]
    )
    n6_notices = [
        _render(
            k % 6,
            f"{_OBJECT_PHRASES[_DOMAINS[k % 3]][k % 6]}, em lotes distintos",
            _slots(rng),
        )
        for k in range(n6_units)
    ]
    n6_date = RUN_DATE - timedelta(days=10)
    preamble = (
        "DIÁRIO OFICIAL DO MUNICÍPIO\n"
        "Poder Executivo\n"
        f"Recife, {n6_date.strftime('%d de %B de %Y')}"
    )

    # Edition assembly: historical notices grouped by date, one run-date
    # edition with the new notices, plus the N6 edition.
    editions: list[dict[str, Any]] = []
    by_date: dict[date, list[str]] = {}
    for text, when in historical:
        by_date.setdefault(when, []).append(text)
    for when in sorted(by_date):
        editions.append(
            {
                "date": when,
                "edition": f"ED-{when.isoformat()}",
                "text": "\n".join(by_date[when]),
                "is_n6": False,
            }
        )
    editions.append(
        {
            "date": RUN_DATE,
            "edition": f"ED-{RUN_DATE.isoformat()}",
            "text": "\n".join(new_notices),
            "is_n6": False,
        }
    )
    editions.append(
        {
            "date": n6_date,
            "edition": "ED-N6",
            "text": f"{preamble}\n" + "\n".join(n6_notices),
            "is_n6": True,
        }
    )

    return {
        "editions": editions,
        "meta": {"pairs": meta_pairs},
        "run_date": RUN_DATE.isoformat(),
    }


_ENCODERS: dict[tuple[str, str], EncoderFn] = {}


def _encoder_for(config: dict[str, Any]) -> EncoderFn:
    """Builds (once per model/device) the encoder pinned in the config."""
    encoder = config.get("encoder", {})
    model = encoder.get("model", ENCODER_MODEL)
    device = encoder.get("device", "cpu")
    key = (model, device)
    if key not in _ENCODERS:
        _ENCODERS[key] = default_encoder(model, device)
    return _ENCODERS[key]


def _compute(
    config: dict[str, Any],
    population: dict[str, Any],
    encode: EncoderFn,
) -> dict[str, Any]:
    """Segments the editions and computes signals + analysis pairs.

    The official signals come from ``notice_clone_signals`` (the
    production entry point); the candidate-pair analysis (similarities,
    ranks, false-positive accounting) recomputes similarities over
    ``candidate_pairs`` and must agree with the signal set — a guard
    against implementation drift.
    """
    thresholds = config["thresholds"]
    territory = str(config["territory_id"])
    min_chars = int(thresholds["min_notice_chars"])
    window_days = int(thresholds["window_days"])
    threshold = float(thresholds["notice_clone_threshold"])
    markers = config["segmentation_markers"]

    notices: list[Notice] = []
    n6_units = 0
    for edition in population["editions"]:
        segments = segment_edition(edition["text"], markers)
        if edition["is_n6"]:
            n6_units = len(segments)
        for index, segment in enumerate(segments):
            notices.append(
                Notice(
                    notice_id=notice_id(
                        territory, edition["date"], edition["edition"], index
                    ),
                    territory_id=territory,
                    date=edition["date"],
                    text=segment,
                )
            )

    signals = notice_clone_signals(
        notices,
        encode=encode,
        threshold=threshold,
        window_days=window_days,
        min_chars=min_chars,
        reference_date=RUN_DATE,
        score_decimals=int(thresholds["score_decimals"]),
    )

    valid = valid_notices(notices, min_chars)
    pairs = candidate_pairs(
        valid,
        window_days=window_days,
        min_chars=min_chars,
        reference_date=RUN_DATE,
    )
    vectors = encode([notice.text for notice in valid])
    position = {notice.notice_id: index for index, notice in enumerate(valid)}

    pair_records: list[dict[str, Any]] = []
    for new, historical in pairs:
        similarity = cosine_similarity(
            vectors[position[new.notice_id]],
            vectors[position[historical.notice_id]],
        )
        pair_records.append(
            {
                "new_id": new.notice_id,
                "hist_id": historical.notice_id,
                "similarity": similarity,
                "signaled": bool(similarity > threshold),
            }
        )

    # Drift guard: the analysis pairs must reproduce the signal set.
    signaled_pairs = {
        (p["new_id"], p["hist_id"]) for p in pair_records if p["signaled"]
    }
    signal_pairs = set()
    for signal in signals:
        details = json.loads(signal["details"])
        signal_pairs.add((details["new_notice_id"], details["historical_notice_id"]))
    if signaled_pairs != signal_pairs:
        raise RuntimeError("signal set diverges from pair analysis")

    # Ground-truth resolution: planted texts -> segmented notices.
    by_text_date = {(n.text, n.date.isoformat()): n for n in valid}
    meta_pairs: list[dict[str, Any]] = []
    for planted in population["meta"]["pairs"]:
        planted_new = by_text_date.get((planted["new_text"], RUN_DATE.isoformat()))
        planted_hist = by_text_date.get(
            (planted["hist_text"], planted["hist_date"])
        )
        meta_pairs.append(
            {
                "case": planted["case"],
                "new_id": planted_new.notice_id if planted_new else None,
                "hist_id": planted_hist.notice_id if planted_hist else None,
            }
        )

    # Rank of the planted historical among the candidates of its new
    # notice (ties do not penalize: rank = 1 + strictly more similar).
    similarity_by_pair = {
        (p["new_id"], p["hist_id"]): p["similarity"] for p in pair_records
    }
    ranks: list[dict[str, Any]] = []
    for meta in meta_pairs:
        if meta["case"] not in ("N0", "N1", "N2"):
            continue
        planted_sim = similarity_by_pair.get((meta["new_id"], meta["hist_id"]))
        if planted_sim is None:
            ranks.append({**meta, "similarity": None, "rank": None})
            continue
        rank = 1 + sum(
            1
            for p in pair_records
            if p["new_id"] == meta["new_id"] and p["similarity"] > planted_sim
        )
        ranks.append({**meta, "similarity": planted_sim, "rank": rank})

    return {
        "signals": signals,
        "pairs": pair_records,
        "meta_pairs": meta_pairs,
        "ranks": ranks,
        "n6_units": n6_units,
    }


def run_seed(
    config: dict[str, Any],
    seed: int,
    encode: EncoderFn | None = None,
) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P7)."""
    encode = encode or _encoder_for(config)
    first = _compute(config, generate_population(config, seed), encode)
    second = _compute(config, generate_population(config, seed), encode)
    divergences = int(
        first["signals"] != second["signals"] or first["pairs"] != second["pairs"]
    )
    return {"seed": seed, **first, "repeat_divergences": divergences}


def recall_at(record: dict[str, Any], case: str, threshold: float) -> float:
    """Recall of a planted case at a similarity threshold (exploratory)."""
    planted = [
        (m["new_id"], m["hist_id"]) for m in record["meta_pairs"] if m["case"] == case
    ]
    if not planted:
        return 1.0
    similarity_by_pair = {
        (p["new_id"], p["hist_id"]): p["similarity"] for p in record["pairs"]
    }
    recovered = sum(
        1 for pair in planted if similarity_by_pair.get(pair, 0.0) > threshold
    )
    return recovered / len(planted)


def fp_rate_at(record: dict[str, Any], threshold: float) -> float:
    """False-positive rate over the control pairs (exploratory, P4).

    Control pairs are the evaluated candidates that are not planted
    positives (N0/N1/N2): N3 minuta pairs, N5 cross pairs and any other
    incidental pair. Vetoed pairs (N4) never reach the candidate set.
    """
    positives = {
        (m["new_id"], m["hist_id"])
        for m in record["meta_pairs"]
        if m["case"] in ("N0", "N1", "N2")
    }
    controls = [
        p for p in record["pairs"] if (p["new_id"], p["hist_id"]) not in positives
    ]
    if not controls:
        return 0.0
    return sum(1 for p in controls if p["similarity"] > threshold) / len(controls)


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P7 over the records.

    P2 has two declarative forms: the legacy D-10 band (``min_score`` +
    rank 1, refuted) and the recalibrated P2b of D-10b (signal +
    ``rank_max``). The N1 ``expected`` block of the config selects the
    form; everything else is unchanged.
    """
    thresholds = config["thresholds"]
    tolerance = float(thresholds["anchor_tolerance"])
    cases = {c["id"]: c for c in config["synthetic"]["cases"]}
    n1_expected = cases["N1"]["expected"]
    # P2 forms: legacy (D-10, refuted) requires score >= min_score and
    # rank == 1; P2b (D-10b) requires the signal and rank <= rank_max.
    n1_min_score = (
        float(n1_expected["min_score"]) if "min_score" in n1_expected else None
    )
    n1_rank_max = int(n1_expected.get("rank_max", 1))
    n6_expected = int(cases["N6"]["expected"]["units"])
    bands = config.get("bands", {})
    p3_band = bands.get("p3_min_recall")
    p4_band = bands.get("p4_max_fp_rate")
    threshold = float(thresholds["notice_clone_threshold"])

    failures: dict[str, list[str]] = {f"P{i}": [] for i in range(1, 8)}
    if p3_band is None:
        failures["P3"].append("bands.p3_min_recall ausente (exploratório pendente)")
    if p4_band is None:
        failures["P4"].append("bands.p4_max_fp_rate ausente (exploratório pendente)")

    for record in records:
        seed = record["seed"]
        signaled = {
            (json.loads(s["details"])["new_notice_id"],
             json.loads(s["details"])["historical_notice_id"]): s
            for s in record["signals"]
        }
        meta_by_pair = {
            (m["new_id"], m["hist_id"]): m for m in record["meta_pairs"]
        }
        ranks = {(r["new_id"], r["hist_id"]): r for r in record["ranks"]}

        if record["repeat_divergences"]:
            failures["P7"].append(f"seed {seed}: repeat diverged")

        # P1 — N0 exact-copy anchor: score 1.0 (±tolerance), rank 1.
        for pair, meta in meta_by_pair.items():
            if meta["case"] != "N0":
                continue
            signal = signaled.get(pair)
            rank = ranks.get(pair, {})
            if signal is None:
                failures["P1"].append(f"seed {seed}: N0 pair not signaled")
                continue
            if abs(float(signal["score"]) - 1.0) > tolerance:
                failures["P1"].append(
                    f"seed {seed}: N0 score {signal['score']} != 1.0"
                )
            if rank.get("rank") != 1:
                failures["P1"].append(f"seed {seed}: N0 rank {rank.get('rank')} != 1")

        # P2 — N1 verbal clones: signaled, plus the declared band of the
        # config (legacy D-10: score >= min_score and rank 1; P2b/D-10b:
        # rank <= rank_max, no score floor beyond the signal threshold).
        for pair, meta in meta_by_pair.items():
            if meta["case"] != "N1":
                continue
            signal = signaled.get(pair)
            rank = ranks.get(pair, {})
            if signal is None:
                failures["P2"].append(f"seed {seed}: N1 pair not signaled")
                continue
            if n1_min_score is not None and float(signal["score"]) < n1_min_score:
                failures["P2"].append(
                    f"seed {seed}: N1 score {signal['score']} < {n1_min_score}"
                )
            rank_value = rank.get("rank")
            if rank_value is None or rank_value > n1_rank_max:
                failures["P2"].append(
                    f"seed {seed}: N1 rank {rank_value} > {n1_rank_max}"
                )

        # P3 — N2 paraphrased clones: recall at the threshold >= band.
        if p3_band is not None:
            recall = recall_at(record, "N2", threshold)
            if recall < float(p3_band) - tolerance:
                failures["P3"].append(
                    f"seed {seed}: N2 recall {recall:.4f} < band {p3_band}"
                )

        # P4 — false-positive discipline: control-pair rate <= band.
        if p4_band is not None:
            fp_rate = fp_rate_at(record, threshold)
            if fp_rate > float(p4_band) + tolerance:
                failures["P4"].append(
                    f"seed {seed}: FP rate {fp_rate:.4f} > band {p4_band}"
                )

        # P5 — reedition veto: zero signals over the N4 pairs.
        for pair, meta in meta_by_pair.items():
            if meta["case"] == "N4" and pair in signaled:
                failures["P5"].append(f"seed {seed}: N4 reedition signaled")

        # P6a — segmentation anchor: N6 recovers exactly the declared units.
        if record["n6_units"] != n6_expected:
            failures["P6"].append(
                f"seed {seed}: N6 units {record['n6_units']} != {n6_expected}"
            )

    predictions: dict[str, dict[str, Any]] = {
        name: {"verdict": "refuted" if fails else "success", "failures": fails}
        for name, fails in failures.items()
    }
    verdict = (
        "success"
        if all(p["verdict"] == "success" for p in predictions.values())
        else "refuted"
    )
    return {"battery": config["id"], "predictions": predictions, "verdict": verdict}


def run_battery(
    config: dict[str, Any],
    out_dir: Path,
    encode: EncoderFn | None = None,
) -> list[dict[str, Any]]:
    """Runs the battery over the configured synthetic seeds.

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``)
            and ``summary.json``.
        encode: Injectable encoder (default: the model pinned in the
            config — the official run; tests inject a deterministic stub).

    Returns:
        The per-seed records (signals, analysis pairs, repeat divergences).
    """
    encode = encode or _encoder_for(config)
    out_dir.mkdir(parents=True, exist_ok=True)
    threshold = float(config["thresholds"]["notice_clone_threshold"])
    records: list[dict[str, Any]] = []
    for seed in config["seeds"]:
        record = run_seed(config, seed, encode)
        with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
            for signal in record["signals"]:
                fh.write(json.dumps(signal, default=str) + "\n")
        # Raw measures per seed (scores and ranks of the planted pairs) —
        # the declared evidence gap of only persisting the exploratory
        # seed's raw measures is closed by writing them for every seed.
        measures = {
            "seed": seed,
            "threshold": threshold,
            "repeat_divergences": record["repeat_divergences"],
            "n6_units": record["n6_units"],
            "n2_recall": recall_at(record, "N2", threshold),
            "fp_rate": fp_rate_at(record, threshold),
        }
        for case in ("n0", "n1", "n2"):
            measures[case] = [
                {"similarity": r["similarity"], "rank": r["rank"]}
                for r in record["ranks"]
                if r["case"] == case.upper()
            ]
        (out_dir / f"measures_seed_{seed}.json").write_text(
            json.dumps(measures, indent=2, default=str) + "\n"
        )
        records.append(record)

    summary = evaluate(config, records)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
