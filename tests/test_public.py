from api.source_manager import load_sources
from config.settings import settings

def test_sources_load_without_private_configuration():
    sources = load_sources()
    assert len(sources) > 10
    assert len({source.id for source in sources}) == len(sources)

def test_public_example_has_no_delivery_credentials():
    assert settings.openrouter_api_key == ""
    assert settings.feishu_briefing_webhook == ""
