"""WebTransport over HTTP/2 の初期フロー制御 SETTINGS 上限テスト

HTTP/2 の SETTINGS 値は uint32 (RFC 9113 Section 6.5.1) のため、Config の
初期フロー制御値が 2^32 以上だと SETTINGS で切り詰められ、WebTransport-Init
や初期 WT_MAX_DATA / WT_MAX_STREAMS カプセルと値が食い違う。生成時に
2^32 - 1 の上限検査を行い、超過は ValueError にする境界を検証する。
"""

from __future__ import annotations

import pytest

from webtransport import h2

# 上限検査の対象になる SETTINGS 送出対象フィールド
_SETTINGS_FIELDS = [
    pytest.param("wt_initial_max_data", id="max_data"),
    pytest.param("wt_initial_max_stream_data", id="max_stream_data"),
    pytest.param("wt_initial_max_streams_bidi", id="max_streams_bidi"),
    pytest.param("wt_initial_max_streams_uni", id="max_streams_uni"),
]


@pytest.mark.parametrize("field", _SETTINGS_FIELDS)
def test_create_session_with_settings_value_max_succeeds(field: str) -> None:
    """上限値 (2^32 - 1) の Config でセッションが生成できることを確認

    上限検査が > ではなく >= に退行すると許容側の最大値が生成できなくなる。
    client / server の両方で 2^32 - 1 が通ることを表明する。
    """
    # client / server の両方で上限値が生成できることを確認する
    for factory in (h2.Session.create_client, h2.Session.create_server):
        config = h2.Config()
        setattr(config, field, 2**32 - 1)
        # 上限値ちょうどは拒否されない
        session = factory(config)
        assert session is not None
        assert session.is_closed() is False


@pytest.mark.parametrize(
    "field, invalid_value",
    [
        pytest.param("wt_initial_max_data", 2**32, id="max_data"),
        pytest.param("wt_initial_max_stream_data", 2**32, id="max_stream_data"),
        pytest.param(
            "wt_initial_max_stream_data",
            2**32 + 5,
            id="max_stream_data_two_pow_32_plus_5",
        ),
        pytest.param("wt_initial_max_streams_bidi", 2**32, id="max_streams_bidi"),
        pytest.param("wt_initial_max_streams_uni", 2**32, id="max_streams_uni"),
    ],
)
def test_create_session_with_settings_value_over_max_raises_value_error(
    field: str, invalid_value: int
) -> None:
    """2^32 以上の Config でセッション生成が ValueError になることを確認

    SETTINGS は uint32 のため、2^32 以上は切り詰められて WebTransport-Init や
    初期 WT_MAX_DATA / WT_MAX_STREAMS カプセルと食い違う。生成時に拒否し、
    SETTINGS と他経路の値が一致することを保証する。2^32 + 5 は切り詰めで 5 に
    なる元の再現値である。
    """
    # client / server の両方で上限値超えが拒否されることを確認する
    for factory in (h2.Session.create_client, h2.Session.create_server):
        config = h2.Config()
        setattr(config, field, invalid_value)
        # エラーメッセージにフィールド名と実際の値が含まれる
        with pytest.raises(ValueError, match=rf"{field} must be less than 2\^32: {invalid_value}"):
            factory(config)
