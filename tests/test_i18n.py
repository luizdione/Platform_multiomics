from omica.web.i18n import DEFAULT_LANG, normalizar_lang, t


def test_default_e_pt():
    assert DEFAULT_LANG == "pt"
    assert "ômicas" in t("titulo_plataforma")


def test_traducao_en():
    assert t("nav_inicio", "en") == "Home"


def test_chave_inexistente_fica_visivel():
    # Falha visível de propósito — nunca string vazia silenciosa.
    assert t("chave_que_nao_existe") == "[chave_que_nao_existe]"


def test_normalizar_lang():
    assert normalizar_lang("en") == "en"
    assert normalizar_lang("xx") == "pt"
    assert normalizar_lang(None) == "pt"
