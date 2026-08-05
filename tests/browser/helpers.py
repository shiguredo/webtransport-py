"""実ブラウザ (Chromium / WebKit) を使った WebTransport E2E 検証のヘルパー

Shiguredo WebTransport DevTools
(https://moqt-devtools.shiguredo.app/webtransport-devtools) をブラウザ側
WebTransport クライアントとして、webtransport-py の echo サーバーへの接続と
送受信を検証する。このモジュールはブラウザ種別ごとのテストファイル
(test_webtransport_chromium.py / test_webtransport_webkit.py) から呼び出される
共通実装であり、pytest の collection 対象にならないように test_ / prop_ 以外の
ファイル名にしている。通常の make test と CI の collection 対象からは
pyproject.toml の addopts の --ignore=tests/browser で除外されている。
"""

import queue
import urllib.parse

from playwright.sync_api import Page, expect

# DevTools テストページの URL
DEVTOOLS_URL = "https://moqt-devtools.shiguredo.app/webtransport-devtools"


def _devtools_url(browser_server, certificate_hash_value: str) -> str:
    """DevTools テストページの URL を組み立てる

    certificateHash は base64 で、URL パラメータに含めるには percent-encoding
    が必要である。base64 に含まれる '+' は URLSearchParams がスペースに
    デコードされ、atob() は空白を無視するため正しいバイト列に復元できず
    証明書ハッシュ検証が失敗する。urllib.parse.urlencode は '+' を '%2B' に
    percent-encode するため正しく渡せる。
    """
    params = urllib.parse.urlencode(
        {
            "url": browser_server.url,
            "certificateHash": certificate_hash_value,
        }
    )
    return f"{DEVTOOLS_URL}?{params}"


def run_browser_e2e_webtransport(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """実ブラウザから WebTransport サーバーへの接続と送受信を検証する

    検証観点 5 項目 (接続確立・双方向ストリームの送受信・単方向ストリームの
    送信・サーバーからの単方向ストリーム送信・データグラムの送受信) を
    1 回の接続で順次検証する。ページロードは外部ホスト依存のため最小限に
    留め、接続イベントの混線を避ける。
    """
    # 接続先 URL と証明書ハッシュを指定して DevTools ページを開く
    page.goto(_devtools_url(browser_server, certificate_hash_value))

    # WebTransport API が利用可能な状態でページがロードされることを確認する
    expect(page.get_by_text("WebTransport Not Supported")).not_to_be_visible()

    # 検証観点 1: 接続確立
    # Connect ボタンをクリックし、ページ側の Connected 表示 (部分一致で
    # "Disconnected" にマッチしないよう完全一致にする) とサーバー側の
    # on_session_ready の両方で確認する
    page.get_by_test_id("connection-connect").click()
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error

    # 検証観点 2: サーバーからの単方向ストリーム送信
    # セッション確立をトリガーに Server.open_stream で単方向ストリームが
    # 開かれ (戻り値が 0 以上)、Incoming Streams セクションに表示されることを
    # 確認する
    try:
        opened_payload = browser_server.wait_event("server_stream_opened", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側で単方向ストリームが開かれませんでした") from error
    opened_stream_id, _ = opened_payload
    assert opened_stream_id >= 0

    incoming_section = (
        page.get_by_role("heading", name="Incoming Streams").locator("..").locator("..")
    )
    incoming_message = (
        incoming_section.locator("div.text-xs")
        .filter(has_text="RECV:")
        .filter(has_text="server-unidirectional-payload")
    )
    expect(incoming_message).to_be_visible(timeout=10_000)

    # 検証観点 3: 双方向ストリームの送受信
    # 双方向ストリーム (QUIC stream_id % 4 == 0) のみエコーバックする。
    # ページ側でメッセージを送信し、RECV 表示とサーバー側の on_stream_data で
    # 確認する
    bidi_section = (
        page.get_by_role("heading", name="Bidirectional Streams").locator("..").locator("..")
    )
    bidi_section.get_by_test_id("bidi-new-stream").click()
    bidi_message = "bidi-echo-message"
    bidi_section.get_by_placeholder("Enter message...").fill(bidi_message)
    bidi_section.get_by_role("button", name="Send").click()

    # DevTools はメッセージをタイムスタンプ・SEND:/RECV: ラベル・データの
    # 別要素で表示するため、"RECV:" ラベルとデータを含むメッセージ要素で確認する
    recv_message = (
        bidi_section.locator("div.text-xs").filter(has_text="RECV:").filter(has_text=bidi_message)
    )
    expect(recv_message).to_be_visible(timeout=10_000)
    try:
        bidi_payload = browser_server.wait_event("stream_data", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側で双方向ストリームが受信されませんでした") from error
    _, bidi_stream_id, bidi_data, _ = bidi_payload
    assert bidi_stream_id % 4 == 0
    assert bidi_data == bidi_message.encode()

    # 検証観点 4: 単方向ストリームの送信
    # クライアント起点の単方向ストリーム (QUIC stream_id % 4 == 2) は
    # エコーバックしないため、ページ側の SEND 表示とサーバー側の
    # on_stream_data で受信を確認する
    outgoing_section = (
        page.get_by_role("heading", name="Outgoing Streams").locator("..").locator("..")
    )
    outgoing_section.get_by_test_id("uni-send-new-stream").click()
    uni_message = "uni-send-message"
    outgoing_section.get_by_placeholder("Enter message...").fill(uni_message)
    outgoing_section.get_by_role("button", name="Send").click()

    send_message = (
        outgoing_section.locator("div.text-xs")
        .filter(has_text="SEND:")
        .filter(has_text=uni_message)
    )
    expect(send_message).to_be_visible(timeout=10_000)
    try:
        uni_payload = browser_server.wait_event("stream_data", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側で単方向ストリームが受信されませんでした") from error
    _, uni_stream_id, uni_data, _ = uni_payload
    assert uni_stream_id % 4 == 2
    assert uni_data == uni_message.encode()

    # 検証観点 5: データグラムの送受信
    # UDP のロスに備えて複数回送信し、サーバー側・ページ側それぞれで
    # 少なくとも 1 回の受信を確認する
    datagram_section = page.get_by_role("heading", name="Datagrams").locator("..").locator("..")
    datagram_message = "datagram-echo-message"
    datagram_input = datagram_section.get_by_test_id("datagram-input")
    for _ in range(5):
        datagram_input.fill(datagram_message)
        datagram_input.press("Enter")

    # 複数回送信するため複数の RECV 要素が生じるので、先頭要素で確認する
    recv_datagram = (
        datagram_section.locator("div.text-xs")
        .filter(has_text="RECV:")
        .filter(has_text=datagram_message)
        .first
    )
    expect(recv_datagram).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("datagram", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でデータグラムが受信されませんでした") from error


def run_browser_e2e_send_order_stream(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """sendOrder を指定した双方向ストリームの作成と送受信を検証する

    sendOrder は W3C WebTransport §6.11 の送信優先度 (整数値、大きいほど優先)。
    ページ側で sendOrder を指定して双方向ストリームを作成し、エコーバックを
    確認する。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))
    page.get_by_test_id("connection-connect").click()
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error

    # sendOrder を指定して双方向ストリームを作成する
    bidi_section = (
        page.get_by_role("heading", name="Bidirectional Streams").locator("..").locator("..")
    )
    bidi_section.get_by_test_id("bidi-stream-send-order").fill("5")
    bidi_section.get_by_test_id("bidi-new-stream").click()

    # 作成したストリームでメッセージを送信し、エコーバックを確認する
    message = "send-order-message"
    bidi_section.get_by_placeholder("Enter message...").fill(message)
    bidi_section.get_by_role("button", name="Send").click()

    recv_message = (
        bidi_section.locator("div.text-xs").filter(has_text="RECV:").filter(has_text=message)
    )
    expect(recv_message).to_be_visible(timeout=10_000)
    try:
        payload = browser_server.wait_event("stream_data", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でストリームデータが受信されませんでした") from error
    _, stream_id, data, _ = payload
    assert stream_id % 4 == 0
    assert data == message.encode()


def run_browser_e2e_close_with_code(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """closeCode / reason を指定した graceful close を検証する

    closeCode / reason は W3C WebTransport §6.10 のセッション終了パラメータ。
    Disconnect ボタン経由で close() に渡され、サーバー側の SESSION_CLOSED
    イベントで確認する。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))
    page.get_by_test_id("connection-connect").click()
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error

    # closeCode / reason を指定して切断する
    page.get_by_test_id("close-code").fill("42")
    page.get_by_test_id("close-reason").fill("test-close-reason")
    page.get_by_test_id("connection-connect").click()

    # サーバー側でセッション終了を確認する
    try:
        browser_server.wait_event("session_closed", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッション終了が確認されませんでした") from error


def run_browser_e2e_datagram_settings(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """データグラムの動的設定 (maxAge / maxBufferedDatagrams) を検証する

    incomingMaxAge / outgoingMaxAge / incomingMaxBufferedDatagrams /
    outgoingMaxBufferedDatagrams は W3C WebTransport §5.3 のデータグラム設定。
    接続中に Apply で反映し、設定後にデータグラムの送受信が引き続き
    機能することを確認する。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))
    page.get_by_test_id("connection-connect").click()
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error

    # データグラム設定を入力して Apply する
    page.get_by_test_id("datagram-incoming-max-age").fill("1000")
    page.get_by_test_id("datagram-outgoing-max-age").fill("2000")
    page.get_by_test_id("datagram-incoming-max-buffered").fill("10")
    page.get_by_test_id("datagram-outgoing-max-buffered").fill("20")
    page.get_by_test_id("datagram-apply").click()

    # Apply が成功し、エラーが表示されないことを確認する
    expect(page.get_by_text("Applied", exact=True)).to_be_visible(timeout=10_000)

    # 設定後にデータグラムの送受信が機能することを確認する
    datagram_section = page.get_by_role("heading", name="Datagrams").locator("..").locator("..")
    datagram_message = "datagram-after-settings"
    datagram_input = datagram_section.get_by_test_id("datagram-input")
    for _ in range(3):
        datagram_input.fill(datagram_message)
        datagram_input.press("Enter")

    recv_datagram = (
        datagram_section.locator("div.text-xs")
        .filter(has_text="RECV:")
        .filter(has_text=datagram_message)
        .first
    )
    expect(recv_datagram).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("datagram", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でデータグラムが受信されませんでした") from error


def run_browser_e2e_connection_options(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """WebTransport オプション指定時の接続を検証する

    requireUnreliable / congestionControl は W3C WebTransport §6.9 の接続
    オプション。サーバーは QUIC (UDP) ベースで unreliable に対応しているため、
    これらのオプションを指定しても接続が確立できることを確認する。

    注: allowPooling は certificateHash と排他のため、自己署名証明書を
    certificateHash でピン留めする本テストでは指定できない。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))

    # requireUnreliable を ON、congestionControl を low-latency に指定する
    page.get_by_test_id("connection-require-unreliable").check()
    page.get_by_test_id("connection-congestion-control").select_option("low-latency")
    page.get_by_test_id("connection-connect").click()

    # オプション指定でも接続が確立できることを確認する
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error


def run_browser_e2e_custom_headers(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """カスタムヘッダー付きの接続を検証する

    headers オプションは W3C WebTransport §6.9 で、CONNECT リクエストに
    カスタムヘッダーを付与する。ヘッダー付きでも接続が確立できることを
    確認する。サーバー側の高レベル API では受信ヘッダーを観測できないため、
    接続成功のみを確認する。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))

    # カスタムヘッダーを指定する
    page.get_by_test_id("connection-headers").fill(
        "X-Custom-Header: custom-value\nX-Another-Header: test-value"
    )
    page.get_by_test_id("connection-connect").click()

    # ヘッダー付きでも接続が確立できることを確認する
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error


def run_browser_e2e_stream_options(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """ストリーム作成オプション (sendOrder / waitUntilAvailable) を検証する

    sendOrder (§6.11) は送信優先度、waitUntilAvailable (§6.12) は送信準備が
    整うまでストリーム作成を待つオプション。sendOrder 付きの単方向ストリームと
    waitUntilAvailable 付きの双方向ストリームをそれぞれ作成し、送受信が
    機能することを確認する。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))
    page.get_by_test_id("connection-connect").click()
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error

    # sendOrder 付きの単方向ストリームを作成して送信する
    outgoing_section = (
        page.get_by_role("heading", name="Outgoing Streams").locator("..").locator("..")
    )
    outgoing_section.get_by_test_id("uni-send-stream-send-order").fill("3")
    outgoing_section.get_by_test_id("uni-send-new-stream").click()
    uni_message = "uni-with-send-order"
    outgoing_section.get_by_placeholder("Enter message...").fill(uni_message)
    outgoing_section.get_by_role("button", name="Send").click()

    send_message = (
        outgoing_section.locator("div.text-xs")
        .filter(has_text="SEND:")
        .filter(has_text=uni_message)
    )
    expect(send_message).to_be_visible(timeout=10_000)
    try:
        uni_payload = browser_server.wait_event("stream_data", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側で単方向ストリームが受信されませんでした") from error
    _, uni_stream_id, uni_data, _ = uni_payload
    assert uni_stream_id % 4 == 2
    assert uni_data == uni_message.encode()

    # waitUntilAvailable 付きの双方向ストリームを作成して送信する
    bidi_section = (
        page.get_by_role("heading", name="Bidirectional Streams").locator("..").locator("..")
    )
    bidi_section.get_by_test_id("bidi-stream-wait-until-available").check()
    bidi_section.get_by_test_id("bidi-new-stream").click()
    bidi_message = "bidi-wait-until-available"
    bidi_section.get_by_placeholder("Enter message...").fill(bidi_message)
    bidi_section.get_by_role("button", name="Send").click()

    recv_message = (
        bidi_section.locator("div.text-xs").filter(has_text="RECV:").filter(has_text=bidi_message)
    )
    expect(recv_message).to_be_visible(timeout=10_000)
    try:
        bidi_payload = browser_server.wait_event("stream_data", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側で双方向ストリームが受信されませんでした") from error
    _, bidi_stream_id, bidi_data, _ = bidi_payload
    assert bidi_stream_id % 4 == 0
    assert bidi_data == bidi_message.encode()


def run_browser_e2e_close_stream(
    page: Page,
    browser_server,
    certificate_hash_value: str,
) -> None:
    """双方向ストリームを close する動作を検証する

    双方向ストリームの Close ボタンをクリックすると writer.close() が呼ばれ、
    ストリームパネルが Closed 表示になることを確認する。
    """
    page.goto(_devtools_url(browser_server, certificate_hash_value))
    page.get_by_test_id("connection-connect").click()
    expect(page.get_by_text("Connected", exact=True)).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("session_ready", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でセッションが確立されませんでした") from error

    # 双方向ストリームを作成してメッセージを送信する
    bidi_section = (
        page.get_by_role("heading", name="Bidirectional Streams").locator("..").locator("..")
    )
    bidi_section.get_by_test_id("bidi-new-stream").click()
    message = "before-close"
    bidi_section.get_by_placeholder("Enter message...").fill(message)
    bidi_section.get_by_role("button", name="Send").click()

    recv_message = (
        bidi_section.locator("div.text-xs").filter(has_text="RECV:").filter(has_text=message)
    )
    expect(recv_message).to_be_visible(timeout=10_000)
    try:
        browser_server.wait_event("stream_data", timeout=10.0)
    except queue.Empty as error:
        raise AssertionError("サーバー側でストリームデータが受信されませんでした") from error

    # Close stream ボタンをクリックしてストリームを閉じる
    bidi_section.get_by_title("Close stream").click()

    # ストリームパネルが Closed 表示になることを確認する
    expect(bidi_section.get_by_text("Closed", exact=True)).to_be_visible(timeout=10_000)
