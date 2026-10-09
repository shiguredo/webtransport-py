"""WebTransport エンドポイント URL の解析

`webtransport.Client` が HTTP/3 と HTTP/2 のどちらを選んでも同じ規則で
URL を扱えるよう、解析をここに集約する。
"""


def parse_wt_url(url: str) -> tuple[str, int, str]:
    """WebTransport のエンドポイント URL を (host, port, path) にする

    `https://` のスキームは大文字小文字を問わず除去しない (入力は小文字を
    前提とする)。ポート省略時は 443 を使う。

    Args:
        url: `https://host:port/path` 形式の URL

    Returns:
        (host, port, path) の 3 要素タプル

    Raises:
        ValueError: ポートが整数でない場合
    """
    url = url.replace("https://", "")
    if "/" in url:
        host_port, path = url.split("/", 1)
        path = "/" + path
    else:
        host_port = url
        path = "/"

    if ":" in host_port:
        host, port_str = host_port.split(":")
        port = int(port_str)
    else:
        host = host_port
        port = 443

    return host, port, path
