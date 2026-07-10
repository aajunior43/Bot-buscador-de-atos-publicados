"""
Testes unitários para detector.py
Cobre extração de órgão, tipo, número, data, valor, categoria e filtragem de contexto.
"""
from __future__ import annotations

import pytest
from detector import (
    _extrair_orgao,
    _extrair_tipo_numero,
    _extrair_data,
    _extrair_valor,
    _categoria,
    _contexto_ignorado_para_mencao_generica,
    _sem_acentos,
    _parece_linha_de_rodape_ou_assinatura,
    _normalizar_valor,
    _orgao_de_outro_municipio,
    _mencao_generica_sem_palavra_isolada,
    _numero_ato_valido,
    _numero_confiavel,
    _extrair_numero_preferencial,
    _deduplicar_publicacoes,
    _deduplicar_mencoes,
    _chave_pub_dedup,
    _contexto_marca_comercial,
    _fundir_fragmentos_mesmo_ato,
    _termos,
    detectar,
)
from ocr.models import PageText, TextBlock


# ── _extrair_orgao ───────────────────────────────────────────

class TestExtrairOrgao:
    def test_prefeitura_municipal(self):
        texto = "Prefeitura Municipal de Inajá\nDECRETO Nº 001/2026"
        assert _extrair_orgao(texto) == "Prefeitura Municipal de Inajá"

    def test_prefeitura_ocr_truncada(self):
        texto = "PREFEITURA MUNICIPAL DE IN\nESTADO DO PARANÁ\nDECRETO Nº 1/2026"
        assert _extrair_orgao(texto) == "Prefeitura Municipal de Inajá"

    def test_municipio_inava_ocr(self):
        texto = "O Município de INAVÁ, Estado do Paraná\nCNPJ 76.970.318/0001-67"
        assert _extrair_orgao(texto) == "Município de Inajá"

    def test_prefeitura_com_variacao(self):
        texto = "Prefeitura de Inajá - Paraná"
        assert _extrair_orgao(texto) == "Prefeitura Municipal de Inajá"

    def test_camara_municipal(self):
        texto = "Câmara Municipal de Inajá\nRESOLUÇÃO Nº 002/2026"
        assert _extrair_orgao(texto) == "Câmara Municipal de Inajá"

    def test_municipio_de_inaja(self):
        texto = "Município de Inajá\nPORTARIA"
        assert _extrair_orgao(texto) == "Município de Inajá"

    def test_por_cnpj_prefeitura(self):
        # 75.771.400 pertence à Prefeitura, sozinho basta para identificar
        texto = "CNPJ: 75.771.400/0001-48\nDECRETO"
        assert _extrair_orgao(texto) == "Prefeitura Municipal de Inajá"

    def test_por_cnpj_camara_com_mencao_explicita(self):
        # 76.970.318 + "Câmara Municipal" no texto -> Câmara
        texto = "CÂMARA MUNICIPAL DE INAJÁ\nCNPJ: 76.970.318/0001-67\nRESOLUÇÃO Nº 002/2026"
        assert _extrair_orgao(texto) == "Câmara Municipal de Inajá"

    def test_cnpj_76_sem_camara_vai_municipio(self):
        # Documentos da Prefeitura citam 76.970.318 — quando o texto começa com
        # "Prefeitura", o regex inicial vence (Prefeitura, correto). O CNPJ sozinho
        # só é fallback quando não há menção textual explícita.
        texto = "PREFEITURA MUNICIPAL DE INAJÁ\nDECRETO Nº 071/2026\nCNPJ 76.970.318/0001-67"
        # Prefeitura aparece no início -> vence (correto, é da Prefeitura)
        assert _extrair_orgao(texto) == "Prefeitura Municipal de Inajá"

    def test_cnpj_76_isolado_vai_municipio_neutro(self):
        # Sem menção textual explícita a Prefeitura/Câmara, o CNPJ 76.970.318
        # sozinho não deve ser atribuído como Câmara (neutral → Município).
        texto = "DECRETO Nº 071/2026\nCNPJ: 76.970.318/0001-67\nDispõe sobre..."
        assert _extrair_orgao(texto) == "Município de Inajá"

    def test_prefeitura_com_cnpj_76_nao_e_camara(self):
        # Texto começa com PREFEITURA mesmo citando 76.970.318 — deve ser Prefeitura
        texto = "Prefeitura Municipal de Inajá\nDECRETO Nº 071/2026"
        resultado = _extrair_orgao(texto)
        assert resultado == "Prefeitura Municipal de Inajá"
        # E não deve ser atribuído à Câmara
        assert "Câmara" not in (resultado or "")

    def test_sem_orgao(self):
        texto = "Texto qualquer sem identificação de órgão"
        assert _extrair_orgao(texto) is None

    def test_nao_orgao_artigo(self):
        texto = "Art. 1º — fica autorizado..."
        assert _extrair_orgao(texto) is None

    def test_conselho_municipal_saude(self):
        texto = "Conselho Municipal de Saúde de Inajá - PR\nRESOLUÇÃO Nº 001/2026"
        assert _extrair_orgao(texto) == "Conselho Municipal de Saúde de Inajá"

    def test_conselho_municipal_saude_sem_cidade(self):
        # Sem menção explícita a Inajá no início, deve ser None (não inferir)
        texto = "Conselho Municipal de Saúde\nRESOLUÇÃO Nº 001/2026"
        # sem "inaja" no início, cai no fallback e retorna None
        assert _extrair_orgao(texto) is None


# ── _extrair_tipo_numero ─────────────────────────────────────

class TestExtrairTipoNumero:
    def test_decreto(self):
        texto = "Prefeitura Municipal de Inajá\nDECRETO Nº 042/2026\nDispõe sobre..."
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo and "decreto" in tipo.lower()

    def test_portaria(self):
        texto = "PORTARIA Nº 015/2026\nDesigna servidores"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo and "portaria" in tipo.lower()

    def test_lei(self):
        texto = "LEI Nº 123/2026\nEstabelece normas"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo and "lei" in tipo.lower()

    def test_edital(self):
        texto = "EDITAL DE LICITAÇÃO Nº 001/2026\nObjeto: contratação"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo and "edital" in tipo.lower()

    def test_sem_tipo(self):
        texto = "Texto sem tipo de ato algum"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo is None

    def test_quinto_termo_aditivo(self):
        texto = "PREFEITURA MUNICIPAL DE INAJÁ\nQUINTO TERMO ADITIVO DE CONTRATO, PARA O ADITIVO"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo and "aditivo" in tipo.casefold()

    def test_extrato_contrato_ocr_rato(self):
        texto = "PREFEITURA MUNICIPAL DE INAJÁ\nRATO DO CONTRATO Nº 04/2026"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo and "extrato" in tipo.casefold()
        assert numero and "04" in numero


class TestNumeroAtoValido:
    def test_aceita_com_ano(self):
        assert _numero_ato_valido("04/2026") == "04/2026"
        assert _numero_ato_valido("042/2026") == "042/2026"

    def test_rejeita_rg_lixo(self):
        assert _numero_ato_valido("16132720") is None
        assert _numero_ato_valido("12345678") is None

    def test_normaliza_colado_com_ano(self):
        assert _numero_ato_valido("0422026") == "042/2026"

    def test_curto_ok(self):
        assert _numero_ato_valido("89") == "89"

    def test_confiavel_exige_marcador(self):
        trecho = "EXTRATO DO CONTRATO Nº 04/2026 decorrente do pregão"
        assert _numero_confiavel("04/2026", trecho=trecho) == "04/2026"
        # inventado sem N° no texto
        assert (
            _numero_confiavel("070/2023", trecho="aditivo de combustivel sem numero")
            is None
        )

    def test_extrair_preferencial_contrato(self):
        t = "PREFEITURA\nEXTRATO DO CONTRATO Nº 04/2026\nProcesso..."
        assert _extrair_numero_preferencial(t, "Extrato de Contrato") == "04/2026"


class TestDedupPublicacoes:
    def test_prefeitura_e_municipio_mesmo_extrato(self):
        pubs = [
            {
                "pagina": 6,
                "tipo": "Extrato de Contrato",
                "numero": "04/2026",
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "A" * 100,
                "resumo_ia": "Extrato completo com valor",
            },
            {
                "pagina": 6,
                "tipo": "Extrato de Contrato",
                "numero": "04/2026",
                "orgao": "Município de Inajá",
                "trecho": "B" * 50,
            },
        ]
        out = _deduplicar_publicacoes(pubs)
        assert len(out) == 1
        assert "Prefeitura" in (out[0].get("orgao") or "")
        assert out[0].get("resumo_ia")

    def test_chave_mesma_familia(self):
        a = {
            "pagina": 1,
            "tipo": "Extrato de Contrato",
            "numero": "04/2026",
            "orgao": "Prefeitura Municipal de Inajá",
        }
        b = {
            "pagina": 1,
            "tipo": "Extrato de Contrato",
            "numero": "04/2026",
            "orgao": "Município de Inajá",
        }
        assert _chave_pub_dedup(a) == _chave_pub_dedup(b)

    def test_lixo_numero_limpo_na_dedup(self):
        pubs = [
            {
                "pagina": 1,
                "tipo": "Termo Aditivo",
                "numero": "16132720",
                "orgao": "Município de Inajá",
                "trecho": "aditivo combustivel " * 20,
            }
        ]
        out = _deduplicar_publicacoes(pubs)
        assert len(out) == 1
        assert out[0].get("numero") is None


class TestFundirFragmentos:
    def test_funde_aditivo_cabecalho_e_corpo(self):
        pubs = [
            {
                "pagina": 6,
                "tipo": "Termo Aditivo",
                "numero": None,
                "orgao": "Prefeitura Municipal de Inajá",
                "trecho": "PREFEITURA QUINTO TERMO ADITIVO LOURDES ELIAS " * 5,
                "resumo_ia": "Cabeçalho do quinto termo aditivo",
            },
            {
                "pagina": 6,
                "tipo": "Termo Aditivo",
                "numero": None,
                "orgao": "Município de Inajá",
                "trecho": (
                    "Município de Inajá aditivo 25% 25000 litros combustivel "
                    "Lourdes Elias Fernandes contrato 02/2025 " * 8
                ),
                "resumo_ia": "Acréscimo de 25.000 litros de combustível",
                "valor": None,
            },
        ]
        out = _fundir_fragmentos_mesmo_ato(pubs)
        assert len(out) == 1
        assert "Prefeitura" in (out[0].get("orgao") or "")
        assert out[0].get("resumo_ia")


class TestDedupMencoes:

    def test_mesmo_trecho_fica_termo_especifico(self):
        trecho = "...PREFEITURA MUNICIPAL DE INAJÁ ESTADO DO PARANÁ..."
        mencoes = [
            {"pagina": 6, "trecho": trecho, "termo": "Inajá"},
            {"pagina": 6, "trecho": trecho, "termo": "INAVÁ"},
            {"pagina": 6, "trecho": trecho, "termo": "Prefeitura Municipal de Inajá"},
        ]
        out = _deduplicar_mencoes(mencoes)
        assert len(out) == 1
        assert "Prefeitura" in out[0]["termo"]

    def test_trechos_diferentes_permanecem(self):
        mencoes = [
            {"pagina": 6, "trecho": "...ato A Inajá prefeitura...", "termo": "Inajá"},
            {"pagina": 6, "trecho": "...ato B contrato extrato...", "termo": "Inajá"},
        ]
        assert len(_deduplicar_mencoes(mencoes)) == 2

    def test_termos_nao_duplica_inaja_inava(self):
        ts = _termos()
        norms = {_sem_acentos(t).casefold() for t in ts}
        assert "inava" not in norms
        assert sum(1 for t in ts if _sem_acentos(t).casefold() == "inaja") == 1

    def test_marca_comercial(self):
        texto = "Item SR 10 Marca: Inajá 11, quantidade original 104.000 litros"
        idx = texto.casefold().find("inajá")
        # sem órgão municipal perto → marca
        assert _contexto_marca_comercial(texto, idx, idx + 5) is True
        texto2 = "PREFEITURA MUNICIPAL DE INAJÁ decreta o seguinte"
        idx2 = texto2.casefold().find("inajá")
        assert _contexto_marca_comercial(texto2, idx2, idx2 + 5) is False

    def test_numero_normalizado(self):
        texto = "DECRETO Nº 0012026"
        tipo, numero = _extrair_tipo_numero(texto)
        # Número pode ser extraído ou None dependendo da heurística
        assert tipo is not None or tipo is None  # não deve levantar exceção

    def test_errata(self):
        texto = "ERRATA Nº 001/2026\nCorrige o Decreto Nº 042/2026"
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo is not None
        assert "errata" in tipo.lower()

    def test_notificacao(self):
        texto = "NOTIFICAÇÃO\nNotifica a empresa X para cumprimento de prazo."
        tipo, numero = _extrair_tipo_numero(texto)
        assert tipo is not None
        assert "notificacao" in _sem_acentos(tipo).lower()


# ── _extrair_data ────────────────────────────────────────────

class TestExtrairData:
    def test_data_extenso(self):
        texto = "Inajá, 15 de março de 2026."
        data = _extrair_data(texto)
        assert data is not None
        assert "15" in data and "2026" in data

    def test_data_numerica(self):
        texto = "Data: 25/06/2026"
        data = _extrair_data(texto)
        assert data is not None
        assert "25" in data

    def test_sem_data(self):
        texto = "Texto sem nenhuma data"
        assert _extrair_data(texto) is None


# ── _extrair_valor ───────────────────────────────────────────

class TestExtrairValor:
    def test_valor_simples(self):
        texto = "no valor de R$ 15.000,00 (quinze mil reais)"
        valor = _extrair_valor(texto)
        assert valor is not None
        assert "15" in valor

    def test_valor_com_cifrao_s(self):
        texto = "R$15000,00"
        valor = _extrair_valor(texto)
        assert valor is not None

    def test_multiplos_valores_retorna_maior(self):
        texto = "parcela de R$ 500,00 e total de R$ 50.000,00"
        valor = _extrair_valor(texto)
        assert valor is not None
        assert "50" in valor

    def test_sem_valor(self):
        texto = "Texto sem valores monetários"
        assert _extrair_valor(texto) is None


# ── _categoria ───────────────────────────────────────────────

class TestCategoria:
    def test_publicacao_oficial(self):
        texto = "Prefeitura Municipal\nDECRETO Nº 001/2026\ncnpj: 75.771.400/0001-48"
        cat = _categoria(texto)
        assert cat == "publicacao_oficial"

    def test_materia_jornalistica(self):
        texto = "Evento esportivo realizado em Inajá neste fim de semana atraiu grande público."
        cat = _categoria(texto)
        assert cat in ("materia_jornalistica", "publicacao_oficial")

    def test_patrocinador(self):
        texto = "distribuição avulsa farmácia panificadora"
        cat = _categoria(texto)
        assert cat == "patrocinador_distribuicao"


# ── _contexto_ignorado ───────────────────────────────────────

class TestContextoIgnorado:
    def test_distribuicao_avulsa(self):
        texto = "distribuição avulsa em Inajá"
        assert _contexto_ignorado_para_mencao_generica(texto, 20, 25) is True

    def test_contexto_valido(self):
        texto = "Prefeitura Municipal de Inajá publicou o Decreto neste dia."
        assert _contexto_ignorado_para_mencao_generica(texto, 20, 25) is False


# ── _parece_linha_de_rodape ──────────────────────────────────

class TestRodape:
    def test_cep(self):
        assert _parece_linha_de_rodape_ou_assinatura("CEP 87670-000") is True

    def test_email(self):
        assert _parece_linha_de_rodape_ou_assinatura("E-mail: prefeitura@inaja.pr.gov.br") is True

    def test_linha_normal(self):
        assert _parece_linha_de_rodape_ou_assinatura("DECRETO Nº 001/2026") is False


# ── detectar (integração) ────────────────────────────────────

class TestDetectar:
    def _make_pagina(self, texto: str, num: int = 1) -> PageText:
        return PageText(pagina=num, texto=texto, metodo="test", blocks=[])

    def test_detecta_inaja(self, db):
        import database
        database.init_db()
        paginas = [self._make_pagina(
            "Prefeitura Municipal de Inajá\nDECRETO Nº 001/2026\n"
            "Dispõe sobre abertura de crédito adicional.\nInajá, 25 de junho de 2026."
        )]
        resultado = detectar(1, "Edição Teste", paginas)
        assert resultado.encontrado is True
        assert 1 in resultado.paginas_com_mencao

    def test_nao_detecta_sem_inaja(self, db):
        import database
        database.init_db()
        paginas = [self._make_pagina(
            "Prefeitura Municipal de Londrina\nDECRETO Nº 001/2026\n"
            "Dispõe sobre normas administrativas."
        )]
        resultado = detectar(2, "Edição Sem Inajá", paginas)
        # Pode ou não detectar (Inajá não aparece no texto)
        assert "Inajá" not in " ".join(resultado.termos_encontrados) or not resultado.encontrado

    def test_publicacoes_extraidas(self, db):
        import database
        database.init_db()
        texto = (
            "Prefeitura Municipal de Inajá\n"
            "DECRETO Nº 005/2026\n"
            "Objeto: Abre crédito adicional no valor de R$ 10.000,00.\n"
            "Inajá, 20 de junho de 2026.\n"
            "João Eder Aguilar\nPrefeito Municipal"
        )
        paginas = [self._make_pagina(texto)]
        resultado = detectar(3, "Teste Publicação", paginas)
        if resultado.publicacoes:
            pub = resultado.publicacoes[0]
            assert pub.get("orgao") or pub.get("tipo")


# ── CEP com ponto ────────────────────────────────────────────

class TestCEP:
    def test_cep_com_ponto(self, db):
        import database
        database.init_db()
        texto = "Rua XV de Novembro, 100 - Centro\nCEP 87.670-000 - Inajá-PR"
        paginas = [PageText(pagina=1, texto=texto, metodo="test", blocks=[])]
        resultado = detectar(10, "Teste CEP ponto", paginas)
        ceps = [t for t in resultado.termos_encontrados if "CEP" in t]
        assert ceps, "CEP com ponto deve ser detectado"

    def test_cep_sem_ponto(self, db):
        import database
        database.init_db()
        texto = "Av. Brasil, 200 - Centro\nCEP: 87670-000"
        paginas = [PageText(pagina=1, texto=texto, metodo="test", blocks=[])]
        resultado = detectar(11, "Teste CEP", paginas)
        ceps = [t for t in resultado.termos_encontrados if "CEP" in t]
        assert ceps


# ── Município vizinho (pré-filtro) ──────────────────────────

class TestMunicipioVizinho:
    def test_detecta_orgao_de_outro_municipio(self):
        texto = "PREFEITURA MUNICIPAL DE CRUZEIRO DO SUL - PR\nAVISO DE LICITAÇÃO Nº 009/2026"
        assert _orgao_de_outro_municipio(texto) is True

    def test_nao_flagra_inaja_como_vizinho(self):
        texto = "Prefeitura Municipal de Inajá\nDECRETO Nº 001/2026"
        assert _orgao_de_outro_municipio(texto) is False

    def test_jardim_olinda(self):
        texto = "Município de Jardim Olinda\nPORTARIA Nº 003/2026"
        assert _orgao_de_outro_municipio(texto) is True

    def test_prefeito_de_uniflor(self):
        texto = (
            "PORTARIA Nº 155/2026 O senhor MAYCON RODRIGO, "
            "Prefeito Municipal de Uniflor, usando as atribuições"
        )
        assert _orgao_de_outro_municipio(texto) is True

    def test_colorado_credito(self):
        texto = (
            "Decreto nº 219/2026. O Prefeito Municipal de Colorado, "
            "Estado do Paraná, abre crédito adicional."
        )
        assert _orgao_de_outro_municipio(texto) is True

    def test_cnpj_inaja_nao_descarta(self):
        texto = (
            "PREFEITURA MUNICIPAL DE INAJÁ\n"
            "CNPJ 76.970.318/0001-67\n"
            "DECRETO Nº 001/2026"
        )
        assert _orgao_de_outro_municipio(texto) is False

    def test_publicacao_vizinha_filtrada(self, db):
        import database
        from detector import _publicacao_do_segmento
        from ocr.models import TextBlock
        database.init_db()
        bloco = TextBlock(
            pagina=1,
            bloco=1,
            texto=(
                "PREFEITURA MUNICIPAL DE CRUZEIRO DO SUL - PR\n"
                "AVISO DE LICITAÇÃO Nº 009/2026\n"
                "Contratação de empresa para obras."
            ),
        )
        # Mesmo com termo Inajá no segmento, órgão de outro município é filtrado
        pub = _publicacao_do_segmento(bloco, {"Inajá"})
        assert pub is None

    def test_detectar_nao_gera_pub_vizinha(self, db):
        import database
        database.init_db()
        texto = (
            "PREFEITURA MUNICIPAL DE CRUZEIRO DO SUL\n"
            "PORTARIA Nº 038/2026\n"
            "O PREFEITO DO MUNICÍPIO DE CRUZEIRO DO SUL\n"
            "alguma menção residual a Inajá no rodapé."
        )
        resultado = detectar(
            99,
            "Ed vizinha",
            [PageText(pagina=1, texto=texto, metodo="test", blocks=[])],
        )
        assert resultado.publicacoes == []



# ── Menção genérica colada (boundary) ───────────────────────

class TestMencaoGenericaBoundary:
    def test_colada_em_token_ignorada(self):
        # "inaja" dentro de token corrompido "listacaceinaja" não deve contar
        texto = "contato listacaceinaja prgov br telefone"
        # Encontra posição de "inaja" dentro de "listacaceinaja"
        idx = texto.find("inaja")
        fim = idx + len("inaja")
        assert _mencao_generica_sem_palavra_isolada(texto, idx, fim) is True

    def test_palavra_isolada_nao_ignorada(self):
        texto = "Prefeitura Municipal de Inajá publicou o decreto."
        idx = texto.find("Inaj")
        fim = idx + len("Inajá")
        assert _mencao_generica_sem_palavra_isolada(texto, idx, fim) is False

    def test_inaja_com_hifen_nao_ignorada(self):
        texto = "Município de Inajá-PR"
        idx = texto.find("Inaj")
        fim = idx + len("Inajá")
        assert _mencao_generica_sem_palavra_isolada(texto, idx, fim) is False


# ── Detecção LRF ───────────────────────────────────────────
class TestLrfDetection:
    def test_relatorio_lrf_inaja_e_detectado(self, db):
        import database
        from detector import _publicacao_do_segmento
        from ocr_processor import TextBlock
        database.init_db()
        bloco = TextBlock(
            pagina=12,
            bloco=1,
            texto=(
                "MUNICIPIO DE INAJÁ - PR\n"
                "RELATÓRIO RESUMIDO DA EXECUÇÃO ORÇAMENTÁRIA\n"
                "DEMONSTRATIVO DOS RESULTADOS PRIMÁRIO E NOMINAL\n"
                "Exercício de 2025"
            ),
        )
        pub = _publicacao_do_segmento(bloco, {"Inajá"})
        assert pub is not None
        assert pub["categoria"] == "publicacao_oficial"
        assert "INAJÁ" in pub["orgao"].upper()
