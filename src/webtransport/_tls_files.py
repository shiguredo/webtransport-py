"""TLS 証明書と秘密鍵ファイルの検証

`quic` / `h3` / `http3` の Server が起動時に共有する検証を置く。
"""

import os


def validate_cert_key_files(certfile: str | None, keyfile: str | None) -> None:
    """証明書と鍵ファイルの存在と読み取り可能性を検証する

    None は未設定として検証を素通りする (接続時に既定動作になる)。
    起動後の削除・権限変更は run() 時の再 raise で検出する。

    Args:
        certfile: 証明書ファイルパス
        keyfile: 秘密鍵ファイルパス

    Raises:
        FileNotFoundError: ファイルが存在しない場合
        PermissionError: ファイルが読み取り不可の場合
    """
    for name, path in (("certfile", certfile), ("keyfile", keyfile)):
        if path is None:
            continue
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{name} not found: {path}")
        if not os.access(path, os.R_OK):
            raise PermissionError(f"{name} is not readable: {path}")
