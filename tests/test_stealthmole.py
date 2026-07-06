"""StealthMole 커넥터 테스트 — 네트워크 mock, 라이브 호출 없음.

커버:
  1. GM 레코드 → NewsEvent 매핑 (id 규약·source·confidence·attrs).
  2. RM 레코드 → NewsEvent 매핑 (title=victim+attack_group, summary=sector/country).
  3. LM 레코드 → NewsEvent 매핑 (title·author).
  4. proof_url "Not supported to FREE version" → source_url = 내부 URI.
  5. 개인정보(이메일·비밀번호) 패턴 레코드 → None(skip).
  6. confidence 상한 = NEWS_MAX_CONFIDENCE(0.4) 이하.
  7. store 왕복: write_newsevent provenance 강제 통과.
  8. ingest mock: httpx.Client patch → 모듈별 write count 검증.

실행: .venv/bin/python -m pytest tests/test_stealthmole.py -v
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from connectors.stealthmole import (
    SM_DEFAULT_LIMIT,
    _has_pii,
    _sm_news_id,
    _sm_source_url,
    gm_record_to_news,
    ingest,
    lm_record_to_news,
    rm_record_to_news,
)
from ontology.model import NEWS_MAX_CONFIDENCE
from ontology.store_local import LocalOntologyStore

# ── 픽스처 ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def db(tmp_path):
    """임시 SQLite store."""
    store = LocalOntologyStore(str(tmp_path / "test.db"))
    from ontology.model import KADIZ_REGION

    store.write_region(KADIZ_REGION)
    return store


_NOW_TS = int(time.time())

# ── 샘플 레코드 ──────────────────────────────────────────────────────────────

_GM_RECORD = {
    "id": "gm-uuid-001",
    "title": "Korean government portal breach",
    "author": "th3actor",
    "detection_datetime": _NOW_TS,
    "proof_url": "https://forum.example/post/123",
}

_GM_FREE_RECORD = {
    "id": "gm-uuid-002",
    "title": "Some government leak",
    "author": "hacker99",
    "detection_datetime": _NOW_TS,
    "proof_url": "Not supported to FREE version",
}

_RM_RECORD = {
    "id": "rm-uuid-001",
    "victim": "Incheon Airport",
    "attack_group": "LockBit",
    "detection_datetime": _NOW_TS,
    "proof_url": "https://darkweb.example/lockbit",
    "site": "lockbit.onion",
    "country": "South Korea",
    "sector": "Transportation",
}

_LM_RECORD = {
    "id": "lm-uuid-001",
    "title": "Korean airline credentials leaked",
    "author": "leakuser",
    "detection_datetime": _NOW_TS,
    "proof_url": "",
}

_PII_RECORD_EMAIL = {
    "id": "gm-pii-001",
    "title": "user@example.com leaked",
    "author": "attacker",
    "detection_datetime": _NOW_TS,
    "proof_url": "",
}

_PII_RECORD_PASSWORD = {
    "id": "gm-pii-002",
    "title": "Credentials dump",
    "author": "attacker",
    "detection_datetime": _NOW_TS,
    "proof_url": "https://example.com",
    "password": "password=s3cr3t123",
}


# ── 1. GM 매핑 ───────────────────────────────────────────────────────────────


def test_gm_mapping_basic():
    """GM 레코드 → NewsEvent id·source·confidence·attrs."""
    nv = gm_record_to_news(_GM_RECORD)
    assert nv is not None
    assert nv.id == "sm-gm-gm-uuid-001"
    assert nv.source == "stealthmole"
    assert nv.source_url == "https://forum.example/post/123"
    assert nv.ts == _NOW_TS
    assert nv.title == "Korean government portal breach"
    assert nv.confidence <= NEWS_MAX_CONFIDENCE
    assert nv.attrs["module"] == "gm"
    assert nv.attrs["author"] == "th3actor"


# ── 2. RM 매핑 ───────────────────────────────────────────────────────────────


def test_rm_mapping_title_and_summary():
    """RM → title=victim—attack_group, summary=sector/country."""
    nv = rm_record_to_news(_RM_RECORD)
    assert nv is not None
    assert nv.id == "sm-rm-rm-uuid-001"
    assert "Incheon Airport" in nv.title
    assert "LockBit" in nv.title
    assert "Transportation" in nv.summary
    assert "South Korea" in nv.summary
    assert nv.attrs["attack_group"] == "LockBit"
    assert nv.attrs["country"] == "South Korea"


# ── 3. LM 매핑 ───────────────────────────────────────────────────────────────


def test_lm_mapping_basic():
    """LM 레코드 → NewsEvent 기본 매핑."""
    nv = lm_record_to_news(_LM_RECORD)
    assert nv is not None
    assert nv.id == "sm-lm-lm-uuid-001"
    assert nv.title == "Korean airline credentials leaked"
    assert nv.attrs["module"] == "lm"
    assert nv.attrs["author"] == "leakuser"


# ── 4. proof_url free-version → 내부 URI ─────────────────────────────────────


def test_free_version_url_becomes_internal_uri():
    """proof_url "Not supported to FREE version" → stealthmole://{module}/{id}."""
    nv = gm_record_to_news(_GM_FREE_RECORD)
    assert nv is not None
    assert nv.source_url == "stealthmole://gm/gm-uuid-002"
    # provenance 요건: source_url이 비어있지 않아야 한다
    assert nv.source_url != ""


def test_sm_source_url_empty_proof():
    """빈 proof_url도 내부 URI로 대체."""
    url = _sm_source_url("lm", "abc-123", "")
    assert url == "stealthmole://lm/abc-123"


# ── 5. 개인정보 패턴 skip ─────────────────────────────────────────────────────


def test_pii_email_record_skipped():
    """이메일 패턴 포함 레코드는 None 반환(skip)."""
    nv = gm_record_to_news(_PII_RECORD_EMAIL)
    assert nv is None


def test_pii_password_record_skipped():
    """password= 패턴 포함 레코드는 None 반환(skip)."""
    nv = gm_record_to_news(_PII_RECORD_PASSWORD)
    assert nv is None


def test_has_pii_detects_email():
    assert _has_pii({"data": "user@example.com"}) is True


def test_has_pii_clean_record():
    assert _has_pii({"title": "aviation threat", "author": "hacker"}) is False


# ── 6. confidence 상한 ───────────────────────────────────────────────────────


def test_confidence_at_most_max():
    """모든 모듈의 confidence ≤ NEWS_MAX_CONFIDENCE(0.4)."""
    for fn, rec in [
        (gm_record_to_news, _GM_RECORD),
        (rm_record_to_news, _RM_RECORD),
        (lm_record_to_news, _LM_RECORD),
    ]:
        nv = fn(rec)
        assert nv is not None
        assert nv.confidence <= NEWS_MAX_CONFIDENCE
        # StealthMole baseline = 0.25
        assert nv.confidence == 0.25


# ── 7. store 왕복 (provenance 강제 통과) ─────────────────────────────────────


def test_store_write_gm(db):
    """GM NewsEvent write → store에 저장됨(provenance 강제 통과)."""
    nv = gm_record_to_news(_GM_RECORD)
    assert nv is not None
    db.write_newsevent(nv, mentions=[])
    news = db.query_news()
    assert len(news) == 1
    assert news[0].source == "stealthmole"
    assert news[0].id == nv.id


def test_store_write_free_version_url(db):
    """내부 URI(stealthmole://)도 source_url 요건 통과."""
    nv = gm_record_to_news(_GM_FREE_RECORD)
    assert nv is not None
    # ProvenanceError 없이 write 돼야 함
    db.write_newsevent(nv, mentions=[])
    news = db.query_news()
    assert news[0].source_url.startswith("stealthmole://")


# ── 8. ingest mock ───────────────────────────────────────────────────────────


def _make_search_response(records: list[dict]) -> MagicMock:
    """httpx.Response mock — json() → {data: records, totalCount: len(records)}."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"data": records, "totalCount": len(records)}
    return resp


def test_ingest_mock_counts(db):
    """ingest mock: GM 2건·RM 1건·LM 1건 → counts 검증."""
    gm_records = [_GM_RECORD, _GM_FREE_RECORD]
    rm_records = [_RM_RECORD]
    lm_records = [_LM_RECORD]

    call_order = [
        _make_search_response(gm_records),
        _make_search_response(rm_records),
        _make_search_response(lm_records),
    ]

    with patch("connectors.stealthmole.BASE_URL", "https://mock.stealthmole.test"):
        with patch("connectors.stealthmole._SECRET_KEY", "test-secret-key-dummy-32-bytes-minimum-for-hmac"):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__enter__.return_value = mock_client
                mock_client.get.side_effect = call_order

                counts = ingest(db)

    assert counts["gm"] == 2
    assert counts["rm"] == 1
    assert counts["lm"] == 1
    # store에도 4건 반영돼야 함
    assert len(db.query_news()) == 4


def test_ingest_skips_pii(db):
    """PII 레코드는 ingest 중 skip → store에 저장 안 됨."""
    pii_gm = [_PII_RECORD_EMAIL]
    clean_rm = [_RM_RECORD]
    clean_lm = [_LM_RECORD]

    call_order = [
        _make_search_response(pii_gm),
        _make_search_response(clean_rm),
        _make_search_response(clean_lm),
    ]

    with patch("connectors.stealthmole.BASE_URL", "https://mock.stealthmole.test"):
        with patch("connectors.stealthmole._SECRET_KEY", "test-secret-key-dummy-32-bytes-minimum-for-hmac"):
            with patch("httpx.Client") as mock_client_cls:
                mock_client = MagicMock()
                mock_client_cls.return_value.__enter__.return_value = mock_client
                mock_client.get.side_effect = call_order

                counts = ingest(db)

    # PII GM은 skip → 0
    assert counts["gm"] == 0
    assert counts["rm"] == 1
    assert counts["lm"] == 1
    assert len(db.query_news()) == 2
