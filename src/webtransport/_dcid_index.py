"""接続の発行済み SCID (= ピアから見た DCID) の索引

`quic` / `h3` / `http3` の Server が共有する。索引は「CID → 接続」の辞書と
「接続 → 発行済み CID 集合」の逆引きで構成し、CID を発行・退役するたびに
両者を同期させる。8 バイト以外の CID は照会形式と合わないため登録しない
(初期 SCID 長起点であり、将来の非 8 バイトは安全側に破棄される)。
"""

from collections.abc import Iterable


def refresh_dcid_index[T](
    index: dict[bytes, T],
    conn_dcids: dict[T, set[bytes]],
    connection: T,
    scids: Iterable[bytes],
) -> None:
    """接続の発行済み SCID を索引に反映する

    新規発行分を登録し、退役で消えた分を破棄する。退役 CID の索引残りは
    無害である (受信結果で破棄と判定され張り替えない) が、接触のたびに
    最新化して残存期間を抑える。

    Args:
        index: 「CID → 接続」の索引
        conn_dcids: 「接続 → 発行済み CID 集合」の逆引き
        connection: 更新する接続
        scids: 接続が発行済みの SCID 列
    """
    current = {cid for cid in scids if len(cid) == 8}
    previous = conn_dcids.get(connection, set())
    for retired in previous - current:
        if index.get(retired) is connection:
            del index[retired]
    for issued in current - previous:
        index[issued] = connection
    conn_dcids[connection] = current


def drop_dcid_index[T](
    index: dict[bytes, T],
    conn_dcids: dict[T, set[bytes]],
    connection: T,
) -> None:
    """接続の索引登録を全て破棄する

    Args:
        index: 「CID → 接続」の索引
        conn_dcids: 「接続 → 発行済み CID 集合」の逆引き
        connection: 破棄する接続
    """
    for cid in conn_dcids.pop(connection, set()):
        if index.get(cid) is connection:
            del index[cid]
